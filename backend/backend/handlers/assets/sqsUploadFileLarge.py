"""
Large file asynchronous processor Lambda for VAMS.
Handles SQS messages for processing large files (>1GB) asynchronously.
"""

import os
import boto3
import json
import logging
from typing import Dict, Any, List
from common.timestamps import utc_now
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_file_upload
from common.dynamodb import to_update_expr
from common.s3 import validateS3AssetExtensionsAndContentType, list_all_objects, is_object_version_archived, region_routing_s3_client, region_routing_s3_resource, register_bucket_region
from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    UPLOAD_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
    replace_metadata_copy_args,
)
from common.s3PathPatterns import (
    PREVIEW_FILE_PATTERN,
    ALLOWED_PREVIEW_FILE_EXTENSIONS,
)
from models.common import VAMSGeneralErrorResponse
from common import largeObjectCopy as loc
from common import uploadStatus
from common.resourceNames import ResourceKeys, get_table_name

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

s3 = region_routing_s3_client()
s3_resource = region_routing_s3_resource()
dynamodb = boto3.resource('dynamodb', config=retry_config)
lambda_client = boto3.client('lambda', config=retry_config)
sfn_client = boto3.client('stepfunctions', config=retry_config)
logger = safeLogger(service_name="SqsUploadFileLarge")

# Constants
MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024  # 5MB maximum size for preview files
# uploadIdS3 sentinels. "zero-byte" marks a file the processor must materialize itself;
# "external" marks a file an external producer already wrote in full at the temporary key,
# so neither has a real S3 multipart upload to complete or abort.
ZERO_BYTE_UPLOAD_ID = "zero-byte"
EXTERNAL_UPLOAD_ID = "external"
# Receives before SQS moves a message to the dead-letter queue. Matches maxReceiveCount on
# LargeFileProcessingQueue (infra apiBuilder-nestedStack.ts). On the final receive a retryable
# failure aborts the multipart upload, since no later receive could complete it.
try:
    LARGE_FILE_MAX_RECEIVE_COUNT = int(os.environ.get("LARGE_FILE_QUEUE_MAX_RECEIVE_COUNT", "") or 3)
except ValueError:
    LARGE_FILE_MAX_RECEIVE_COUNT = 3
# complete_multipart_upload error codes that a retry with the same parts cannot fix.
DETERMINISTIC_COMPLETE_ERROR_CODES = (
    'InvalidPart', 'InvalidPartOrder', 'EntityTooSmall', 'MalformedXML', 'InvalidArgument',
)
# head_object error codes for a key with no current version.
S3_NOT_FOUND_ERROR_CODES = ('404', 'NoSuchKey', 'NotFound')
# Tolerance, in seconds, when comparing the stored file's LastModified (whole seconds) with the
# SQS SentTimestamp of the message (milliseconds) to decide whether the file was stored by it.
ALREADY_STORED_CLOCK_TOLERANCE_SECONDS = 1
# Sample size for asset-type detection. This is a best-effort visibility
# classification (empty vs. single-file vs. folder), not a correctness-critical
# read, so it is intentionally capped rather than scanning the entire asset —
# paging every object of a very large asset would add latency to the upload path.
ASSET_TYPE_DETECTION_SAMPLE_SIZE = 1000
allowed_preview_extensions = ALLOWED_PREVIEW_FILE_EXTENSIONS

# Load environment variables
try:
    from common.resourceNames import ResourceKeys, get_table_name
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

# Initialize DynamoDB tables
asset_table = dynamodb.Table(asset_storage_table_name)


class LargeFileAlreadyProcessed(Exception):
    """A redelivered message names a file that an earlier receive already stored.

    SQS standard queues deliver at least once, so the same message can arrive after an
    earlier receive stored the file, including one that timed out or failed after it deleted
    the staged object and before it wrote its audit entry or the file's outcome. The message
    is consumed with no cleanup; finish_stored_file writes what the earlier receive did not
    (the audit entry once, the outcome) when the upload record tracks the file.
    """


def build_upload_change_metadata(user_id):
    """Build change-provenance metadata for an uploaded file version."""
    return {
        VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_UPLOAD,
        VAMS_CHANGE_USER_ID_METADATA_KEY: user_id or "SYSTEM_USER",
    }


def build_workflow_change_metadata(user_id, workflow_id, execution_id):
    """Build change-provenance metadata for a workflow-execution output file.

    Returns an empty dict when no workflow context is supplied so non-workflow
    uploads keep their default change source.
    """
    if not workflow_id and not execution_id:
        return {}
    return {
        VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
        VAMS_CHANGE_USER_ID_METADATA_KEY: user_id or "SYSTEM_USER",
        VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY: workflow_id or "",
        VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: execution_id or "",
    }


def resolve_change_metadata(file_info: Dict[str, Any]) -> Dict[str, str]:
    """Resolve the change-provenance metadata for a queued file.

    Workflow provenance wins when the queuing handler supplied a workflow context;
    otherwise the file is attributed to a user upload.
    """
    user_id = file_info.get('changeUserId')
    change_metadata = build_workflow_change_metadata(
        user_id,
        file_info.get('workflowId'),
        file_info.get('workflowExecutionId')
    )
    if not change_metadata:
        change_metadata = build_upload_change_metadata(user_id)
    return change_metadata


def is_large_file_already_stored(file_info: Dict[str, Any], sent_timestamp_ms: int = None) -> bool:
    """Whether an earlier receive of this message already stored the file at its final key.

    Called only once the staged object is gone, so the message is either a duplicate of a
    finished file or names a file that can no longer be finished. The final object counts
    as stored by this message when it carries this file's asset, database and change
    provenance and was written after the message was sent; an older version at the same key
    (an earlier upload of the same path) does not. Without the message's SentTimestamp the
    answer is False, so the file is treated as not stored.

    Args:
        file_info: Dictionary containing file processing information
        sent_timestamp_ms: The SQS SentTimestamp of the message, in epoch milliseconds

    Returns:
        True if the final object was stored by this message

    Raises:
        ClientError: a failed read other than a missing key, which is retryable
    """
    if not sent_timestamp_ms:
        return False

    try:
        head = s3.head_object(Bucket=file_info['bucketName'], Key=file_info['finalS3Key'])
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') in S3_NOT_FOUND_ERROR_CODES:
            return False
        raise

    stored_metadata = head.get('Metadata') or {}
    expected_metadata = {
        DATABASE_ID_METADATA_KEY: file_info.get('databaseId'),
        ASSET_ID_METADATA_KEY: file_info.get('assetId'),
        # Blank provenance entries are left off the stored object (replace_metadata_copy_args)
        **{key: value for key, value in resolve_change_metadata(file_info).items() if value not in ('', None)},
    }
    if any(stored_metadata.get(key) != value for key, value in expected_metadata.items()):
        return False

    last_modified = head.get('LastModified')
    if last_modified is None:
        return False
    return last_modified.timestamp() >= sent_timestamp_ms / 1000 - ALREADY_STORED_CLOCK_TOLERANCE_SECONDS


def validate_sqs_message(message_data: Dict[str, Any], correlation_id: str = None) -> bool:
    """
    Validate the structure of an SQS message for large file processing.
    
    Args:
        message_data: The parsed SQS message data
        correlation_id: Optional correlation ID for logging
        
    Returns:
        True if message is valid, False otherwise
    """
    try:
        correlation_suffix = f" - CorrelationId: {correlation_id}" if correlation_id else ""
        
        # Check for required top-level fields
        if 'fileInfo' not in message_data:
            logger.error(f"Missing 'fileInfo' in SQS message{correlation_suffix}")
            return False
            
        file_info = message_data['fileInfo']
        
        # Check for required fileInfo fields
        required_fields = [
            'relativeKey', 'uploadIdS3', 'parts', 'tempS3Key', 'finalS3Key',
            'bucketName', 'databaseId', 'assetId', 'uploadId', 'uploadType'
        ]
        
        missing_fields = []
        for field in required_fields:
            if field not in file_info:
                missing_fields.append(field)
                
        if missing_fields:
            logger.error(f"Missing required fields in fileInfo: {missing_fields}{correlation_suffix}")
            return False
                
        # Validate parts structure
        parts = file_info.get('parts', [])
        if not isinstance(parts, list):
            logger.error(f"Parts must be a list, got {type(parts)}{correlation_suffix}")
            return False
            
        # Validate each part structure
        invalid_parts = []
        for i, part in enumerate(parts):
            if not isinstance(part, dict):
                invalid_parts.append(f"Part {i}: not a dict")
            elif 'PartNumber' not in part:
                invalid_parts.append(f"Part {i}: missing PartNumber")
            elif 'ETag' not in part:
                invalid_parts.append(f"Part {i}: missing ETag")
            elif not isinstance(part['PartNumber'], int) or part['PartNumber'] < 1:
                invalid_parts.append(f"Part {i}: invalid PartNumber")
                
        if invalid_parts:
            logger.error(f"Invalid part structures: {invalid_parts}{correlation_suffix}")
            return False
                
        # Validate upload type
        upload_type = file_info.get('uploadType')
        if upload_type not in ['assetFile', 'assetPreview']:
            logger.error(f"Invalid uploadType: {upload_type}, must be 'assetFile' or 'assetPreview'{correlation_suffix}")
            return False
            
        # Validate string fields are not empty
        string_fields = ['relativeKey', 'uploadIdS3', 'tempS3Key', 'finalS3Key', 'bucketName', 'databaseId', 'assetId', 'uploadId']
        empty_fields = []
        for field in string_fields:
            value = file_info.get(field)
            if not value or not isinstance(value, str) or not value.strip():
                empty_fields.append(field)
                
        if empty_fields:
            logger.error(f"Empty or invalid string fields in fileInfo: {empty_fields}{correlation_suffix}")
            return False
            
        logger.debug(f"SQS message validation passed{correlation_suffix}")
        return True
        
    except Exception as e:
        logger.exception(f"Error validating SQS message{correlation_suffix}: {e}")
        return False

def create_zero_byte_file(bucket_name: str, key: str, upload_id: str, database_id: str, asset_id: str, change_metadata: Dict[str, str] = None) -> bool:
    """
    Create a zero-byte file in S3.
    Ported from uploadFile.py.

    Args:
        bucket_name: The S3 bucket name
        key: The S3 object key
        upload_id: The upload ID for metadata
        database_id: The database ID for metadata
        asset_id: The asset ID for metadata
        change_metadata: Change-provenance metadata for the new object version

    Returns:
        True if successful, False otherwise
    """
    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=b'',  # Empty content for zero-byte file
            ContentType='application/octet-stream',
            # Grant the bucket owner full control so a zero-byte file written into a
            # cross-account asset bucket is owned/readable by that account.
            ACL='bucket-owner-full-control',
            Metadata={
                DATABASE_ID_METADATA_KEY: database_id,
                ASSET_ID_METADATA_KEY: asset_id,
                UPLOAD_ID_METADATA_KEY: upload_id,
                **(change_metadata or build_upload_change_metadata(None))
            }
        )
        logger.info(f"Created zero-byte file: {key}")
        return True
    except Exception as e:
        logger.exception(f"Error creating zero-byte file {key}: {e}")
        return False

def delete_s3_object(bucket: str, key: str) -> bool:
    """
    Delete a staged upload object, removing its current version rather than hiding it.
    Ported from uploadFile.py.

    On a versioned bucket a delete without a version ID only adds a delete marker and keeps the
    staged copy as a noncurrent version, so the current version is deleted by its ID. A key with
    no current version needs no delete; a refused versioned delete falls back to a plain delete.

    Args:
        bucket: The S3 bucket name
        key: The S3 object key
        
    Returns:
        True if successful, False otherwise
    """
    try:
        try:
            version_id = s3.head_object(Bucket=bucket, Key=key).get('VersionId')
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                return True
            raise
        if version_id:
            try:
                s3.delete_object(Bucket=bucket, Key=key, VersionId=version_id)
                logger.info(f"Deleted S3 object: {key}")
                return True
            except ClientError as e:
                if e.response.get('Error', {}).get('Code') != 'AccessDenied':
                    raise
                logger.warning(f"Version delete of staged object {key} refused; deleting without a version ID")
        s3.delete_object(Bucket=bucket, Key=key)
        logger.info(f"Deleted S3 object: {key}")
        return True
    except Exception as e:
        logger.exception(f"Error deleting S3 object {key}: {e}")
        return False

def is_preview_file(file_path: str) -> bool:
    """
    Check if file is a preview file (.previewFile.X pattern).
    Ported from uploadFile.py.
    
    Args:
        file_path: The file path to check
        
    Returns:
        True if the file is a preview file, False otherwise
    """
    return PREVIEW_FILE_PATTERN in file_path

def copy_s3_object(source_bucket: str, source_key: str, dest_bucket: str, dest_key: str,
                   database_id: str, asset_id: str, change_metadata: Dict[str, str] = None) -> bool:
    """
    Copy an object from one S3 location to another with replaced metadata.
    Ported from uploadFile.py.

    Args:
        source_bucket: Source S3 bucket name
        source_key: Source S3 object key
        dest_bucket: Destination S3 bucket name
        dest_key: Destination S3 object key
        database_id: Database ID to set in metadata
        asset_id: Asset ID to set in metadata
        change_metadata: Change-provenance metadata for the new object version

    Returns:
        True if successful, False otherwise
    """
    try:
        # Use s3_resource.copy with ExtraArgs for metadata replacement
        # This handles large files with managed transfer
        copy_source = {
            'Bucket': source_bucket,
            'Key': source_key
        }

        # The final object keeps the content headers of the object it is copied from
        source_head = s3.head_object(Bucket=source_bucket, Key=source_key)
        metadata = {
            DATABASE_ID_METADATA_KEY: database_id,
            ASSET_ID_METADATA_KEY: asset_id,
            **(change_metadata or build_upload_change_metadata(None))
        }

        extra_args = {
            **replace_metadata_copy_args(source_head, metadata),
            # Grant the bucket owner full control so the finalized object written into a
            # cross-account asset bucket is owned/readable by that account.
            'ACL': 'bucket-owner-full-control'
        }

        s3_resource.meta.client.copy(
            CopySource=copy_source,
            Bucket=dest_bucket,
            Key=dest_key,
            ExtraArgs=extra_args
        )
        logger.info(f"Successfully copied S3 object from {source_key} to {dest_key}")
        return True
    except Exception as e:
        logger.exception(f"Error copying S3 object from {source_key} to {dest_key}: {e}")
        return False

def validate_preview_file_extension(file_path: str) -> bool:
    """
    Validate preview file has allowed extension.
    Ported from uploadFile.py.
    
    Args:
        file_path: The file path to check
        
    Returns:
        True if the file has an allowed extension, False otherwise
    """
    import os

    # Extract the extension after .previewFile.
    if PREVIEW_FILE_PATTERN in file_path:
        extension = '.' + file_path.split(PREVIEW_FILE_PATTERN)[1].lower()
        return extension in allowed_preview_extensions

    # For direct assetPreview uploads, check the file extension
    file_extension = os.path.splitext(file_path)[1].lower()
    return file_extension in allowed_preview_extensions

def update_asset_after_file_processing(asset_id: str, database_id: str, bucket_name: str, final_s3_key: str):
    """
    Update asset record after successful file processing.
    Simplified version of the logic from uploadFile.py.
    
    Args:
        asset_id: The asset ID
        database_id: The database ID
        bucket_name: The S3 bucket name
        final_s3_key: The final S3 key of the processed file
    """
    try:
        # Get asset details
        asset = get_asset_details(database_id, asset_id)
        if not asset:
            logger.error(f"Asset not found: {database_id}/{asset_id}")
            return
        
        # Determine asset type using the asset's bucket and key location
        asset_base_key = asset.get('assetLocation', {}).get('Key', f"{asset_id}/")
        asset_type = determine_asset_type(asset_id, bucket_name, asset_base_key)
        logger.info(f"Asset type determined for asset {asset_id}: {asset_type}")

        # Update asset type - ensure we're not overriding with None
        if asset_type:
            asset['assetType'] = asset_type
        elif 'assetType' not in asset or not asset.get('assetType'):
            asset['assetType'] = 'none'
        # If asset already has a type and asset_type is None, keep the existing type

        # Save updated asset
        update_asset_attributes(database_id, asset_id, {'assetType': asset['assetType']})

        # Send notification to subscribers
        send_subscription_email(database_id, asset_id)
        
        logger.info(f"Updated asset {asset_id} after file processing")
        
    except Exception as e:
        logger.exception(f"Error updating asset after file processing: {e}")

def update_asset_preview_location(asset_id: str, database_id: str, final_s3_key: str):
    """
    Update asset with preview location after successful preview file processing.
    
    Args:
        asset_id: The asset ID
        database_id: The database ID
        final_s3_key: The final S3 key of the preview file
    """
    try:
        # Get asset details
        asset = get_asset_details(database_id, asset_id)
        if not asset:
            logger.error(f"Asset not found: {database_id}/{asset_id}")
            return
        
        # Update asset with preview location
        update_asset_attributes(database_id, asset_id, {
            'previewLocation': {
                'Key': final_s3_key
            }
        })

        logger.info(f"Updated asset {asset_id} with preview location: {final_s3_key}")
        
    except Exception as e:
        logger.exception(f"Error updating asset preview location: {e}")

def get_asset_details(database_id: str, asset_id: str) -> Dict[str, Any]:
    """
    Get asset details from DynamoDB.
    Ported from uploadFile.py.
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        
    Returns:
        The asset details or None if not found
    """
    try:
        response = asset_table.get_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            }
        )
        return response.get('Item')
    except Exception as e:
        logger.exception(f"Error getting asset details: {e}")
        return None

def update_asset_attributes(database_id: str, asset_id: str, updates: Dict[str, Any]):
    """Update the named attributes of an existing asset record in DynamoDB.

    Only the supplied attributes are written, so a field another writer changed while
    the large file was being processed is not reverted. Conditional on the record still
    existing so an asset removed during processing is not recreated.

    Args:
        database_id: The database ID
        asset_id: The asset ID
        updates: Map of attribute name to value to write
    """
    keys_map, values_map, expr = to_update_expr(updates)
    try:
        asset_table.update_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            },
            UpdateExpression=expr,
            ExpressionAttributeNames=keys_map,
            ExpressionAttributeValues=values_map,
            ConditionExpression='attribute_exists(databaseId) AND attribute_exists(assetId)'
        )
        logger.info(f"Saved asset details for {asset_id}")
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            logger.warning(f"Asset {asset_id} in database {database_id} no longer exists, asset record not updated")
            raise VAMSGeneralErrorResponse(f"Error saving asset.")
        logger.exception(f"Error saving asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error saving asset.")
    except Exception as e:
        logger.exception(f"Error saving asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error saving asset.")

def determine_asset_type(asset_id: str, bucket: str, prefix: str) -> str:
    """
    Determine the asset type based on S3 contents.
    Simplified version from uploadFile.py for async processing.
    
    Args:
        asset_id: The asset ID
        bucket: The S3 bucket name
        prefix: The S3 prefix to check
        
    Returns:
        The determined asset type or None
    """
    try:
        import os
        
        logger.info(f"Determining asset type from bucket: {bucket}, prefix: {prefix}")

        # Sample objects under the prefix to classify the asset (empty / single
        # file / folder). Capped at ASSET_TYPE_DETECTION_SAMPLE_SIZE — this is a
        # best-effort visibility classification, so a sample is sufficient and
        # avoids adding upload latency by scanning a very large asset in full.
        contents = list_all_objects(bucket, prefix, client=s3, max_objects=ASSET_TYPE_DETECTION_SAMPLE_SIZE)
        
        # Filter out archived files and count non-archived files
        non_archived_files = []
        file_count = 0
        for item in contents:
            if item['Key'].endswith('/'):
                # Skip folder markers
                continue
                
            try:
                # Check if file is archived (simplified check for async processing)
                if not is_file_archived(bucket, item['Key']):
                    non_archived_files.append(item)
                    file_count += 1
                    
                    # Short circuit if we've found more than one file
                    if file_count > 1:
                        logger.info(f"Found multiple files, returning 'folder'")
                        return 'folder'
            except Exception as e:
                logger.warning(f"Error checking if file {item['Key']} is archived: {e}")
                # If we can't check archive status, include the file by default
                non_archived_files.append(item)
                file_count += 1
                
                # Short circuit if we've found more than one file
                if file_count > 1:
                    logger.info(f"Found multiple files, returning 'folder'")
                    return 'folder'
        
        # At this point, we have 0 or 1 files
        logger.info(f"Found {file_count} non-archived files in {bucket}/{prefix}")
        
        # Determine asset type
        if file_count == 0:
            logger.info("No non-archived files found, returning None")
            return None  # No files found
        else:  # file_count == 1
            # Extract file extension from the single file
            file_key = non_archived_files[0]['Key']
            file_name = os.path.basename(file_key)
            
            # Skip if the file is just a folder marker
            if file_name == '':
                logger.info("Single object is a folder marker, returning 'folder'")
                return 'folder'
                
            if '.' in file_name:
                extension = '.' + file_name.split('.')[-1].lower()  # Convert to lowercase for consistency
                logger.info(f"Determined asset type as file with extension: {extension}")
                return extension
            else:
                logger.info("Determined asset type as unknown (no file extension)")
                return 'unknown'
    except Exception as e:
        logger.exception(f"Error determining asset type: {e}")
        return None

def is_file_archived(bucket: str, key: str, version_id: str = None) -> bool:
    """Determine if file is archived based on S3 delete markers.

    Delegates to the shared head_object-based helper, which is O(1) per check
    regardless of how many versions the key has.
    """
    return is_object_version_archived(bucket, key, version_id, client=s3)

def send_subscription_email(database_id: str, asset_id: str):
    """
    Send email notifications to subscribers when an asset is updated.
    Ported from uploadFile.py.
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
    """
    try:
        payload = {
            'databaseId': database_id,
            'assetId': asset_id,
        }
        lambda_client.invoke(
            FunctionName=send_email_function_name,
            InvocationType='Event',
            Payload=json.dumps(payload)
        )
        logger.info(f"Sent subscription email for asset {asset_id}")
    except Exception as e:
        logger.exception(f"Error invoking send_email Lambda function: {e}")

def format_correlation_ids(correlation_ids: Dict[str, str]) -> str:
    """
    Format correlation IDs for consistent logging.
    
    Args:
        correlation_ids: Dictionary containing correlation IDs
        
    Returns:
        Formatted string with correlation IDs
    """
    parts = []
    
    # Add key correlation IDs in a consistent order
    if correlation_ids.get('requestId'):
        parts.append(f"RequestId: {correlation_ids['requestId']}")
    if correlation_ids.get('recordCorrelationId'):
        parts.append(f"RecordId: {correlation_ids['recordCorrelationId']}")
    if correlation_ids.get('uploadId'):
        parts.append(f"UploadId: {correlation_ids['uploadId']}")
    if correlation_ids.get('assetId'):
        parts.append(f"AssetId: {correlation_ids['assetId']}")
    if correlation_ids.get('databaseId'):
        parts.append(f"DatabaseId: {correlation_ids['databaseId']}")
    if correlation_ids.get('relativeKey'):
        parts.append(f"File: {correlation_ids['relativeKey']}")
    if correlation_ids.get('fileSize'):
        parts.append(f"Size: {correlation_ids['fileSize']} bytes")
    
    return " | ".join(parts)

def get_file_size_from_parts(parts: List[Dict[str, Any]]) -> str:
    """
    Estimate file size from parts information for logging.
    
    Args:
        parts: List of multipart upload parts
        
    Returns:
        Estimated file size as string
    """
    try:
        if not parts:
            return "0"
        
        # For large files, estimate based on number of parts
        # Assuming average part size of 100MB (parts can be 5MB to 5GB)
        estimated_size = len(parts) * 100 * 1024 * 1024  # 100MB per part estimate
        
        # Format size in human-readable format
        if estimated_size >= 1024 * 1024 * 1024:  # GB
            return f"{estimated_size / (1024 * 1024 * 1024):.1f}GB"
        elif estimated_size >= 1024 * 1024:  # MB
            return f"{estimated_size / (1024 * 1024):.1f}MB"
        else:
            return f"{estimated_size}"
            
    except Exception:
        return f"~{len(parts)} parts"


def cleanup_failed_processing(file_info: Dict[str, Any], correlation_ids: Dict[str, str],
                              delete_temp: bool = True):
    """
    Cleanup resources after failed processing.

    Called for a deterministic rejection (delete_temp=True: the staged object and any
    multipart upload are removed) and for a retryable failure on the final receive
    (delete_temp=False: only the multipart upload is aborted, so a completed staged object
    is kept for a redrive from the dead-letter queue).

    Args:
        file_info: Dictionary containing file processing information
        correlation_ids: Dictionary containing correlation IDs for logging
        delete_temp: Whether to delete the staged temporary object
    """
    correlation_str = format_correlation_ids(correlation_ids)
    
    try:
        bucket_name = file_info.get('bucketName')
        # The staged object lives in the source bucket (a workflow output stages it in the
        # run bucket); the multipart upload, when there is one, is in the destination bucket.
        source_bucket = file_info.get('sourceBucketName') or bucket_name
        temp_s3_key = file_info.get('tempS3Key')
        upload_id_s3 = file_info.get('uploadIdS3')

        if not bucket_name or not temp_s3_key:
            logger.warning(f"Insufficient information for cleanup - {correlation_str}")
            return

        logger.info(f"Starting cleanup after failed processing - {correlation_str}")

        # Delete the temporary file; a key that does not exist needs no delete
        if delete_temp:
            if delete_s3_object(source_bucket, temp_s3_key):
                logger.info(f"Cleaned up temporary file: {temp_s3_key} - {correlation_str}")
            else:
                logger.warning(f"Could not delete temporary file {temp_s3_key} during cleanup - {correlation_str}")

        # Try to abort multipart upload if it's still active. The sentinel upload ids
        # identify files with no real multipart upload behind them.
        if upload_id_s3 and upload_id_s3 not in (ZERO_BYTE_UPLOAD_ID, EXTERNAL_UPLOAD_ID):
            try:
                s3.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=temp_s3_key,
                    UploadId=upload_id_s3
                )
                # S3 also accepts an abort for an upload that has already completed, so this
                # does not confirm that any uploaded parts were discarded.
                logger.info(f"Abort requested for multipart upload {upload_id_s3} (accepted by S3; "
                            f"the upload may already have been completed) - {correlation_str}")
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchUpload':
                    logger.debug(f"Multipart upload does not exist, no abort needed - {correlation_str}")
                else:
                    logger.warning(f"Error aborting multipart upload during cleanup - {correlation_str}: {e}")
        
        logger.info(f"Cleanup completed - {correlation_str}")
        
    except Exception as e:
        logger.exception(f"Error during cleanup - {correlation_str}: {e}")

def log_processing_metrics(correlation_ids: Dict[str, str], step: str, duration: float, success: bool):
    """
    Log processing metrics for monitoring and debugging.
    
    Args:
        correlation_ids: Dictionary containing correlation IDs
        step: The processing step name
        duration: Duration in seconds
        success: Whether the step was successful
    """
    try:
        correlation_str = format_correlation_ids(correlation_ids)
        status = "SUCCESS" if success else "FAILURE"
        
        logger.info(f"METRICS - Step: {step}, Status: {status}, Duration: {duration:.2f}s - {correlation_str}")
        
        # Additional structured logging for monitoring systems
        metrics_data = {
            'step': step,
            'status': status,
            'duration_seconds': duration,
            'upload_id': correlation_ids.get('uploadId'),
            'asset_id': correlation_ids.get('assetId'),
            'database_id': correlation_ids.get('databaseId'),
            'file_size': correlation_ids.get('fileSize'),
            'timestamp': utc_now().isoformat()
        }
        
        logger.info(f"STRUCTURED_METRICS: {json.dumps(metrics_data)}")
        
    except Exception as e:
        logger.warning(f"Error logging processing metrics: {e}")


def validate_and_move_large_file(file_info: Dict[str, Any], correlation_ids: Dict[str, str]) -> bool:
    """
    Validate file content and MIME type, then move file from temporary to final location.
    Ported from uploadFile.py complete_upload function.

    Args:
        file_info: Dictionary containing file processing information
        correlation_ids: Dictionary containing correlation IDs for logging

    Returns:
        True if validation and movement was successful, False when the file is rejected
        (blocked type or preview extension), which no retry can change

    Raises:
        Exception: a retryable failure (copy or S3 error); the staged object is kept
    """
    try:
        bucket_name = file_info['bucketName']
        # Source bucket the temp file is read from; defaults to the destination bucket, but a
        # workflow-execution output stages its temp file in the VAMS default run bucket.
        source_bucket = file_info.get('sourceBucketName') or bucket_name
        temp_s3_key = file_info['tempS3Key']
        final_s3_key = file_info['finalS3Key']
        relative_key = file_info['relativeKey']
        upload_type = file_info['uploadType']
        database_id = file_info['databaseId']
        asset_id = file_info['assetId']
        change_metadata = resolve_change_metadata(file_info)

        logger.info(f"Validating and moving file {relative_key} from {temp_s3_key} to {final_s3_key}")

        # Validate file content type and check for malicious executables (in the source bucket).
        if not validateS3AssetExtensionsAndContentType(source_bucket, temp_s3_key):
            logger.error(f"File {relative_key} contains a potentially malicious executable type object")
            # Delete the uploaded file
            delete_s3_object(source_bucket, temp_s3_key)
            return False

        # Additional validation for preview files
        if upload_type == "assetPreview":
            # Validate preview file extension
            if not validate_preview_file_extension(relative_key):
                logger.error(f"Preview file {relative_key} must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif")
                # Delete the uploaded file
                delete_s3_object(source_bucket, temp_s3_key)
                return False

        # Check if this is a preview file in an assetFile upload
        if upload_type == "assetFile" and is_preview_file(relative_key):
            # Validate preview file extension
            if not validate_preview_file_extension(relative_key):
                logger.error(f"Preview file {relative_key} must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif")
                # Delete the uploaded file
                delete_s3_object(source_bucket, temp_s3_key)
                return False

            # For preview files, we need to validate that the base file exists
            # This validation is simplified for async processing - we assume the base file exists
            # since the original upload validation would have caught missing base files
            logger.info(f"Preview file {relative_key} validation passed (base file existence assumed)")

        # A file over the engine threshold is handed to the large object copy engine, which has no
        # time limit and resumes after a failure; the engine finishes the file (temp delete, asset
        # update, audit). A failed start raises, so the message is retried and dead-lettered.
        if hand_off_to_copy_engine(file_info, source_bucket, temp_s3_key, final_s3_key, change_metadata):
            return True

        # Copy file from temporary (source bucket) to final (destination asset bucket) location
        logger.info(f"Copying file from {temp_s3_key} to {final_s3_key}")

        copy_success = copy_s3_object(
            source_bucket,
            temp_s3_key,
            bucket_name,
            final_s3_key,
            database_id,
            asset_id,
            change_metadata=change_metadata
        )

        if not copy_success:
            # A failed copy is retryable: the staged object is kept so a redelivery can copy it
            raise RuntimeError(f"Failed to copy file from {temp_s3_key} to {final_s3_key}")

        # Delete temporary source file after successful copy
        delete_s3_object(source_bucket, temp_s3_key)
        
        # Update asset record if this is an assetFile upload
        if upload_type == "assetFile":
            update_asset_after_file_processing(asset_id, database_id, bucket_name, final_s3_key)
        elif upload_type == "assetPreview":
            update_asset_preview_location(asset_id, database_id, final_s3_key)
        
        logger.info(f"Successfully validated and moved file {relative_key}")
        return True
        
    except Exception as e:
        logger.exception(f"Error validating and moving large file: {e}")
        raise


_upload_table = None


def upload_table():
    """The upload records table, resolved on first use (an async file's outcome is recorded there)."""
    global _upload_table
    if _upload_table is None:
        _upload_table = dynamodb.Table(get_table_name(ResourceKeys.ASSET_UPLOADS_STORAGE_TABLE))
    return _upload_table


def record_file_outcome(file_info: Dict[str, Any], succeeded: bool, reason: str = "",
                        raise_errors: bool = False):
    """Record a queued file's outcome on its upload record (common.uploadStatus). Idempotent. Never
    raises unless ``raise_errors``, which the stored-file path uses so a failed write returns the
    message to the queue and its redelivery records the outcome."""
    try:
        uploadStatus.record_async_file_outcome(
            upload_table(), file_info.get('uploadId'), file_info.get('assetId'),
            file_info.get('relativeKey'), succeeded, reason, raise_errors=raise_errors)
    except Exception as e:
        logger.warning(f"Could not record the outcome of {file_info.get('relativeKey')}: {e}")
        if raise_errors:
            raise


def audit_stored_file(file_info: Dict[str, Any], correlation_id: str) -> bool:
    """Write the completion audit entry of a file this consumer stored. Never raises; returns False
    (logged) when the entry could not be written."""
    try:
        log_file_upload(
            build_async_audit_event(file_info),
            file_info.get('databaseId'),
            file_info.get('assetId'),
            file_info.get('relativeKey'),
            False,  # Not denied
            None,
            {
                "uploadId": file_info.get('uploadId'),
                "uploadType": file_info.get('uploadType'),
                "status": "completed_async",
                "processingType": "large_file_async",
                "correlationId": correlation_id
            }
        )
        return True
    except Exception as audit_error:
        logger.exception(f"Failed to log large file upload audit - {correlation_id}: {audit_error}")
        return False


def finish_stored_file(file_info: Dict[str, Any], correlation_id: str, redelivered_stored: bool):
    """The completion audit entry and the outcome of a file this consumer stored, written once
    across receives of its message.

    When the upload record tracks the file, the audit entry is claimed on its entry first, so a
    receive that stops after the staged object is deleted (a timeout, a failed outcome write) and
    its redelivery write one entry between them, and the outcome is written last and raises when it
    fails, so the message returns to the queue and the redelivery (``redelivered_stored``: the
    file is found already stored) writes it. A file the record does not track (no record, or one
    that expired) keeps the earlier behaviour: the receive that stored it writes the entry, and a
    redelivery writes nothing.

    A redelivery whose file the copy engine may have stored (a copy job exists, or its row cannot be
    read) writes nothing: the engine writes that file's entry and outcome.

    Raises when the upload record cannot be read or written, so the message is redelivered."""
    if redelivered_stored and copy_job_state(file_info) != COPY_JOB_NONE:
        return
    claim = uploadStatus.claim_async_file_audit(
        upload_table(), file_info.get('uploadId'), file_info.get('assetId'),
        file_info.get('relativeKey'))
    if claim is True:
        if not audit_stored_file(file_info, correlation_id):
            # Release the claim (as the copy engine's _audit_once does), so a redelivery of this
            # message (the outcome write below failing, or a later receive) writes the entry.
            uploadStatus.release_async_file_audit(
                upload_table(), file_info.get('uploadId'), file_info.get('assetId'),
                file_info.get('relativeKey'))
    elif claim is None and not redelivered_stored:
        audit_stored_file(file_info, correlation_id)
    if claim is not None:
        record_file_outcome(file_info, True, raise_errors=True)


COPY_JOB_NONE = "none"
COPY_JOB_EXISTS = "exists"
COPY_JOB_UNKNOWN = "unknown"


def copy_job_state(file_info: Dict[str, Any]) -> str:
    """Whether the copy engine owns this file: COPY_JOB_EXISTS (a job row exists), COPY_JOB_NONE
    (no engine, or no job), or COPY_JOB_UNKNOWN (the job row could not be read)."""
    if not loc.state_machine_arn() or not file_info.get('uploadId') or not file_info.get('relativeKey'):
        return COPY_JOB_NONE
    job_id = loc.job_id_for(file_info['uploadId'], file_info['relativeKey'])
    try:
        return COPY_JOB_EXISTS if loc.get_job_row(copy_jobs_table(), job_id) else COPY_JOB_NONE
    except Exception as e:
        logger.warning(f"Could not read copy job {job_id}: {e}")
        return COPY_JOB_UNKNOWN


def already_handed_off(file_info: Dict[str, Any]) -> str:
    """The copy job id when an earlier delivery of this message already handed the file to the copy
    engine (the job row exists), else "". A redelivered message then neither re-validates the staged
    object (the engine deleted it once the file landed) nor audits a rejection."""
    if not loc.state_machine_arn() or not file_info.get('uploadId') or not file_info.get('relativeKey'):
        return ""
    job_id = loc.job_id_for(file_info['uploadId'], file_info['relativeKey'])
    try:
        return job_id if loc.get_job_row(copy_jobs_table(), job_id) else ""
    except Exception as e:
        logger.warning(f"Could not read copy job {job_id}; processing the message as new: {e}")
        return ""


_copy_jobs_table = None


def copy_jobs_table():
    """The large object copy jobs table, resolved on first use."""
    global _copy_jobs_table
    if _copy_jobs_table is None:
        _copy_jobs_table = dynamodb.Table(get_table_name(ResourceKeys.LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE))
    return _copy_jobs_table


def hand_off_to_copy_engine(file_info: Dict[str, Any], source_bucket: str, temp_s3_key: str,
                            final_s3_key: str, change_metadata: Dict[str, str]) -> bool:
    """Start a large object copy job for a file over the engine threshold and return True; return
    False to copy the file here as before (at or below the threshold, or no engine configured).

    At or below the threshold (default 5 GiB) a managed copy is a single CopyObject that finishes
    well inside this function's time limit, so those files keep today's path unchanged. Raises when
    the job cannot be started, which the caller treats as retryable."""
    machine_arn = loc.state_machine_arn()
    if not machine_arn:
        return False
    head = s3.head_object(Bucket=source_bucket, Key=temp_s3_key)
    size = int(head.get('ContentLength', 0) or 0)
    if size <= loc.settings()['apiUploadEngineThresholdBytes']:
        return False
    job = loc.build_job(
        kind=loc.KIND_UPLOAD,
        source={"bucket": source_bucket, "key": temp_s3_key,
                "region": file_info.get('sourceBucketRegion') or file_info.get('bucketRegion') or "",
                "versionId": head.get('VersionId') or "", "etag": head.get('ETag') or "", "size": size},
        dest={"bucket": file_info['bucketName'], "key": final_s3_key,
              "region": file_info.get('bucketRegion') or ""},
        metadata={DATABASE_ID_METADATA_KEY: file_info['databaseId'],
                  ASSET_ID_METADATA_KEY: file_info['assetId'],
                  **(change_metadata or build_upload_change_metadata(None))},
        content_headers={},
        database_id=file_info['databaseId'],
        asset_id=file_info['assetId'],
        upload_id=file_info['uploadId'],
        upload_type=file_info['uploadType'],
        relative_key=file_info['relativeKey'],
        change_user_id=file_info.get('changeUserId'),
        scope_id=file_info['uploadId'],
        workflow_id=file_info.get('workflowId') or "",
        workflow_execution_id=file_info.get('workflowExecutionId') or "",
        delete_source_on_success=True,
    )
    loc.start_copy_job(copy_jobs_table(), sfn_client, machine_arn, job)
    file_info['copyJobId'] = job['jobId']
    logger.info(f"File {file_info['relativeKey']} ({size} bytes) handed to copy job {job['jobId']}")
    return True


def complete_multipart_upload_for_large_file(file_info: Dict[str, Any], correlation_ids: Dict[str, str],
                                             sent_timestamp_ms: int = None) -> bool:
    """
    Complete the multipart upload for a large file at the temporary location.
    Ported from uploadFile.py complete_upload function.
    
    Args:
        file_info: Dictionary containing file processing information
        correlation_ids: Dictionary containing correlation IDs for logging
        sent_timestamp_ms: The SQS SentTimestamp of the message, in epoch milliseconds, used
            to recognise a redelivery of a file an earlier receive already stored
        
    Returns:
        True if multipart upload completion was successful, False when the file is rejected
        (cancelled, invalid parts, upload ID mismatch, oversized preview), which no retry
        can change

    Raises:
        LargeFileAlreadyProcessed: the staged object is gone and an earlier receive of this
            message already stored the file
        Exception: a retryable failure (S3 5xx, throttling, timeouts); the multipart upload
        and any staged object are kept so a redelivery can finish the file
    """
    try:
        bucket_name = file_info['bucketName']
        temp_s3_key = file_info['tempS3Key']
        upload_id_s3 = file_info['uploadIdS3']
        parts = file_info['parts']
        upload_id = file_info['uploadId']
        database_id = file_info['databaseId']
        asset_id = file_info['assetId']
        relative_key = file_info['relativeKey']
        
        logger.info(f"Completing multipart upload for {relative_key} at {temp_s3_key}")

        # Handle external uploads (identified by uploadIdS3 = "external"): the object is
        # already fully written at the temporary key by the external producer, so there is
        # no multipart upload to complete.
        if upload_id_s3 == EXTERNAL_UPLOAD_ID:
            # A redelivery after an earlier receive moved the file finds the staged object gone
            source_bucket = file_info.get('sourceBucketName') or bucket_name
            try:
                s3.head_object(Bucket=source_bucket, Key=temp_s3_key)
            except ClientError as head_error:
                if head_error.response.get('Error', {}).get('Code') not in S3_NOT_FOUND_ERROR_CODES:
                    raise
                if is_large_file_already_stored(file_info, sent_timestamp_ms):
                    raise LargeFileAlreadyProcessed(f"External upload {relative_key} was already stored")
            logger.info(f"External upload {relative_key} already staged at {temp_s3_key}, nothing to complete")
            return True

        # Handle zero-byte files (identified by uploadIdS3 = "zero-byte")
        if upload_id_s3 == ZERO_BYTE_UPLOAD_ID:
            logger.info(f"Creating zero-byte file {relative_key} during async processing")
            if not create_zero_byte_file(bucket_name, temp_s3_key, upload_id, database_id, asset_id,
                                         change_metadata=resolve_change_metadata(file_info)):
                raise RuntimeError(f"Failed to create zero-byte file {relative_key}")
            return True

        # Cancelled uploads (a multipart upload with no parts): abort the multipart upload,
        # store nothing and report the file as not completed
        if not parts or len(parts) == 0:
            logger.info(f"No parts provided for file {relative_key}, cancelling its upload")

            try:
                s3.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=temp_s3_key,
                    UploadId=upload_id_s3
                )
            except Exception as abort_error:
                logger.warning(f"Error aborting multipart upload for cancelled file: {abort_error}")

            return False

        # Regular multipart upload completion
        actual_parts = sorted([p['PartNumber'] for p in parts])
        
        # Check for duplicates in part numbers
        if len(actual_parts) != len(set(actual_parts)):
            logger.error(f"Duplicate part numbers provided for {relative_key}")
            return False
        
        # Log the parts we received
        logger.info(f"Received {len(actual_parts)} parts for file {relative_key}: {actual_parts}")
        
        # Complete multipart upload in temporary location. The multipart upload is not aborted
        # here: a transient error is raised so SQS redelivers the message and a later receive
        # can still complete it; the caller cleans up on a rejection or on the final receive.
        try:
            s3.complete_multipart_upload(
                Bucket=bucket_name,
                Key=temp_s3_key,
                UploadId=upload_id_s3,
                MultipartUpload={'Parts': [{'PartNumber': p['PartNumber'], 'ETag': p['ETag']} for p in parts]}
            )
            logger.info(f"Successfully completed multipart upload for {relative_key}")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == 'NoSuchUpload':
                # An earlier receive may have completed the upload and then failed later on:
                # the staged object is then already in place and processing continues with it.
                try:
                    s3.head_object(Bucket=bucket_name, Key=temp_s3_key)
                    logger.info(f"Multipart upload for {relative_key} already completed by an earlier receive")
                except ClientError as head_error:
                    if head_error.response.get('Error', {}).get('Code') in S3_NOT_FOUND_ERROR_CODES:
                        # No upload and no staged object: either an earlier receive finished the
                        # file (a duplicate delivery) or the file can no longer be finished.
                        if is_large_file_already_stored(file_info, sent_timestamp_ms):
                            raise LargeFileAlreadyProcessed(f"File {relative_key} was already stored")
                        logger.error(f"Multipart upload for {relative_key} no longer exists and no staged object was found")
                        return False
                    raise
            elif error_code in DETERMINISTIC_COMPLETE_ERROR_CODES:
                logger.error(f"Multipart upload for {relative_key} was rejected by S3 ({error_code})")
                return False
            else:
                logger.exception(f"Error completing multipart upload for {relative_key}: {e}")
                raise

        # Verify the metadata of the completed object. A failed read is retryable and is
        # raised, keeping the staged object for the redelivery. S3 can accept a repeated
        # CompleteMultipartUpload on an upload that already completed, so a duplicate delivery
        # can get here after an earlier receive moved the file and deleted the staged object.
        try:
            head_response = s3.head_object(
                Bucket=bucket_name,
                Key=temp_s3_key
            )
        except ClientError as head_error:
            if (head_error.response.get('Error', {}).get('Code') in S3_NOT_FOUND_ERROR_CODES
                    and is_large_file_already_stored(file_info, sent_timestamp_ms)):
                raise LargeFileAlreadyProcessed(f"File {relative_key} was already stored")
            raise
        # Extract metadata
        metadata = head_response.get('Metadata', {})
        s3_upload_id = metadata.get(UPLOAD_ID_METADATA_KEY)

        # Verify the uploadId matches
        if s3_upload_id != upload_id:
            logger.error(f"Upload ID mismatch for {relative_key}. Expected: {upload_id}, Got: {s3_upload_id}")
            return False

        # Get file size for validation
        file_size = head_response.get('ContentLength', 0)
        logger.info(f"Completed file {relative_key} has size: {file_size} bytes")

        # Check file size for preview files - both assetPreview type and .previewFile. files
        upload_type = file_info.get('uploadType')
        if (upload_type == "assetPreview" or is_preview_file(relative_key)) and file_size > MAX_PREVIEW_FILE_SIZE:
            logger.error(f"Preview file {relative_key} exceeds maximum allowed size of 5MB")
            return False

        return True

    except LargeFileAlreadyProcessed:
        raise
    except Exception as e:
        logger.exception(f"Error completing multipart upload for large file: {e}")
        raise


def build_async_audit_event(file_info: Dict[str, Any]) -> Dict[str, Any]:
    """Build the audit event for an SQS-processed large file.

    SQS-triggered, so the actor is supplied as an internal cross-call identity: the
    initiating user the queuing handler recorded on the message, falling back to
    SYSTEM_USER. This is the same identity the change provenance is stamped with, so the
    audit entry correlates with the synchronous completion entry. The queued message
    carries no MFA state, so an end-user actor is recorded as not MFA-satisfied rather
    than taking the system cross-call default.
    """
    audit_user_id = file_info.get('changeUserId') or 'SYSTEM_USER'
    cross_call = {'userName': audit_user_id}
    if audit_user_id != 'SYSTEM_USER':
        cross_call['mfaEnabled'] = False
    return {'lambdaCrossCall': cross_call}


def audit_rejected_large_file(file_info: Dict[str, Any], correlation_ids: Dict[str, str], reason: str):
    """Write the audit entry for a large file rejected during asynchronous processing."""
    try:
        log_file_upload(
            build_async_audit_event(file_info),
            file_info.get('databaseId'),
            file_info.get('assetId'),
            file_info.get('relativeKey'),
            True,  # Denied
            reason,
            {
                "uploadId": file_info.get('uploadId'),
                "uploadType": file_info.get('uploadType'),
                "status": "rejected_async",
                "processingType": "large_file_async",
                "correlationId": correlation_ids.get('recordCorrelationId')
            }
        )
    except Exception as audit_error:
        logger.exception(f"Failed to log rejected large file audit - {format_correlation_ids(correlation_ids)}: {audit_error}")


def process_large_file(file_info: Dict[str, Any], correlation_ids: Dict[str, str],
                       receive_count: int = 1, sent_timestamp_ms: int = None) -> bool:
    """
    Process a single large file from SQS message with comprehensive error handling.

    A rejection that no retry can change (blocked type, invalid preview, cancelled or
    invalid multipart upload) is cleaned up (multipart upload aborted, staged object
    deleted), audited and reported as False so the message is consumed. Any other failure
    is retryable and is raised so SQS redelivers the message and, after the queue's
    maxReceiveCount receives, moves it to the dead-letter queue. The multipart upload and
    staged object are kept for the redelivery; on the final receive the multipart upload
    is aborted, as no later receive could complete it.

    Args:
        file_info: Dictionary containing file processing information
        correlation_ids: Dictionary containing correlation IDs for logging
        receive_count: The SQS ApproximateReceiveCount of the message
        sent_timestamp_ms: The SQS SentTimestamp of the message, in epoch milliseconds

    Returns:
        True if processing was successful, False if the file was rejected

    Raises:
        LargeFileAlreadyProcessed: a duplicate delivery of a file already stored; nothing is
            cleaned up or audited
        Exception: a retryable failure
    """
    start_time = utc_now()
    correlation_str = format_correlation_ids(correlation_ids)

    try:
        logger.info(f"Starting large file processing (receive {receive_count}) - {correlation_str}")

        # The message names each bucket's Region, so the Region-routing S3 client signs for it
        # without a buckets-table lookup; a message queued without the fields falls back to the
        # lookup.
        register_bucket_region(file_info.get('bucketName'), file_info.get('bucketRegion'))
        register_bucket_region(file_info.get('sourceBucketName'), file_info.get('sourceBucketRegion'))

        # A redelivery after the hand-off to the copy engine: the engine owns the file.
        handed_off_job = already_handed_off(file_info)
        if handed_off_job:
            logger.info(f"File already handed to copy job {handed_off_job}; nothing to do - {correlation_str}")
            file_info['copyJobId'] = handed_off_job
            return True

        # Step 1: Complete multipart upload at temporary location
        logger.info(f"Step 1: Completing multipart upload - {correlation_str}")
        step1_start = utc_now()

        if not complete_multipart_upload_for_large_file(file_info, correlation_ids, sent_timestamp_ms):
            logger.error(f"Step 1 rejected: multipart upload completion - {correlation_str}")
            cleanup_failed_processing(file_info, correlation_ids)
            audit_rejected_large_file(file_info, correlation_ids, "Multipart upload completion rejected")
            record_file_outcome(file_info, False, "The upload could not be completed.")
            return False

        step1_duration = (utc_now() - step1_start).total_seconds()
        logger.info(f"Step 1 completed in {step1_duration:.2f}s - {correlation_str}")

        # Step 2: Validate file content and move to final location
        logger.info(f"Step 2: Validating and moving file - {correlation_str}")
        step2_start = utc_now()

        if not validate_and_move_large_file(file_info, correlation_ids):
            logger.error(f"Step 2 rejected: file validation - {correlation_str}")
            cleanup_failed_processing(file_info, correlation_ids)
            audit_rejected_large_file(file_info, correlation_ids, "File type or extension not allowed")
            record_file_outcome(file_info, False, loc.REASON_BLOCKED)
            return False

        step2_duration = (utc_now() - step2_start).total_seconds()
        logger.info(f"Step 2 completed in {step2_duration:.2f}s - {correlation_str}")

        total_duration = (utc_now() - start_time).total_seconds()
        logger.info(f"Large file processing completed successfully in {total_duration:.2f}s - {correlation_str}")
        return True

    except LargeFileAlreadyProcessed:
        raise
    except Exception as e:
        total_duration = (utc_now() - start_time).total_seconds()
        if receive_count >= LARGE_FILE_MAX_RECEIVE_COUNT:
            logger.exception(f"Retryable error processing large file on final receive {receive_count} after "
                             f"{total_duration:.2f}s, the message moves to the dead-letter queue - {correlation_str}: {e}")
            try:
                cleanup_failed_processing(file_info, correlation_ids, delete_temp=False)
            except Exception as cleanup_error:
                logger.exception(f"Error during cleanup after final processing failure - {correlation_str}: {cleanup_error}")
            # No later delivery will process the file.
            record_file_outcome(file_info, False, uploadStatus.REASON_PROCESSING_FAILED)
        else:
            logger.exception(f"Retryable error processing large file on receive {receive_count} after "
                             f"{total_duration:.2f}s, the message will be redelivered - {correlation_str}: {e}")
        raise


def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> None:
    """
    Lambda handler for processing SQS events containing large file processing requests.
    Implements comprehensive error handling and structured logging with correlation IDs.
    
    Args:
        event: SQS event containing Records with large file processing information
        context: Lambda context
    """
    # Add request ID for correlation
    request_id = context.aws_request_id if context else 'unknown'
    logger.info(f"Starting SQS processing - RequestId: {request_id}, Records: {len(event.get('Records', []))}")
    
    processed_count = 0
    success_count = 0
    failure_count = 0
    retryable_errors = []
    
    # Process each SQS record
    for record_index, record in enumerate(event.get('Records', [])):
        record_correlation_id = f"{request_id}-{record_index}"
        
        try:
            logger.info(f"Processing SQS record {record_index + 1}/{len(event.get('Records', []))} - CorrelationId: {record_correlation_id}")
            
            # Extract message body
            message_body = record.get('body')
            if not message_body:
                logger.error(f"SQS record missing body - CorrelationId: {record_correlation_id}")
                failure_count += 1
                continue
                
            # Parse message body
            try:
                message_data = json.loads(message_body)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in SQS message body - CorrelationId: {record_correlation_id}, Error: {e}")
                failure_count += 1
                continue
                
            # Validate message structure
            if not validate_sqs_message(message_data, record_correlation_id):
                logger.error(f"Invalid SQS message structure - CorrelationId: {record_correlation_id}")
                failure_count += 1
                continue
                
            # Extract file information
            file_info = message_data.get('fileInfo')
            if not file_info:
                logger.error(f"Missing fileInfo in SQS message - CorrelationId: {record_correlation_id}")
                failure_count += 1
                continue
                
            # Add correlation IDs for logging
            correlation_ids = {
                'requestId': request_id,
                'recordCorrelationId': record_correlation_id,
                'uploadId': file_info.get('uploadId'),
                'assetId': file_info.get('assetId'),
                'databaseId': file_info.get('databaseId'),
                'relativeKey': file_info.get('relativeKey'),
                'fileSize': get_file_size_from_parts(file_info.get('parts', []))
            }
            
            logger.info(f"Processing large file - {format_correlation_ids(correlation_ids)}")

            try:
                receive_count = int(record.get('attributes', {}).get('ApproximateReceiveCount', 1))
            except (TypeError, ValueError):
                receive_count = 1
            try:
                sent_timestamp_ms = int(record.get('attributes', {}).get('SentTimestamp'))
            except (TypeError, ValueError):
                sent_timestamp_ms = None

            # Process the large file. A retryable failure is raised; it is recorded and
            # re-raised after the loop so SQS redelivers the message.
            try:
                success = process_large_file(file_info, correlation_ids, receive_count, sent_timestamp_ms)
            except LargeFileAlreadyProcessed:
                # An earlier receive stored the file, but it may have stopped before its audit
                # entry or the file's outcome: write what is missing (once) when the upload
                # record tracks the file.
                logger.info(f"Duplicate delivery of a file already stored, message consumed - "
                            f"{format_correlation_ids(correlation_ids)}")
                try:
                    finish_stored_file(file_info, record_correlation_id, redelivered_stored=True)
                except Exception as outcome_error:
                    retryable_errors.append(outcome_error)
                    failure_count += 1
                    processed_count += 1
                    continue
                success_count += 1
                processed_count += 1
                continue
            except Exception as process_error:
                retryable_errors.append(process_error)
                failure_count += 1
                processed_count += 1
                continue
            
            if success and file_info.get('copyJobId'):
                # Handed to the copy engine, which writes the completion (or rejection) audit
                # entry once the file has landed.
                logger.info(f"Large file handed to copy job {file_info['copyJobId']} - {format_correlation_ids(correlation_ids)}")
                success_count += 1
            elif success:
                logger.info(f"Successfully processed large file - {format_correlation_ids(correlation_ids)}")
                # AUDIT LOG (once across receives, actor resolved by build_async_audit_event) and
                # the file's outcome. A failed outcome write returns the message to the queue; its
                # redelivery finds the file stored and writes the outcome.
                try:
                    finish_stored_file(file_info, record_correlation_id, redelivered_stored=False)
                except Exception as outcome_error:
                    retryable_errors.append(outcome_error)
                    failure_count += 1
                    processed_count += 1
                    continue
                success_count += 1
            else:
                logger.error(f"Large file rejected and cleaned up, message consumed - {format_correlation_ids(correlation_ids)}")
                failure_count += 1
                
            processed_count += 1
                
        except Exception as e:
            logger.exception(f"Unexpected error processing SQS record - CorrelationId: {record_correlation_id}, Error: {e}")
            failure_count += 1
            # Continue processing other records even if one fails
            continue
    
    # Log final processing summary
    logger.info(f"SQS processing completed - RequestId: {request_id}, "
                f"Total: {len(event.get('Records', []))}, "
                f"Processed: {processed_count}, "
                f"Success: {success_count}, "
                f"Failures: {failure_count}, "
                f"Retryable: {len(retryable_errors)}")

    # Returning normally deletes every message in the batch, including a malformed message
    # or a rejected file, both of which no retry can change. Raising returns the batch to the
    # queue: SQS redelivers it after the visibility timeout and moves it to the dead-letter
    # queue after maxReceiveCount receives. The event source mapping uses batchSize 1, so a
    # raise affects only the failing message.
    if retryable_errors:
        raise RuntimeError(
            f"{len(retryable_errors)} large file record(s) failed with a retryable error - RequestId: {request_id}"
        ) from retryable_errors[0]
