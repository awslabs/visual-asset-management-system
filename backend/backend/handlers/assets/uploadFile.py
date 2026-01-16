# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
import time
import re
from typing import List, Optional
from datetime import datetime, timedelta
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_file_upload
from botocore.exceptions import ClientError
from common.s3 import validateS3AssetExtensionsAndContentType, validateUnallowedFileExtensionAndContentType
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetsV3 import (
    InitializeUploadRequestModel, InitializeUploadResponseModel, UploadPartModel, UploadFileResponseModel,
    CompleteUploadRequestModel, CompleteUploadResponseModel, FileCompletionResult,
    CompleteExternalUploadRequestModel, ExternalFileModel,
    AssetUploadTableModel
)

#Set environment variable for S3 client configuration
#'regional' set to add region decriptor to presigned urls for us-east-1 (ignored for non us-east-1 regions)
os.environ["AWS_S3_US_EAST_1_REGIONAL_ENDPOINT"] = "regional" 

# Configure AWS clients with retry configuration
region = os.environ['AWS_REGION']

# Standardized retry configuration merged with existing S3 config
s3_config = Config(
    signature_version='s3v4', 
    s3={'addressing_style': 'path'},
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

s3 = boto3.client('s3', region_name=region, config=s3_config)
s3_resource = boto3.resource('s3', region_name=region, config=s3_config)
lambda_client = boto3.client('lambda', config=s3_config)
dynamodb = boto3.resource('dynamodb', config=s3_config)
dynamodb_client = boto3.client('dynamodb', config=s3_config)
sqs = boto3.client('sqs', config=s3_config)
logger = safeLogger(service_name="UploadFile")

# Constants
UPLOAD_EXPIRATION_DAYS = 7  # TTL for upload records and S3 multipart uploads
TEMPORARY_UPLOAD_PREFIX = 'temp-uploads/'  # Prefix for temporary uploads
PREVIEW_PREFIX = 'previews/'
MAX_PART_SIZE = 150 * 1024 * 1024  # 150MB per part
MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024  # 5MB maximum size for preview files
MAX_ALLOWED_UPLOAD_PERUSER_PERMINUTE = 20
LARGE_FILE_THRESHOLD_BYTES = 1 * 1024 * 1024 * 1024   # 1GB threshold for asynchronous processing
allowed_preview_extensions = ['.png', '.jpg', '.jpeg', '.svg', '.gif']

# Load environment variables
try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    database_storage_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_upload_table_name = os.environ["ASSET_UPLOAD_TABLE_NAME"]
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
    token_timeout = os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]
    large_file_processing_queue_url = os.environ.get("LARGE_FILE_PROCESSING_QUEUE_URL")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
buckets_table = dynamodb.Table(s3_asset_buckets_table)
asset_table = dynamodb.Table(asset_storage_table_name)
asset_upload_table = dynamodb.Table(asset_upload_table_name)

#######################
# Utility Functions
#######################

def calculate_num_parts(file_size=None, num_parts=None, max_part_size=MAX_PART_SIZE):
    """Calculate the number of parts needed for a multipart upload"""
    if num_parts is not None:
        # User specified parts directly - validate against S3 limits but allow large part sizes
        if num_parts > 10000:
            raise ValueError("Number of parts cannot exceed 10,000 (S3 limit)")
        return num_parts
    elif file_size is not None:
        # Handle zero-byte files
        if file_size == 0:
            return 0
        # Calculate parts using standard 150MB chunks
        return -(-file_size // max_part_size)  # Ceiling division
    else:
        raise ValueError("Either file_size or num_parts must be provided")


def check_user_rate_limit(user_id: str) -> bool:
    """
    Check if user has exceeded rate limit of 5 upload initializations per minute.
    Uses UserIdGSI with STRING type for both UserId and createdAt fields.
    
    Args:
        user_id: The user ID to check
        
    Returns:
        True if user is within rate limit, False if exceeded
    """
    try:
        # Calculate timestamp for 1 minute ago as ISO string
        one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
        one_minute_ago_iso = one_minute_ago.isoformat()  # STRING format for DynamoDB
        
        logger.info(f"Checking rate limit for user {user_id} since {one_minute_ago_iso}")
        
        # Query UserIdGSI: partition by UserId, filter by createdAt > one_minute_ago
        response = asset_upload_table.query(
            IndexName='UserIdGSI',
            KeyConditionExpression=Key('UserId').eq(user_id) & Key('createdAt').gt(one_minute_ago_iso),
            Select='COUNT'  # Only count, don't return items for efficiency
        )
        
        upload_count = response.get('Count', 0)
        logger.info(f"User {user_id} has {upload_count} uploads in the last minute")
        
        return upload_count < MAX_ALLOWED_UPLOAD_PERUSER_PERMINUTE  # Allow up to X uploads per minute
        
    except Exception as e:
        logger.warning(f"Error checking rate limit for user {user_id}: {e}")
        return True  # Fail open for availability

def generate_presigned_url(key, upload_id, part_number, bucket, expiration=token_timeout):
    """Generate a presigned URL for a multipart upload part"""
    url = s3.generate_presigned_url(
        ClientMethod='upload_part',
        Params={
            'Bucket': bucket,
            'Key': key,
            'PartNumber': part_number,
            'UploadId': upload_id
        },
        ExpiresIn=expiration
    )
    return url

def get_database_details(databaseId):
    """Get database details from DynamoDB including file upload restrictions
    
    Args:
        databaseId: The database ID
        
    Returns:
        Database details dictionary with restrictFileUploadsToExtensions field
    """
    try:
        database_table = dynamodb.Table(database_storage_table_name)
        response = database_table.get_item(
            Key={'databaseId': databaseId}
        )
        return response.get('Item')
    except Exception as e:
        logger.exception(f"Error getting database details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving database configuration")

def validate_file_extension_against_database(file_path: str, allowed_extensions: str) -> tuple:
    """Validate file extension against database restrictions
    
    Args:
        file_path: The file path to validate
        allowed_extensions: Comma-delimited list of allowed extensions (e.g., ".pdf,.jpg,.png")
        
    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if extension is allowed, False otherwise
        - error_message: Error message if validation fails, None otherwise
    """
    # Handle empty or None allowed_extensions (no restrictions)
    if not allowed_extensions or allowed_extensions.strip() == "":
        return True, None
    
    # Parse comma-delimited extensions and normalize to lowercase
    allowed_list = [ext.strip().lower() for ext in allowed_extensions.split(',') if ext.strip()]
    
    # Check for ".all" bypass
    if ".all" in allowed_list:
        return True, None
    
    # Extract file extension (case-insensitive)
    file_extension = os.path.splitext(file_path)[1].lower()
    
    # Validate extension
    if file_extension not in allowed_list:
        error_message = f"Database does not allow this file extension. Allowed extensions: {', '.join(allowed_list)}"
        return False, error_message
    
    return True, None

def get_default_bucket_details(bucketId):
    """Get default S3 bucket details from database default bucket DynamoDB"""
    try:

        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucketId),
            Limit=1
        )
        # Use the first item from the query results
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        #Check to make sure we have what we need
        if not bucket_name or not base_assets_prefix:
            raise VAMSGeneralErrorResponse(f"Error getting database default bucket details.")
        
        #Make sure we end in a slash for the path
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        # Remove leading slash from file path if present
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting bucket details.")

def get_asset_details(databaseId, assetId):
    """Get asset details from DynamoDB"""
    try:
        response = asset_table.get_item(
            Key={
                'databaseId': databaseId,
                'assetId': assetId
            }
        )
        return response.get('Item')
    except Exception as e:
        logger.exception(f"Error getting asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving asset.")

def save_asset_details(asset_data):
    """Save asset details to DynamoDB"""
    try:
        asset_table.put_item(Item=asset_data)
    except Exception as e:
        logger.exception(f"Error saving asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error saving asset.")

def save_upload_details(upload_data):
    """Save upload details to DynamoDB"""
    try:
        asset_upload_table.put_item(Item=upload_data.to_dict())
    except Exception as e:
        logger.exception(f"Error saving upload details: {e}")
        raise VAMSGeneralErrorResponse(f"Error saving upload details.")

def get_upload_details(uploadId, assetId):
    """Get upload details from DynamoDB
    
    Args:
        uploadId: The upload ID
        assetId: The asset ID (required if the table has a composite key)
        
    Returns:
        The upload details from DynamoDB
    """
    try:

        logger.info(f"Getting upload details for uploadId: {uploadId}, assetId: {assetId}")
        response = asset_upload_table.get_item(
            Key={
                'uploadId': uploadId,
                'assetId': assetId
            }
        )

        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Upload record not found")
        return response['Item']
    except Exception as e:
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        logger.exception(f"Error getting upload details: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving upload details.")

def delete_upload_details(uploadId, assetId):
    """Delete upload details from DynamoDB
    
    Args:
        uploadId: The upload ID
        assetId: The asset ID (required if the table has a composite key)
    """
    try:
        # If assetId is provided, use it as part of the key

        logger.info(f"Deleting upload details for uploadId: {uploadId}, assetId: {assetId}")
        asset_upload_table.delete_item(
            Key={
                'uploadId': uploadId,
                'assetId': assetId
            }
        )

    except Exception as e:
        logger.exception(f"Error deleting upload details: {e}")
        # Don't raise here, just log the error

def is_file_archived(bucket: str, key: str, version_id: str = None) -> bool:
    """Determine if file is archived based on S3 delete markers
    
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

def determine_asset_type(assetId, bucket, prefix):
    """Determine the asset type based on S3 contents"""
    try:
        
        logger.info(f"Determining asset type from bucket: {bucket}, prefix: {prefix}")
        
        # List all objects with the specified prefix
        response = s3.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
        )
        
        # Get the contents and filter out folder markers (objects ending with '/')
        contents = response.get('Contents', [])
        
        # Filter out archived files
        non_archived_files = []
        file_count = 0
        for item in contents:
            if item['Key'].endswith('/'):
                # Skip folder markers
                continue
                
            try:
                # Check if file is archived using the new method
                if not is_file_archived(bucket, item['Key']):
                    non_archived_files.append(item)
                    file_count += 1
                    
                    # Short circuit if we've found more than one file
                    if file_count > 1:
                        logger.info(f"Found multiple files, short-circuiting and returning 'folder'")
                        return 'folder'
            except Exception as e:
                logger.warning(f"Error checking if file {item['Key']} is archived: {e}")
                # If we can't check archive status, include the file by default
                non_archived_files.append(item)
                file_count += 1
                
                # Short circuit if we've found more than one file
                if file_count > 1:
                    logger.info(f"Found multiple files, short-circuiting and returning 'folder'")
                    return 'folder'
        
        # At this point, we have 0 or 1 files
        logger.info(f"Found {file_count} non-archived files in {bucket}/{prefix} (total objects: {len(contents)})")
        
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

def send_subscription_email(database_id, asset_id):
    """Send email notifications to subscribers when an asset is updated"""
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
    except Exception as e:
        logger.exception(f"Error invoking send_email Lambda function: {e}")

def copy_s3_object(source_bucket, source_key, dest_bucket, dest_key, database_id, asset_id):
    """Copy an object from one S3 location to another with replaced metadata
    
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
        return True
    except Exception as e:
        logger.exception(f"Error copying S3 object from {source_key} to {dest_key}: {e}")
        return False

def delete_s3_object(bucket, key):
    """Delete an object from S3"""
    try:
        s3.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        logger.exception(f"Error deleting S3 object {key}: {e}")
        return False

def normalize_s3_path(asset_base_key, file_path):
    """
    Intelligently resolve the full S3 key, avoiding duplication if file_path already contains the asset base key.
    
    Args:
        asset_base_key: The base key from assetLocation (e.g., "assetId/" or "custom/path/")
        file_path: The file path from the request (may or may not include the base key)
        
    Returns:
        The properly resolved S3 key without duplication
    """
    # Normalize the asset base key to ensure it ends with '/'
    if asset_base_key and not asset_base_key.endswith('/'):
        asset_base_key = asset_base_key + '/'
    
    # Remove leading slash from file path if present
    if file_path.startswith('/'):
        file_path = file_path[1:]
    
    # Check if file_path already starts with the asset_base_key
    if file_path.startswith(asset_base_key):
        # File path already contains the base key, use as-is
        logger.info(f"File path '{file_path}' already contains base key '{asset_base_key}', using as-is")
        return file_path
    else:
        # File path doesn't contain base key, combine them
        resolved_path = asset_base_key + file_path
        logger.info(f"Combined base key '{asset_base_key}' with file path '{file_path}' to get '{resolved_path}'")
        return resolved_path

def is_preview_file(file_path: str) -> bool:
    """Check if file is a preview file (.previewFile.X pattern)
    
    Args:
        file_path: The file path to check
        
    Returns:
        True if the file is a preview file, False otherwise
    """
    return '.previewFile.' in file_path

def get_base_file_path(preview_file_path: str) -> str:
    """Extract base file path from preview file path
    
    Args:
        preview_file_path: The preview file path
        
    Returns:
        The base file path
    """
    if not is_preview_file(preview_file_path):
        return preview_file_path
    
    # Split at .previewFile. and take the first part
    return preview_file_path.split('.previewFile.')[0]

def validate_preview_files_with_base_files(files_in_request, asset_base_key, bucket_name):
    """Validate that all preview files in the request have corresponding base files
    
    This function checks if all .previewFile. files in the request have their associated
    base files either in the same request or already existing in S3.
    
    Args:
        files_in_request: List of file details in the current request
        asset_base_key: The base key for the asset in S3
        bucket_name: The S3 bucket name
        
    Returns:
        Tuple of (is_valid, error_message, invalid_files)
        - is_valid: True if all preview files have valid base files, False otherwise
        - error_message: Error message if validation fails, None otherwise
        - invalid_files: List of preview files that failed validation
    """
    # Extract all preview files and their base paths
    preview_files = []
    base_files_in_request = set()
    invalid_files = []
    
    # First pass: identify all files in the request
    for file in files_in_request:
        relative_key = file.get('relativeKey')
        if is_preview_file(relative_key):
            preview_files.append(file)
        else:
            # Add to the set of base files in this request
            base_files_in_request.add(relative_key)
    
    # If no preview files, validation passes
    if not preview_files:
        return True, None, []
    
    # Second pass: validate each preview file
    for preview_file in preview_files:
        preview_relative_key = preview_file.get('relativeKey')
        base_file_path = get_base_file_path(preview_relative_key)
        
        # Check if base file exists in the current request
        if base_file_path in base_files_in_request:
            continue
        
        # If not in request, check if it exists in S3
        base_file_key = normalize_s3_path(asset_base_key, base_file_path)
        try:
            s3.head_object(Bucket=bucket_name, Key=base_file_key)
            # Base file exists in S3, so this preview file is valid
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # Base file doesn't exist in S3 or in the current request
                invalid_files.append(preview_relative_key)
                logger.warning(f"Preview file {preview_relative_key} is missing its base file {base_file_path}")
            else:
                # Other error occurred, log and continue
                logger.warning(f"Error checking if base file {base_file_key} exists: {e}")
                # Conservatively mark as invalid if we can't verify
                invalid_files.append(preview_relative_key)
    
    # If any invalid files were found, return validation failure
    if invalid_files:
        error_message = f"Preview files are missing their base files."
        return False, error_message, invalid_files
    
    return True, None, []

def check_preview_file_size(bucket_name, key, file_path):
    """Check if a preview file exceeds the maximum allowed size
    
    Args:
        bucket_name: The S3 bucket name
        key: The S3 object key
        file_path: The file path for error reporting
        
    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if file size is valid, False otherwise
        - error_message: Error message if validation fails, None otherwise
    """
    try:
        head_response = s3.head_object(Bucket=bucket_name, Key=key)
        file_size = head_response.get('ContentLength', 0)
        
        if file_size > MAX_PREVIEW_FILE_SIZE:
            error_message = f"Preview files exceeds maximum allowed size of 5MB"
            return False, error_message
        
        return True, None
    except Exception as e:
        logger.warning(f"Error checking size of preview file {key}: {e}")
        return False, f"Error checking size of preview files"

def validate_preview_file_extension(file_path: str) -> bool:
    """Validate preview file has allowed extension
    
    Args:
        file_path: The file path to check
        
    Returns:
        True if the file has an allowed extension, False otherwise
    """
    
    # Extract the extension after .previewFile.
    if '.previewFile.' in file_path:
        extension = '.' + file_path.split('.previewFile.')[1].lower()
        return extension in allowed_preview_extensions
    
    # For direct assetPreview uploads, check the file extension
    file_extension = os.path.splitext(file_path)[1].lower()
    return file_extension in allowed_preview_extensions

def find_existing_preview_files(bucket: str, base_file_key: str) -> List[str]:
    """Find existing preview files for a base file
    
    Args:
        bucket: The S3 bucket name
        base_file_key: The base file key
        
    Returns:
        List of preview file keys
    """
    preview_files = []
    
    try:
        # Get the directory and filename parts
        directory = os.path.dirname(base_file_key)
        filename = os.path.basename(base_file_key)
        
        # Create the prefix for listing objects
        prefix = f"{directory}/" if directory else ""
        
        # Create the pattern to match preview files for this base file
        pattern = f"{filename}.previewFile."
        
        # List objects in the directory
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                base_filename = os.path.basename(key)
                
                # Check if this is a preview file for our base file
                if base_filename.startswith(pattern):
                    preview_files.append(key)
        
        # Sort the preview files alphabetically
        preview_files.sort()
        
    except Exception as e:
        logger.warning(f"Error finding preview files for {base_file_key}: {e}")
    
    return preview_files

def delete_existing_preview_files(bucket: str, base_file_key: str) -> List[str]:
    """Delete existing preview files for a base file
    
    Args:
        bucket: The S3 bucket name
        base_file_key: The base file key
        
    Returns:
        List of deleted preview file keys
    """
    deleted_files = []
    
    # Find existing preview files
    preview_files = find_existing_preview_files(bucket, base_file_key)
    
    # Delete each preview file
    for preview_file in preview_files:
        try:
            # Add delete marker (soft delete)
            s3.delete_object(Bucket=bucket, Key=preview_file)
            deleted_files.append(preview_file)
            logger.info(f"Deleted preview file: {preview_file}")
        except Exception as e:
            logger.warning(f"Error deleting preview file {preview_file}: {e}")
    
    return deleted_files

def create_zero_byte_file(bucket_name: str, key: str, upload_id: str, database_id: str, asset_id: str) -> bool:
    """Create a zero-byte file in S3
    
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

def get_completed_file_size(bucket_name: str, key: str) -> int:
    """Get the size of a completed file in S3
    
    Args:
        bucket_name: The S3 bucket name
        key: The S3 object key
        
    Returns:
        The file size in bytes, or 0 if file doesn't exist or error occurs
    """
    try:
        if not bucket_name or not key:
            logger.warning(f"Invalid parameters for get_completed_file_size: bucket_name='{bucket_name}', key='{key}'")
            return 0
            
        head_response = s3.head_object(Bucket=bucket_name, Key=key)
        file_size = head_response.get('ContentLength')
        
        if file_size is None:
            logger.warning(f"ContentLength is None for file {key}, defaulting to 0")
            return 0
            
        return file_size
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        if error_code == 'NoSuchKey':
            logger.warning(f"File {key} not found in bucket {bucket_name}")
        elif error_code == 'NoSuchBucket':
            logger.warning(f"Bucket {bucket_name} not found")
        else:
            logger.warning(f"AWS ClientError getting file size for {key}: {error_code} - {e.response.get('Error', {}).get('Message', str(e))}")
        return 0
    except Exception as e:
        logger.warning(f"Unexpected error getting file size for {key}: {e}")
        return 0

def should_process_asynchronously(file_size: int) -> bool:
    """Determine if file should be processed asynchronously based on size
    
    Args:
        file_size: The file size in bytes
        
    Returns:
        True if file should be processed asynchronously, False otherwise
    """
    try:
        # Validate file_size parameter
        if file_size is None:
            logger.warning("File size is None, defaulting to synchronous processing")
            return False
            
        if not isinstance(file_size, (int, float)):
            logger.warning(f"File size is not a number ({type(file_size)}), defaulting to synchronous processing")
            return False
            
        if file_size < 0:
            logger.warning(f"File size is negative ({file_size}), defaulting to synchronous processing")
            return False
            
        # Check against threshold
        return file_size > LARGE_FILE_THRESHOLD_BYTES
        
    except Exception as e:
        logger.warning(f"Error in should_process_asynchronously: {e}")
        # Default to synchronous processing on any error
        return False

def queue_large_file_for_processing(file_info: dict, sqs_queue_url: str) -> bool:
    """Queue a large file for asynchronous processing
    
    Args:
        file_info: Dictionary containing file processing information
        sqs_queue_url: The SQS queue URL for large file processing
        
    Returns:
        True if successfully queued, False otherwise
    """
    try:
        if not sqs_queue_url:
            logger.warning("Large file processing queue URL not configured")
            return False
            
        # Validate required file info fields
        required_fields = ['relativeKey', 'uploadIdS3', 'tempS3Key', 'finalS3Key', 'bucketName', 'databaseId', 'assetId', 'uploadId', 'uploadType']
        for field in required_fields:
            if field not in file_info:
                logger.error(f"Missing required field '{field}' in file_info for SQS message")
                return False
        
        # Create SQS message payload
        message_body = {
            "fileInfo": file_info
        }
        
        # Send message to SQS queue with retry handling
        try:
            response = sqs.send_message(
                QueueUrl=sqs_queue_url,
                MessageBody=json.dumps(message_body)
            )
            
            message_id = response.get('MessageId')
            if not message_id:
                logger.error(f"SQS send_message returned no MessageId for file {file_info.get('relativeKey')}")
                return False
                
            logger.info(f"Successfully queued large file for processing: {file_info.get('relativeKey')} (MessageId: {message_id})")
            return True
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"AWS ClientError queuing file {file_info.get('relativeKey')} to SQS: {error_code} - {error_message}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error queuing file {file_info.get('relativeKey')} to SQS: {e}")
            return False
        
    except Exception as e:
        logger.exception(f"Error in queue_large_file_for_processing for file {file_info.get('relativeKey', 'unknown')}: {e}")
        return False

def calculate_total_file_size_from_parts(bucket_name: str, key: str, upload_id: str, expected_parts: list) -> tuple:
    """Calculate total file size by listing parts of an incomplete multipart upload
    
    Args:
        bucket_name: The S3 bucket name
        key: The S3 object key
        upload_id: The multipart upload ID
        expected_parts: List of expected part objects with PartNumber and ETag
        
    Returns:
        Tuple of (total_size, success, error_message)
    """
    try:
        list_parts_response = s3.list_parts(
            Bucket=bucket_name,
            Key=key,
            UploadId=upload_id
        )
        
        # Calculate total size from all uploaded parts
        total_file_size = 0
        uploaded_parts = list_parts_response.get('Parts', [])
        
        # Verify all requested parts are uploaded and calculate size
        uploaded_part_numbers = {part['PartNumber'] for part in uploaded_parts}
        requested_part_numbers = {p.PartNumber for p in expected_parts}
        
        if uploaded_part_numbers != requested_part_numbers:
            missing_parts = requested_part_numbers - uploaded_part_numbers
            extra_parts = uploaded_part_numbers - requested_part_numbers
            error_msg = f"Part mismatch."
            if missing_parts:
                error_msg += f" Missing parts: {sorted(missing_parts)}."
            if extra_parts:
                error_msg += f" Extra parts: {sorted(extra_parts)}."
            return 0, False, error_msg
        
        # Calculate total size
        for part in uploaded_parts:
            total_file_size += part['Size']
        
        return total_file_size, True, None
        
    except Exception as e:
        logger.exception(f"Error calculating file size from parts: {e}")
        return 0, False, f"Error verifying upload parts: {str(e)}"

#######################
# API Implementations
#######################

def initialize_upload(request_model: InitializeUploadRequestModel, claims_and_roles):
    """Initialize a multipart upload for asset files or preview"""
    assetId = request_model.assetId
    databaseId = request_model.databaseId
    uploadType = request_model.uploadType
    
    # Extract user ID and check rate limit
    user_id = claims_and_roles.get("tokens", ["system"])[0]
    
    if not check_user_rate_limit(user_id):
        # Return 429 Too Many Requests
        raise VAMSGeneralErrorResponse(f"Rate limit exceeded. Maximum {MAX_ALLOWED_UPLOAD_PERUSER_PERMINUTE} upload initializations per user per minute allowed.")
    
    # Verify asset exists
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse("Asset not found")
    
    # Get database details to check file upload restrictions
    database = get_database_details(databaseId)
    if not database:
        raise VAMSGeneralErrorResponse("Database not found")
    
    allowed_extensions = database.get('restrictFileUploadsToExtensions', '')
    
    # Validate file extensions if restrictions are configured
    # Only apply to regular asset files, not asset previews or file preview files
    if allowed_extensions and allowed_extensions.strip() != "" and uploadType == "assetFile":
        for file in request_model.files:
            # Skip validation for .previewFile. files (they have their own extension restrictions)
            if not is_preview_file(file.relativeKey):
                is_valid, error_message = validate_file_extension_against_database(
                    file.relativeKey, 
                    allowed_extensions
                )
                if not is_valid:
                    raise VAMSGeneralErrorResponse(error_message)
        
    # Additional business logic validation
    if uploadType == "assetPreview" and asset.get('previewLocation'):
        logger.info(f"Asset {assetId} already has a preview. The existing preview will be replaced.")
        
        # Validate file extensions before proceeding
        for file in request_model.files:
            if not validateUnallowedFileExtensionAndContentType(file.relativeKey, ""):
                raise VAMSGeneralErrorResponse(f"File provided has an unsupported file extension")
            
            # Additional validation for preview files
            if uploadType == "assetPreview":
                # Validate preview file extension
                if not validate_preview_file_extension(file.relativeKey):
                    raise VAMSGeneralErrorResponse(f"Preview files must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif")
            
            # Check if this is a preview file in an assetFile upload
            if uploadType == "assetFile" and is_preview_file(file.relativeKey):
                # Validate preview file extension
                if not validate_preview_file_extension(file.relativeKey):
                    raise VAMSGeneralErrorResponse(f"Preview files must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif")
    
    # Generate upload ID
    uploadId = f"y{str(uuid.uuid4())}"
    
    # Calculate expiration time (7 days from now)
    now = datetime.utcnow()
    expires_at = int((now + timedelta(days=UPLOAD_EXPIRATION_DAYS)).timestamp())
    
    # Process files
    file_responses = []
    total_parts = 0
            
    # Get bucket details from asset's bucketId
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    bucket_name = bucketDetails['bucketName']
    baseAssetsPrefix = bucketDetails['baseAssetsPrefix']
    
    for file in request_model.files:
        # Validate file extension
        if not validateUnallowedFileExtensionAndContentType(file.relativeKey, ""):
            raise VAMSGeneralErrorResponse(f"Files contain an unsupported file extension")
        
        # Validate file size for preview files
        if uploadType == "assetPreview" and file.file_size > MAX_PREVIEW_FILE_SIZE:
            raise VAMSGeneralErrorResponse(f"Preview files exceeds maximum allowed size of 5MB per file")
        
        # Determine final S3 key based on upload type
        if uploadType == "assetFile":
            # Get the asset's base key from assetLocation
            asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")
            final_s3_key = normalize_s3_path(asset_base_key, file.relativeKey)
        else:  # assetPreview
            #We only want the filename and none of the path if there is a path
            filename = os.path.basename(file.relativeKey)
            final_s3_key = f"{baseAssetsPrefix}{PREVIEW_PREFIX}{assetId}/{filename}"
            
        # Determine temporary S3 key by adding temp prefix to final key
        temp_s3_key = f"{baseAssetsPrefix}{TEMPORARY_UPLOAD_PREFIX}{final_s3_key}"
        
        # Calculate number of parts
        num_parts = calculate_num_parts(file.file_size, file.num_parts)
        total_parts += num_parts
        
        # Handle zero-byte files differently - don't create them yet, just return special response
        if num_parts == 0:
            # Add to response with special uploadIdS3 to identify zero-byte files
            # Zero-byte file will be created during completion
            file_responses.append(UploadFileResponseModel(
                relativeKey=file.relativeKey,
                uploadIdS3="zero-byte",  # Special identifier for zero-byte files
                numParts=0,
                partUploadUrls=[]  # No presigned URLs needed
            ))
        else:
            # Create multipart upload in temporary location with uploadId in metadata
            resp = s3.create_multipart_upload(
                Bucket=bucket_name,
                Key=temp_s3_key,
                ContentType='application/octet-stream',
                Metadata={
                    "databaseid": databaseId,
                    "assetid": assetId,
                    "uploadid": uploadId,  # Store the overall uploadId in S3 metadata
                }
            )
            s3_upload_id = resp['UploadId']
            
            # Generate presigned URLs for parts
            part_urls = []
            for part_number in range(1, num_parts + 1):
                url = generate_presigned_url(
                    temp_s3_key, 
                    s3_upload_id, 
                    part_number, 
                    bucket_name
                )
                part_urls.append(UploadPartModel(
                    PartNumber=part_number,
                    UploadUrl=url
                ))
            
            # Add to response
            file_responses.append(UploadFileResponseModel(
                relativeKey=file.relativeKey,
                uploadIdS3=s3_upload_id,
                numParts=num_parts,
                partUploadUrls=part_urls
            ))
    
    # Save summary upload details to DynamoDB
    upload_record = AssetUploadTableModel(
        uploadId=uploadId,
        assetId=assetId,
        databaseId=databaseId,
        uploadType=uploadType,
        createdAt=now.isoformat(),
        expiresAt=expires_at,
        totalFiles=len(request_model.files),
        totalParts=total_parts,
        status="initialized",
        UserId=user_id  # Include user ID for rate limiting
    )
    save_upload_details(upload_record)
    
    # Return response
    return InitializeUploadResponseModel(
        uploadId=uploadId,
        files=file_responses,
        message="Upload initialized successfully"
    )

def complete_external_upload(uploadId: str, request_model: CompleteExternalUploadRequestModel, event):
    """Complete an external upload and update the asset"""
    assetId = request_model.assetId
    databaseId = request_model.databaseId
    uploadType = request_model.uploadType
    
    # Get upload details from DynamoDB
    upload_details = get_upload_details(uploadId, assetId)
    
    # Verify asset exists
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse("Asset not found")
    
    # Verify upload details match request
    if upload_details['assetId'] != assetId or upload_details['databaseId'] != databaseId:
        raise VAMSGeneralErrorResponse("Upload details do not match request")
        
    # Verify upload type matches
    if upload_details['uploadType'] != uploadType:
        raise VAMSGeneralErrorResponse(f"Upload type mismatch.")
    
    # Verify this is an external upload
    if not upload_details.get('isExternalUpload', False):
        raise VAMSGeneralErrorResponse("This upload was not initialized as an external upload")
    
    # Verify temporary prefix is present
    if 'temporaryPrefix' not in upload_details:
        raise VAMSGeneralErrorResponse("Missing temporary prefix in upload details")
    
    # Update upload status in DynamoDB
    try:
        # Use both uploadId and assetId as the key
        asset_upload_table.update_item(
            Key={
                'uploadId': uploadId,
                'assetId': assetId
            },
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'processing'}
        )
    except Exception as e:
        logger.warning(f"Failed to update upload status: {e}")

    # Get bucket details from asset's bucketId
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    bucket_name = bucketDetails['bucketName']
    baseAssetsPrefix = bucketDetails['baseAssetsPrefix']
    
    # Get database details to check file upload restrictions
    database = get_database_details(databaseId)
    if not database:
        raise VAMSGeneralErrorResponse("Database not found")
    
    allowed_extensions = database.get('restrictFileUploadsToExtensions', '')
    
    # Track file completion results
    file_results = []
    successful_files = []
    has_failures = False
    
    # Process each file in the request
    for file in request_model.files:
        try:
            # Validate file extension if restrictions are configured
            # Only apply to regular asset files, not asset previews or file preview files
            if allowed_extensions and allowed_extensions.strip() != "" and uploadType == "assetFile":
                # Skip validation for .previewFile. files (they have their own extension restrictions)
                if not is_preview_file(file.relativeKey):
                    is_valid, error_message = validate_file_extension_against_database(
                        file.relativeKey, 
                        allowed_extensions
                    )
                    if not is_valid:
                        file_results.append(FileCompletionResult(
                            relativeKey=file.relativeKey,
                            uploadIdS3="external",
                            success=False,
                            error=error_message
                        ))
                        has_failures = True
                        continue
            
            # Verify the temporary key starts with the expected prefix
            if not file.tempKey.startswith(upload_details['temporaryPrefix']):
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3="external",  # External uploads don't have S3 upload IDs
                    success=False,
                    error=f"File temporary key {file.tempKey} does not start with expected prefix {upload_details['temporaryPrefix']}"
                ))
                has_failures = True
                continue
            
            # Determine final S3 key based on upload type
            if uploadType == "assetFile":
                # Get the asset's base key from assetLocation
                asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")
                final_s3_key = normalize_s3_path(asset_base_key, file.relativeKey)
            else:  # assetPreview
                #We only want the filename and none of the path if there is a path
                filename = os.path.basename(file.relativeKey)
                final_s3_key = f"{baseAssetsPrefix}{PREVIEW_PREFIX}{assetId}/{filename}"
            
            # Verify the file exists in S3
            try:
                head_response = s3.head_object(
                    Bucket=bucket_name,
                    Key=file.tempKey
                )
                
                # Get file size with error handling
                file_size = 0
                try:
                    file_size = head_response.get('ContentLength', 0)
                    if file_size is None:
                        logger.warning(f"ContentLength is None for external file {file.relativeKey}, defaulting to 0")
                        file_size = 0
                except Exception as size_error:
                    logger.warning(f"Error getting file size for external file {file.relativeKey}: {size_error}")
                    # Default to 0 for unknown file sizes (will process synchronously)
                    file_size = 0
                
                # Check file size for preview files - both assetPreview type and .previewFile. files
                if (uploadType == "assetPreview" or is_preview_file(file.relativeKey)) and file_size > MAX_PREVIEW_FILE_SIZE:
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3="external",
                        success=False,
                        error=f"Preview file {file.relativeKey} exceeds maximum allowed size of 5MB"
                    ))
                    has_failures = True
                    continue
                
                # Check if file should be processed asynchronously based on size
                process_async = False
                try:
                    process_async = should_process_asynchronously(file_size)
                except Exception as e:
                    logger.warning(f"Error determining if external file {file.relativeKey} should be processed asynchronously: {e}")
                    # Default to synchronous processing for unknown file sizes
                    process_async = False
                
                if process_async:
                    logger.info(f"External file {file.relativeKey} ({file_size} bytes) will be processed asynchronously")
                    
                    # Create file info for SQS message
                    file_info = {
                        "relativeKey": file.relativeKey,
                        "uploadIdS3": "external",
                        "parts": [],  # External uploads don't have parts
                        "tempS3Key": file.tempKey,
                        "finalS3Key": final_s3_key,
                        "bucketName": bucket_name,
                        "databaseId": databaseId,
                        "assetId": assetId,
                        "uploadId": uploadId,
                        "uploadType": uploadType
                    }
                    
                    # Try to queue the file for asynchronous processing with comprehensive error handling
                    try:
                        if queue_large_file_for_processing(file_info, large_file_processing_queue_url):
                            # Successfully queued - mark as successful with async flag
                            file_results.append(FileCompletionResult(
                                relativeKey=file.relativeKey,
                                uploadIdS3="external",
                                success=True,
                                largeFileAsynchronousHandling=True
                            ))
                            logger.info(f"Large external file {file.relativeKey} queued for asynchronous processing")
                            continue
                        else:
                            # Failed to queue - fall back to synchronous processing
                            logger.warning(f"Failed to queue large external file {file.relativeKey}, falling back to synchronous processing")
                            process_async = False
                    except Exception as e:
                        # SQS queuing error - fall back to synchronous processing
                        logger.warning(f"Error queuing large external file {file.relativeKey} for asynchronous processing: {e}")
                        logger.info(f"Falling back to synchronous processing for external file {file.relativeKey}")
                        process_async = False
                
            except Exception as e:
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3="external",
                    success=False,
                    error=f"File not found in S3."
                ))
                has_failures = True
                continue
            
            # Validate file content type
            if not validateS3AssetExtensionsAndContentType(bucket_name, file.tempKey):
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3="external",
                    success=False,
                    error="File contains a potentially malicious executable type object"
                ))
                has_failures = True
                continue
                
            # Check if this is a preview file in an assetFile upload
            if uploadType == "assetFile" and is_preview_file(file.relativeKey):
                # Get the base file path
                base_file_path = get_base_file_path(file.relativeKey)
                base_file_key = normalize_s3_path(asset_base_key, base_file_path)
                
                # Check if the base file exists in the current request
                base_file_in_request = False
                for other_file in request_model.files:
                    if other_file.relativeKey == base_file_path:
                        base_file_in_request = True
                        break
                
                # If not in the current request, check if it exists in S3
                if not base_file_in_request:
                    try:
                        s3.head_object(Bucket=bucket_name, Key=base_file_key)
                    except ClientError as e:
                        if e.response['Error']['Code'] == 'NoSuchKey':
                            # Base file doesn't exist in S3 or in the current request
                            file_results.append(FileCompletionResult(
                                relativeKey=file.relativeKey,
                                uploadIdS3="external",
                                success=False,
                                error=f"Base file {base_file_path} does not exist for preview file {file.relativeKey}"
                            ))
                            logger.warning(f"Preview file {file.relativeKey} is missing its base file {base_file_path}")
                            has_failures = True
                            continue
                        else:
                            # Other error occurred, log and conservatively reject the file
                            logger.warning(f"Error checking if base file {base_file_key} exists: {e}")
                            file_results.append(FileCompletionResult(
                                relativeKey=file.relativeKey,
                                uploadIdS3="external",
                                success=False,
                                error=f"Error verifying base file for preview file {file.relativeKey}"
                            ))
                            has_failures = True
                            continue
            
            # Create a file_detail dictionary with the information we need
            file_detail = {
                'relativeKey': file.relativeKey,
                'temp_s3_key': file.tempKey,
                'final_s3_key': final_s3_key,
                'uploadIdS3': "external"
            }
            
            # Add to successful files list
            successful_files.append(file_detail)
            file_results.append(FileCompletionResult(
                relativeKey=file.relativeKey,
                uploadIdS3="external",
                success=True,
                largeFileAsynchronousHandling=False
            ))
            
        except Exception as e:
            logger.exception(f"Error processing external file {file.relativeKey}: {e}")
            file_results.append(FileCompletionResult(
                relativeKey=file.relativeKey,
                uploadIdS3="external",
                success=False,
                error=str(e)
            ))
            has_failures = True
    
    # If no files were successfully uploaded, return error
    if not successful_files:
        delete_upload_details(uploadId, assetId)
        # Check if any file has largeFileAsynchronousHandling=true
        has_async_files = any(result.largeFileAsynchronousHandling for result in file_results)
        return CompleteUploadResponseModel(
            message="No files were successfully uploaded",
            uploadId=uploadId,
            assetId=assetId,
            fileResults=file_results,
            overallSuccess=False,
            largeFileAsynchronousHandling=has_async_files
        )
    
    # Only for assetFile uploads, validate that .previewFile. files have corresponding base files
    if uploadType == "assetFile":
        asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")
        
        # Check if there are any .previewFile. files in the successful files
        preview_files = [file_detail for file_detail in successful_files if is_preview_file(file_detail['relativeKey'])]
        
        if preview_files:
            # Create a list of file details for validation
            files_for_validation = [{'relativeKey': file_detail['relativeKey']} for file_detail in successful_files]
            
            # Validate preview files have base files
            is_valid, error_message, invalid_files = validate_preview_files_with_base_files(
                files_for_validation, 
                asset_base_key, 
                bucket_name
            )
            
            if not is_valid:
                # Mark invalid files as failed
                for file_detail in successful_files[:]:
                    if file_detail['relativeKey'] in invalid_files:
                        # Delete the uploaded file
                        delete_s3_object(bucket_name, file_detail['temp_s3_key'])
                        
                        # Update file result
                        for result in file_results:
                            if result.relativeKey == file_detail['relativeKey'] and result.success:
                                result.success = False
                                result.error = f"Base file does not exist for preview file {file_detail['relativeKey']}"
                                has_failures = True
                        
                        # Remove from successful files
                        successful_files.remove(file_detail)
                
                # If no files remain successful, return error
                if not successful_files:
                    delete_upload_details(uploadId, assetId)
                    # Check if any file has largeFileAsynchronousHandling=true
                    has_async_files = any(result.largeFileAsynchronousHandling for result in file_results)
                    return CompleteUploadResponseModel(
                        message="No files were successfully uploaded",
                        uploadId=uploadId,
                        assetId=assetId,
                        fileResults=file_results,
                        overallSuccess=False,
                        largeFileAsynchronousHandling=has_async_files
                    )
    
            # Copy successful files from temporary to final location
    for file_detail in successful_files:
        
        logger.info(f"Copying file from {file_detail['temp_s3_key']} to {file_detail['final_s3_key']}")
        
        # If this is a preview file in an assetFile upload, delete any existing preview files for the base file
        if uploadType == "assetFile" and is_preview_file(file_detail['relativeKey']):
            # Get the base file path
            base_file_path = get_base_file_path(file_detail['relativeKey'])
            base_file_key = normalize_s3_path(asset_base_key, base_file_path)
            
            # Delete existing preview files
            deleted_files = delete_existing_preview_files(bucket_name, base_file_key)
            if deleted_files:
                logger.info(f"Deleted {len(deleted_files)} existing preview files for {base_file_path}")
        
        copy_success = copy_s3_object(
            bucket_name, 
            file_detail['temp_s3_key'], 
            bucket_name, 
            file_detail['final_s3_key'],
            databaseId,
            assetId
        )
        
        if not copy_success:
            logger.error(f"Failed to copy file from {file_detail['temp_s3_key']} to {file_detail['final_s3_key']}")
            # Update the file result to indicate copy failure
            for result in file_results:
                if result.relativeKey == file_detail['relativeKey'] and result.success:
                    result.success = False
                    result.error = "Failed to copy file to final location"
                    has_failures = True
        else:
            # Delete temporary file after successful copy
            delete_s3_object(bucket_name, file_detail['temp_s3_key'])
    
    # Update asset record based on upload type
    if uploadType == "assetFile" and any(f.success for f in file_results):
        # Determine asset type using the asset's bucket and key location
        assetType = determine_asset_type(assetId, bucket_name, asset_base_key)
        logger.info(f"Asset type determined for asset {assetId}: {assetType}")
        
        
        # Update asset type - ensure we're not overriding with None
        if assetType:
            asset['assetType'] = assetType
        elif 'assetType' not in asset or not asset.get('assetType'):
            asset['assetType'] = 'none'
        # If asset already has a type and assetType is None, keep the existing type
        
        # Save updated asset
        save_asset_details(asset)
        
        # Send notification to subscribers
        send_subscription_email(databaseId, assetId)
        
    elif uploadType == "assetPreview" and file_results[0].success:
        # Find the successful preview file
        successful_preview = next((f for f in successful_files if f['relativeKey'] == file_results[0].relativeKey), None)
        if successful_preview:
            # Update asset with preview location
            asset['previewLocation'] = {
                'Key': successful_preview['final_s3_key']
            }
            
            # Save updated asset
            save_asset_details(asset)
    
    # Update upload status in DynamoDB
    try:
        if has_failures:
            # Use both uploadId and assetId as the key
            asset_upload_table.update_item(
                Key={
                    'uploadId': uploadId,
                    'assetId': assetId
                },
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'completed_with_errors'}
            )
        else:
            # Delete upload record if all successful
            delete_upload_details(uploadId, assetId)
    except Exception as e:
        logger.warning(f"Failed to update upload status: {e}")
    
    # Check if all files failed
    all_files_failed = all(not result.success for result in file_results)
    
    # Check if any file has largeFileAsynchronousHandling=true
    has_async_files = any(result.largeFileAsynchronousHandling for result in file_results)
    
    # Create response model
    response = CompleteUploadResponseModel(
        message="External upload completed" + (" with some failures" if has_failures else " successfully"),
        uploadId=uploadId,
        assetId=assetId,
        assetType=asset.get('assetType'),
        fileResults=file_results,
        overallSuccess=not has_failures,
        largeFileAsynchronousHandling=has_async_files
    )
    
    # AUDIT LOG: Upload completed - log all successful files
    if successful_files:
        successful_file_paths = [f['relativeKey'] for f in successful_files]
        log_file_upload(
            event,
            databaseId,
            assetId,
            ", ".join(successful_file_paths),
            False,  # Not denied
            None,
            {
                "uploadId": uploadId,
                "uploadType": uploadType,
                "status": "completed",
                "successfulFiles": len(successful_files),
                "totalFiles": len(request_model.files),
                "hasFailures": has_failures
            }
        )
    
    # Return 409 status if all files failed, otherwise return 200
    if all_files_failed:
        return general_error(status_code=409, body=response.dict(), event=event)
    else:
        return response

def complete_upload(uploadId: str, request_model: CompleteUploadRequestModel, event):
    """Complete a multipart upload and update the asset"""
    assetId = request_model.assetId
    databaseId = request_model.databaseId
    uploadType = request_model.uploadType
    
    # Get upload details from DynamoDB (just for basic validation)
    upload_details = get_upload_details(uploadId, assetId)
    
    # Verify asset exists
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse("Asset not found")
    
    # Verify upload details match request
    if upload_details['assetId'] != assetId or upload_details['databaseId'] != databaseId:
        raise VAMSGeneralErrorResponse("Upload details do not match request")
        
    # Verify upload type matches
    if upload_details['uploadType'] != uploadType:
        raise VAMSGeneralErrorResponse(f"Upload type mismatch.")
    
    # Update upload status in DynamoDB
    try:
        # Use both uploadId and assetId as the key
        asset_upload_table.update_item(
            Key={
                'uploadId': uploadId,
                'assetId': assetId
            },
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'processing'}
        )
    except Exception as e:
        logger.warning(f"Failed to update upload status: {e}")

    # Get bucket details from asset's bucketId
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    bucket_name = bucketDetails['bucketName']
    baseAssetsPrefix = bucketDetails['baseAssetsPrefix']
    
    # Get database details to check file upload restrictions
    database = get_database_details(databaseId)
    if not database:
        raise VAMSGeneralErrorResponse("Database not found")
    
    allowed_extensions = database.get('restrictFileUploadsToExtensions', '')
    
    # Track file completion results
    file_results = []
    successful_files = []
    has_failures = False
    
    # Complete multipart uploads for each file
    for file in request_model.files:
        try:
            # Validate file extension if restrictions are configured
            # Only apply to regular asset files, not asset previews or file preview files
            if allowed_extensions and allowed_extensions.strip() != "" and uploadType == "assetFile":
                # Skip validation for .previewFile. files (they have their own extension restrictions)
                if not is_preview_file(file.relativeKey):
                    is_valid, error_message = validate_file_extension_against_database(
                        file.relativeKey, 
                        allowed_extensions
                    )
                    if not is_valid:
                        # Mark this file as failed
                        file_results.append(FileCompletionResult(
                            relativeKey=file.relativeKey,
                            uploadIdS3=file.uploadIdS3,
                            success=False,
                            error=error_message
                        ))
                        has_failures = True
                        
                        # Abort the multipart upload if it exists
                        if file.uploadIdS3 != "zero-byte":
                            try:
                                # Construct temp key to abort the upload
                                asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")
                                final_s3_key = normalize_s3_path(asset_base_key, file.relativeKey)
                                temp_s3_key = f"{baseAssetsPrefix}{TEMPORARY_UPLOAD_PREFIX}{final_s3_key}"
                                
                                s3.abort_multipart_upload(
                                    Bucket=bucket_name,
                                    Key=temp_s3_key,
                                    UploadId=file.uploadIdS3
                                )
                            except Exception as abort_error:
                                logger.warning(f"Error aborting multipart upload for invalid extension: {abort_error}")
                        continue
            
            # Construct the temporary S3 key directly (same logic as initialization)
            if uploadType == "assetFile":
                # Get the asset's base key from assetLocation
                asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")
                final_s3_key = normalize_s3_path(asset_base_key, file.relativeKey)
            else:  # assetPreview
                #We only want the filename and none of the path if there is a path
                filename = os.path.basename(file.relativeKey)
                final_s3_key = f"{baseAssetsPrefix}{PREVIEW_PREFIX}{assetId}/{filename}"
                
            temp_s3_key = f"{baseAssetsPrefix}{TEMPORARY_UPLOAD_PREFIX}{final_s3_key}"
            
            # Handle zero-byte files (identified by uploadIdS3 = "zero-byte")
            if file.uploadIdS3 == "zero-byte":
                # Create zero-byte file now during completion
                logger.info(f"Creating zero-byte file {file.relativeKey} during completion")
                
                if create_zero_byte_file(bucket_name, temp_s3_key, uploadId, databaseId, assetId):
                    # Create file detail for zero-byte file
                    file_detail = {
                        'relativeKey': file.relativeKey,
                        'temp_s3_key': temp_s3_key,
                        'final_s3_key': final_s3_key,
                        'uploadIdS3': file.uploadIdS3
                    }
                    
                    successful_files.append(file_detail)
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=True,
                        largeFileAsynchronousHandling=False
                    ))
                    continue
                else:
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error="Failed to create zero-byte file"
                    ))
                    has_failures = True
                    continue
            
            # Handle abandoned uploads (no parts provided) - create empty file
            if not file.parts or len(file.parts) == 0:
                logger.info(f"No parts provided for file {file.relativeKey}, creating empty file")
                
                # Abort the existing multipart upload
                try:
                    s3.abort_multipart_upload(
                        Bucket=bucket_name,
                        Key=temp_s3_key,
                        UploadId=file.uploadIdS3
                    )
                except Exception as abort_error:
                    logger.warning(f"Error aborting multipart upload for abandoned file: {abort_error}")
                
                # Create empty file in temporary location
                if create_zero_byte_file(bucket_name, temp_s3_key, uploadId, databaseId, assetId):
                    file_detail = {
                        'relativeKey': file.relativeKey,
                        'temp_s3_key': temp_s3_key,
                        'final_s3_key': final_s3_key,
                        'uploadIdS3': file.uploadIdS3
                    }
                    
                    successful_files.append(file_detail)
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=True,
                        largeFileAsynchronousHandling=False
                    ))
                    continue
                else:
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error="Failed to create empty file for abandoned upload"
                    ))
                    has_failures = True
                    continue
            
            # Regular multipart upload - first check file size before completing
            actual_parts = sorted([p.PartNumber for p in file.parts])
            
            # Check for duplicates in part numbers
            if len(actual_parts) != len(set(actual_parts)):
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error=f"Duplicate part numbers provided"
                ))
                has_failures = True
                continue
            
            # Log the parts we received
            logger.info(f"Received {len(actual_parts)} parts for file {file.relativeKey}: {actual_parts}")
            
            # Calculate total file size by listing parts before completing upload
            total_file_size, size_calc_success, size_calc_error = calculate_total_file_size_from_parts(
                bucket_name, temp_s3_key, file.uploadIdS3, file.parts
            )
            
            if not size_calc_success:
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error=size_calc_error
                ))
                has_failures = True
                continue
                
            logger.info(f"File {file.relativeKey} total size: {total_file_size} bytes")
            
            # Check file size for preview files - both assetPreview type and .previewFile. files
            if (uploadType == "assetPreview" or is_preview_file(file.relativeKey)) and total_file_size > MAX_PREVIEW_FILE_SIZE:
                # Abort the multipart upload since it exceeds the size limit
                try:
                    s3.abort_multipart_upload(
                        Bucket=bucket_name,
                        Key=temp_s3_key,
                        UploadId=file.uploadIdS3
                    )
                except Exception as abort_error:
                    logger.warning(f"Error aborting multipart upload for oversized preview file: {abort_error}")
                
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error=f"Preview file exceeds maximum allowed size of 5MB"
                ))
                has_failures = True
                continue
            
            # Check if file should be processed asynchronously based on size
            process_async = should_process_asynchronously(total_file_size)
            
            if process_async:
                logger.info(f"File {file.relativeKey} ({total_file_size} bytes) will be processed asynchronously")
                
                # Create file info for SQS message - DO NOT complete the multipart upload yet
                file_info = {
                    "relativeKey": file.relativeKey,
                    "uploadIdS3": file.uploadIdS3,
                    "parts": [{"PartNumber": p.PartNumber, "ETag": p.ETag} for p in file.parts],
                    "tempS3Key": temp_s3_key,
                    "finalS3Key": final_s3_key,
                    "bucketName": bucket_name,
                    "databaseId": databaseId,
                    "assetId": assetId,
                    "uploadId": uploadId,
                    "uploadType": uploadType,
                    "totalFileSize": total_file_size
                }
                
                # Try to queue the file for asynchronous processing
                if queue_large_file_for_processing(file_info, large_file_processing_queue_url):
                    # Successfully queued - mark as successful with async flag
                    # DO NOT add to successful_files since we haven't completed the upload yet
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=True,
                        largeFileAsynchronousHandling=True
                    ))
                    logger.info(f"Large file {file.relativeKey} queued for asynchronous processing")
                    continue
                else:
                    # Failed to queue - fall back to synchronous processing
                    logger.warning(f"Failed to queue large file {file.relativeKey}, falling back to synchronous processing")
                    process_async = False
            
            # Complete multipart upload synchronously (for small files or fallback)
            try:
                s3.complete_multipart_upload(
                    Bucket=bucket_name,
                    Key=temp_s3_key,
                    UploadId=file.uploadIdS3,
                    MultipartUpload={'Parts': [{'PartNumber': p.PartNumber, 'ETag': p.ETag} for p in file.parts]}
                )
            except Exception as e:
                logger.exception(f"Error completing multipart upload for {file.relativeKey}: {e}")
                
                # Abort the multipart upload to clean up S3 resources
                try:
                    s3.abort_multipart_upload(
                        Bucket=bucket_name,
                        Key=temp_s3_key,
                        UploadId=file.uploadIdS3
                    )
                except Exception as abort_error:
                    logger.exception(f"Error aborting multipart upload: {abort_error}")
                
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error=f"Error completing multipart upload."
                ))
                has_failures = True
                continue
            
            # Now verify the metadata of the completed object
            try:
                head_response = s3.head_object(
                    Bucket=bucket_name,
                    Key=temp_s3_key
                )
                
                # Extract metadata
                metadata = head_response.get('Metadata', {})
                s3_upload_id = metadata.get('uploadid')
                
                # Get file size with error handling
                file_size = 0
                try:
                    file_size = head_response.get('ContentLength', 0)
                    if file_size is None:
                        logger.warning(f"ContentLength is None for file {file.relativeKey}, defaulting to 0")
                        file_size = 0
                except Exception as size_error:
                    logger.warning(f"Error getting file size for {file.relativeKey}: {size_error}")
                    # Default to 0 for unknown file sizes (will process synchronously)
                    file_size = 0
                
                # Verify the uploadId matches
                if s3_upload_id != uploadId:
                    # Delete the uploaded file since metadata doesn't match
                    delete_s3_object(bucket_name, temp_s3_key)
                    
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error=f"Upload ID mismatch."
                    ))
                    has_failures = True
                    continue
                
                # Check file size for preview files - both assetPreview type and .previewFile. files
                if (uploadType == "assetPreview" or is_preview_file(file.relativeKey)) and file_size > MAX_PREVIEW_FILE_SIZE:
                    # Delete the uploaded file since it exceeds the size limit
                    delete_s3_object(bucket_name, temp_s3_key)
                    
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error=f"Preview file exceeds maximum allowed size of 5MB"
                    ))
                    has_failures = True
                    continue
                
                # At this point, we've already completed the multipart upload synchronously
                # (large files would have been queued earlier and continued)
                
            except Exception as e:
                # Delete the uploaded file since we couldn't verify metadata
                delete_s3_object(bucket_name, temp_s3_key)
                
                logger.exception(f"Error verifying file metadata: {e}")
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error=f"Error verifying file metadata."
                ))
                has_failures = True
                continue
            
            # Create a file_detail dictionary with the information we need
            file_detail = {
                'relativeKey': file.relativeKey,
                'temp_s3_key': temp_s3_key,
                'final_s3_key': final_s3_key,
                'uploadIdS3': file.uploadIdS3
            }
            
            # Validate file content type
            if not validateS3AssetExtensionsAndContentType(bucket_name, temp_s3_key):
                # Delete the uploaded file
                delete_s3_object(bucket_name, temp_s3_key)
                
                # AUDIT LOG: Malicious file detected and upload denied
                log_file_upload(
                    event,
                    databaseId,
                    assetId,
                    file.relativeKey,
                    True,  # Upload denied
                    "File contains potentially malicious executable type",
                    {
                        "uploadId": uploadId,
                        "uploadType": uploadType
                    }
                )
                
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error="File contains a potentially malicious executable type object"
                ))
                has_failures = True
                continue
            
            # Additional validation for preview files
            if uploadType == "assetPreview":
                # Validate preview file extension
                if not validate_preview_file_extension(file.relativeKey):
                    # Delete the uploaded file
                    delete_s3_object(bucket_name, temp_s3_key)
                    
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error=f"Preview file must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif"
                    ))
                    has_failures = True
                    continue
            
            # Check if this is a preview file in an assetFile upload
            if uploadType == "assetFile" and is_preview_file(file.relativeKey):
                # Validate preview file extension
                if not validate_preview_file_extension(file.relativeKey):
                    # Delete the uploaded file
                    delete_s3_object(bucket_name, temp_s3_key)
                    
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error=f"Preview file must have one of the allowed extensions: .png, .jpg, .jpeg, .svg, .gif"
                    ))
                    has_failures = True
                    continue
                
                # Get the base file path
                base_file_path = get_base_file_path(file.relativeKey)
                base_file_key = normalize_s3_path(asset_base_key, base_file_path)
                
                # Check if the base file exists in the current request
                base_file_in_request = False
                for other_file in request_model.files:
                    if other_file.relativeKey == base_file_path:
                        base_file_in_request = True
                        break
                
                # If not in the current request, check if it exists in S3
                if not base_file_in_request:
                    try:
                        s3.head_object(Bucket=bucket_name, Key=base_file_key)
                    except ClientError as e:
                        if e.response['Error']['Code'] == 'NoSuchKey':
                            # Base file doesn't exist in S3 or in the current request
                            # Delete the uploaded file
                            delete_s3_object(bucket_name, temp_s3_key)
                            
                            file_results.append(FileCompletionResult(
                                relativeKey=file.relativeKey,
                                uploadIdS3=file.uploadIdS3,
                                success=False,
                                error=f"Base files does not exist for all preview files"
                            ))
                            logger.warning(f"Preview file {file.relativeKey} is missing its base file {base_file_path}")
                            has_failures = True
                            continue
                        else:
                            # Other error occurred, log and conservatively reject the file
                            logger.warning(f"Error checking if base file {base_file_key} exists: {e}")
                            delete_s3_object(bucket_name, temp_s3_key)
                            
                            file_results.append(FileCompletionResult(
                                relativeKey=file.relativeKey,
                                uploadIdS3=file.uploadIdS3,
                                success=False,
                                error=f"Error verifying base file for preview file"
                            ))
                            has_failures = True
                            continue
            
            # Add to successful files list
            successful_files.append(file_detail)
            file_results.append(FileCompletionResult(
                relativeKey=file.relativeKey,
                uploadIdS3=file.uploadIdS3,
                success=True,
                largeFileAsynchronousHandling=False
            ))
            
        except Exception as e:
            logger.exception(f"Error completing multipart upload for {file.relativeKey}: {e}")
            
            # Abort the multipart upload to clean up S3 resources
            try:
                s3.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=temp_s3_key,  # Use temp_s3_key directly since it's defined in the outer try block
                    UploadId=file.uploadIdS3
                )
            except Exception as abort_error:
                logger.exception(f"Error aborting multipart upload: {abort_error}")
            
            file_results.append(FileCompletionResult(
                relativeKey=file.relativeKey,
                uploadIdS3=file.uploadIdS3,
                success=False,
                error=str(e)
            ))
            has_failures = True
    
    # If no files were successfully uploaded, return error
    if not successful_files:
        delete_upload_details(uploadId, assetId)
        # Check if any file has largeFileAsynchronousHandling=true
        has_async_files = any(result.largeFileAsynchronousHandling for result in file_results)
        return CompleteUploadResponseModel(
            message="No files were successfully uploaded",
            uploadId=uploadId,
            assetId=assetId,
            fileResults=file_results,
            overallSuccess=False,
            largeFileAsynchronousHandling=has_async_files
        )
    
    # Get the asset's specified bucket and key location
    asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")
    
    # Only for assetFile uploads, validate that .previewFile. files have corresponding base files
    if uploadType == "assetFile":
        # Check if there are any .previewFile. files in the successful files
        preview_files = [file_detail for file_detail in successful_files if is_preview_file(file_detail['relativeKey'])]
        
        if preview_files:
            # Create a list of file details for validation
            files_for_validation = [{'relativeKey': file_detail['relativeKey']} for file_detail in successful_files]
            
            # Validate preview files have base files
            is_valid, error_message, invalid_files = validate_preview_files_with_base_files(
                files_for_validation, 
                asset_base_key, 
                bucket_name
            )
            
            if not is_valid:
                # Mark invalid files as failed
                for file_detail in successful_files[:]:
                    if file_detail['relativeKey'] in invalid_files:
                        # Delete the uploaded file
                        delete_s3_object(bucket_name, file_detail['temp_s3_key'])
                        
                        # Update file result
                        for result in file_results:
                            if result.relativeKey == file_detail['relativeKey'] and result.success:
                                result.success = False
                                result.error = f"Base files does not exist for all preview files"
                                has_failures = True
                        
                        # Remove from successful files
                        successful_files.remove(file_detail)
                
                # If no files remain successful, return error
                if not successful_files:
                    delete_upload_details(uploadId, assetId)
                    # Check if any file has largeFileAsynchronousHandling=true
                    has_async_files = any(result.largeFileAsynchronousHandling for result in file_results)
                    return CompleteUploadResponseModel(
                        message="No files were successfully uploaded",
                        uploadId=uploadId,
                        assetId=assetId,
                        fileResults=file_results,
                        overallSuccess=False,
                        largeFileAsynchronousHandling=has_async_files
                    )
    
    # Copy successful files from temporary to final location
    for file_detail in successful_files:
        
        logger.info(f"Copying file from {file_detail['temp_s3_key']} to {file_detail['final_s3_key']}")
        
        copy_success = copy_s3_object(
            bucket_name, 
            file_detail['temp_s3_key'], 
            bucket_name, 
            file_detail['final_s3_key'],
            databaseId,
            assetId
        )
        
        if not copy_success:
            logger.error(f"Failed to copy file from {file_detail['temp_s3_key']} to {file_detail['final_s3_key']}")
            # Update the file result to indicate copy failure
            for result in file_results:
                if result.relativeKey == file_detail['relativeKey'] and result.success:
                    result.success = False
                    result.error = "Failed to copy file to final location"
                    has_failures = True
        else:
            # Delete temporary file after successful copy
            delete_s3_object(bucket_name, file_detail['temp_s3_key'])
    
    # Update asset record based on upload type
    if uploadType == "assetFile" and any(f.success for f in file_results):
        # Determine asset type using the asset's bucket and key location
        assetType = determine_asset_type(assetId, bucket_name, asset_base_key)
        logger.info(f"Asset type determined for asset {assetId}: {assetType}")
        
        
        # Update asset type - ensure we're not overriding with None
        if assetType:
            asset['assetType'] = assetType
        elif 'assetType' not in asset or not asset.get('assetType'):
            asset['assetType'] = 'none'
        # If asset already has a type and assetType is None, keep the existing type
        
        # Save updated asset
        save_asset_details(asset)
        
        # Send notification to subscribers
        send_subscription_email(databaseId, assetId)
        
    elif uploadType == "assetPreview" and file_results[0].success:
        # Find the successful preview file
        successful_preview = next((f for f in successful_files if f['relativeKey'] == file_results[0].relativeKey), None)
        if successful_preview:
            # Update asset with preview location
            asset['previewLocation'] = {
                'Key': successful_preview['final_s3_key']
            }
            
            # Save updated asset
            save_asset_details(asset)
    
    # Update upload status in DynamoDB
    try:
        if has_failures:
            # Use both uploadId and assetId as the key
            asset_upload_table.update_item(
                Key={
                    'uploadId': uploadId,
                    'assetId': assetId
                },
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'completed_with_errors'}
            )
        else:
            # Delete upload record if all successful
            delete_upload_details(uploadId, assetId)
    except Exception as e:
        logger.warning(f"Failed to update upload status: {e}")
    
    # Check if all files failed
    all_files_failed = all(not result.success for result in file_results)
    
    # Check if any file has largeFileAsynchronousHandling=true
    has_async_files = any(result.largeFileAsynchronousHandling for result in file_results)
    
    # Create response model
    response = CompleteUploadResponseModel(
        message="Upload completed" + (" with some failures" if has_failures else " successfully"),
        uploadId=uploadId,
        assetId=assetId,
        assetType=asset.get('assetType'),
        fileResults=file_results,
        overallSuccess=not has_failures,
        largeFileAsynchronousHandling=has_async_files
    )
    
    # AUDIT LOG: Upload completed - log all successful files
    if successful_files:
        successful_file_paths = [f['relativeKey'] for f in successful_files]
        log_file_upload(
            event,
            databaseId,
            assetId,
            ", ".join(successful_file_paths),
            False,  # Not denied
            None,
            {
                "uploadId": uploadId,
                "uploadType": uploadType,
                "status": "completed",
                "successfulFiles": len(successful_files),
                "totalFiles": len(request_model.files),
                "hasFailures": has_failures
            }
        )
    
    # Return 409 status if all files failed, otherwise return 200
    if all_files_failed:
        return general_error(status_code=409, body=response.dict(), event=event)
    else:
        return response

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for file upload APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Get API path and method
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Determine which API to call based on path and method
        if method == 'POST' and path == '/uploads':
            # Initialize Upload API
            request_model = parse(body, model=InitializeUploadRequestModel)
            
            # Check authorization
            asset = get_asset_details(request_model.databaseId, request_model.assetId)
            if not asset:
                return validation_error(body={'message': "Asset not found"}, event=event)
            
            asset["object__type"] = "asset"
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(asset, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = initialize_upload(request_model, claims_and_roles)
            
            # AUDIT LOG: Upload initialized
            log_file_upload(
                event,
                request_model.databaseId,
                request_model.assetId,
                f"{len(request_model.files)} files",
                False,  # Not denied
                None,
                {
                    "uploadId": response.uploadId,
                    "uploadType": request_model.uploadType,
                    "status": "initialized",
                    "fileCount": len(request_model.files)
                }
            )
            
            return success(body=response.dict())
            
        elif method == 'POST' and '/uploads/' in path and path.endswith('/complete/external'):
            # External Complete Upload API - Extract uploadId from path parameters
            if not event.get('pathParameters') or not event['pathParameters'].get('uploadId'):
                return validation_error(body={'message': "Missing uploadId in path parameters"}, event=event)
                
            uploadId = event['pathParameters']['uploadId']
            
            # Parse request model
            request_model = parse(body, model=CompleteExternalUploadRequestModel)
            
            # Check authorization
            asset = get_asset_details(request_model.databaseId, request_model.assetId)
            if not asset:
                return validation_error(body={'message': "Asset not found"}, event=event)
            
            asset["object__type"] = "asset"
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(asset, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = complete_external_upload(uploadId, request_model, event)
            # Check if response is already an APIGatewayProxyResponseV2 (error case)
            if isinstance(response, dict) and 'statusCode' in response and 'body' in response:
                return response
            else:
                return success(body=response.dict())
            
        elif method == 'POST' and '/uploads/' in path and path.endswith('/complete'):
            # Complete Upload API - Extract uploadId from path parameters
            if not event.get('pathParameters') or not event['pathParameters'].get('uploadId'):
                return validation_error(body={'message': "Missing uploadId in path parameters"}, event=event)
                
            uploadId = event['pathParameters']['uploadId']
            
            # Parse request model
            request_model = parse(body, model=CompleteUploadRequestModel)
            
            # Check authorization
            asset = get_asset_details(request_model.databaseId, request_model.assetId)
            if not asset:
                return validation_error(body={'message': "Asset not found"}, event=event)
            
            asset["object__type"] = "asset"
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(asset, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = complete_upload(uploadId, request_model, event)
            # Check if response is already an APIGatewayProxyResponseV2 (error case)
            if isinstance(response, dict) and 'statusCode' in response and 'body' in response:
                return response
            else:
                return success(body=response.dict())
            
        else:
            return validation_error(body={'message': "Invalid API path or method"}, event=event)
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        
        # Check if this is a rate limit error
        if "Rate limit exceeded" in str(v):
            return {
                'statusCode': 429,
                'headers': {
                    'Content-Type': 'application/json',
                    'Cache-Control': 'no-cache, no-store',
                    'Retry-After': '60'  # Suggest retry after 60 seconds
                },
                'body': json.dumps({'message': str(v)})
            }
        else:
            return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)