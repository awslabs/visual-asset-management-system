# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import base64
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
    ArchiveAssetRequestModel, UnarchiveAssetRequestModel, DeleteAssetRequestModel, 
    AssetResponseModel, AssetOperationResponseModel, CurrentVersionModel, 
    AssetLocationModel, AssetPreviewLocationModel
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
lambda_client = boto3.client('lambda', config=retry_config)
sns_client = boto3.client('sns', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="AssetService")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables
try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    s3_assetAuxiliary_bucket = os.environ["S3_ASSET_AUXILIARY_BUCKET"]
    asset_upload_table_name = os.environ.get("ASSET_UPLOAD_TABLE_NAME")
    asset_links_table_name = os.environ.get("ASSET_LINKS_STORAGE_TABLE_NAME")
    asset_links_metadata_table_name = os.environ.get("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME")
    metadata_table_name = os.environ.get("METADATA_STORAGE_TABLE_NAME")
    asset_versions_table_name = os.environ.get("ASSET_VERSIONS_STORAGE_TABLE_NAME")
    asset_versions_files_table_name = os.environ.get("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME")
    comment_table_name = os.environ.get("COMMENT_STORAGE_TABLE_NAME")
    subscription_table_name = os.environ["SUBSCRIPTIONS_STORAGE_TABLE_NAME"]
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
    
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
buckets_table = dynamodb.Table(s3_asset_buckets_table)
asset_table = dynamodb.Table(asset_database)
db_table = dynamodb.Table(db_database)
asset_upload_table = dynamodb.Table(asset_upload_table_name) if asset_upload_table_name else None
asset_links_table = dynamodb.Table(asset_links_table_name) if asset_links_table_name else None
asset_links_metadata_table = dynamodb.Table(asset_links_metadata_table_name) if asset_links_metadata_table_name else None
metadata_table = dynamodb.Table(metadata_table_name) if metadata_table_name else None
versions_table = dynamodb.Table(asset_versions_table_name) if asset_versions_table_name else None
comment_table = dynamodb.Table(comment_table_name) if comment_table_name else None
asset_versions_files_table = dynamodb.Table(asset_versions_files_table_name) if asset_versions_files_table_name else None
subscription_table = dynamodb.Table(subscription_table_name) if subscription_table_name else None

#######################
# Version Functions
#######################

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
                Version=version_item.get('assetVersionId', '0'),
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

def mark_file_as_archived(key, bucket):
    """Mark an S3 object as archived by creating a delete marker
    
    Args:
        key: The S3 object key
        bucket: The S3 bucket name (optional, defaults to asset_bucket_name_default)
        
    Returns:
        The S3 response
    """
    # Use provided bucket or fall back to default
    
    # Delete the object to create a delete marker (archives it in versioned bucket)
    return s3.delete_object(
        Bucket=bucket,
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

def archive_multi_assetFiles(location, bucket):
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

def archive_file_preview(location, bucket):
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

def unarchive_multi_assetFiles(location, bucket):
    """Unarchive all files in a multi-file asset by removing delete markers
    
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
    
    logger.info(f'Unarchiving folder with multiple files from bucket: {bucket}')

    try:
        # List all versions to find delete markers
        paginator = s3.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            # Remove delete markers
            for delete_marker in page.get('DeleteMarkers', []):
                if delete_marker.get('IsLatest'):
                    logger.info(f"Removing delete marker for {delete_marker['Key']}")
                    s3.delete_object(
                        Bucket=bucket,
                        Key=delete_marker['Key'],
                        VersionId=delete_marker['VersionId']
                    )
    except Exception as e:
        logger.exception(f"Error unarchiving files: {e}")

    return

def unarchive_file_preview(location, bucket):
    """Unarchive a single file by removing delete marker
    
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
    
    logger.info(f"Unarchiving item: {bucket}:{key}")

    try:
        # List versions to find the delete marker
        response = s3.list_object_versions(Bucket=bucket, Prefix=key, MaxKeys=1)
        
        # Remove delete marker if it exists
        for delete_marker in response.get('DeleteMarkers', []):
            if delete_marker.get('IsLatest') and delete_marker['Key'] == key:
                logger.info(f"Removing delete marker for {key}")
                s3.delete_object(
                    Bucket=bucket,
                    Key=key,
                    VersionId=delete_marker['VersionId']
                )
    except Exception as e:
        logger.exception(f"Error unarchiving file {key}: {e}")
    
    return

def delete_s3_objects(prefix, bucket):
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
        
        # Delete the prefix folder itself if it still exists
        try:
            # Check if the prefix exists as a folder (with trailing slash)
            folder_key = prefix if prefix.endswith('/') else f"{prefix}/"
            s3.head_object(Bucket=bucket, Key=folder_key)
            # If head_object succeeds, the folder exists, so delete it
            s3.delete_object(Bucket=bucket, Key=folder_key)
            deleted_keys.append(folder_key)
            logger.info(f"Deleted prefix folder: {folder_key} from {bucket}")
        except ClientError as e:
            # If the folder doesn't exist, that's fine
            if e.response['Error']['Code'] != 'NoSuchKey':
                logger.warning(f"Error checking/deleting prefix folder: {e}")
    except Exception as e:
        logger.exception(f"Error deleting S3 objects with prefix {prefix}: {e}")
        raise VAMSGeneralErrorResponse(f"Error deleting S3 objects.")
    
    return deleted_keys

def delete_asset_link_metadata_for_permanent_deletion(asset_link_id: str):
    """Delete all metadata associated with an asset link during permanent asset deletion
    
    Args:
        asset_link_id: The asset link ID
    """
    if not asset_links_metadata_table:
        return
    
    try:
        # Query all metadata for this asset link
        response = asset_links_metadata_table.query(
            KeyConditionExpression=Key('assetLinkId').eq(asset_link_id)
        )
        
        # Delete all metadata items
        for item in response.get('Items', []):
            asset_links_metadata_table.delete_item(
                Key={
                    'assetLinkId': item['assetLinkId'],
                    'metadataKey': item['metadataKey']
                }
            )
        
        logger.info(f"Deleted {len(response.get('Items', []))} metadata items for asset link {asset_link_id}")
        
    except Exception as e:
        logger.exception(f"Error deleting asset link metadata: {e}")
        # Don't fail the whole operation if metadata deletion fails
        pass

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
        raise VAMSGeneralErrorResponse(f"Error retrieving asset.")

def get_assets(databaseId, query_params, showArchived=False):
    """Get assets for a database
    
    Args:
        databaseId: The database ID
        query_params: Query parameters for pagination
        showArchived: Whether to show archived assets
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    # If showArchived is True, we need to query both active and archived assets
    db_ids = [databaseId]
    if showArchived:
        db_ids.append(f"{databaseId}#deleted")
    
    all_items = []
    next_token = None
    
    # Query for each database ID (active and possibly archived)
    for db_id in db_ids:
        try:
            # Build query parameters
            query_params_dict = {
                'TableName': asset_database,
                'KeyConditionExpression': 'databaseId = :dbId',
                'ExpressionAttributeValues': {
                    ':dbId': {'S': db_id}
                },
                'ScanIndexForward': False,
                'Limit': int(query_params['pageSize'])
            }
            
            # Add ExclusiveStartKey if startingToken provided
            if query_params.get('startingToken'):
                try:
                    decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
                    query_params_dict['ExclusiveStartKey'] = json.loads(decoded_token)
                except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
                    logger.exception(f"Invalid startingToken format: {e}")
                    raise VAMSGeneralErrorResponse("Invalid pagination token")
            
            # Single query call with pagination
            response = dynamodb_client.query(**query_params_dict)
            
            # Process items and check permissions
            for item in response.get('Items', []):
                # Deserialize the item
                deserialized_item = {k: TypeDeserializer().deserialize(v) for k, v in item.items()}
                
                # Add status field for archived assets if not present
                if db_id.endswith('#deleted') and 'status' not in deserialized_item:
                    deserialized_item['status'] = 'archived'
                
                # Add object type for Casbin enforcement
                deserialized_item.update({"object__type": "asset"})
                
                # Check if user has permission to GET the asset
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(deserialized_item, "GET"):
                        all_items.append(deserialized_item)
            
            # Keep track of the next token from the last query (base64 encoded)
            if 'LastEvaluatedKey' in response:
                json_str = json.dumps(response['LastEvaluatedKey'])
                next_token = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
                
        except Exception as e:
            logger.exception(f"Error querying assets for database {db_id}: {e}")
            raise VAMSGeneralErrorResponse(f"Error retrieving assets.")
    
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
        # Build scan parameters
        scan_params = {
            'TableName': asset_database,
            'ScanFilter': filter_expression,
            'Limit': int(query_params['pageSize'])
        }
        
        # Add ExclusiveStartKey if startingToken provided
        if query_params.get('startingToken'):
            try:
                decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
                scan_params['ExclusiveStartKey'] = json.loads(decoded_token)
            except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
                logger.exception(f"Invalid startingToken format: {e}")
                raise VAMSGeneralErrorResponse("Invalid pagination token")
        
        # Single scan call with pagination
        response = dynamodb_client.scan(**scan_params)
        
        # Process results
        items = []
        
        for item in response.get('Items', []):
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
        
        # Build response with nextToken
        result = {'Items': items}
        
        # Return LastEvaluatedKey as nextToken if present (base64 encoded)
        if 'LastEvaluatedKey' in response:
            json_str = json.dumps(response['LastEvaluatedKey'])
            result['NextToken'] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
            
        return result
        
    except Exception as e:
        logger.exception(f"Error scanning all assets: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving all assets.")

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
        raise VAMSGeneralErrorResponse("Asset not found in database")
    
    # Check authorization
    asset.update({"object__type": "asset"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(asset, "PUT"):
            raise authorization_error()
    
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
        
        #send email for asset file change
        send_subscription_email(databaseId, assetId)

        return AssetOperationResponseModel(
            success=True,
            message="Asset updated successfully",
            assetId=assetId,
            operation="update",
            timestamp=timestamp
        )
    except Exception as e:
        logger.exception(f"Error updating asset: {e}")
        raise VAMSGeneralErrorResponse(f"Error updating asset.")

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
        raise VAMSGeneralErrorResponse("Asset not found in database")
    
    # Check if asset is already archived
    if databaseId.endswith('#deleted') or asset.get('status') == 'archived':
        raise VAMSGeneralErrorResponse(f"Asset {assetId} is already archived")
    
    # Check authorization
    asset.update({"object__type": "asset"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(asset, "DELETE"):
            raise authorization_error()
        
    #Get bucket details for asset
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    bucket_name = bucketDetails['bucketName']
    
    # Archive S3 files
    logger.info(f"Archiving asset {assetId} in database {databaseId}")
    
    try:
        # Archive asset files in S3
        if "assetLocation" in asset:
            archive_multi_assetFiles(asset['assetLocation'], bucket_name)

        # Archive preview if exists
        if "previewLocation" in asset:
            archive_file_preview(asset['previewLocation'], bucket_name)
        
        # Update asset record with archived status
        now = datetime.utcnow().isoformat()
        username = claims_and_roles.get("tokens", ["system"])[0]
        
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

        #send email for asset file change
        send_subscription_email(databaseId, assetId)
        
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
        raise VAMSGeneralErrorResponse(f"Error archiving asset.")

def unarchive_asset(databaseId, assetId, request_model, claims_and_roles):
    """Unarchive an asset (restore from soft delete)
    
    Args:
        databaseId: The database ID (may contain #deleted suffix)
        assetId: The asset ID
        request_model: UnarchiveAssetRequestModel with unarchive options
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        AssetOperationResponseModel with operation result
    """
    # Normalize databaseId - remove #deleted if present
    original_db_id = databaseId.replace("#deleted", "")
    archived_db_id = f"{original_db_id}#deleted"
    
    # Get the asset from archived location
    asset = get_asset_details(archived_db_id, assetId, showArchived=True)
    if not asset:
        # Try without #deleted suffix in case user provided clean ID
        asset = get_asset_details(original_db_id, assetId, showArchived=True)
        if not asset:
            raise VAMSGeneralErrorResponse("Asset not found")
        # If found in original location, check if it's actually archived
        if asset.get('status') != 'archived':
            raise VAMSGeneralErrorResponse("Asset is not archived")
    
    # Check authorization
    asset.update({"object__type": "asset"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(asset, "PUT"):
            raise authorization_error()
    
    # Get bucket details for asset
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    bucket_name = bucketDetails['bucketName']
    
    # Unarchive S3 files
    logger.info(f"Unarchiving asset {assetId} from database {archived_db_id}")
    
    try:
        # Unarchive asset files in S3
        if "assetLocation" in asset:
            unarchive_multi_assetFiles(asset['assetLocation'], bucket_name)

        # Unarchive preview if exists
        if "previewLocation" in asset:
            unarchive_file_preview(asset['previewLocation'], bucket_name)
        
        # Update asset record
        now = datetime.utcnow().isoformat()
        username = claims_and_roles.get("tokens", ["system"])[0]
        
        # Remove archive metadata - INCLUDING status field
        asset.pop('status', None)  # Remove status entirely
        asset.pop('archivedAt', None)
        asset.pop('archivedBy', None)
        asset.pop('archivedReason', None)
        
        # Add unarchive metadata
        asset['unarchivedAt'] = now
        asset['unarchivedBy'] = username
        if request_model.reason:
            asset['unarchivedReason'] = request_model.reason
        
        # Move back to original database ID
        asset['databaseId'] = original_db_id
        
        # Save to original location
        asset_table.put_item(Item=asset)
        
        # Delete from archived location
        asset_table.delete_item(Key={'databaseId': archived_db_id, 'assetId': assetId})
        
        # Update asset count
        update_asset_count(db_database, asset_database, {}, original_db_id)

        # Send email notification
        send_subscription_email(original_db_id, assetId)
        
        # Return success response
        return AssetOperationResponseModel(
            success=True,
            message=f"Asset {assetId} unarchived successfully",
            assetId=assetId,
            operation="unarchive",
            timestamp=now
        )
    except Exception as e:
        logger.exception(f"Error unarchiving asset: {e}")
        raise VAMSGeneralErrorResponse("Error unarchiving asset")

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
        raise VAMSGeneralErrorResponse("Asset not found in database")
    
    # Check authorization
    asset.update({"object__type": "asset"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(asset, "DELETE"):
            raise authorization_error()
    
    #Get bucket details for asset
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    bucket_name = bucketDetails['bucketName']
    
    # Begin deletion process
    logger.info(f"Permanently deleting asset {assetId} from database {databaseId}")
    
    try:
        # Track what was deleted for the response
        deleted_items = {
            "s3_objects": [],
            "dynamodb_tables": []
        }

        # Delete SNS topic if it exists
        if 'snsTopic' in asset and asset['snsTopic']:
            try:
                sns_topic_arn = asset['snsTopic']
                logger.info(f"Deleting SNS topic: {sns_topic_arn}")
                sns_client.delete_topic(TopicArn=sns_topic_arn)
                logger.info(f"Successfully deleted SNS topic: {sns_topic_arn}")
            except Exception as e:
                # Log the error but continue with deletion process
                logger.warning(f"Error deleting SNS topic for asset {assetId}: {e}")
        
        # Delete subscription record if it exists
        if subscription_table:
            try:
                # Delete the subscription record where eventName is 'Asset Version Change' and entityName_entityId is 'Asset#{assetId}'
                subscription_table.delete_item(
                    Key={
                        'eventName': 'Asset Version Change',
                        'entityName_entityId': f'Asset#{assetId}'
                    }
                )
                logger.info(f"Successfully deleted subscription record for asset {assetId}")
            except Exception as e:
                # Log the error but continue with deletion process
                logger.warning(f"Error deleting subscription record for asset {assetId}: {e}")

        #send email for asset file change
        send_subscription_email(databaseId, assetId)
        
        # 1. Delete all S3 objects (assets files and preview)
        if "assetLocation" in asset and "Key" in asset["assetLocation"]:
            prefix = asset["assetLocation"]["Key"]
            if prefix:
                # Get bucket from assetLocation or use default
                logger.info(f"Deleting S3 asset objects and all versions with prefix {prefix} from bucket {bucket_name}")
                
                # Delete all objects and all versions with this prefix
                deleted_keys = delete_s3_prefix_all_versions(bucket_name, prefix)
                deleted_items["s3_objects"].extend(deleted_keys)
                
                # Also delete any auxiliary files
                delete_assetAuxiliary_files(asset["assetLocation"])

        if "previewLocation" in asset and "Key" in asset["previewLocation"]:
            prefix = asset["previewLocation"]["Key"]
            if prefix:
                # Get bucket from assetLocation or use default
                logger.info(f"Deleting S3 preview objects and all versions with prefix {prefix} from bucket {bucket_name}")
                
                # Delete all objects and all versions with this prefix
                deleted_keys = delete_s3_prefix_all_versions(bucket_name, prefix)
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
            # Query and delete links where this asset is the source
            try:
                response = asset_links_table.query(
                    KeyConditionExpression=Key('assetIdFrom').eq(assetId)
                )
                
                for item in response.get('Items', []):
                    if 'assetIdTo' in item:
                        # Delete associated metadata first
                        if 'assetLinkId' in item:
                            delete_asset_link_metadata_for_permanent_deletion(item['assetLinkId'])
                        
                        # Then delete the link
                        asset_links_table.delete_item(Key={
                            'assetIdFrom': assetId,
                            'assetIdTo': item['assetIdTo']
                        })
                        deleted_items["dynamodb_tables"].append(f"{asset_links_table_name} (assetIdFrom={assetId}, assetIdTo={item['assetIdTo']})")
            except Exception as e:
                logger.warning(f"Error deleting asset links where asset is source: {e}")
            
            # Query and delete links where this asset is the target
            # This requires using a GSI, so we need to query first
            try:
                response = asset_links_table.query(
                    IndexName='AssetIdToGSI',
                    KeyConditionExpression=Key('assetIdTo').eq(assetId)
                )
                
                for item in response.get('Items', []):
                    if 'assetIdFrom' in item:
                        # Delete associated metadata first
                        if 'assetLinkId' in item:
                            delete_asset_link_metadata_for_permanent_deletion(item['assetLinkId'])
                        
                        # Then delete the link
                        asset_links_table.delete_item(Key={
                            'assetIdFrom': item['assetIdFrom'],
                            'assetIdTo': assetId
                        })
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
        raise VAMSGeneralErrorResponse(f"Error permanently deleting asset.")

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
                # Handle boolean parameter conversion for showArchived
                if 'showArchived' in query_parameters:
                    show_archived_value = query_parameters['showArchived']
                    if isinstance(show_archived_value, str):
                        if show_archived_value.lower() in ['true', '1', 'yes']:
                            query_parameters['showArchived'] = True
                        elif show_archived_value.lower() in ['false', '0', 'no', '']:
                            query_parameters['showArchived'] = False
                        else:
                            logger.error(f"Invalid showArchived parameter: {show_archived_value}")
                            return validation_error(body={'message': "showArchived parameter must be a valid boolean value (true/false)"})
                
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

                #Get bucket details for asset
                bucketDetails = get_default_bucket_details(asset['bucketId'])
                enhanced_asset["bucketName"] = bucketDetails['bucketName']
                
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
                # Handle boolean parameter conversion for showArchived
                if 'showArchived' in query_parameters:
                    show_archived_value = query_parameters['showArchived']
                    if isinstance(show_archived_value, str):
                        if show_archived_value.lower() in ['true', '1', 'yes']:
                            query_parameters['showArchived'] = True
                        elif show_archived_value.lower() in ['false', '0', 'no', '']:
                            query_parameters['showArchived'] = False
                        else:
                            logger.error(f"Invalid showArchived parameter: {show_archived_value}")
                            return validation_error(body={'message': "showArchived parameter must be a valid boolean value (true/false)"})
                
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
                error_msg = str(v)
                return validation_error(body={'message': f"Invalid parameter: {error_msg}"})
                # # Fall back to default pagination with validation
                # validate_pagination_info(query_parameters)
                # query_params = query_parameters
                # show_archived = query_parameters.get('showArchived', '').lower() == 'true'
            
            # Get the assets
            assets_result = get_assets(path_parameters['databaseId'], query_params, show_archived)
            
            # Enhance each asset with version information
            enhanced_items = []
            for item in assets_result.get('Items', []):
                enhanced_item = enhance_asset_with_version_info(item)

                #Get bucket details for asset
                bucketDetails = get_default_bucket_details(enhanced_item['bucketId'])
                enhanced_item["bucketName"] = bucketDetails['bucketName']

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
                # Handle boolean parameter conversion for showArchived
                if 'showArchived' in query_parameters:
                    show_archived_value = query_parameters['showArchived']
                    if isinstance(show_archived_value, str):
                        if show_archived_value.lower() in ['true', '1', 'yes']:
                            query_parameters['showArchived'] = True
                        elif show_archived_value.lower() in ['false', '0', 'no', '']:
                            query_parameters['showArchived'] = False
                        else:
                            logger.error(f"Invalid showArchived parameter: {show_archived_value}")
                            return validation_error(body={'message': "showArchived parameter must be a valid boolean value (true/false)"})
                
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
                error_msg = str(v)
                return validation_error(body={'message': f"Invalid parameter: {error_msg}"})
                # # Fall back to default pagination with validation
                # validate_pagination_info(query_parameters)
                # query_params = query_parameters
                # show_archived = query_parameters.get('showArchived', '').lower() == 'true'
            
            # Get all assets
            assets_result = get_all_assets(query_params, show_archived)
            
            # Enhance each asset with version information
            enhanced_items = []
            for item in assets_result.get('Items', []):
                enhanced_item = enhance_asset_with_version_info(item)

                #Get bucket details for asset
                bucketDetails = get_default_bucket_details(enhanced_item['bucketId'])
                enhanced_item["bucketName"] = bucketDetails['bucketName']

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
    path = event['requestContext']['http']['path']
    path_parameters = event.get('pathParameters', {})
    
    # Validate required path parameters
    if 'databaseId' not in path_parameters:
        return validation_error(body={'message': "No database ID in API Call"})
    
    if 'assetId' not in path_parameters:
        return validation_error(body={'message': "No asset ID in API Call"})
    
    # Normalize databaseId for validation - remove #deleted suffix if present
    normalized_database_id = path_parameters['databaseId'].replace("#deleted", "")
    
    # Validate path parameters
    (valid, message) = validate({
        'databaseId': {
            'value': normalized_database_id,
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
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Check if this is an unarchive request
        if path.endswith('/unarchiveAsset'):
            request_model = parse(body, model=UnarchiveAssetRequestModel)
            result = unarchive_asset(
                path_parameters['databaseId'],
                path_parameters['assetId'],
                request_model,
                claims_and_roles
            )
            return success(body=result.dict())
        
        # Otherwise, handle regular update
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
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
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
