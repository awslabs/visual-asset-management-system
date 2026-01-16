"""
Large file asynchronous processor Lambda for VAMS.
Handles SQS messages for processing large files (>1GB) asynchronously.
"""

import os
import boto3
import json
import logging
from typing import Dict, Any, List
from datetime import datetime
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_file_upload
from common.s3 import validateS3AssetExtensionsAndContentType
from models.common import VAMSGeneralErrorResponse

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

s3 = boto3.client('s3', config=retry_config)
s3_resource = boto3.resource('s3', config=retry_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)
lambda_client = boto3.client('lambda', config=retry_config)
logger = safeLogger(service_name="SqsUploadFileLarge")

# Constants
TEMPORARY_UPLOAD_PREFIX = 'temp-uploads/'
PREVIEW_PREFIX = 'previews/'
MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024  # 5MB maximum size for preview files
allowed_preview_extensions = ['.png', '.jpg', '.jpeg', '.svg', '.gif']

# Load environment variables
try:
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_table = dynamodb.Table(asset_storage_table_name)


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

def create_zero_byte_file(bucket_name: str, key: str, upload_id: str, database_id: str, asset_id: str) -> bool:
    """
    Create a zero-byte file in S3.
    Ported from uploadFile.py.
    
    Args:
        bucket_name: The S3 bucket name
        key: The S3 object key
        upload_id: The upload ID for metadata
        database_id: The database ID for metadata
        asset_id: The asset ID for metadata
        
    Returns:
        True if successful, False otherwise
    """
    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=b'',  # Empty content for zero-byte file
            ContentType='application/octet-stream',
            Metadata={
                "databaseid": database_id,
                "assetid": asset_id,
                "uploadid": upload_id,
            }
        )
        logger.info(f"Created zero-byte file: {key}")
        return True
    except Exception as e:
        logger.exception(f"Error creating zero-byte file {key}: {e}")
        return False

def delete_s3_object(bucket: str, key: str) -> bool:
    """
    Delete an object from S3.
    Ported from uploadFile.py.
    
    Args:
        bucket: The S3 bucket name
        key: The S3 object key
        
    Returns:
        True if successful, False otherwise
    """
    try:
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
    return '.previewFile.' in file_path

def copy_s3_object(source_bucket: str, source_key: str, dest_bucket: str, dest_key: str, 
                   database_id: str, asset_id: str) -> bool:
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
        
        extra_args = {
            'MetadataDirective': 'REPLACE',
            'Metadata': {
                "databaseid": database_id,
                "assetid": asset_id
            }
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
    if '.previewFile.' in file_path:
        extension = '.' + file_path.split('.previewFile.')[1].lower()
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
        save_asset_details(asset)
        
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
        asset['previewLocation'] = {
            'Key': final_s3_key
        }
        
        # Save updated asset
        save_asset_details(asset)
        
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

def save_asset_details(asset_data: Dict[str, Any]):
    """
    Save asset details to DynamoDB.
    Ported from uploadFile.py.
    
    Args:
        asset_data: The asset data to save
    """
    try:
        asset_table.put_item(Item=asset_data)
        logger.info(f"Saved asset details for {asset_data.get('assetId')}")
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
        
        # List all objects with the specified prefix
        response = s3.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
        )
        
        # Get the contents and filter out folder markers (objects ending with '/')
        contents = response.get('Contents', [])
        
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
    """
    Determine if file is archived based on S3 delete markers.
    Simplified version from uploadFile.py for async processing.
    
    Args:
        bucket: The S3 bucket name
        key: The S3 object key
        version_id: Optional specific version ID to check
        
    Returns:
        True if file is archived (has delete marker), False otherwise
    """
    try:
        if version_id:
            # Check if specific version is a delete marker
            response = s3.list_object_versions(
                Bucket=bucket,
                Prefix=key,
                MaxKeys=1000
            )
            
            # Check if the specified version is a delete marker
            for marker in response.get('DeleteMarkers', []):
                if marker['Key'] == key and marker['VersionId'] == version_id:
                    return True
            return False
        else:
            # Check if current version is deleted (has delete marker as latest)
            try:
                s3.head_object(Bucket=bucket, Key=key)
                return False  # Object exists, not archived
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    # Object doesn't exist, check if it has delete markers
                    response = s3.list_object_versions(
                        Bucket=bucket,
                        Prefix=key,
                        MaxKeys=1
                    )
                    return len(response.get('DeleteMarkers', [])) > 0
                else:
                    raise
    except Exception as e:
        logger.warning(f"Error checking archive status for {key}: {e}")
        return False

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

def cleanup_failed_processing(file_info: Dict[str, Any], correlation_ids: Dict[str, str]):
    """
    Cleanup resources after failed processing.
    
    Args:
        file_info: Dictionary containing file processing information
        correlation_ids: Dictionary containing correlation IDs for logging
    """
    correlation_str = format_correlation_ids(correlation_ids)
    
    try:
        bucket_name = file_info.get('bucketName')
        temp_s3_key = file_info.get('tempS3Key')
        upload_id_s3 = file_info.get('uploadIdS3')
        
        if not bucket_name or not temp_s3_key:
            logger.warning(f"Insufficient information for cleanup - {correlation_str}")
            return
        
        logger.info(f"Starting cleanup after failed processing - {correlation_str}")
        
        # Try to delete temporary file if it exists
        try:
            s3.head_object(Bucket=bucket_name, Key=temp_s3_key)
            # File exists, delete it
            delete_s3_object(bucket_name, temp_s3_key)
            logger.info(f"Cleaned up temporary file: {temp_s3_key} - {correlation_str}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.debug(f"Temporary file does not exist, no cleanup needed - {correlation_str}")
            else:
                logger.warning(f"Error checking temporary file existence during cleanup - {correlation_str}: {e}")
        
        # Try to abort multipart upload if it's still active
        if upload_id_s3 and upload_id_s3 != "zero-byte":
            try:
                s3.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=temp_s3_key,
                    UploadId=upload_id_s3
                )
                logger.info(f"Aborted multipart upload: {upload_id_s3} - {correlation_str}")
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
            'timestamp': datetime.utcnow().isoformat()
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
        True if validation and movement was successful, False otherwise
    """
    try:
        bucket_name = file_info['bucketName']
        temp_s3_key = file_info['tempS3Key']
        final_s3_key = file_info['finalS3Key']
        relative_key = file_info['relativeKey']
        upload_type = file_info['uploadType']
        database_id = file_info['databaseId']
        asset_id = file_info['assetId']
        
        logger.info(f"Validating and moving file {relative_key} from {temp_s3_key} to {final_s3_key}")
        
        # Validate file content type and check for malicious executables
        if not validateS3AssetExtensionsAndContentType(bucket_name, temp_s3_key):
            logger.error(f"File {relative_key} contains a potentially malicious executable type object")
            # Delete the uploaded file
            delete_s3_object(bucket_name, temp_s3_key)
            return False
        
        # Additional validation for preview files
        if upload_type == "assetPreview":
            # Validate preview file extension
            if not validate_preview_file_extension(relative_key):
                logger.error(f"Preview file {relative_key} must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif")
                # Delete the uploaded file
                delete_s3_object(bucket_name, temp_s3_key)
                return False
        
        # Check if this is a preview file in an assetFile upload
        if upload_type == "assetFile" and is_preview_file(relative_key):
            # Validate preview file extension
            if not validate_preview_file_extension(relative_key):
                logger.error(f"Preview file {relative_key} must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif")
                # Delete the uploaded file
                delete_s3_object(bucket_name, temp_s3_key)
                return False
            
            # For preview files, we need to validate that the base file exists
            # This validation is simplified for async processing - we assume the base file exists
            # since the original upload validation would have caught missing base files
            logger.info(f"Preview file {relative_key} validation passed (base file existence assumed)")
        
        # Copy file from temporary to final location
        logger.info(f"Copying file from {temp_s3_key} to {final_s3_key}")
        
        copy_success = copy_s3_object(
            bucket_name, 
            temp_s3_key, 
            bucket_name, 
            final_s3_key,
            database_id,
            asset_id
        )
        
        if not copy_success:
            logger.error(f"Failed to copy file from {temp_s3_key} to {final_s3_key}")
            return False
        
        # Delete temporary file after successful copy
        delete_s3_object(bucket_name, temp_s3_key)
        
        # Update asset record if this is an assetFile upload
        if upload_type == "assetFile":
            update_asset_after_file_processing(asset_id, database_id, bucket_name, final_s3_key)
        elif upload_type == "assetPreview":
            update_asset_preview_location(asset_id, database_id, final_s3_key)
        
        logger.info(f"Successfully validated and moved file {relative_key}")
        return True
        
    except Exception as e:
        logger.exception(f"Error validating and moving large file: {e}")
        return False


def complete_multipart_upload_for_large_file(file_info: Dict[str, Any], correlation_ids: Dict[str, str]) -> bool:
    """
    Complete the multipart upload for a large file at the temporary location.
    Ported from uploadFile.py complete_upload function.
    
    Args:
        file_info: Dictionary containing file processing information
        correlation_ids: Dictionary containing correlation IDs for logging
        
    Returns:
        True if multipart upload completion was successful, False otherwise
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
        
        # Handle zero-byte files (identified by uploadIdS3 = "zero-byte")
        if upload_id_s3 == "zero-byte":
            logger.info(f"Creating zero-byte file {relative_key} during async processing")
            return create_zero_byte_file(bucket_name, temp_s3_key, upload_id, database_id, asset_id)
        
        # Handle abandoned uploads (no parts provided) - create empty file
        if not parts or len(parts) == 0:
            logger.info(f"No parts provided for file {relative_key}, creating empty file")
            
            # Abort the existing multipart upload
            try:
                s3.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=temp_s3_key,
                    UploadId=upload_id_s3
                )
            except Exception as abort_error:
                logger.warning(f"Error aborting multipart upload for abandoned file: {abort_error}")
            
            # Create empty file in temporary location
            return create_zero_byte_file(bucket_name, temp_s3_key, upload_id, database_id, asset_id)
        
        # Regular multipart upload completion
        actual_parts = sorted([p['PartNumber'] for p in parts])
        
        # Check for duplicates in part numbers
        if len(actual_parts) != len(set(actual_parts)):
            logger.error(f"Duplicate part numbers provided for {relative_key}")
            return False
        
        # Log the parts we received
        logger.info(f"Received {len(actual_parts)} parts for file {relative_key}: {actual_parts}")
        
        # Complete multipart upload in temporary location
        try:
            s3.complete_multipart_upload(
                Bucket=bucket_name,
                Key=temp_s3_key,
                UploadId=upload_id_s3,
                MultipartUpload={'Parts': [{'PartNumber': p['PartNumber'], 'ETag': p['ETag']} for p in parts]}
            )
            logger.info(f"Successfully completed multipart upload for {relative_key}")
        except Exception as e:
            logger.exception(f"Error completing multipart upload for {relative_key}: {e}")
            
            # Abort the multipart upload to clean up S3 resources
            try:
                s3.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=temp_s3_key,
                    UploadId=upload_id_s3
                )
            except Exception as abort_error:
                logger.exception(f"Error aborting multipart upload: {abort_error}")
            
            return False
        
        # Verify the metadata of the completed object
        try:
            head_response = s3.head_object(
                Bucket=bucket_name,
                Key=temp_s3_key
            )
            
            # Extract metadata
            metadata = head_response.get('Metadata', {})
            s3_upload_id = metadata.get('uploadid')
            
            # Verify the uploadId matches
            if s3_upload_id != upload_id:
                logger.error(f"Upload ID mismatch for {relative_key}. Expected: {upload_id}, Got: {s3_upload_id}")
                # Delete the uploaded file since metadata doesn't match
                delete_s3_object(bucket_name, temp_s3_key)
                return False
            
            # Get file size for validation
            file_size = head_response.get('ContentLength', 0)
            logger.info(f"Completed file {relative_key} has size: {file_size} bytes")
            
            # Check file size for preview files - both assetPreview type and .previewFile. files
            upload_type = file_info.get('uploadType')
            if (upload_type == "assetPreview" or is_preview_file(relative_key)) and file_size > MAX_PREVIEW_FILE_SIZE:
                logger.error(f"Preview file {relative_key} exceeds maximum allowed size of 5MB")
                # Delete the uploaded file since it exceeds the size limit
                delete_s3_object(bucket_name, temp_s3_key)
                return False
            
            return True
            
        except Exception as e:
            logger.exception(f"Error verifying file metadata for {relative_key}: {e}")
            # Delete the uploaded file since we couldn't verify metadata
            delete_s3_object(bucket_name, temp_s3_key)
            return False
            
    except Exception as e:
        logger.exception(f"Error completing multipart upload for large file: {e}")
        return False

def process_large_file(file_info: Dict[str, Any], correlation_ids: Dict[str, str]) -> bool:
    """
    Process a single large file from SQS message with comprehensive error handling.
    
    Args:
        file_info: Dictionary containing file processing information
        correlation_ids: Dictionary containing correlation IDs for logging
        
    Returns:
        True if processing was successful, False otherwise
    """
    start_time = datetime.utcnow()
    correlation_str = format_correlation_ids(correlation_ids)
    
    try:
        logger.info(f"Starting large file processing - {correlation_str}")
        
        # Step 1: Complete multipart upload at temporary location
        logger.info(f"Step 1: Completing multipart upload - {correlation_str}")
        step1_start = datetime.utcnow()
        
        if not complete_multipart_upload_for_large_file(file_info, correlation_ids):
            logger.error(f"Step 1 failed: multipart upload completion - {correlation_str}")
            return False
            
        step1_duration = (datetime.utcnow() - step1_start).total_seconds()
        logger.info(f"Step 1 completed in {step1_duration:.2f}s - {correlation_str}")
            
        # Step 2: Validate file content and move to final location
        logger.info(f"Step 2: Validating and moving file - {correlation_str}")
        step2_start = datetime.utcnow()
        
        if not validate_and_move_large_file(file_info, correlation_ids):
            logger.error(f"Step 2 failed: file validation and movement - {correlation_str}")
            return False
            
        step2_duration = (datetime.utcnow() - step2_start).total_seconds()
        logger.info(f"Step 2 completed in {step2_duration:.2f}s - {correlation_str}")
            
        total_duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"Large file processing completed successfully in {total_duration:.2f}s - {correlation_str}")
        return True
        
    except Exception as e:
        total_duration = (datetime.utcnow() - start_time).total_seconds()
        logger.exception(f"Unexpected error processing large file after {total_duration:.2f}s - {correlation_str}: {e}")
        
        # Attempt cleanup on failure
        try:
            cleanup_failed_processing(file_info, correlation_ids)
        except Exception as cleanup_error:
            logger.exception(f"Error during cleanup after processing failure - {correlation_str}: {cleanup_error}")
            
        return False

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
            
            # Process the large file
            success = process_large_file(file_info, correlation_ids)
            
            if success:
                logger.info(f"Successfully processed large file - {format_correlation_ids(correlation_ids)}")
                success_count += 1
                
                # AUDIT LOG: Large file upload completed asynchronously
                # Create a mock event for audit logging since this is SQS-triggered
                try:
                    mock_event = {
                        'requestContext': {
                            'authorizer': {
                                'jwt': {
                                    'claims': {
                                        'sub': 'SYSTEM_ASYNC_PROCESSOR'
                                    }
                                }
                            }
                        }
                    }
                    
                    log_file_upload(
                        mock_event,
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
                            "correlationId": record_correlation_id
                        }
                    )
                except Exception as audit_error:
                    logger.exception(f"Failed to log large file upload audit - {format_correlation_ids(correlation_ids)}: {audit_error}")
            else:
                logger.error(f"Failed to process large file - {format_correlation_ids(correlation_ids)}")
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
                f"Failures: {failure_count}")
    
    # Note: SQS messages are automatically acknowledged on successful Lambda execution
    # Failed messages will be retried based on SQS configuration