# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetsV3 import (
    AssetFileVersionItemModel, CreateAssetVersionRequestModel, RevertAssetVersionRequestModel,
    GetAssetVersionRequestModel, GetAssetVersionsRequestModel, AssetVersionFileModel,
    AssetVersionResponseModel, AssetVersionsListResponseModel, AssetVersionOperationResponseModel,
    AssetVersionListItemModel
)

retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

# Configure AWS clients
region = os.environ.get('AWS_REGION', 'us-east-1')
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
s3_resource = boto3.resource('s3', config=retry_config)
lambda_client = boto3.client('lambda', config=retry_config)
logger = safeLogger(service_name="AssetVersions")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables
try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_file_versions_table_name = os.environ["ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME"]
    asset_versions_table_name = os.environ.get("ASSET_VERSIONS_STORAGE_TABLE_NAME")
    bucket_name_default = os.environ["S3_ASSET_STORAGE_BUCKET"]
    asset_aux_bucket_name = os.environ["S3_ASSET_AUXILIARY_BUCKET"]
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_table = dynamodb.Table(asset_database)
asset_file_versions_table = dynamodb.Table(asset_file_versions_table_name)
asset_versions_table = dynamodb.Table(asset_versions_table_name)

#######################
# Utility Functions
#######################

def send_subscription_email(asset_id):
    """Send email notifications to subscribers when an asset is updated"""
    try:
        payload = {
            'asset_id': asset_id,
        }
        lambda_client.invoke(
            FunctionName=send_email_function_name,
            InvocationType='Event',
            Payload=json.dumps(payload)
        )
    except Exception as e:
        logger.exception(f"Error invoking send_email Lambda function: {e}")

def get_asset_with_permissions(databaseId: str, assetId: str, operation: str, claims_and_roles: Dict) -> Dict:
    """Get asset and verify permissions for the specified operation
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        operation: The operation to check permissions for (GET, POST, PUT, DELETE)
        claims_and_roles: The claims and roles from the request
        
    Returns:
        The asset if found and user has permissions, otherwise raises an exception
        
    Raises:
        VAMSGeneralErrorResponse: If asset not found or user doesn't have permissions
    """
    try:
        # Get the asset from DynamoDB
        response = asset_table.get_item(Key={'databaseId': databaseId, 'assetId': assetId})
        asset = response.get('Item', {})
        
        if not asset:
            raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
        
        # Check permissions
        asset["object__type"] = "asset"
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(asset, operation):
                raise VAMSGeneralErrorResponse("Not authorized to perform this operation on the asset")
        
        return asset
    except Exception as e:
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        logger.exception(f"Error getting asset with permissions: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving asset: {str(e)}")

def get_asset_s3_location(asset: Dict) -> Tuple[str, str]:
    """Extract bucket and key from asset location
    
    Args:
        asset: The asset dictionary
        
    Returns:
        Tuple of (bucket, key)
        
    Raises:
        VAMSGeneralErrorResponse: If asset location is missing
    """
    asset_location = asset.get('assetLocation', {})
    
    if not asset_location:
        raise VAMSGeneralErrorResponse("Asset location not found")
    
    bucket = asset_location.get('Bucket', bucket_name_default)
    key = asset_location.get('Key')
    
    if not key:
        raise VAMSGeneralErrorResponse("Asset key not found in asset location")
    
    return bucket, key

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
            # For specific version, try head_object with version_id
            # If it's a delete marker, head_object will return 405 Method Not Allowed
            try:
                s3_client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
                return False  # Version exists and is not a delete marker
            except ClientError as e:
                if e.response['Error']['Code'] == 'MethodNotAllowed':
                    # This version is a delete marker
                    return True
                elif e.response['Error']['Code'] == 'NoSuchKey':
                    # Version doesn't exist
                    return False
                else:
                    raise
        else:
            # Check if current version is a delete marker
            try:
                response = s3_client.head_object(Bucket=bucket, Key=key)
                # If head_object succeeds, check if it's a delete marker
                return response.get('DeleteMarker', False)
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    # Object doesn't exist at all (no versions)
                    return False
                else:
                    raise
    except Exception as e:
        logger.warning(f"Error checking archive status for {key}: {e}")
        return False

def does_file_version_exist(bucket: str, key: str, version_id: str) -> bool:
    """Check if a specific file version still exists (wasn't permanently deleted)
    
    Args:
        bucket: The S3 bucket name
        key: The S3 object key
        version_id: The specific version ID to check
        
    Returns:
        True if the file version exists, False if it was permanently deleted
    """
    try:
        # Try to get the object with the specific version ID
        s3_client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
        return True  # Version exists (could be a regular file or delete marker)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            # Version was permanently deleted
            return False
        elif e.response['Error']['Code'] == 'MethodNotAllowed':
            # This is a delete marker, but it still exists as a version
            return True
        else:
            # Other errors, assume it doesn't exist
            logger.warning(f"Error checking if file version exists for {key} version {version_id}: {e}")
            return False
    except Exception as e:
        logger.warning(f"Error checking if file version exists for {key} version {version_id}: {e}")
        return False
    
def delete_assetAuxiliary_files(prefix):
    """Delete auxiliary files for an asset
    
    Args:
        assetLocation: The asset location object with Key (dict or AssetLocationModel)
    """

    if not prefix:
        return

    # Add the folder delimiter to the end of the key if not already
    if not prefix.endswith('/'):
        prefix = prefix + '/'

    logger.info(f"Deleting Temporary Auxiliary Assets Files Under Folder Prefix: {asset_aux_bucket_name}:{prefix}")

    try:
        # Get all assets in assetAuxiliary bucket (unversioned, temporary files for the auxiliary assets) for deletion
        # Use assetLocation key as root folder key for assetAuxiliaryFiles
        assetAuxiliaryBucketFilesDeleted = []
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=asset_aux_bucket_name, Prefix=prefix):
            if 'Contents' in page:
                for item in page['Contents']:
                    assetAuxiliaryBucketFilesDeleted.append(item['Key'])
                    logger.info(f"Deleting auxiliary asset file: {item['Key']}")
                    s3_client.delete_object(Bucket=asset_aux_bucket_name, Key=item['Key'])

    except Exception as e:
        logger.exception(f"Error deleting auxiliary files (they may not exist in the first place): {e}")

    return

def list_s3_files_with_versions(bucket: str, prefix: str, include_archived: bool = False) -> List[Dict]:
    """List all files in an S3 bucket prefix with their version information
    
    Args:
        bucket: The S3 bucket name
        prefix: The S3 key prefix
        include_archived: Whether to include archived files
        
    Returns:
        List of file dictionaries with version information
    """
    result = []
    
    try:
        # Ensure prefix ends with a slash if it doesn't already
        if not prefix.endswith('/'):
            prefix = prefix + '/'
            
        # List all objects with the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                # Skip folder markers (keys ending with '/')
                if obj['Key'].endswith('/'):
                    continue
                
                # Get relative key by removing prefix
                relative_key = obj['Key'][len(prefix):]
                
                # Get object metadata and version information
                try:
                    head_response = s3_client.head_object(
                        Bucket=bucket,
                        Key=obj['Key']
                    )
                    
                    # Check if file is archived
                    is_archived = is_file_archived(bucket, obj['Key'])
                    
                    # Skip archived files if not including them
                    if is_archived and not include_archived:
                        continue
                    
                    # Get version ID
                    version_id = head_response.get('VersionId', 'null')
                    
                    # Add file to result
                    result.append({
                        'relativeKey': relative_key,
                        'key': obj['Key'],
                        'versionId': version_id,
                        'size': obj['Size'],
                        'lastModified': obj['LastModified'].isoformat(),
                        'etag': obj.get('ETag', '').strip('"'),
                        'isArchived': is_archived
                    })
                    
                except Exception as e:
                    logger.warning(f"Error getting metadata for {obj['Key']}: {e}")
                    # Skip files with errors
                    continue
                    
    except Exception as e:
        logger.exception(f"Error listing S3 files: {e}")
        raise VAMSGeneralErrorResponse(f"Error listing files: {str(e)}")
    
    return result

def validate_s3_files_exist(bucket: str, prefix: str, files: List[AssetFileVersionItemModel]) -> List[str]:
    """Validate that all specified files exist in S3 and are not archived
    
    Args:
        bucket: The S3 bucket name
        prefix: The S3 key prefix
        files: List of files with version IDs
        
    Returns:
        List of files that don't exist or are archived
    """
    invalid_files = []
    
    # Ensure prefix ends with a slash if it doesn't already
    if not prefix.endswith('/'):
        prefix = prefix + '/'
    
    for file in files:
        full_key = prefix + file.relativeKey.lstrip('/')
        
        try:
            # Check if file exists with specified version
            response = s3_client.head_object(
                Bucket=bucket,
                Key=full_key,
                VersionId=file.versionId
            )
            
            # Check if file is archived
            if is_file_archived(bucket, full_key, file.versionId) and not file.isArchived:
                invalid_files.append(file.relativeKey)
                
        except Exception as e:
            logger.warning(f"Error validating file {full_key}: {e}")
            invalid_files.append(file.relativeKey)
    
    return invalid_files

def copy_s3_object_version(source_bucket: str, source_key: str, source_version_id: str, 
                          dest_bucket: str, dest_key: str) -> Optional[str]:
    """Copy a specific version of an S3 object
    
    Args:
        source_bucket: Source bucket name
        source_key: Source object key
        source_version_id: Source object version ID
        dest_bucket: Destination bucket name
        dest_key: Destination object key
        
    Returns:
        New version ID if successful, None otherwise
    """
    try:
        # Copy the object with the specified version using managed transfer for large files
        s3_resource.meta.client.copy(
            CopySource={
                'Bucket': source_bucket,
                'Key': source_key,
                'VersionId': source_version_id
            },
            Bucket=dest_bucket,
            Key=dest_key
        )
        
        # Get the new version ID by checking the object after copy
        response = s3_client.head_object(Bucket=dest_bucket, Key=dest_key)
        return response.get('VersionId')
        
    except Exception as e:
        logger.exception(f"Error copying S3 object version: {e}")
        return None


def save_asset_file_versions(assetId: str, assetVersionId: str, files: List[Dict]) -> bool:
    """Save file version mappings to DynamoDB
    
    Args:
        assetId: The asset ID
        assetVersionId: The asset version ID
        files: List of file dictionaries with version information
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Create partition key in the format {assetId}:{assetVersionId}
        partition_key = f"{assetId}:{assetVersionId}"
        created_at = datetime.utcnow().isoformat()
        
        # Create individual records for each file
        with asset_file_versions_table.batch_writer() as batch:
            for file in files:
                # Use the file's relativeKey as the sort key
                file_key = file['relativeKey']
                
                # Create item for DynamoDB
                item = {
                    'assetId:assetVersionId': partition_key,
                    'fileKey': file_key,
                    'versionId': file.get('versionId'),
                    'size': file.get('size'),
                    'lastModified': file.get('lastModified'),
                    'etag': file.get('etag'),
                    'createdAt': created_at
                }
                
                # Save to DynamoDB
                batch.put_item(Item=item)
                
        return True
        
    except Exception as e:
        logger.exception(f"Error saving asset file versions: {e}")
        return False

def get_asset_file_versions(assetId: str, assetVersionId: str) -> Optional[Dict]:
    """Get file versions for a specific asset version
    
    Args:
        assetId: The asset ID
        assetVersionId: The asset version ID
        
    Returns:
        Dictionary with file versions or None if not found
    """
    try:
        # Create partition key in the format {assetId}:{assetVersionId}
        partition_key = f"{assetId}:{assetVersionId}"
        
        # Query all records with the same partition key
        response = asset_file_versions_table.query(
            KeyConditionExpression=Key('assetId:assetVersionId').eq(partition_key)
        )
        
        items = response.get('Items', [])
        
        # If no items found, return None
        if not items:
            return None
        
        # Reconstruct the file versions structure
        files = []
        for item in items:
            file_info = {
                'relativeKey': item.get('fileKey'),
                'versionId': item.get('versionId'),
                'size': item.get('size'),
                'lastModified': item.get('lastModified'),
                'etag': item.get('etag')
            }
            files.append(file_info)
        
        # Return in the original format for backward compatibility
        return {
            'assetId': assetId,
            'assetVersionId': assetVersionId,
            'files': files,
            'createdAt': items[0].get('createdAt') if items else datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.exception(f"Error getting asset file versions: {e}")
        return None

def get_asset_version_file_count(assetId: str, assetVersionId: str) -> int:
    """Get count of available files for a specific asset version
    
    Args:
        assetId: The asset ID
        assetVersionId: The asset version ID
        
    Returns:
        Number of files that are not permanently deleted
    """
    try:
        # Create partition key in the format {assetId}:{assetVersionId}
        partition_key = f"{assetId}:{assetVersionId}"
        
        # Query to count records with the same partition key
        response = asset_file_versions_table.query(
            KeyConditionExpression=Key('assetId:assetVersionId').eq(partition_key),
            Select='COUNT'
        )
        
        # Return the count
        return response.get('Count', 0)
        
    except Exception as e:
        logger.exception(f"Error getting asset version file count: {e}")
        return 0

def save_asset_version_metadata(assetId: str, assetVersionId: str, version_number: str, 
                               comment: str, description: str, created_by: str) -> bool:
    """Save asset version metadata to the asset versions table
    
    Args:
        assetId: The asset ID
        assetVersionId: The asset version ID (e.g., "v1", "v2")
        version_number: The version number (e.g., "1", "2")
        comment: Version comment
        description: Version description
        created_by: Username who created the version
        
    Returns:
        True if successful, False otherwise
    """
    try:
        now = datetime.utcnow().isoformat()
        
        # Create version record
        version_record = {
            'assetId': assetId,
            'assetVersionId': assetVersionId,
            'versionNumber': version_number,
            'dateCreated': now,
            'comment': comment,
            'description': description,
            'createdBy': created_by,
            'isCurrentVersion': True,  # This will be updated when new versions are created
            'specifiedPipelines': [],
            'createdAt': now
        }
        
        # Save to asset versions table
        asset_versions_table.put_item(Item=version_record)
        return True
        
    except Exception as e:
        logger.exception(f"Error saving asset version metadata: {e}")
        return False

def get_asset_version_metadata(assetId: str, assetVersionId: str) -> Optional[Dict]:
    """Get asset version metadata from the asset versions table
    
    Args:
        assetId: The asset ID
        assetVersionId: The asset version ID
        
    Returns:
        Dictionary with version metadata or None if not found
    """
    try:
        response = asset_versions_table.get_item(
            Key={
                'assetId': assetId,
                'assetVersionId': assetVersionId
            }
        )
        
        return response.get('Item')
        
    except Exception as e:
        logger.exception(f"Error getting asset version metadata: {e}")
        return None

def get_all_asset_versions(assetId: str) -> List[Dict]:
    """Get all versions for an asset from the asset versions table
    
    Args:
        assetId: The asset ID
        
    Returns:
        List of version dictionaries, sorted by version number descending
    """
    try:
        response = asset_versions_table.query(
            KeyConditionExpression=Key('assetId').eq(assetId),
            ScanIndexForward=False  # Get newest first
        )
        
        versions = response.get('Items', [])
        
        # Sort by version number (descending)
        versions.sort(key=lambda x: int(x.get('versionNumber', '0')), reverse=True)
        
        return versions
        
    except Exception as e:
        logger.exception(f"Error getting all asset versions: {e}")
        return []

def update_current_version_reference(asset: Dict, new_version_id: str) -> bool:
    """Update the asset's currentVersionId reference
    
    Args:
        asset: The asset dictionary
        new_version_id: The new current version ID
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Update only the currentVersionId field
        asset['currentVersionId'] = new_version_id
        
        # Remove legacy version fields if they exist
        if 'currentVersion' in asset:
            del asset['currentVersion']
        if 'versions' in asset:
            del asset['versions']
        
        # Save updated asset
        asset_table.put_item(Item=asset)
        return True
        
    except Exception as e:
        logger.exception(f"Error updating current version reference: {e}")
        return False

def mark_version_as_current(assetId: str, new_current_version_id: str) -> bool:
    """Mark a version as current and unmark previous current version
    
    Args:
        assetId: The asset ID
        new_current_version_id: The version ID to mark as current
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Get all versions for the asset
        versions = get_all_asset_versions(assetId)
        
        # Update isCurrentVersion flag for all versions
        for version in versions:
            version_id = version['assetVersionId']
            is_current = (version_id == new_current_version_id)
            
            # Update the version record
            asset_versions_table.update_item(
                Key={
                    'assetId': assetId,
                    'assetVersionId': version_id
                },
                UpdateExpression='SET isCurrentVersion = :is_current',
                ExpressionAttributeValues={
                    ':is_current': is_current
                }
            )
        
        return True
        
    except Exception as e:
        logger.exception(f"Error marking version as current: {e}")
        return False

def update_asset_version_metadata(asset: Dict, new_version_number: str, comment: Optional[str] = None, created_by: str = 'system') -> Dict:
    """Update asset's version tracking metadata using new asset versions table
    
    Args:
        asset: The asset dictionary
        new_version_number: The new version number
        comment: Optional comment for the version
        created_by: Username who created the version
        
    Returns:
        Updated asset dictionary
    """
    asset_id = asset['assetId']
    new_version_id = f"v{new_version_number}"
    
    # Mark previous current version as not current in asset versions table
    current_version_id = asset.get('currentVersionId')
    if current_version_id:
        mark_version_as_current(asset_id, new_version_id)
    
    # Save new version metadata to asset versions table (which also sets current version)
    success = save_asset_version_metadata(
        asset_id,
        new_version_id,
        new_version_number,
        comment if comment else f"Version {new_version_number}",
        asset.get('description', ''),
        created_by
    )
    

    # Update asset's tables current version reference
    update_current_version_reference(asset, new_version_id)
    return asset


#######################
# Business Logic Functions
#######################

def create_asset_version(databaseId: str, assetId: str, request_model: CreateAssetVersionRequestModel, 
                        claims_and_roles: Dict) -> AssetVersionOperationResponseModel:
    """Create a new asset version
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        request_model: The request model with version details
        claims_and_roles: The claims and roles from the request
        
    Returns:
        AssetVersionOperationResponseModel with the result
    """
    # Get asset and verify permissions
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Get asset location
    bucket, prefix = get_asset_s3_location(asset)
    
    # Determine next version number
    current_version = int(asset.get('currentVersionId', '0'))

    #strip out all letters from current version. If the stripped version cannot be converted to an integer, assume the currentVersionId is 0.
    try:
        current_version = int(str(current_version).replace('v', ''))
        logger.info(f"Current version: {current_version}")
    except Exception as e:
        current_version = 0
        logger.info(f"Could not parse existing version number, setting current to: {current_version}")
    
    new_version = current_version + 1
    new_version_id = f"{new_version}"
    
    # Get files based on request
    files_to_version = []
    skipped_files = []
    
    if request_model.useLatestFiles:
        # Get latest files from S3
        s3_files = list_s3_files_with_versions(bucket, prefix, include_archived=False)
        
        # Format for storage
        for file in s3_files:
            files_to_version.append({
                'relativeKey': file['relativeKey'],
                'versionId': file['versionId'],
                'size': file['size'],
                'lastModified': file['lastModified'],
                'etag': file['etag']
            })
            
    else:
        # Validate provided files (not archived for the version provided and not permanently deleted)
        invalid_files = validate_s3_files_exist(bucket, prefix, request_model.files)
        
        # Add valid files to version
        for file in request_model.files:
            if file.relativeKey not in invalid_files:
                # Get additional metadata
                try:
                    full_key = prefix + file.relativeKey.lstrip('/')
                    response = s3_client.head_object(
                        Bucket=bucket,
                        Key=full_key,
                        VersionId=file.versionId
                    )
                    
                    files_to_version.append({
                        'relativeKey': file.relativeKey,
                        'versionId': file.versionId,
                        'size': response.get('ContentLength'),
                        'lastModified': response.get('LastModified').isoformat(),
                        'etag': response.get('ETag', '').strip('"')
                    })
                    
                except Exception as e:
                    logger.warning(f"Error getting metadata for {file.relativeKey}: {e}")
                    invalid_files.append(file.relativeKey)
            
        skipped_files = invalid_files
    
    # Ensure we have at least one file
    if not files_to_version:
        raise VAMSGeneralErrorResponse("No valid files found for versioning")
    
    # Save file versions to DynamoDB
    if not save_asset_file_versions(assetId, new_version_id, files_to_version):
        raise VAMSGeneralErrorResponse("Failed to save file versions")
    
    # Update asset version metadata
    username = claims_and_roles.get("tokens", ["system"])[0]
    updated_asset = update_asset_version_metadata(asset, new_version, request_model.comment, username)
    
    # Return response
    now = datetime.utcnow().isoformat()
    return AssetVersionOperationResponseModel(
        success=True,
        message=f"Successfully created version {new_version} with {len(files_to_version)} files",
        assetId=assetId,
        assetVersionId=new_version_id,
        versionNumber=new_version,
        operation="create",
        timestamp=now,
        skippedFiles=skipped_files if skipped_files else None
    )

def revert_asset_version(databaseId: str, assetId: str, request_model: RevertAssetVersionRequestModel, 
                        claims_and_roles: Dict) -> AssetVersionOperationResponseModel:
    """Revert to a previous asset version
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        request_model: The request model with version details
        claims_and_roles: The claims and roles from the request
        
    Returns:
        AssetVersionOperationResponseModel with the result
    """
    # Get asset and verify permissions
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Get asset location
    bucket, prefix = get_asset_s3_location(asset)
    
    # Get target version files
    target_version = get_asset_file_versions(assetId, request_model.assetVersionId)
    if not target_version:
        raise VAMSGeneralErrorResponse(f"Version {request_model.assetVersionId} not found")
    
    # Get current files in S3
    current_files = list_s3_files_with_versions(bucket, prefix, include_archived=True)
    current_files_by_key = {file['relativeKey']: file for file in current_files}
    
    # Determine next version number
    current_version = int(asset.get('currentVersion', {}).get('Version', '0'))
    new_version = str(current_version + 1)
    new_version_id = f"v{new_version}"
    
    # Process files
    files_to_version = []
    skipped_files = []
    
    for file in target_version.get('files', []):
        relative_key = file['relativeKey']
        source_version_id = file['versionId']
        full_key = prefix + relative_key.lstrip('/')
        
        # Copy the file version to make it current
        new_version_id = copy_s3_object_version(
            bucket, full_key, source_version_id,
            bucket, full_key
        )
        
        if new_version_id:
            #Delete the aux files since they are most likely wrong with the version revert
            delete_assetAuxiliary_files(full_key)

            # Add to files to version
            files_to_version.append({
                'relativeKey': relative_key,
                'versionId': new_version_id,
                'size': file.get('size'),
                'lastModified': datetime.utcnow().isoformat(),
                'etag': file.get('etag')
            })
        else:
            # Skip files that couldn't be copied
            skipped_files.append(relative_key)
    
    # Ensure we have at least one file
    if not files_to_version:
        raise VAMSGeneralErrorResponse("No files could be reverted")
    
    # Save file versions to DynamoDB
    if not save_asset_file_versions(assetId, new_version_id, files_to_version):
        raise VAMSGeneralErrorResponse("Failed to save file versions")
    
    #Get user of request
    username = claims_and_roles.get("tokens", ["system"])[0]

    # Update asset version metadata
    comment = request_model.comment if request_model.comment else f"Reverted to version {request_model.assetVersionId}"
    updated_asset = update_asset_version_metadata(asset, new_version, comment, username)
    
    # Return response
    now = datetime.utcnow().isoformat()
    return AssetVersionOperationResponseModel(
        success=True,
        message=f"Successfully reverted to version {request_model.assetVersionId} with {len(files_to_version)} files",
        assetId=assetId,
        assetVersionId=new_version_id,
        versionNumber=new_version,
        operation="revert",
        timestamp=now,
        skippedFiles=skipped_files if skipped_files else None
    )

def get_asset_versions(databaseId: str, assetId: str, query_params: Dict, 
                      claims_and_roles: Dict) -> AssetVersionsListResponseModel:
    """Get all versions for an asset
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        query_params: Query parameters for pagination
        claims_and_roles: The claims and roles from the request
        
    Returns:
        AssetVersionsListResponseModel with the versions
    """
    # Get asset and verify permissions
    asset = get_asset_with_permissions(databaseId, assetId, "GET", claims_and_roles)
    
    all_versions = get_all_asset_versions(assetId)
    
    # Get Version Data - create properly typed AssetVersionListItemModel instances
    versions = []
    for version in all_versions:
        try:
            # Get file count for this version
            file_count = get_asset_version_file_count(assetId, version.get('assetVersionId', f"v{version.get('versionNumber', '0')}"))
            
            version_item = AssetVersionListItemModel(
                Version=version.get('versionNumber', '0'),
                DateModified=version.get('dateCreated', ''),
                Comment=version.get('comment', ''),
                description=version.get('description', ''),
                specifiedPipelines=version.get('specifiedPipelines', []),
                createdBy=version.get('createdBy', 'system'),
                isCurrent=version.get('isCurrentVersion', False),
                fileCount=file_count
            )
            versions.append(version_item)
        except Exception as e:
            logger.warning(f"Error creating version model for version {version.get('versionNumber', 'unknown')}: {e}")
            # Skip invalid versions
            continue
    
    # Apply pagination
    max_items = int(query_params.get('maxItems', 100))
    starting_token = query_params.get('startingToken')
    
    # If starting token is provided, find the starting index
    start_index = 0
    if starting_token:
        try:
            start_index = int(starting_token)
        except ValueError:
            start_index = 0
    
    # Get paginated results
    end_index = start_index + max_items
    paginated_versions = versions[start_index:end_index]
    
    # Determine if there are more results
    next_token = None
    if end_index < len(versions):
        next_token = str(end_index)
    
    # Return response
    return AssetVersionsListResponseModel(
        versions=paginated_versions,
        nextToken=next_token
    )

def get_asset_version_details(databaseId: str, assetId: str, request_model: GetAssetVersionRequestModel, 
                             claims_and_roles: Dict) -> AssetVersionResponseModel:
    """Get details for a specific asset version
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        request_model: The request model with version details
        claims_and_roles: The claims and roles from the request
        
    Returns:
        AssetVersionResponseModel with the version details
    """
    # Get asset and verify permissions
    asset = get_asset_with_permissions(databaseId, assetId, "GET", claims_and_roles)
    
    # Get version information from asset
    version_info = None
    version_number = None
    version_id = request_model.assetVersionId
    
    # Normalize version ID format
    if version_id.startswith('v'):
        version_number = version_id[1:]
    else:
        version_number = version_id
        version_id = f"v{version_number}"
    
    # Try to get from new asset versions table
    version_metadata = get_asset_version_metadata(assetId, version_id)
    if version_metadata:
        version_info = {
            'Version': version_metadata.get('versionNumber', version_number),
            'DateModified': version_metadata.get('dateCreated', ''),
            'Comment': version_metadata.get('comment', ''),
            'description': version_metadata.get('description', ''),
            'createdBy': version_metadata.get('createdBy', 'system')
        }
    
    if not version_info:
        raise VAMSGeneralErrorResponse(f"Version {request_model.assetVersionId} not found")
    
    # Get file versions from DynamoDB
    file_versions = get_asset_file_versions(assetId, version_id)
    
    if not file_versions:
        # For older versions that might not have entries in the file versions table
        file_versions = {'files': []}
    
    # Get asset location for checking file existence
    bucket, prefix = get_asset_s3_location(asset)
    
    # Ensure prefix ends with a slash if it doesn't already
    if not prefix.endswith('/'):
        prefix = prefix + '/'
    
    # Format response
    files = []
    for file in file_versions.get('files', []):
        # Construct full S3 key
        full_key = prefix + file['relativeKey'].lstrip('/')
        
        # Check if the file version was permanently deleted
        file_permanently_deleted = not does_file_version_exist(bucket, full_key, file['versionId'])
        
        # Check if the latest version of this file is archived
        latest_version_archived = is_file_archived(bucket, full_key)  # Check current/latest version
        
        files.append(AssetVersionFileModel(
            relativeKey=file['relativeKey'],
            versionId=file['versionId'],
            isPermanentlyDeleted=file_permanently_deleted,
            isLatestVersionArchived=latest_version_archived,
            size=file.get('size'),
            lastModified=file.get('lastModified'),
            etag=file.get('etag')
        ))
    
    # Return response
    return AssetVersionResponseModel(
        assetId=assetId,
        assetVersionId=version_id,
        versionNumber=version_number,
        dateCreated=version_info.get('DateModified', ''),
        comment=version_info.get('Comment', ''),
        files=files,
        createdBy=version_info.get('createdBy')
    )

#######################
# API Route Handlers
#######################

def handle_create_version(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /createVersion requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body
        if not event.get('body'):
            return validation_error(body={'message': "Request body is required"})
        
        if isinstance(event['body'], str):
            body = json.loads(event['body'])
        else:
            body = event['body']
        
        # Parse request model
        request_model = parse(body, model=CreateAssetVersionRequestModel)
        
        # Process request
        response = create_asset_version(
            path_params['databaseId'],
            path_params['assetId'],
            request_model,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_revert_version(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /revertVersion/{assetVersionId} requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
            
        if 'assetVersionId' not in path_params:
            return validation_error(body={'message': "No asset version ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Get request body for optional comment
        body = {}
        if event.get('body'):
            if isinstance(event['body'], str):
                body = json.loads(event['body'])
            else:
                body = event['body']
        
        # Create request model with assetVersionId from path parameter
        request_model = RevertAssetVersionRequestModel(
            assetVersionId=path_params['assetVersionId'],
            comment=body.get('comment')
        )
        
        # Process request
        response = revert_asset_version(
            path_params['databaseId'],
            path_params['assetId'],
            request_model,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_get_versions(event, context) -> APIGatewayProxyResponseV2:
    """Handle GET /getVersions requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Get query parameters
        query_params = event.get('queryStringParameters', {}) or {}
        
        # Parse request model
        request_model = parse(query_params, model=GetAssetVersionsRequestModel)
        
        # Process request
        response = get_asset_versions(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.dict(),
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_get_version(event, context) -> APIGatewayProxyResponseV2:
    """Handle GET /getVersion/{assetVersionId} requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
            
        if 'assetVersionId' not in path_params:
            return validation_error(body={'message': "No asset version ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Create request model with assetVersionId from path parameter
        request_model = GetAssetVersionRequestModel(assetVersionId=path_params['assetVersionId'])
        
        # Process request
        response = get_asset_version_details(
            path_params['databaseId'],
            path_params['assetId'],
            request_model,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset version operations
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Get API path and method
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Route to appropriate handler based on path pattern
        if method == 'POST' and path.endswith('/createVersion'):
            return handle_create_version(event, context)
        elif method == 'POST' and '/revertAssetVersion/' in path:
            return handle_revert_version(event, context)
        elif method == 'GET' and path.endswith('/getVersions'):
            return handle_get_versions(event, context)
        elif method == 'GET' and '/getVersion/' in path:
            return handle_get_version(event, context)
        else:
            return validation_error(body={'message': "Invalid API path or method"})
    
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return internal_error()
