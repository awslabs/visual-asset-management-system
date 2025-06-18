# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
import time
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from handlers.assets.assetCount import update_asset_count
from handlers.assets.assetFiles import delete_s3_prefix_all_versions
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetsV3 import (
    GetAssetRequestModel, GetAssetsRequestModel, UpdateAssetRequestModel,
    ArchiveAssetRequestModel, DeleteAssetRequestModel, AssetResponseModel,
    AssetOperationResponseModel, CurrentVersionModel, AssetLocationModel,
    AssetPreviewLocationModel
)

# Configure AWS clients with retry configuration
region = os.environ.get('AWS_REGION', 'us-east-1')
#lambda_client = boto3.client('lambda')
# Standardized retry configuration for all AWS clients
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="AssetService")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables
try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    asset_bucket_name_default = os.environ["S3_ASSET_STORAGE_BUCKET"]
    s3_assetAuxiliary_bucket = os.environ["S3_ASSET_AUXILIARY_BUCKET"]
    asset_upload_table_name = os.environ.get("ASSET_UPLOAD_TABLE_NAME")
    asset_links_table_name = os.environ.get("ASSET_LINKS_STORAGE_TABLE_NAME")
    metadata_table_name = os.environ.get("METADATA_STORAGE_TABLE_NAME")
    asset_versions_table_name = os.environ.get("ASSET_VERSIONS_STORAGE_TABLE_NAME")
    asset_versions_files_table_name = os.environ.get("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME")
    comment_table_name = os.environ.get("COMMENT_STORAGE_TABLE_NAME")
    
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_table = dynamodb.Table(asset_database)
db_table = dynamodb.Table(db_database)
asset_upload_table = dynamodb.Table(asset_upload_table_name) if asset_upload_table_name else None
asset_links_table = dynamodb.Table(asset_links_table_name) if asset_links_table_name else None
metadata_table = dynamodb.Table(metadata_table_name) if metadata_table_name else None
versions_table = dynamodb.Table(asset_versions_table_name) if asset_versions_table_name else None
comment_table = dynamodb.Table(comment_table_name) if comment_table_name else None
asset_versions_files_table = dynamodb.Table(asset_versions_files_table_name) if asset_versions_files_table_name else None

#######################
# Version Functions
#######################

def get_current_version_info(asset):
    """Get current version information from asset versions table
    
    Args:
        asset: The asset dictionary
        
    Returns:
        CurrentVersionModel instance or None
    """
    if not asset or 'currentVersionId' not in asset:
        return None
    
    try:
        response = versions_table.get_item(
            Key={
                'assetId': asset['assetId'],
                'assetVersionId': asset['currentVersionId']
            }
        )
        
        if 'Item' in response:
            version_item = response['Item']
            # Create CurrentVersionModel instance
            return CurrentVersionModel(
                Version=version_item.get('versionNumber', '0'),
                DateModified=version_item.get('dateCreated', ''),
                Comment=version_item.get('comment', ''),
                description=version_item.get('description', ''),
                specifiedPipelines=version_item.get('specifiedPipelines', []),
                createdBy=version_item.get('createdBy', 'system')
            )
    except Exception as e:
        logger.exception(f"Error fetching current version from versions table: {e}")
    
    return None

def enhance_asset_with_version_info(asset):
    """Enhance asset with version information from versions table
    
    Args:
        asset: The asset dictionary
        
    Returns:
        Enhanced asset dictionary with version information and proper location models
    """
    if not asset:
        return asset
    
    enhanced_asset = asset.copy()
    
    # Get current version info
    current_version = get_current_version_info(asset)
    if current_version:
        enhanced_asset['currentVersion'] = current_version
    
    return enhanced_asset

#######################
# Utility Functions
#######################

def is_file_archived(bucket, key, version_id=None):
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
                s3.head_object(Bucket=bucket, Key=key, VersionId=version_id)
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
                response = s3.head_object(Bucket=bucket, Key=key)
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

def mark_file_as_archived(key, bucket=None):
    """Mark an S3 object as archived by creating a delete marker
    
    Args:
        key: The S3 object key
        bucket: The S3 bucket name (optional, defaults to asset_bucket_name_default)
        
    Returns:
        The S3 response
    """
    # Use provided bucket or fall back to default
    bucket_to_use = bucket if bucket else asset_bucket_name_default
    
    # Delete the object to create a delete marker (archives it in versioned bucket)
    return s3.delete_object(
        Bucket=bucket_to_use,
        Key=key
    )

def delete_assetAuxiliary_files(assetLocation):
    """Delete auxiliary files for an asset
    
    Args:
        assetLocation: The asset location object with Key (dict or AssetLocationModel)
    """
    # Convert to AssetLocationModel if it's a dictionary
    if isinstance(assetLocation, dict):
        try:
            location_model = AssetLocationModel(**assetLocation)
        except ValidationError as e:
            logger.warning(f"Invalid asset location format: {e}")
            return
    elif isinstance(assetLocation, AssetLocationModel):
        location_model = assetLocation
    else:
        logger.warning("Invalid asset location type")
        return

    key = location_model.Key
    if not key:
        return

    # Add the folder delimiter to the end of the key
    key = key + '/'

    logger.info(f"Deleting Temporary Auxiliary Assets Files Under Folder: {s3_assetAuxiliary_bucket}:{key}")

    try:
        # Get all assets in assetAuxiliary bucket (unversioned, temporary files for the auxiliary assets) for deletion
        # Use assetLocation key as root folder key for assetAuxiliaryFiles
        assetAuxiliaryBucketFilesDeleted = []
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=s3_assetAuxiliary_bucket, Prefix=key):
            if 'Contents' in page:
                for item in page['Contents']:
                    assetAuxiliaryBucketFilesDeleted.append(item['Key'])
                    logger.info(f"Deleting auxiliary asset file: {item['Key']}")
                    s3.delete_object(Bucket=s3_assetAuxiliary_bucket, Key=item['Key'])

    except Exception as e:
        logger.exception(f"Error deleting auxiliary files: {e}")

    return

def archive_multi_assetFiles(location):
    """Archive all files in a multi-file asset
    
    Args:
        location: The asset location object with Key and optional Bucket (dict or AssetLocationModel)
    """
    # Convert to AssetLocationModel if it's a dictionary
    if isinstance(location, dict):
        try:
            location_model = AssetLocationModel(**location)
        except ValidationError as e:
            logger.warning(f"Invalid asset location format: {e}")
            return
    elif isinstance(location, AssetLocationModel):
        location_model = location
    else:
        logger.warning("Invalid asset location type")
        return

    prefix = location_model.Key
    if not prefix:
        return
    
    # Get bucket from location or use default
    bucket = location_model.Bucket if location_model.Bucket else asset_bucket_name_default
    logger.info(f'Archiving folder with multiple files from bucket: {bucket}')

    paginator = s3.get_paginator('list_objects_v2')
    files = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        if 'Contents' in page:
            for obj in page.get('Contents', []):
                files.append(obj['Key'])

    for key in files:
        try:
            response = mark_file_as_archived(key, bucket)
            logger.info(f"S3 archive response for {key}: {response}")

        except s3.exceptions.InvalidObjectState as ios:
            logger.exception(f"S3 object already archived: {key}")
            logger.exception(ios)

        except Exception as e:
            logger.exception(f"Error archiving file {key}: {e}")

    return

def archive_file_preview(location):
    """Archive a single file
    
    Args:
        location: The asset location object with Key and optional Bucket (dict or AssetPreviewLocationModel)
    """
    # Convert to AssetPreviewLocationModel if it's a dictionary
    if isinstance(location, dict):
        try:
            location_model = AssetPreviewLocationModel(**location)
        except ValidationError as e:
            logger.warning(f"Invalid preview location format: {e}")
            return
    elif isinstance(location, AssetPreviewLocationModel):
        location_model = location
    else:
        logger.warning("Invalid preview location type")
        return

    key = location_model.Key
    if not key:
        return
    
    # Get bucket from location or use default
    bucket = location_model.Bucket if location_model.Bucket else asset_bucket_name_default
    logger.info(f"Archiving item: {bucket}:{key}")

    try:
        response = mark_file_as_archived(key, bucket)
        logger.info(f"S3 archive response: {response}")

    except s3.exceptions.InvalidObjectState as ios:
        logger.exception(f"S3 object already archived: {key}")
        logger.exception(ios)

    except Exception as e:
        logger.exception(f"Error archiving file {key}: {e}")
    return

def delete_s3_objects(prefix, bucket=asset_bucket_name_default):
    """Delete all S3 objects with the given prefix
    
    Args:
        prefix: The S3 object key prefix
        bucket: The S3 bucket name
        
    Returns:
        List of deleted keys
    """
    deleted_keys = []
    try:
        # List all objects with the prefix
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' in page:
                # Create a list of objects to delete
                objects_to_delete = [{'Key': obj['Key']} for obj in page['Contents']]
                if objects_to_delete:
                    # Delete the objects
                    s3.delete_objects(
                        Bucket=bucket,
                        Delete={'Objects': objects_to_delete}
                    )
                    deleted_keys.extend([obj['Key'] for obj in objects_to_delete])
                    logger.info(f"Deleted {len(objects_to_delete)} objects from {bucket}")
    except Exception as e:
        logger.exception(f"Error deleting S3 objects with prefix {prefix}: {e}")
        raise VAMSGeneralErrorResponse(f"Error deleting S3 objects: {str(e)}")
    
    return deleted_keys

#######################
# Business Logic Functions
#######################

def get_asset_details(databaseId, assetId, showArchived=False):
    """Get asset details from DynamoDB
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        showArchived: Whether to show archived assets
        
    Returns:
        The asset details or None if not found
    """
    try:
        # If showArchived is False, we only look in the active assets table
        # If showArchived is True, we first look in the active table, then try the archived suffix
        db_id = databaseId
        response = asset_table.get_item(Key={'databaseId': db_id, 'assetId': assetId})
        item = response.get('Item')
        
        # If not found and showArchived is True, try with the archived suffix
        if not item and showArchived:
            archived_db_id = f"{databaseId}#deleted"
            response = asset_table.get_item(Key={'databaseId': archived_db_id, 'assetId': assetId})
            item = response.get('Item')
            
            # If found in archived, add status field if not present
            if item and 'status' not in item:
                item['status'] = 'archived'
        
        return item
    except Exception as e:
        logger.exception(f"Error getting asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving asset: {str(e)}")

def get_assets(databaseId, query_params, showArchived=False):
    """Get assets for a database
    
    Args:
        databaseId: The database ID
        query_params: Query parameters for pagination
        showArchived: Whether to show archived assets
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    paginator = dynamodb.meta.client.get_paginator('query')
    
    # If showArchived is True, we need to query both active and archived assets
    db_ids = [databaseId]
    if showArchived:
        db_ids.append(f"{databaseId}#deleted")
    
    all_items = []
    next_token = None
    
    # Query for each database ID (active and possibly archived)
    for db_id in db_ids:
        try:
            page_iterator = paginator.paginate(
                TableName=asset_database,
                KeyConditionExpression=Key('databaseId').eq(db_id),
                ScanIndexForward=False,
                PaginationConfig={
                    'MaxItems': int(query_params['maxItems']),
                    'PageSize': int(query_params['pageSize']),
                    'StartingToken': query_params.get('startingToken')
                }
            ).build_full_result()
            
            # Process items and check permissions
            for item in page_iterator.get('Items', []):
                # Add status field for archived assets if not present
                if db_id.endswith('#deleted') and 'status' not in item:
                    item['status'] = 'archived'
                
                # Add object type for Casbin enforcement
                item.update({"object__type": "asset"})
                
                # Check if user has permission to GET the asset
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(item, "GET"):
                        all_items.append(item)
            
            # Keep track of the next token from the last query
            if 'NextToken' in page_iterator:
                next_token = page_iterator['NextToken']
                
        except Exception as e:
            logger.exception(f"Error querying assets for database {db_id}: {e}")
            raise VAMSGeneralErrorResponse(f"Error retrieving assets: {str(e)}")
    
    # Return the combined results
    result = {"Items": all_items}
    if next_token:
        result["NextToken"] = next_token
        
    return result

def get_all_assets(query_params, showArchived=False):
    """Get all assets across all databases
    
    Args:
        query_params: Query parameters for pagination
        showArchived: Whether to show archived assets
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    deserializer = TypeDeserializer()
    paginator = dynamodb_client.get_paginator('scan')
    
    # Set up the filter based on showArchived
    operator = "NOT_CONTAINS"
    if showArchived:
        operator = "CONTAINS"
    
    filter_expression = {
        "databaseId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}"
        }
    }
    
    try:
        page_iterator = paginator.paginate(
            TableName=asset_database,
            ScanFilter=filter_expression,
            PaginationConfig={
                'MaxItems': int(query_params['maxItems']),
                'PageSize': int(query_params['pageSize']),
                'StartingToken': query_params.get('startingToken')
            }
        ).build_full_result()
        
        # Process results
        result = {}
        items = []
        
        for item in page_iterator.get('Items', []):
            # Deserialize the DynamoDB item
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
            
            # Add status field for archived assets if not present
            if '#deleted' in deserialized_document.get('databaseId', '') and 'status' not in deserialized_document:
                deserialized_document['status'] = 'archived'
            
            # Add object type for Casbin enforcement
            deserialized_document.update({"object__type": "asset"})
            
            # Check if user has permission to GET the asset
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_document, "GET"):
                    items.append(deserialized_document)
        
        result['Items'] = items
        
        if 'NextToken' in page_iterator:
            result['NextToken'] = page_iterator['NextToken']
            
        return result
        
    except Exception as e:
        logger.exception(f"Error scanning all assets: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving all assets: {str(e)}")

def update_asset(databaseId, assetId, update_data, claims_and_roles):
    """Update an existing asset with new data
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        update_data: Dictionary with fields to update
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        Updated asset data
    """
    # Get the existing asset
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
    
    # Check authorization
    asset.update({"object__type": "asset"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(asset, "PUT"):
            raise VAMSGeneralErrorResponse("Not authorized to update this asset")
    
    # Update the fields
    logger.info(f"Updating asset {assetId} in database {databaseId}")
    
    # Update only the editable fields
    if 'assetName' in update_data:
        asset['assetName'] = update_data['assetName']
    
    if 'description' in update_data:
        asset['description'] = update_data['description']
    
    if 'isDistributable' in update_data:
        asset['isDistributable'] = update_data['isDistributable']
    
    if 'tags' in update_data:
        asset['tags'] = update_data['tags']
    
    # Save the updated asset
    try:
        asset_table.put_item(Item=asset)
        
        # Create response
        timestamp = datetime.utcnow().isoformat()
        
        return AssetOperationResponseModel(
            success=True,
            message="Asset updated successfully",
            assetId=assetId,
            operation="update",
            timestamp=timestamp
        )
    except Exception as e:
        logger.exception(f"Error updating asset: {e}")
        raise VAMSGeneralErrorResponse(f"Error updating asset: {str(e)}")

def archive_asset(databaseId, assetId, request_model, claims_and_roles):
    """Archive an asset (soft delete)
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        request_model: ArchiveAssetRequestModel with archive options
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        AssetOperationResponseModel with operation result
    """
    # Get the existing asset
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
    
    # Check if asset is already archived
    if databaseId.endswith('#deleted') or asset.get('status') == 'archived':
        raise VAMSGeneralErrorResponse(f"Asset {assetId} is already archived")
    
    # Check authorization
    asset.update({"object__type": "asset"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(asset, "DELETE"):
            raise VAMSGeneralErrorResponse("Not authorized to archive this asset")
    
    # Archive S3 files
    logger.info(f"Archiving asset {assetId} in database {databaseId}")
    
    try:
        # Archive asset files in S3
        if "assetLocation" in asset:
            archive_multi_assetFiles(asset['assetLocation'])

        # Archive preview if exists
        if "previewLocation" in asset:
            archive_file_preview(asset['previewLocation'])
        
        # Update asset record with archived status
        now = datetime.utcnow().isoformat()
        username = claims_and_roles.get("username", "system")
        
        # Add archive metadata
        asset['status'] = 'archived'
        asset['archivedAt'] = now
        asset['archivedBy'] = username
        if request_model.reason:
            asset['archivedReason'] = request_model.reason
        
        # Move to archived database ID
        archived_db_id = f"{databaseId}#deleted"
        asset['databaseId'] = archived_db_id
        
        # Save to archived location
        asset_table.put_item(Item=asset)
        
        # Delete from original location
        asset_table.delete_item(Key={'databaseId': databaseId, 'assetId': assetId})
        
        # Update asset count
        update_asset_count(db_database, asset_database, {}, databaseId)
        
        # Return success response
        return AssetOperationResponseModel(
            success=True,
            message=f"Asset {assetId} archived successfully",
            assetId=assetId,
            operation="archive",
            timestamp=now
        )
    except Exception as e:
        logger.exception(f"Error archiving asset: {e}")
        raise VAMSGeneralErrorResponse(f"Error archiving asset: {str(e)}")

def delete_asset_permanent(databaseId, assetId, request_model, claims_and_roles):
    """Permanently delete an asset from all systems
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        request_model: DeleteAssetRequestModel with delete options
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        AssetOperationResponseModel with operation result
    """
    # Verify confirmPermanentDelete is True
    if not request_model.confirmPermanentDelete:
        raise VAMSGeneralErrorResponse("Permanent deletion requires explicit confirmation")
    
    # Get the existing asset (including archived)
    asset = get_asset_details(databaseId, assetId, showArchived=True)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
    
    # Check authorization
    asset.update({"object__type": "asset"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(asset, "DELETE"):
            raise VAMSGeneralErrorResponse("Not authorized to delete this asset")
    
    # Begin deletion process
    logger.info(f"Permanently deleting asset {assetId} from database {databaseId}")
    
    try:
        # Track what was deleted for the response
        deleted_items = {
            "s3_objects": [],
            "dynamodb_tables": []
        }
        
        # 1. Delete all S3 objects (assets files and preview)
        if "assetLocation" in asset and "Key" in asset["assetLocation"]:
            prefix = asset["assetLocation"]["Key"]
            if prefix:
                # Get bucket from assetLocation or use default
                bucket = asset["assetLocation"].get('Bucket', asset_bucket_name_default)
                logger.info(f"Deleting S3 asset objects and all versions with prefix {prefix} from bucket {bucket}")
                
                # Delete all objects and all versions with this prefix
                deleted_keys = delete_s3_prefix_all_versions(bucket, prefix)
                deleted_items["s3_objects"].extend(deleted_keys)
                
                # Also delete any auxiliary files
                delete_assetAuxiliary_files(asset["assetLocation"])

        if "previewLocation" in asset and "Key" in asset["previewLocation"]:
            prefix = asset["previewLocation"]["Key"]
            if prefix:
                # Get bucket from assetLocation or use default
                bucket = asset["previewLocation"].get('Bucket', asset_bucket_name_default)
                logger.info(f"Deleting S3 preview objects and all versions with prefix {prefix} from bucket {bucket}")
                
                # Delete all objects and all versions with this prefix
                deleted_keys = delete_s3_prefix_all_versions(bucket, prefix)
                deleted_items["s3_objects"].extend(deleted_keys)
        
        # 2. Delete from asset table (both active and archived locations)
        # First try the original database ID
        original_db_id = databaseId.replace("#deleted", "")
        asset_table.delete_item(Key={'databaseId': original_db_id, 'assetId': assetId})
        deleted_items["dynamodb_tables"].append(f"{asset_database} (databaseId={original_db_id})")
        
        # Then try the archived version
        archived_db_id = f"{original_db_id}#deleted"
        asset_table.delete_item(Key={'databaseId': archived_db_id, 'assetId': assetId})
        deleted_items["dynamodb_tables"].append(f"{asset_database} (databaseId={archived_db_id})")
        
        # 3. Delete from metadata table if available
        if metadata_table:
            metadata_table.delete_item(Key={'databaseId': original_db_id, 'assetId': assetId})
            deleted_items["dynamodb_tables"].append(f"{metadata_table_name} (databaseId={original_db_id})")
        
        # 4. Delete from asset links table if available
        if asset_links_table:
            # Delete links where this asset is the source
            asset_links_table.delete_item(Key={'assetIdFrom': assetId})
            deleted_items["dynamodb_tables"].append(f"{asset_links_table_name} (assetIdFrom={assetId})")
            
            # Query and delete links where this asset is the target
            # This requires using a GSI, so we need to query first
            try:
                response = asset_links_table.query(
                    IndexName='AssetIdToGSI',
                    KeyConditionExpression=Key('assetIdTo').eq(assetId)
                )
                
                for item in response.get('Items', []):
                    if 'assetIdFrom' in item:
                        asset_links_table.delete_item(Key={'assetIdFrom': item['assetIdFrom'], 'assetIdTo': assetId})
                        deleted_items["dynamodb_tables"].append(f"{asset_links_table_name} (assetIdFrom={item['assetIdFrom']}, assetIdTo={assetId})")
            except Exception as e:
                logger.warning(f"Error deleting asset links where asset is target: {e}")
        
        # 5. Delete from asset uploads table if available
        if asset_upload_table:
            try:
                # Query using the GSI to find uploads for this asset
                response = asset_upload_table.query(
                    IndexName='AssetIdGSI',
                    KeyConditionExpression=Key('assetId').eq(assetId)
                )
                
                for item in response.get('Items', []):
                    if 'uploadId' in item:
                        asset_upload_table.delete_item(Key={'uploadId': item['uploadId'], 'assetId': assetId})
                        deleted_items["dynamodb_tables"].append(f"{asset_upload_table_name} (uploadId={item['uploadId']}, assetId={assetId})")
            except Exception as e:
                logger.warning(f"Error deleting asset uploads: {e}")
        
        # 6. Delete from comments table if available
        if comment_table:
            try:
                # Query to find all comments for this asset
                response = comment_table.query(
                    KeyConditionExpression=Key('assetId').eq(assetId)
                )
                
                for item in response.get('Items', []):
                    if 'assetVersionId:commentId' in item:
                        comment_table.delete_item(Key={
                            'assetId': assetId,
                            'assetVersionId:commentId': item['assetVersionId:commentId']
                        })
                        deleted_items["dynamodb_tables"].append(f"{comment_table_name} (assetId={assetId}, assetVersionId:commentId={item['assetVersionId:commentId']})")
            except Exception as e:
                logger.warning(f"Error deleting asset comments: {e}")
        
        # 7. Delete from asset file versions table if available
        if versions_table and asset_versions_files_table:
            try:
                # First get all version IDs for this asset
                response = versions_table.query(
                    KeyConditionExpression=Key('assetId').eq(assetId)
                )
                
                # For each version, delete the corresponding file versions
                for version_item in response.get('Items', []):
                    if 'assetVersionId' in version_item:
                        asset_version_id = version_item['assetVersionId']
                        partition_key = f"{assetId}:{asset_version_id}"
                        
                        # Query to find all file versions for this asset version
                        file_response = asset_versions_files_table.query(
                            KeyConditionExpression=Key('assetId:assetVersionId').eq(partition_key)
                        )
                        
                        # Delete each file version
                        for file_item in file_response.get('Items', []):
                            if 'fileKey' in file_item:
                                asset_versions_files_table.delete_item(Key={
                                    'assetId:assetVersionId': partition_key,
                                    'fileKey': file_item['fileKey']
                                })
                                deleted_items["dynamodb_tables"].append(f"{asset_versions_files_table_name} (assetId:assetVersionId={partition_key}, fileKey={file_item['fileKey']})")
                
                # Delete from versions table after getting all version IDs
                for version_item in response.get('Items', []):
                    if 'assetVersionId' in version_item:
                        versions_table.delete_item(Key={
                            'assetId': assetId,
                            'assetVersionId': version_item['assetVersionId']
                        })
                        deleted_items["dynamodb_tables"].append(f"{asset_versions_table_name} (assetId={assetId}, assetVersionId={version_item['assetVersionId']})")
            except Exception as e:
                logger.warning(f"Error deleting asset file versions: {e}")
        
        # 8. Update asset count
        update_asset_count(db_database, asset_database, {}, original_db_id)
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return AssetOperationResponseModel(
            success=True,
            message=f"Asset {assetId} permanently deleted from all systems",
            assetId=assetId,
            operation="delete",
            timestamp=now
        )
    except Exception as e:
        logger.exception(f"Error permanently deleting asset: {e}")
        raise VAMSGeneralErrorResponse(f"Error permanently deleting asset: {str(e)}")

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for assets
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Case 1: Get a specific asset
        if 'assetId' in path_parameters and 'databaseId' in path_parameters:
            logger.info(f"Getting asset {path_parameters['assetId']} from database {path_parameters['databaseId']}")
            
            # Validate parameters
            (valid, message) = validate({
                'databaseId': {
                    'value': path_parameters['databaseId'],
                    'validator': 'ID'
                },
                'assetId': {
                    'value': path_parameters['assetId'],
                    'validator': 'ASSET_ID'
                },
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message})
            
            # Parse and validate query parameters using GetAssetRequestModel
            try:
                request_model = parse(query_parameters, model=GetAssetRequestModel)
                show_archived = request_model.showArchived
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                return validation_error(body={'message': str(v)})
            
            # Get the asset
            asset = get_asset_details(path_parameters['databaseId'], path_parameters['assetId'], show_archived)
            
            # Check if asset exists and user has permission
            if asset:
                asset.update({"object__type": "asset"})
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if not casbin_enforcer.enforce(asset, "GET"):
                        return authorization_error()
                
                # Enhance asset with version information
                enhanced_asset = enhance_asset_with_version_info(asset)
                
                # Convert to AssetResponseModel for consistent response format
                try:
                    response_model = AssetResponseModel(**enhanced_asset)
                    return success(body=response_model.dict())
                except ValidationError as v:
                    logger.exception(f"Error converting asset to response model: {v}")
                    # Fall back to raw response if conversion fails
                    return success(body={"message": enhanced_asset})
            else:
                return general_error(body={"message": "Asset not found"}, status_code=404)
        
        # Case 2: Get assets for a specific database
        elif 'databaseId' in path_parameters:
            logger.info(f"Listing assets for database {path_parameters['databaseId']}")
            
            # Validate parameters
            (valid, message) = validate({
                'databaseId': {
                    'value': path_parameters['databaseId'],
                    'validator': 'ID'
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message})
            
            # Parse and validate query parameters using GetAssetsRequestModel
            try:
                request_model = parse(query_parameters, model=GetAssetsRequestModel)
                # Extract validated parameters for the query
                query_params = {
                    'maxItems': request_model.maxItems,
                    'pageSize': request_model.pageSize,
                    'startingToken': request_model.startingToken
                }
                show_archived = request_model.showArchived
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                # Fall back to default pagination with validation
                validate_pagination_info(query_parameters)
                query_params = query_parameters
                show_archived = query_parameters.get('showArchived', '').lower() == 'true'
            
            # Get the assets
            assets_result = get_assets(path_parameters['databaseId'], query_params, show_archived)
            
            # Enhance each asset with version information
            enhanced_items = []
            for item in assets_result.get('Items', []):
                enhanced_item = enhance_asset_with_version_info(item)
                enhanced_items.append(enhanced_item)
            
            # Convert enhanced items to AssetResponseModel instances
            formatted_items = []
            for item in enhanced_items:
                try:
                    asset_model = AssetResponseModel(**item)
                    formatted_items.append(asset_model.dict())
                except ValidationError:
                    # Fall back to raw item if conversion fails
                    formatted_items.append(item)
            
            # Build response with formatted items
            response = {
                "Items": formatted_items
            }
            if 'NextToken' in assets_result:
                response['NextToken'] = assets_result['NextToken']
                
            return success(body=response)
        
        # Case 3: Get all assets across all databases
        else:
            logger.info("Listing all assets")
            
            # Parse and validate query parameters using GetAssetsRequestModel
            try:
                request_model = parse(query_parameters, model=GetAssetsRequestModel)
                # Extract validated parameters for the query
                query_params = {
                    'maxItems': request_model.maxItems,
                    'pageSize': request_model.pageSize,
                    'startingToken': request_model.startingToken
                }
                show_archived = request_model.showArchived
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                # Fall back to default pagination with validation
                validate_pagination_info(query_parameters)
                query_params = query_parameters
                show_archived = query_parameters.get('showArchived', '').lower() == 'true'
            
            # Get all assets
            assets_result = get_all_assets(query_params, show_archived)
            
            # Enhance each asset with version information
            enhanced_items = []
            for item in assets_result.get('Items', []):
                enhanced_item = enhance_asset_with_version_info(item)
                enhanced_items.append(enhanced_item)
            
            # Convert enhanced items to AssetResponseModel instances
            formatted_items = []
            for item in enhanced_items:
                try:
                    asset_model = AssetResponseModel(**item)
                    formatted_items.append(asset_model.dict())
                except ValidationError:
                    # Fall back to raw item if conversion fails
                    formatted_items.append(item)
            
            # Build response with formatted items
            response = {
                "Items": formatted_items
            }
            if 'NextToken' in assets_result:
                response['NextToken'] = assets_result['NextToken']
                
            return success(body=response)
            
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()

def handle_put_request(event):
    """Handle PUT requests to update assets
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    # Validate required path parameters
    if 'databaseId' not in path_parameters:
        return validation_error(body={'message': "No database ID in API Call"})
    
    if 'assetId' not in path_parameters:
        return validation_error(body={'message': "No asset ID in API Call"})
    
    # Validate path parameters
    (valid, message) = validate({
        'databaseId': {
            'value': path_parameters['databaseId'],
            'validator': 'ID'
        },
        'assetId': {
            'value': path_parameters['assetId'],
            'validator': 'ASSET_ID'
        },
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message})
    
    try:
        # Parse request body
        body = event.get('body', {})
        if isinstance(body, str):
            body = json.loads(body)
            
        # Parse and validate the update model
        update_model = parse(body, model=UpdateAssetRequestModel)
        
        # Update the asset
        result = update_asset(
            path_parameters['databaseId'], 
            path_parameters['assetId'], 
            update_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error()

def handle_delete_request(event):
    """Handle DELETE requests for assets
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path = event['requestContext']['http']['path']
    path_parameters = event.get('pathParameters', {})
    
    # Validate required path parameters
    if 'databaseId' not in path_parameters:
        return validation_error(body={'message': "No database ID in API Call"})
    
    if 'assetId' not in path_parameters:
        return validation_error(body={'message': "No asset ID in API Call"})
    
    # Validate path parameters
    (valid, message) = validate({
        'databaseId': {
            'value': path_parameters['databaseId'],
            'validator': 'ID'
        },
        'assetId': {
            'value': path_parameters['assetId'],
            'validator': 'ASSET_ID'
        },
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message})
    
    try:
        # Parse request body
        body = event.get('body', {})
        if isinstance(body, str) and body:
            body = json.loads(body)
        else:
            body = {}
        
        # Determine which operation to perform based on the path
        if path.endswith('/archiveAsset'):
            # Archive asset operation
            request_model = parse(body, model=ArchiveAssetRequestModel)
            result = archive_asset(
                path_parameters['databaseId'],
                path_parameters['assetId'],
                request_model,
                claims_and_roles
            )
            return success(body=result.dict())
            
        elif path.endswith('/deleteAsset'):
            # Permanent delete operation
            request_model = parse(body, model=DeleteAssetRequestModel)
            result = delete_asset_permanent(
                path_parameters['databaseId'],
                path_parameters['assetId'],
                request_model,
                claims_and_roles
            )
            return success(body=result.dict())
            
        else:
            # Default DELETE behavior - archive asset
            request_model = ArchiveAssetRequestModel(confirmArchive=True)
            result = archive_asset(
                path_parameters['databaseId'],
                path_parameters['assetId'],
                request_model,
                claims_and_roles
            )
            return success(body=result.dict())
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error()

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        
        if not method_allowed_on_api:
            return authorization_error()
        
        # Route to appropriate handler
        if method == 'GET':
            return handle_get_request(event)
        elif method == 'PUT':
            return handle_put_request(event)
        elif method == 'DELETE':
            return handle_delete_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"})
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
