# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
import time
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
from common.s3 import validateS3AssetExtensionsAndContentType, validateUnallowedFileExtensionAndContentType
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetsV3 import (
    InitializeUploadRequestModel, InitializeUploadResponseModel, UploadPartModel, UploadFileResponseModel,
    CompleteUploadRequestModel, CompleteUploadResponseModel, FileCompletionResult,
    CompleteExternalUploadRequestModel, ExternalFileModel,
    AssetUploadTableModel, CreateFolderRequestModel, CreateFolderResponseModel
)

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
logger = safeLogger(service_name="FileIngestion")

# Constants
UPLOAD_EXPIRATION_DAYS = 7  # TTL for upload records and S3 multipart uploads
TEMPORARY_UPLOAD_PREFIX = 'temp-uploads/'  # Prefix for temporary uploads
PREVIEW_PREFIX = 'previews/'
MAX_PART_SIZE = 150 * 1024 * 1024  # 150MB per part
MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024  # 5MB maximum size for preview files

# Load environment variables
try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_upload_table_name = os.environ["ASSET_UPLOAD_TABLE_NAME"]
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
    token_timeout = os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]
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
        return num_parts
    elif file_size is not None:
        return -(-file_size // max_part_size)  # Ceiling division
    else:
        raise ValueError("Either file_size or num_parts must be provided")

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
            raise VAMSGeneralErrorResponse(f"Error getting database default bucket details: {str(e)}")
        
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
        raise VAMSGeneralErrorResponse(f"Error getting bucket details: {str(e)}")

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
        raise VAMSGeneralErrorResponse(f"Error retrieving asset: {str(e)}")

def save_asset_details(asset_data):
    """Save asset details to DynamoDB"""
    try:
        asset_table.put_item(Item=asset_data)
    except Exception as e:
        logger.exception(f"Error saving asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error saving asset: {str(e)}")

def save_upload_details(upload_data):
    """Save upload details to DynamoDB"""
    try:
        asset_upload_table.put_item(Item=upload_data.to_dict())
    except Exception as e:
        logger.exception(f"Error saving upload details: {e}")
        raise VAMSGeneralErrorResponse(f"Error saving upload details: {str(e)}")

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
        raise VAMSGeneralErrorResponse(f"Error retrieving upload details: {str(e)}")

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

def is_file_archived(metadata):
    """Determine if file is archived based on S3 metadata
    
    Args:
        metadata: The S3 object metadata
        
    Returns:
        True if file is archived, False otherwise
    """
    vams_status = metadata.get('Metadata', {}).get('vams-status', '')
    storage_class = metadata.get('StorageClass', 'STANDARD')
    
    # File is archived if:
    # 1. Has vams-status=archived or deleted metadata, OR
    # 2. Storage class is GLACIER/DEEP_ARCHIVE
    return (vams_status in ['archived', 'deleted'] or 
            storage_class in ['GLACIER', 'DEEP_ARCHIVE'])

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
        for item in contents:
            if item['Key'].endswith('/'):
                # Skip folder markers
                continue
                
            try:
                # Get object metadata to check if archived
                head_response = s3.head_object(
                    Bucket=bucket,
                    Key=item['Key']
                )
                
                # Only include non-archived files
                if not is_file_archived(head_response):
                    non_archived_files.append(item)
            except Exception as e:
                logger.warning(f"Error checking if file {item['Key']} is archived: {e}")
                # If we can't check archive status, include the file by default
                non_archived_files.append(item)
        
        # Count the actual files (excluding folder markers and archived files)
        file_count = len(non_archived_files)
        logger.info(f"Found {file_count} non-archived files in {bucket}/{prefix} (total objects: {len(contents)})")
        
        # Determine asset type
        if file_count == 0:
            logger.info("No non-archived files found, returning None")
            return None  # No files found
        elif file_count == 1:
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
        else:
            logger.info(f"Determined asset type as folder (multiple files: {file_count})")
            return 'folder'
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

def copy_s3_object(source_bucket, source_key, dest_bucket, dest_key):
    """Copy an object from one S3 location to another"""
    try:
        # Use s3_resource for managed transfer to handle large files
        s3_resource.meta.client.copy(
            CopySource={'Bucket': source_bucket, 'Key': source_key},
            Bucket=dest_bucket,
            Key=dest_key
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

def create_folder(databaseId: str, assetId: str, request_model: CreateFolderRequestModel, claims_and_roles):
    """Create a folder in S3 for the specified asset"""
    # Verify asset exists
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
    
    # Get bucket details from asset's bucketId
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    asset_bucket = bucketDetails['bucketName']
    baseAssetsPrefix = bucketDetails['baseAssetsPrefix']
    
    # Get the asset's base location
    asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")

    # Normalize the path by combining asset base key with the relative folder path
    normalized_key_path = normalize_s3_path(asset_base_key, request_model.relativeKey)
    
    # Create the folder in S3 (in S3, folders are represented by zero-byte objects with a trailing slash)
    try:
        s3.put_object(
            Bucket=asset_bucket,
            Key=normalized_key_path,
            Body=''
        )
        
        logger.info(f"Created folder {normalized_key_path} in bucket {asset_bucket}")
        
        return CreateFolderResponseModel(
            message=f"Folder created successfully",
            relativeKey=request_model.relativeKey
        )
    except Exception as e:
        logger.exception(f"Error creating folder: {e}")
        raise VAMSGeneralErrorResponse(f"Error creating folder: {str(e)}")

#######################
# API Implementations
#######################

def initialize_upload(request_model: InitializeUploadRequestModel, claims_and_roles):
    """Initialize a multipart upload for asset files or preview"""
    assetId = request_model.assetId
    databaseId = request_model.databaseId
    uploadType = request_model.uploadType
    
    # Verify asset exists
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
        
    # Additional business logic validation
    if uploadType == "assetPreview" and asset.get('previewLocation'):
        logger.info(f"Asset {assetId} already has a preview. The existing preview will be replaced.")
        
    # Validate file extensions before proceeding
    for file in request_model.files:
        if not validateUnallowedFileExtensionAndContentType(file.relativeKey, ""):
            raise ValidationError(f"File {file.relativeKey} has an unsupported file extension")
    
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
            raise ValidationError(f"File {file.relativeKey} has an unsupported file extension")
        
        # Validate file size for preview files
        if uploadType == "assetPreview" and file.file_size > MAX_PREVIEW_FILE_SIZE:
            raise ValidationError(f"Preview file {file.relativeKey} exceeds maximum allowed size of 5MB")
        
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
        status="initialized"
    )
    save_upload_details(upload_record)
    
    # Return response
    return InitializeUploadResponseModel(
        uploadId=uploadId,
        files=file_responses,
        message="Upload initialized successfully"
    )

def complete_external_upload(uploadId: str, request_model: CompleteExternalUploadRequestModel, claims_and_roles):
    """Complete an external upload and update the asset"""
    assetId = request_model.assetId
    databaseId = request_model.databaseId
    uploadType = request_model.uploadType
    
    # Get upload details from DynamoDB
    upload_details = get_upload_details(uploadId, assetId)
    
    # Verify asset exists
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
    
    # Verify upload details match request
    if upload_details['assetId'] != assetId or upload_details['databaseId'] != databaseId:
        raise VAMSGeneralErrorResponse("Upload details do not match request")
        
    # Verify upload type matches
    if upload_details['uploadType'] != uploadType:
        raise VAMSGeneralErrorResponse(f"Upload type mismatch. Expected {upload_details['uploadType']}, got {uploadType}")
    
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
    
    # Track file completion results
    file_results = []
    successful_files = []
    has_failures = False
    
    # Process each file in the request
    for file in request_model.files:
        try:
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
                
                # Check file size for preview files
                if uploadType == "assetPreview" and head_response.get('ContentLength', 0) > MAX_PREVIEW_FILE_SIZE:
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3="external",
                        success=False,
                        error=f"Preview file {file.relativeKey} exceeds maximum allowed size of 5MB"
                    ))
                    has_failures = True
                    continue
            except Exception as e:
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3="external",
                    success=False,
                    error=f"File not found in S3: {str(e)}"
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
                success=True
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
        return CompleteUploadResponseModel(
            message="No files were successfully uploaded",
            uploadId=uploadId,
            assetId=assetId,
            fileResults=file_results,
            overallSuccess=False
        )
    
    # Copy successful files from temporary to final location
    for file_detail in successful_files:
        
        logger.info(f"Copying file from {file_detail['temp_s3_key']} to {file_detail['final_s3_key']}")
        
        copy_success = copy_s3_object(
            bucket_name, 
            file_detail['temp_s3_key'], 
            bucket_name, 
            file_detail['final_s3_key']
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
    
    # Create response model
    response = CompleteUploadResponseModel(
        message="External upload completed" + (" with some failures" if has_failures else " successfully"),
        uploadId=uploadId,
        assetId=assetId,
        assetType=asset.get('assetType'),
        fileResults=file_results,
        overallSuccess=not has_failures
    )
    
    # Return 409 status if all files failed, otherwise return 200
    if all_files_failed:
        return general_error(status_code=409, body=response.dict())
    else:
        return response

def complete_upload(uploadId: str, request_model: CompleteUploadRequestModel, claims_and_roles):
    """Complete a multipart upload and update the asset"""
    assetId = request_model.assetId
    databaseId = request_model.databaseId
    uploadType = request_model.uploadType
    
    # Get upload details from DynamoDB (just for basic validation)
    upload_details = get_upload_details(uploadId, assetId)
    
    # Verify asset exists
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
    
    # Verify upload details match request
    if upload_details['assetId'] != assetId or upload_details['databaseId'] != databaseId:
        raise VAMSGeneralErrorResponse("Upload details do not match request")
        
    # Verify upload type matches
    if upload_details['uploadType'] != uploadType:
        raise VAMSGeneralErrorResponse(f"Upload type mismatch. Expected {upload_details['uploadType']}, got {uploadType}")
    
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
    
    # Track file completion results
    file_results = []
    successful_files = []
    has_failures = False
    
    # Complete multipart uploads for each file
    for file in request_model.files:
        try:
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
            
            # Get the number of parts from the request
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
            
            # Complete multipart upload in temporary location
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
                    error=f"Error completing multipart upload: {str(e)}"
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
                
                # Verify the uploadId matches
                if s3_upload_id != uploadId:
                    # Delete the uploaded file since metadata doesn't match
                    delete_s3_object(bucket_name, temp_s3_key)
                    
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error=f"Upload ID mismatch. Expected {uploadId}, got {s3_upload_id}"
                    ))
                    has_failures = True
                    continue
                
                # Check file size for preview files
                if uploadType == "assetPreview" and head_response.get('ContentLength', 0) > MAX_PREVIEW_FILE_SIZE:
                    # Delete the uploaded file since it exceeds the size limit
                    delete_s3_object(bucket_name, temp_s3_key)
                    
                    file_results.append(FileCompletionResult(
                        relativeKey=file.relativeKey,
                        uploadIdS3=file.uploadIdS3,
                        success=False,
                        error=f"Preview file {file.relativeKey} exceeds maximum allowed size of 5MB"
                    ))
                    has_failures = True
                    continue
                
            except Exception as e:
                # Delete the uploaded file since we couldn't verify metadata
                delete_s3_object(bucket_name, temp_s3_key)
                
                logger.exception(f"Error verifying file metadata: {e}")
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error=f"Error verifying file metadata: {str(e)}"
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
                
                file_results.append(FileCompletionResult(
                    relativeKey=file.relativeKey,
                    uploadIdS3=file.uploadIdS3,
                    success=False,
                    error="File contains a potentially malicious executable type object"
                ))
                has_failures = True
                continue
            
            # Add to successful files list
            successful_files.append(file_detail)
            file_results.append(FileCompletionResult(
                relativeKey=file.relativeKey,
                uploadIdS3=file.uploadIdS3,
                success=True
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
        return CompleteUploadResponseModel(
            message="No files were successfully uploaded",
            uploadId=uploadId,
            assetId=assetId,
            fileResults=file_results,
            overallSuccess=False
        )
    
    # Get the asset's specified bucket and key location
    asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")
    
    # Copy successful files from temporary to final location
    for file_detail in successful_files:
        
        logger.info(f"Copying file from {file_detail['temp_s3_key']} to {file_detail['final_s3_key']}")
        
        copy_success = copy_s3_object(
            bucket_name, 
            file_detail['temp_s3_key'], 
            bucket_name, 
            file_detail['final_s3_key']
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
    
    # Create response model
    response = CompleteUploadResponseModel(
        message="Upload completed" + (" with some failures" if has_failures else " successfully"),
        uploadId=uploadId,
        assetId=assetId,
        assetType=asset.get('assetType'),
        fileResults=file_results,
        overallSuccess=not has_failures
    )
    
    # Return 409 status if all files failed, otherwise return 200
    if all_files_failed:
        return general_error(status_code=409, body=response.dict())
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
        # Parse request body
        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])
        
        # Get API path and method
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Determine which API to call based on path and method
        if method == 'POST' and path == '/uploads':
            # Initialize Upload API
            request_model = parse(event['body'], model=InitializeUploadRequestModel)
            
            # Check authorization
            asset = get_asset_details(request_model.databaseId, request_model.assetId)
            if not asset:
                return validation_error(body={'message': f"Asset {request_model.assetId} not found"})
            
            asset["object__type"] = "asset"
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(asset, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = initialize_upload(request_model, claims_and_roles)
            return success(body=response.dict())
            
        elif method == 'POST' and '/uploads/' in path and path.endswith('/complete/external'):
            # External Complete Upload API - Extract uploadId from path parameters
            if not event.get('pathParameters') or not event['pathParameters'].get('uploadId'):
                return validation_error(body={'message': "Missing uploadId in path parameters"})
                
            uploadId = event['pathParameters']['uploadId']
            
            # Parse request model
            request_model = parse(event['body'], model=CompleteExternalUploadRequestModel)
            
            # Check authorization
            asset = get_asset_details(request_model.databaseId, request_model.assetId)
            if not asset:
                return validation_error(body={'message': f"Asset {request_model.assetId} not found"})
            
            asset["object__type"] = "asset"
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(asset, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = complete_external_upload(uploadId, request_model, claims_and_roles)
            # Check if response is already an APIGatewayProxyResponseV2 (error case)
            if isinstance(response, dict) and 'statusCode' in response and 'body' in response:
                return response
            else:
                return success(body=response.dict())
            
        elif method == 'POST' and '/uploads/' in path and path.endswith('/complete'):
            # Complete Upload API - Extract uploadId from path parameters
            if not event.get('pathParameters') or not event['pathParameters'].get('uploadId'):
                return validation_error(body={'message': "Missing uploadId in path parameters"})
                
            uploadId = event['pathParameters']['uploadId']
            
            # Parse request model
            request_model = parse(event['body'], model=CompleteUploadRequestModel)
            
            # Check authorization
            asset = get_asset_details(request_model.databaseId, request_model.assetId)
            if not asset:
                return validation_error(body={'message': f"Asset {request_model.assetId} not found"})
            
            asset["object__type"] = "asset"
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(asset, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = complete_upload(uploadId, request_model, claims_and_roles)
            # Check if response is already an APIGatewayProxyResponseV2 (error case)
            if isinstance(response, dict) and 'statusCode' in response and 'body' in response:
                return response
            else:
                return success(body=response.dict())
            
        elif method == 'POST' and '/assets/' in path and path.endswith('/createFolder'):
            # Create Folder API - Extract databaseId and assetId from path parameters
            if not event.get('pathParameters') or not event['pathParameters'].get('databaseId') or not event['pathParameters'].get('assetId'):
                return validation_error(body={'message': "Missing databaseId or assetId in path parameters"})
                
            databaseId = event['pathParameters']['databaseId']
            assetId = event['pathParameters']['assetId']
            
            # Parse request model
            request_model = parse(event['body'], model=CreateFolderRequestModel)
            
            # Check authorization
            asset = get_asset_details(databaseId, assetId)
            if not asset:
                return validation_error(body={'message': f"Asset {assetId} not found"})
            
            asset["object__type"] = "asset"
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(asset, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = create_folder(databaseId, assetId, request_model, claims_and_roles)
            return success(body=response.dict())
            
        else:
            return validation_error(body={'message': "Invalid API path or method"})
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
