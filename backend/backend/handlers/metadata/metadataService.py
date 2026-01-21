# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Centralized metadata service handler for VAMS - Handles metadata across all entity types."""

import os
import boto3
import json
import base64
from datetime import datetime
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
from customLogging.logger import safeLogger
from common.metadataSchemaValidation import (
    get_aggregated_schemas,
    validate_metadata_against_schema,
    validate_metadata_keys_against_schema,
    enrich_metadata_with_schema
)
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.metadata import (
    # Asset Link Metadata Models
    AssetLinkMetadataPathRequestModel,
    GetAssetLinkMetadataRequestModel,
    CreateAssetLinkMetadataRequestModel,
    UpdateAssetLinkMetadataRequestModel,
    DeleteAssetLinkMetadataRequestModel,
    AssetLinkMetadataResponseModel,
    GetAssetLinkMetadataResponseModel,
    # Asset Metadata Models
    AssetMetadataPathRequestModel,
    GetAssetMetadataRequestModel,
    CreateAssetMetadataRequestModel,
    UpdateAssetMetadataRequestModel,
    DeleteAssetMetadataRequestModel,
    AssetMetadataResponseModel,
    GetAssetMetadataResponseModel,
    # File Metadata Models
    FileMetadataPathRequestModel,
    GetFileMetadataRequestModel,
    CreateFileMetadataRequestModel,
    UpdateFileMetadataRequestModel,
    DeleteFileMetadataRequestModel,
    FileMetadataResponseModel,
    GetFileMetadataResponseModel,
    # Database Metadata Models
    DatabaseMetadataPathRequestModel,
    GetDatabaseMetadataRequestModel,
    CreateDatabaseMetadataRequestModel,
    UpdateDatabaseMetadataRequestModel,
    DeleteDatabaseMetadataRequestModel,
    DatabaseMetadataResponseModel,
    GetDatabaseMetadataResponseModel,
    # Common Models
    BulkOperationResponseModel,
    MetadataValueType,
    UpdateType
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

region = os.environ.get('AWS_REGION', 'us-east-1')
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="MetadataService")

# Global variables for claims and roles
claims_and_roles = {}

# Constants
MAX_METADATA_RECORDS_PER_ENTITY = 500

# Load environment variables
try:
    asset_links_table_v2_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    asset_links_metadata_table_name = os.environ["ASSET_LINKS_METADATA_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    database_storage_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    database_metadata_table_name = os.environ["DATABASE_METADATA_STORAGE_TABLE_NAME"]
    asset_file_metadata_table_name = os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"]
    file_attribute_table_name = os.environ["FILE_ATTRIBUTE_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    metadata_schema_table_v2_name = os.environ["METADATA_SCHEMA_STORAGE_TABLE_V2_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_links_table = dynamodb.Table(asset_links_table_v2_name)
asset_links_metadata_table = dynamodb.Table(asset_links_metadata_table_name)
asset_storage_table = dynamodb.Table(asset_storage_table_name)
database_storage_table = dynamodb.Table(database_storage_table_name)
database_metadata_table = dynamodb.Table(database_metadata_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name)
file_attribute_table = dynamodb.Table(file_attribute_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)

#######################
# Common Utility Functions
#######################

def get_bucket_details(bucket_id: str) -> dict:
    """Get S3 bucket details from buckets table
    
    Args:
        bucket_id: The bucket ID
        
    Returns:
        Dictionary with bucketName and baseAssetsPrefix
    """
    try:
        response = s3_asset_buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucket_id),
            Limit=1
        )
        bucket = response.get("Items", [{}])[0] if response.get("Items") else {}
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix', '/')
        
        if not bucket_name:
            raise VAMSGeneralErrorResponse("Bucket configuration not found")
        
        # Ensure prefix ends with slash
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'
        
        # Remove leading slash
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]
        
        return {
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving bucket configuration")


def check_entity_authorization(entity: dict, action: str, claims_and_roles: dict) -> bool:
    """Check if user has permission to perform action on entity
    
    Args:
        entity: The entity dictionary with object__type
        action: The action to check (GET, POST, PUT, DELETE)
        claims_and_roles: User claims and roles
        
    Returns:
        True if authorized, False otherwise
    """
    try:
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            return casbin_enforcer.enforce(entity, action)
        return False
    except Exception as e:
        logger.exception(f"Error checking authorization: {e}")
        return False


def check_multi_action_authorization(entity: dict, actions: list, claims_and_roles: dict) -> bool:
    """Check if user has ALL specified permissions on entity
    
    Args:
        entity: The entity dictionary with object__type
        actions: List of actions to check (e.g., ["PUT", "POST", "DELETE"])
        claims_and_roles: User claims and roles
        
    Returns:
        True if user has all permissions, False otherwise
    """
    try:
        if len(claims_and_roles.get("tokens", [])) == 0:
            return False
        
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        for action in actions:
            if not casbin_enforcer.enforce(entity, action):
                logger.warning(f"User lacks {action} permission for entity")
                return False
        
        return True
    except Exception as e:
        logger.exception(f"Error checking multi-action authorization: {e}")
        return False


#######################
# Entity Validation Functions
#######################

def validate_asset_link_exists(asset_link_id: str) -> dict:
    """Validate that an asset link exists and return it
    
    Args:
        asset_link_id: The asset link ID
        
    Returns:
        The asset link dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If asset link not found
    """
    try:
        response = asset_links_table.get_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Asset link not found")
        
        return response['Item']
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating asset link: {e}")
        raise VAMSGeneralErrorResponse("Error validating asset link")


def validate_asset_exists(database_id: str, asset_id: str) -> dict:
    """Validate that an asset exists and return it
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        
    Returns:
        The asset dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If asset not found
    """
    try:
        response = asset_storage_table.get_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            }
        )
        
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Asset not found")
        
        return response['Item']
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating asset: {e}")
        raise VAMSGeneralErrorResponse("Error validating asset")


def validate_file_exists(database_id: str, asset_id: str, file_path: str) -> bool:
    """Validate that a file exists in S3
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        file_path: The relative file path (with leading slash)
        
    Returns:
        True if file exists
        
    Raises:
        VAMSGeneralErrorResponse: If file not found or validation fails
    """
    try:
        # First get the asset to get bucket and location information
        asset = validate_asset_exists(database_id, asset_id)
        
        # Get bucket details
        bucket_details = get_bucket_details(asset['bucketId'])
        bucket_name = bucket_details['bucketName']
        
        # Get the asset location from the asset details
        if 'assetLocation' not in asset or 'Key' not in asset['assetLocation']:
            raise VAMSGeneralErrorResponse("Asset location not found")
        
        # Use the asset's actual location as the base path
        asset_base_path = asset['assetLocation']['Key']
        
        # Ensure asset base path ends with slash
        if not asset_base_path.endswith('/'):
            asset_base_path += '/'
        
        # Remove leading slash from file_path before combining (to avoid double slash)
        normalized_file_path = file_path.lstrip('/')
        
        # Construct full S3 key using asset's actual location
        full_key = f"{asset_base_path}{normalized_file_path}"
        
        # Check if file exists in S3
        try:
            s3.head_object(Bucket=bucket_name, Key=full_key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey' or e.response['Error']['Code'] == '404':
                raise VAMSGeneralErrorResponse("File not found in S3")
            raise
            
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating file: {e}")
        raise VAMSGeneralErrorResponse("Error validating file")


def validate_database_exists(database_id: str) -> dict:
    """Validate that a database exists and return it
    
    Args:
        database_id: The database ID
        
    Returns:
        The database dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If database not found
    """
    try:
        response = database_storage_table.get_item(
            Key={'databaseId': database_id}
        )
        
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Database not found")
        
        return response['Item']
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating database: {e}")
        raise VAMSGeneralErrorResponse("Error validating database")


def get_database_config(database_id: str) -> dict:
    """Get database configuration including restrictMetadataOutsideSchemas
    
    This is an alias for validate_database_exists that emphasizes
    retrieving configuration settings.
    
    Args:
        database_id: The database ID
        
    Returns:
        Database configuration dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If database not found
    """
    return validate_database_exists(database_id)


#######################
# Asset Link Metadata CRUD Operations
#######################

def get_asset_link_metadata(asset_link_id: str, query_params: dict, claims_and_roles: dict) -> GetAssetLinkMetadataResponseModel:
    """Get metadata for an asset link - Returns ALL records (pagination ignored)
    
    Args:
        asset_link_id: The asset link ID
        query_params: Query parameters (ignored - for backward compatibility)
        claims_and_roles: User claims and roles
        
    Returns:
        GetAssetLinkMetadataResponseModel with ALL metadata records
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        if not (check_entity_authorization(from_asset, "GET", claims_and_roles) and 
                check_entity_authorization(to_asset, "GET", claims_and_roles)):
            raise PermissionError("Not authorized to view metadata for this asset link")
        
        # Fetch ALL metadata using paginator (ignore query_params pagination)
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_links_metadata_table_name,
            KeyConditionExpression='assetLinkId = :linkId',
            ExpressionAttributeValues={':linkId': {'S': asset_link_id}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            metadata_list.append(deserialized_item)
        
        # Fetch database configs and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            # Get schemas from both databases + GLOBAL
            database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
            # Remove duplicates while preserving order
            database_ids = list(dict.fromkeys(database_ids))
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetLinkMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            # For asset links: true if EITHER database has restriction AND schemas exist
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    from_db_config = get_database_config(asset_link['fromAssetDatabaseId'])
                    to_db_config = get_database_config(asset_link['toAssetDatabaseId'])
                    
                    from_db_restricts = from_db_config.get('restrictMetadataOutsideSchemas', False) == True
                    to_db_restricts = to_db_config.get('restrictMetadataOutsideSchemas', False) == True
                    
                    restrict_metadata_outside_schemas = from_db_restricts or to_db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database configs for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)
            
            # Convert to response models
            response_models = []
            for item in enriched_metadata:
                response_models.append(AssetLinkMetadataResponseModel(
                    assetLinkId=item.get('assetLinkId', asset_link_id),
                    metadataKey=item['metadataKey'],
                    metadataValue=item['metadataValue'],
                    metadataValueType=item['metadataValueType'],
                    metadataSchemaName=item.get('metadataSchemaName'),
                    metadataSchemaField=item.get('metadataSchemaField'),
                    metadataSchemaRequired=item.get('metadataSchemaRequired'),
                    metadataSchemaSequence=item.get('metadataSchemaSequence'),
                    metadataSchemaDefaultValue=item.get('metadataSchemaDefaultValue'),
                    metadataSchemaDependsOn=item.get('metadataSchemaDependsOn'),
                    metadataSchemaMultiFieldConflict=item.get('metadataSchemaMultiFieldConflict'),
                    metadataSchemaControlledListKeys=item.get('metadataSchemaControlledListKeys')
                ))
            
            metadata_list = response_models
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = [AssetLinkMetadataResponseModel(
                assetLinkId=item['assetLinkId'],
                metadataKey=item['metadataKey'],
                metadataValue=item['metadataValue'],
                metadataValueType=item['metadataValueType']
            ) for item in metadata_list]
            restrict_metadata_outside_schemas = False
        
        # Build response (NextToken always empty/None)
        result = GetAssetLinkMetadataResponseModel(
            metadata=metadata_list,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas
        )
        # NextToken is always None (no pagination)
        
        return result
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def create_asset_link_metadata(asset_link_id: str, request_model: CreateAssetLinkMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Create metadata for an asset link (bulk operation) - Now supports upsert (create or update)
    
    Args:
        asset_link_id: The asset link ID
        request_model: The create request model with metadata items
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        if not (check_entity_authorization(from_asset, "POST", claims_and_roles) and 
                check_entity_authorization(to_asset, "POST", claims_and_roles)):
            raise PermissionError("Not authorized to create metadata for this asset link")
        
        # Validate 500 record limit: Fetch existing + count with new
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_links_metadata_table_name,
                KeyConditionExpression='assetLinkId = :linkId',
                ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_keys.add(deserialized['metadataKey'])
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        if not skip_schema_validation:
            try:
                # Get schemas from both databases + GLOBAL
                database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
                database_ids = list(dict.fromkeys(database_ids))  # Remove duplicates
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetLinkMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_links_metadata_table_name,
                    KeyConditionExpression='assetLinkId = :linkId',
                    ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    existing_metadata[deserialized['metadataKey']] = {
                        'metadataValue': deserialized['metadataValue'],
                        'metadataValueType': deserialized['metadataValueType']
                    }
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    from_db_config = get_database_config(asset_link['fromAssetDatabaseId'])
                    to_db_config = get_database_config(asset_link['toAssetDatabaseId'])
                    restrict = (from_db_config.get('restrictMetadataOutsideSchemas', False) or 
                               to_db_config.get('restrictMetadataOutsideSchemas', False))
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields)
                updated_metadata = []
                for item in request_model.metadata:
                    updated_metadata.append(item)
                
                # Add any new fields with defaults that weren't in the request
                for key, value_dict in metadata_with_defaults.items():
                    if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                        from models.metadata import MetadataItemModel
                        updated_metadata.append(MetadataItemModel(
                            metadataKey=key,
                            metadataValue=value_dict['metadataValue'],
                            metadataValueType=value_dict['metadataValueType']
                        ))
                request_model.metadata = updated_metadata
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        # Process metadata items in bulk - UNIFIED UPSERT (create or update)
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update) - FIX: Use DynamoDB typed format
                item = {
                    'assetLinkId': {'S': asset_link_id},
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_links_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    # Mark all items in this batch as failed
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadata)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_asset_link_metadata(asset_link_id: str, request_model: UpdateAssetLinkMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Update metadata for an asset link (bulk operation) - Supports UPDATE and REPLACE_ALL modes
    
    Args:
        asset_link_id: The asset link ID
        request_model: The update request model with metadata items and updateType
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not (check_multi_action_authorization(from_asset, ["PUT", "POST", "DELETE"], claims_and_roles) and 
                    check_multi_action_authorization(to_asset, ["PUT", "POST", "DELETE"], claims_and_roles)):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions on both assets")
        else:
            # UPDATE mode requires only PUT permission
            if not (check_entity_authorization(from_asset, "PUT", claims_and_roles) and 
                    check_entity_authorization(to_asset, "PUT", claims_and_roles)):
                raise PermissionError("Not authorized to update metadata for this asset link")
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this asset link
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_links_metadata_table_name,
                    KeyConditionExpression='assetLinkId = :linkId',
                    ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    existing_metadata[deserialized['metadataKey']] = {
                        'metadataValue': deserialized['metadataValue'],
                        'metadataValueType': deserialized['metadataValueType']
                    }
                
                # Validate 500 record limit based on updateType
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
                database_ids = list(dict.fromkeys(database_ids))
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetLinkMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT"
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_asset_link_metadata(asset_link_id, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_asset_link_metadata(asset_link_id, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_asset_link_metadata(asset_link_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert asset link metadata (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update) - Use DynamoDB typed format
                item = {
                    'assetLinkId': {'S': asset_link_id},
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_links_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_asset_link_metadata(asset_link_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all asset link metadata with rollback on failure"""
    try:
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_links_metadata_table_name,
            KeyConditionExpression='assetLinkId = :linkId',
            ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        existing_keys = {item['metadataKey'] for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if item['metadataKey'] in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'assetLinkId': {'S': asset_link_id},
                            'metadataKey': {'S': key}
                        }
                    }
                })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_links_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error deleting metadata in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse("Failed to delete existing metadata")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            for metadata_item in metadata_items:
                # Use DynamoDB typed format
                item = {
                    'assetLinkId': {'S': asset_link_id},
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                dynamodb_client.batch_write_item(
                    RequestItems={
                        asset_links_metadata_table_name: batch
                    }
                )
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all metadata: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items - Use DynamoDB typed format
                    items_to_restore = []
                    for item in deleted_items_backup:
                        restore_item = {
                            'assetLinkId': {'S': item['assetLinkId']},
                            'metadataKey': {'S': item['metadataKey']},
                            'metadataValue': {'S': item['metadataValue']},
                            'metadataValueType': {'S': item['metadataValueType']}
                        }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        dynamodb_client.batch_write_item(
                            RequestItems={
                                asset_links_metadata_table_name: batch
                            }
                        )
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    raise VAMSGeneralErrorResponse("REPLACE_ALL operation failed, all changes rolled back successfully")
                    
                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                        "Please contact administrator."
                    )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_asset_link_metadata(asset_link_id: str, request_model: DeleteAssetLinkMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Delete metadata for an asset link (bulk operation)
    
    Args:
        asset_link_id: The asset link ID
        request_model: The delete request model with metadata keys
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        if not (check_entity_authorization(from_asset, "DELETE", claims_and_roles) and 
                check_entity_authorization(to_asset, "DELETE", claims_and_roles)):
            raise PermissionError("Not authorized to delete metadata for this asset link")
        
        # NEW: Schema validation for deletion
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_links_metadata_table_name,
                KeyConditionExpression='assetLinkId = :linkId',
                ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_metadata[deserialized['metadataKey']] = {
                    'metadataValue': deserialized['metadataValue'],
                    'metadataValueType': deserialized['metadataValueType']
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
            database_ids = list(dict.fromkeys(database_ids))  # Remove duplicates
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetLinkMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                error_message = "Deletion validation failed: " + "; ".join(validation_errors)
                raise VAMSGeneralErrorResponse(error_message)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error during deletion validation: {e}")
            # Continue without validation if it fails
        
        # Process metadata keys
        successful_items = []
        failed_items = []
        
        # Use batch write for efficiency
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if metadata exists
                existing_response = asset_links_metadata_table.get_item(
                    Key={
                        'assetLinkId': asset_link_id,
                        'metadataKey': metadata_key
                    }
                )
                
                if 'Item' not in existing_response:
                    failed_items.append({
                        'key': metadata_key,
                        'error': 'Metadata key not found'
                    })
                    continue
                
                # Prepare item for batch delete
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'assetLinkId': {'S': asset_link_id},
                            'metadataKey': {'S': metadata_key}
                        }
                    }
                })
                successful_items.append(metadata_key)
                
            except Exception as e:
                logger.warning(f"Error preparing delete for metadata key {metadata_key}: {e}")
                failed_items.append({
                    'key': metadata_key,
                    'error': str(e)
                })
        
        # Delete items in batches of 25
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_links_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    # Mark all items in this batch as failed
                    for item in batch:
                        key = item['DeleteRequest']['Key']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch delete failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadataKeys)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - Asset Link Metadata
#######################

def handle_asset_link_metadata_get(event):
    """Handle GET requests for asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse query parameters
        try:
            query_request_model = parse(query_parameters, model=GetAssetLinkMetadataRequestModel)
            query_params = {
                'pageSize': query_request_model.pageSize,
                'startingToken': query_request_model.startingToken
            }
        except ValidationError as v:
            logger.exception(f"Validation error in query parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Get metadata
        response = get_asset_link_metadata(path_request_model.assetLinkId, query_params, claims_and_roles)
        return success(body=response.dict())
        
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_asset_link_metadata_post(event):
    """Handle POST requests to create asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetLinkMetadataRequestModel)
        
        # Create metadata
        response = create_asset_link_metadata(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_asset_link_metadata_put(event):
    """Handle PUT requests to update asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateAssetLinkMetadataRequestModel)
        
        # Update metadata
        response = update_asset_link_metadata(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_asset_link_metadata_delete(event):
    """Handle DELETE requests to delete asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=DeleteAssetLinkMetadataRequestModel)
        
        # Delete metadata
        response = delete_asset_link_metadata(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# Asset Metadata CRUD Operations
#######################

def get_asset_metadata(database_id: str, asset_id: str, query_params: dict, claims_and_roles: dict) -> GetAssetMetadataResponseModel:
    """Get metadata for an asset - Returns ALL records (pagination ignored)
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        query_params: Query parameters (ignored - for backward compatibility)
        claims_and_roles: User claims and roles
        
    Returns:
        GetAssetMetadataResponseModel with ALL metadata records
    """
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this asset")
        
        # Build composite key for query
        composite_key = f"{database_id}:{asset_id}:/"
        
        # Fetch ALL metadata using paginator (ignore query_params pagination)
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_file_metadata_table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            metadata_list.append(deserialized_item)
        
        # Fetch database config and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)
            
            # Convert to response models
            response_models = []
            for item in enriched_metadata:
                response_models.append(AssetMetadataResponseModel(
                    databaseId=database_id,
                    assetId=asset_id,
                    metadataKey=item['metadataKey'],
                    metadataValue=item['metadataValue'],
                    metadataValueType=item['metadataValueType'],
                    metadataSchemaName=item.get('metadataSchemaName'),
                    metadataSchemaField=item.get('metadataSchemaField'),
                    metadataSchemaRequired=item.get('metadataSchemaRequired'),
                    metadataSchemaSequence=item.get('metadataSchemaSequence'),
                    metadataSchemaDefaultValue=item.get('metadataSchemaDefaultValue'),
                    metadataSchemaDependsOn=item.get('metadataSchemaDependsOn'),
                    metadataSchemaMultiFieldConflict=item.get('metadataSchemaMultiFieldConflict'),
                    metadataSchemaControlledListKeys=item.get('metadataSchemaControlledListKeys')
                ))
            
            metadata_list = response_models
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = [AssetMetadataResponseModel(
                databaseId=database_id,
                assetId=asset_id,
                metadataKey=item['metadataKey'],
                metadataValue=item['metadataValue'],
                metadataValueType=item['metadataValueType']
            ) for item in metadata_list]
            restrict_metadata_outside_schemas = False
        
        # Build response (NextToken always empty/None)
        result = GetAssetMetadataResponseModel(
            metadata=metadata_list,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas
        )
        # NextToken is always None (no pagination)
        
        return result
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def create_asset_metadata(database_id: str, asset_id: str, request_model: CreateAssetMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Create metadata for an asset (bulk operation)
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        request_model: The create request model with metadata items
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "POST", claims_and_roles):
            raise PermissionError("Not authorized to create metadata for this asset")
        
        # Validate 500 record limit: Fetch existing + count with new
        composite_key = f"{database_id}:{asset_id}:/"
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_file_metadata_table_name,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_keys.add(deserialized['metadataKey'])
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        if not skip_schema_validation:
            try:
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_file_metadata_table_name,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    existing_metadata[deserialized['metadataKey']] = {
                        'metadataValue': deserialized['metadataValue'],
                        'metadataValueType': deserialized['metadataValueType']
                    }
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields)
                updated_metadata = []
                for item in request_model.metadata:
                    updated_metadata.append(item)
                
                # Add any new fields with defaults that weren't in the request
                for key, value_dict in metadata_with_defaults.items():
                    if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                        from models.metadata import MetadataItemModel
                        updated_metadata.append(MetadataItemModel(
                            metadataKey=key,
                            metadataValue=value_dict['metadataValue'],
                            metadataValueType=value_dict['metadataValueType']
                        ))
                request_model.metadata = updated_metadata
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        # Process metadata items in bulk
        successful_items = []
        failed_items = []
        asset_composite_key = f"{database_id}:{asset_id}"
        
        # Prepare items for batch write
        items_to_write = []
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId:assetId:filePath': {'S': composite_key},
                    'databaseId:assetId': {'S': asset_composite_key},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_file_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadata)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Created {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_asset_metadata(database_id: str, asset_id: str, request_model: UpdateAssetMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Update metadata for an asset (bulk operation) - Supports UPDATE and REPLACE_ALL modes
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        request_model: The update request model with metadata items and updateType
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not check_multi_action_authorization(asset, ["PUT", "POST", "DELETE"], claims_and_roles):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions")
        else:
            # UPDATE mode requires only PUT permission
            if not check_entity_authorization(asset, "PUT", claims_and_roles):
                raise PermissionError("Not authorized to update metadata for this asset")
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        composite_key = f"{database_id}:{asset_id}:/"
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this asset
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_file_metadata_table_name,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    existing_metadata[deserialized['metadataKey']] = {
                        'metadataValue': deserialized['metadataValue'],
                        'metadataValueType': deserialized['metadataValueType']
                    }
                
                # Validate 500 record limit based on updateType
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            metadata_to_validate, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_asset_metadata(database_id, asset_id, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_asset_metadata(database_id, asset_id, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_asset_metadata(database_id: str, asset_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert asset metadata (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        composite_key = f"{database_id}:{asset_id}:/"
        asset_composite_key = f"{database_id}:{asset_id}"
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId:assetId:filePath': {'S': composite_key},
                    'databaseId:assetId': {'S': asset_composite_key},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_file_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_asset_metadata(database_id: str, asset_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all asset metadata with rollback on failure"""
    try:
        composite_key = f"{database_id}:{asset_id}:/"
        
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_file_metadata_table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        existing_keys = {item['metadataKey'] for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if item['metadataKey'] in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': key},
                            'databaseId:assetId:filePath': {'S': composite_key}
                        }
                    }
                })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_file_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error deleting metadata in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse("Failed to delete existing metadata")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            asset_composite_key = f"{database_id}:{asset_id}"
            for metadata_item in metadata_items:
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId:assetId:filePath': {'S': composite_key},
                    'databaseId:assetId': {'S': asset_composite_key},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                dynamodb_client.batch_write_item(
                    RequestItems={
                        asset_file_metadata_table_name: batch
                    }
                )
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all metadata: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items
                    items_to_restore = []
                    for item in deleted_items_backup:
                        restore_item = {
                            'metadataKey': {'S': item['metadataKey']},
                            'databaseId:assetId:filePath': {'S': composite_key},
                            'databaseId:assetId': {'S': asset_composite_key},
                            'metadataValue': {'S': item['metadataValue']},
                            'metadataValueType': {'S': item['metadataValueType']}
                        }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        dynamodb_client.batch_write_item(
                            RequestItems={
                                asset_file_metadata_table_name: batch
                            }
                        )
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    raise VAMSGeneralErrorResponse("REPLACE_ALL operation failed, all changes rolled back successfully")
                    
                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                        "Please contact administrator."
                    )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_asset_metadata(database_id: str, asset_id: str, request_model: DeleteAssetMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Delete metadata for an asset (bulk operation)
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        request_model: The delete request model with metadata keys
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "DELETE", claims_and_roles):
            raise PermissionError("Not authorized to delete metadata for this asset")
        
        # NEW: Schema validation for deletion
        composite_key = f"{database_id}:{asset_id}:/"
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_file_metadata_table_name,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_metadata[deserialized['metadataKey']] = {
                    'metadataValue': deserialized['metadataValue'],
                    'metadataValueType': deserialized['metadataValueType']
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                error_message = "Deletion validation failed: " + "; ".join(validation_errors)
                raise VAMSGeneralErrorResponse(error_message)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error during deletion validation: {e}")
            # Continue without validation if it fails
        
        # Process metadata keys
        successful_items = []
        failed_items = []
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if metadata exists
                existing_response = asset_file_metadata_table.get_item(
                    Key={
                        'metadataKey': metadata_key,
                        'databaseId:assetId:filePath': composite_key
                    }
                )
                
                if 'Item' not in existing_response:
                    failed_items.append({
                        'key': metadata_key,
                        'error': 'Metadata key not found'
                    })
                    continue
                
                # Prepare item for batch delete
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': metadata_key},
                            'databaseId:assetId:filePath': {'S': composite_key}
                        }
                    }
                })
                successful_items.append(metadata_key)
                
            except Exception as e:
                logger.warning(f"Error preparing delete for metadata key {metadata_key}: {e}")
                failed_items.append({
                    'key': metadata_key,
                    'error': str(e)
                })
        
        # Delete items in batches of 25
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            asset_file_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    for item in batch:
                        key = item['DeleteRequest']['Key']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch delete failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadataKeys)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - Asset Metadata
#######################

def handle_asset_metadata_get(event):
    """Handle GET requests for asset metadata"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse query parameters
        try:
            query_request_model = parse(query_parameters, model=GetAssetMetadataRequestModel)
            query_params = {
                'pageSize': query_request_model.pageSize,
                'startingToken': query_request_model.startingToken
            }
        except ValidationError as v:
            logger.exception(f"Validation error in query parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Get metadata
        response = get_asset_metadata(path_request_model.databaseId, path_request_model.assetId, query_params, claims_and_roles)
        return success(body=response.dict())
        
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_asset_metadata_post(event):
    """Handle POST requests to create asset metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetMetadataRequestModel)
        
        # Create metadata
        response = create_asset_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_asset_metadata_put(event):
    """Handle PUT requests to update asset metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateAssetMetadataRequestModel)
        
        # Update metadata
        response = update_asset_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_asset_metadata_delete(event):
    """Handle DELETE requests to delete asset metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=DeleteAssetMetadataRequestModel)
        
        # Delete metadata
        response = delete_asset_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# File Metadata/Attribute CRUD Operations
#######################

def get_file_metadata(database_id: str, asset_id: str, file_path: str, metadata_type: str, query_params: dict, claims_and_roles: dict):
    """Get metadata or attributes for a file - Returns ALL records (pagination ignored)"""
    try:
        # No S3 validation for GET - metadata can exist even if file doesn't
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this file")
        
        composite_key = f"{database_id}:{asset_id}:{file_path}"
        table_name = asset_file_metadata_table_name if metadata_type == 'metadata' else file_attribute_table_name
        
        # Fetch ALL metadata using paginator (ignore query_params pagination)
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            # Normalize field names to metadataKey/metadataValue/metadataValueType
            if metadata_type == 'attribute':
                key_field = deserialized_item.get('attributeKey', deserialized_item.get('metadataKey'))
                value_field = deserialized_item.get('attributeValue', deserialized_item.get('metadataValue'))
                type_field = deserialized_item.get('attributeValueType', deserialized_item.get('metadataValueType'))
            else:
                key_field = deserialized_item['metadataKey']
                value_field = deserialized_item['metadataValue']
                type_field = deserialized_item['metadataValueType']
            
            # Store as dict for enrichment
            metadata_list.append({
                'metadataKey': key_field,
                'metadataValue': value_field,
                'metadataValueType': type_field
            })
        
        # Fetch database config and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']
            entity_type = 'fileMetadata' if metadata_type == 'metadata' else 'fileAttribute'
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type=entity_type,
                file_path=file_path,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)
            
            # Convert to response models
            response_models = []
            for item in enriched_metadata:
                response_models.append(FileMetadataResponseModel(
                    databaseId=database_id,
                    assetId=asset_id,
                    filePath=file_path,
                    metadataKey=item['metadataKey'],
                    metadataValue=item['metadataValue'],
                    metadataValueType=item['metadataValueType'],
                    metadataSchemaName=item.get('metadataSchemaName'),
                    metadataSchemaField=item.get('metadataSchemaField'),
                    metadataSchemaRequired=item.get('metadataSchemaRequired'),
                    metadataSchemaSequence=item.get('metadataSchemaSequence'),
                    metadataSchemaDefaultValue=item.get('metadataSchemaDefaultValue'),
                    metadataSchemaDependsOn=item.get('metadataSchemaDependsOn'),
                    metadataSchemaMultiFieldConflict=item.get('metadataSchemaMultiFieldConflict'),
                    metadataSchemaControlledListKeys=item.get('metadataSchemaControlledListKeys')
                ))
            
            metadata_list = response_models
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = [FileMetadataResponseModel(
                databaseId=database_id,
                assetId=asset_id,
                filePath=file_path,
                metadataKey=item['metadataKey'],
                metadataValue=item['metadataValue'],
                metadataValueType=item['metadataValueType']
            ) for item in metadata_list]
            restrict_metadata_outside_schemas = False
        
        # Build response (NextToken always empty/None)
        result = GetFileMetadataResponseModel(
            metadata=metadata_list,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas
        )
        # NextToken is always None (no pagination)
        
        return result
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def create_file_metadata(database_id: str, asset_id: str, request_model: CreateFileMetadataRequestModel, claims_and_roles: dict):
    """Create metadata or attributes for a file (bulk operation)"""
    try:
        validate_file_exists(database_id, asset_id, request_model.filePath)
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "POST", claims_and_roles):
            raise PermissionError("Not authorized to create metadata for this file")
        
        # Validate 500 record limit: Fetch existing + count with new (separate limits for metadata vs attributes)
        composite_key = f"{database_id}:{asset_id}:{request_model.filePath}"
        table_name_for_limit_check = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=table_name_for_limit_check,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                if request_model.type == 'attribute':
                    key = deserialized.get('attributeKey', deserialized.get('metadataKey'))
                else:
                    key = deserialized['metadataKey']
                existing_keys.add(key)
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} {request_model.type} records allowed per file "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        if not skip_schema_validation:
            try:
                database_ids = [database_id, 'GLOBAL']
                entity_type = 'fileMetadata' if request_model.type == 'metadata' else 'fileAttribute'
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type=entity_type,
                    file_path=request_model.filePath,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=table_name_for_limit_check,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict (normalize field names)
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    if request_model.type == 'attribute':
                        key = deserialized.get('attributeKey', deserialized.get('metadataKey'))
                        value = deserialized.get('attributeValue', deserialized.get('metadataValue'))
                        value_type = deserialized.get('attributeValueType', deserialized.get('metadataValueType'))
                    else:
                        key = deserialized['metadataKey']
                        value = deserialized['metadataValue']
                        value_type = deserialized['metadataValueType']
                    
                    existing_metadata[key] = {
                        'metadataValue': value,
                        'metadataValueType': value_type
                    }
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields)
                updated_metadata = []
                for item in request_model.metadata:
                    updated_metadata.append(item)
                
                # Add any new fields with defaults that weren't in the request
                for key, value_dict in metadata_with_defaults.items():
                    if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                        from models.metadata import MetadataItemModel
                        updated_metadata.append(MetadataItemModel(
                            metadataKey=key,
                            metadataValue=value_dict['metadataValue'],
                            metadataValueType=value_dict['metadataValueType']
                        ))
                request_model.metadata = updated_metadata
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        successful_items = []
        failed_items = []
        table_name = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
        table = asset_file_metadata_table if request_model.type == 'metadata' else file_attribute_table
        items_to_write = []
        
        # Composite key for asset-level lookups (without file path)
        asset_composite_key = f"{database_id}:{asset_id}"
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update)
                if request_model.type == 'metadata':
                    item = {
                        'metadataKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'metadataValue': {'S': metadata_item.metadataValue},
                        'metadataValueType': {'S': metadata_item.metadataValueType.value}
                    }
                else:  # attribute
                    item = {
                        'attributeKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'attributeValue': {'S': metadata_item.metadataValue},
                        'attributeValueType': {'S': metadata_item.metadataValueType.value}
                    }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
            except Exception as e:
                logger.warning(f"Error preparing {request_model.type} item {metadata_item.metadataKey}: {e}")
                failed_items.append({'key': metadata_item.metadataKey, 'error': str(e)})
        
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(RequestItems={table_name: batch})
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key_field = 'metadataKey' if request_model.type == 'metadata' else 'attributeKey'
                        key = item['PutRequest']['Item'][key_field]['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch write failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadata),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Created {len(successful_items)} of {len(request_model.metadata)} {request_model.type} items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_file_metadata(database_id: str, asset_id: str, request_model: UpdateFileMetadataRequestModel, claims_and_roles: dict):
    """Update metadata or attributes for a file (bulk operation) - Supports UPDATE and REPLACE_ALL modes"""
    try:
        validate_file_exists(database_id, asset_id, request_model.filePath)
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not check_multi_action_authorization(asset, ["PUT", "POST", "DELETE"], claims_and_roles):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions")
        else:
            # UPDATE mode requires only PUT permission
            if not check_entity_authorization(asset, "PUT", claims_and_roles):
                raise PermissionError("Not authorized to update metadata for this file")
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        composite_key = f"{database_id}:{asset_id}:{request_model.filePath}"
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this file
                table_name_for_query = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=table_name_for_query,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict (normalize field names)
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    if request_model.type == 'attribute':
                        key = deserialized.get('attributeKey', deserialized.get('metadataKey'))
                        value = deserialized.get('attributeValue', deserialized.get('metadataValue'))
                        value_type = deserialized.get('attributeValueType', deserialized.get('metadataValueType'))
                    else:
                        key = deserialized['metadataKey']
                        value = deserialized['metadataValue']
                        value_type = deserialized['metadataValueType']
                    
                    existing_metadata[key] = {
                        'metadataValue': value,
                        'metadataValueType': value_type
                    }
                
                # Validate 500 record limit based on updateType (separate limits for metadata vs attributes)
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} {request_model.type} records allowed per file "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} {request_model.type} records allowed per file "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [database_id, 'GLOBAL']
                entity_type = 'fileMetadata' if request_model.type == 'metadata' else 'fileAttribute'
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type=entity_type,
                    file_path=request_model.filePath,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            metadata_to_validate, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_file_metadata(database_id, asset_id, request_model.filePath, request_model.type, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_file_metadata(database_id, asset_id, request_model.filePath, request_model.type, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_file_metadata(database_id: str, asset_id: str, file_path: str, metadata_type: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert file metadata/attributes (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        composite_key = f"{database_id}:{asset_id}:{file_path}"
        asset_composite_key = f"{database_id}:{asset_id}"
        table_name = asset_file_metadata_table_name if metadata_type == 'metadata' else file_attribute_table_name
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update)
                if metadata_type == 'metadata':
                    item = {
                        'metadataKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'metadataValue': {'S': metadata_item.metadataValue},
                        'metadataValueType': {'S': metadata_item.metadataValueType.value}
                    }
                else:  # attribute
                    item = {
                        'attributeKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'attributeValue': {'S': metadata_item.metadataValue},
                        'attributeValueType': {'S': metadata_item.metadataValueType.value}
                    }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing {metadata_type} item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key_field = 'metadataKey' if metadata_type == 'metadata' else 'attributeKey'
                        key = item['PutRequest']['Item'][key_field]['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} {metadata_type} items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_file_metadata(database_id: str, asset_id: str, file_path: str, metadata_type: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all file metadata/attributes with rollback on failure"""
    try:
        composite_key = f"{database_id}:{asset_id}:{file_path}"
        table_name = asset_file_metadata_table_name if metadata_type == 'metadata' else file_attribute_table_name
        
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        if metadata_type == 'metadata':
            existing_keys = {item['metadataKey'] for item in existing_metadata}
        else:  # attribute
            existing_keys = {item.get('attributeKey', item.get('metadataKey')) for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if (item.get('metadataKey') if metadata_type == 'metadata' else item.get('attributeKey', item.get('metadataKey'))) in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                if metadata_type == 'metadata':
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'metadataKey': {'S': key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
                else:  # attribute
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'attributeKey': {'S': key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error deleting {metadata_type} in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse(f"Failed to delete existing {metadata_type}")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            asset_composite_key = f"{database_id}:{asset_id}"
            for metadata_item in metadata_items:
                if metadata_type == 'metadata':
                    item = {
                        'metadataKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'metadataValue': {'S': metadata_item.metadataValue},
                        'metadataValueType': {'S': metadata_item.metadataValueType.value}
                    }
                else:  # attribute
                    item = {
                        'attributeKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'attributeValue': {'S': metadata_item.metadataValue},
                        'attributeValueType': {'S': metadata_item.metadataValueType.value}
                    }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                dynamodb_client.batch_write_item(
                    RequestItems={
                        table_name: batch
                    }
                )
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all {metadata_type}: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items
                    items_to_restore = []
                    for item in deleted_items_backup:
                        if metadata_type == 'metadata':
                            restore_item = {
                                'metadataKey': {'S': item['metadataKey']},
                                'databaseId:assetId:filePath': {'S': composite_key},
                                'databaseId:assetId': {'S': asset_composite_key},
                                'metadataValue': {'S': item['metadataValue']},
                                'metadataValueType': {'S': item['metadataValueType']}
                            }
                        else:  # attribute
                            key = item.get('attributeKey', item.get('metadataKey'))
                            value = item.get('attributeValue', item.get('metadataValue'))
                            value_type = item.get('attributeValueType', item.get('metadataValueType'))
                            restore_item = {
                                'attributeKey': {'S': key},
                                'databaseId:assetId:filePath': {'S': composite_key},
                                'databaseId:assetId': {'S': asset_composite_key},
                                'attributeValue': {'S': value},
                                'attributeValueType': {'S': value_type}
                            }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        dynamodb_client.batch_write_item(
                            RequestItems={
                                table_name: batch
                            }
                        )
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    raise VAMSGeneralErrorResponse("REPLACE_ALL operation failed, all changes rolled back successfully")
                    
                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                        "Please contact administrator."
                    )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_file_metadata(database_id: str, asset_id: str, request_model: DeleteFileMetadataRequestModel, claims_and_roles: dict):
    """Delete metadata or attributes for a file (bulk operation)"""
    try:
        # No S3 validation for DELETE - allow deleting metadata even if file doesn't exist
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "DELETE", claims_and_roles):
            raise PermissionError("Not authorized to delete metadata for this file")
        
        # NEW: Schema validation for deletion
        composite_key = f"{database_id}:{asset_id}:{request_model.filePath}"
        table_name = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
        
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=table_name,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                if request_model.type == 'attribute':
                    key = deserialized.get('attributeKey', deserialized.get('metadataKey'))
                    value = deserialized.get('attributeValue', deserialized.get('metadataValue'))
                    value_type = deserialized.get('attributeValueType', deserialized.get('metadataValueType'))
                else:
                    key = deserialized['metadataKey']
                    value = deserialized['metadataValue']
                    value_type = deserialized['metadataValueType']
                
                existing_metadata[key] = {
                    'metadataValue': value,
                    'metadataValueType': value_type
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [database_id, 'GLOBAL']
            entity_type = 'fileMetadata' if request_model.type == 'metadata' else 'fileAttribute'
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type=entity_type,
                file_path=request_model.filePath,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                error_message = "Deletion validation failed: " + "; ".join(validation_errors)
                raise VAMSGeneralErrorResponse(error_message)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error during deletion validation: {e}")
            # Continue without validation if it fails
        
        successful_items = []
        failed_items = []
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if item exists and prepare for delete with appropriate field names
                if request_model.type == 'metadata':
                    existing_response = asset_file_metadata_table.get_item(
                        Key={
                            'metadataKey': metadata_key,
                            'databaseId:assetId:filePath': composite_key
                        }
                    )
                    
                    if 'Item' not in existing_response:
                        failed_items.append({'key': metadata_key, 'error': 'Metadata key not found'})
                        continue
                    
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'metadataKey': {'S': metadata_key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
                else:  # attribute
                    existing_response = file_attribute_table.get_item(
                        Key={
                            'attributeKey': metadata_key,
                            'databaseId:assetId:filePath': composite_key
                        }
                    )
                    
                    if 'Item' not in existing_response:
                        failed_items.append({'key': metadata_key, 'error': 'Attribute key not found'})
                        continue
                    
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'attributeKey': {'S': metadata_key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
                
                successful_items.append(metadata_key)
            except Exception as e:
                logger.warning(f"Error preparing delete for {request_model.type} key {metadata_key}: {e}")
                failed_items.append({'key': metadata_key, 'error': str(e)})
        
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(RequestItems={table_name: batch})
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    for item in batch:
                        key_field = 'metadataKey' if request_model.type == 'metadata' else 'attributeKey'
                        key = item['DeleteRequest']['Key'][key_field]['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch delete failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadataKeys),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {len(successful_items)} of {len(request_model.metadataKeys)} {request_model.type} items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - File Metadata/Attributes
#######################

def handle_file_metadata_get(event):
    """Handle GET requests for file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        # Parse query parameters - validation handled in model (adds leading slash)
        query_request_model = parse(query_parameters, model=GetFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        file_path = query_request_model.filePath
        if file_path.startswith(f"/{path_request_model.assetId}/"):
            file_path = file_path[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath: {query_request_model.filePath} -> {file_path}")
        
        query_params = {'pageSize': query_request_model.pageSize, 'startingToken': query_request_model.startingToken}
        response = get_file_metadata(path_request_model.databaseId, path_request_model.assetId, file_path, query_request_model.type, query_params, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_file_metadata_post(event):
    """Handle POST requests to create file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse request model - validation handled in model (adds leading slash)
        request_model = parse(body, model=CreateFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        if request_model.filePath.startswith(f"/{path_request_model.assetId}/"):
            request_model.filePath = request_model.filePath[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath")
        
        response = create_file_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_file_metadata_put(event):
    """Handle PUT requests to update file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse request model - validation handled in model (adds leading slash)
        request_model = parse(body, model=UpdateFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        if request_model.filePath.startswith(f"/{path_request_model.assetId}/"):
            request_model.filePath = request_model.filePath[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath")
        
        response = update_file_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_file_metadata_delete(event):
    """Handle DELETE requests to delete file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse request model - validation handled in model (adds leading slash)
        request_model = parse(body, model=DeleteFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        if request_model.filePath.startswith(f"/{path_request_model.assetId}/"):
            request_model.filePath = request_model.filePath[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath")
        
        response = delete_file_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# Database Metadata CRUD Operations
#######################

def get_database_metadata(database_id: str, query_params: dict, claims_and_roles: dict):
    """Get metadata for a database - Returns ALL records (pagination ignored)"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        if not check_entity_authorization(database, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this database")
        
        # Fetch ALL metadata using paginator (ignore query_params pagination)
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=database_metadata_table_name,
            IndexName='DatabaseIdIndex',
            KeyConditionExpression='databaseId = :dbId',
            ExpressionAttributeValues={':dbId': {'S': database_id}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            metadata_list.append(deserialized_item)
        
        # Fetch database config and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='databaseMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)
            
            # Convert to response models
            response_models = []
            for item in enriched_metadata:
                response_models.append(DatabaseMetadataResponseModel(
                    databaseId=database_id,
                    metadataKey=item['metadataKey'],
                    metadataValue=item['metadataValue'],
                    metadataValueType=item['metadataValueType'],
                    metadataSchemaName=item.get('metadataSchemaName'),
                    metadataSchemaField=item.get('metadataSchemaField'),
                    metadataSchemaRequired=item.get('metadataSchemaRequired'),
                    metadataSchemaSequence=item.get('metadataSchemaSequence'),
                    metadataSchemaDefaultValue=item.get('metadataSchemaDefaultValue'),
                    metadataSchemaDependsOn=item.get('metadataSchemaDependsOn'),
                    metadataSchemaMultiFieldConflict=item.get('metadataSchemaMultiFieldConflict'),
                    metadataSchemaControlledListKeys=item.get('metadataSchemaControlledListKeys')
                ))
            
            metadata_list = response_models
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = [DatabaseMetadataResponseModel(
                databaseId=database_id,
                metadataKey=item['metadataKey'],
                metadataValue=item['metadataValue'],
                metadataValueType=item['metadataValueType']
            ) for item in metadata_list]
            restrict_metadata_outside_schemas = False
        
        # Build response (NextToken always empty/None)
        result = GetDatabaseMetadataResponseModel(
            metadata=metadata_list,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas
        )
        # NextToken is always None (no pagination)
        
        return result
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def create_database_metadata(database_id: str, request_model: CreateDatabaseMetadataRequestModel, claims_and_roles: dict):
    """Create metadata for a database (bulk operation)"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        if not check_entity_authorization(database, "POST", claims_and_roles):
            raise PermissionError("Not authorized to create metadata for this database")
        
        # Validate 500 record limit: Fetch existing + count with new
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=database_metadata_table_name,
                IndexName='DatabaseIdIndex',
                KeyConditionExpression='databaseId = :dbId',
                ExpressionAttributeValues={':dbId': {'S': database_id}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_keys.add(deserialized['metadataKey'])
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        if not skip_schema_validation:
            try:
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='databaseMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=database_metadata_table_name,
                    IndexName='DatabaseIdIndex',
                    KeyConditionExpression='databaseId = :dbId',
                    ExpressionAttributeValues={':dbId': {'S': database_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    existing_metadata[deserialized['metadataKey']] = {
                        'metadataValue': deserialized['metadataValue'],
                        'metadataValueType': deserialized['metadataValueType']
                    }
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields)
                updated_metadata = []
                for item in request_model.metadata:
                    updated_metadata.append(item)
                
                # Add any new fields with defaults that weren't in the request
                for key, value_dict in metadata_with_defaults.items():
                    if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                        from models.metadata import MetadataItemModel
                        updated_metadata.append(MetadataItemModel(
                            metadataKey=key,
                            metadataValue=value_dict['metadataValue'],
                            metadataValueType=value_dict['metadataValueType']
                        ))
                request_model.metadata = updated_metadata
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId': {'S': database_id},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
            except Exception as e:
                failed_items.append({'key': metadata_item.metadataKey, 'error': str(e)})
        
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(RequestItems={database_metadata_table_name: batch})
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch write failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadata),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Created {len(successful_items)} of {len(request_model.metadata)} metadata items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_database_metadata(database_id: str, request_model: UpdateDatabaseMetadataRequestModel, claims_and_roles: dict):
    """Update metadata for a database (bulk operation) - Supports UPDATE and REPLACE_ALL modes"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not check_multi_action_authorization(database, ["PUT", "POST", "DELETE"], claims_and_roles):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions")
        else:
            # UPDATE mode requires only PUT permission
            if not check_entity_authorization(database, "PUT", claims_and_roles):
                raise PermissionError("Not authorized to update metadata for this database")
        
        # Check if user is SYSTEM - bypass schema validation
        username = claims_and_roles.get("tokens", ["system"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM users
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this database
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=database_metadata_table_name,
                    IndexName='DatabaseIdIndex',
                    KeyConditionExpression='databaseId = :dbId',
                    ExpressionAttributeValues={':dbId': {'S': database_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = {}
                deserializer = TypeDeserializer()
                for item in page_iterator.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    existing_metadata[deserialized['metadataKey']] = {
                        'metadataValue': deserialized['metadataValue'],
                        'metadataValueType': deserialized['metadataValueType']
                    }
                
                # Validate 500 record limit based on updateType
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='databaseMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            metadata_to_validate, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.warning(f"Error during schema validation: {e}")
                # Continue without schema validation if it fails
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_database_metadata(database_id, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_database_metadata(database_id, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_database_metadata(database_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert database metadata (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId': {'S': database_id},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            database_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_database_metadata(database_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all database metadata with rollback on failure"""
    try:
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=database_metadata_table_name,
            IndexName='DatabaseIdIndex',
            KeyConditionExpression='databaseId = :dbId',
            ExpressionAttributeValues={':dbId': {'S': database_id}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        existing_keys = {item['metadataKey'] for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if item['metadataKey'] in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': key},
                            'databaseId': {'S': database_id}
                        }
                    }
                })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(
                        RequestItems={
                            database_metadata_table_name: batch
                        }
                    )
                except Exception as e:
                    logger.exception(f"Error deleting metadata in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse("Failed to delete existing metadata")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            for metadata_item in metadata_items:
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId': {'S': database_id},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                dynamodb_client.batch_write_item(
                    RequestItems={
                        database_metadata_table_name: batch
                    }
                )
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all metadata: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items
                    items_to_restore = []
                    for item in deleted_items_backup:
                        restore_item = {
                            'metadataKey': {'S': item['metadataKey']},
                            'databaseId': {'S': database_id},
                            'metadataValue': {'S': item['metadataValue']},
                            'metadataValueType': {'S': item['metadataValueType']}
                        }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        dynamodb_client.batch_write_item(
                            RequestItems={
                                database_metadata_table_name: batch
                            }
                        )
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    raise VAMSGeneralErrorResponse("REPLACE_ALL operation failed, all changes rolled back successfully")
                    
                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                        "Please contact administrator."
                    )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_database_metadata(database_id: str, request_model: DeleteDatabaseMetadataRequestModel, claims_and_roles: dict):
    """Delete metadata for a database (bulk operation)"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        if not check_entity_authorization(database, "DELETE", claims_and_roles):
            raise PermissionError("Not authorized to delete metadata for this database")
        
        # NEW: Schema validation for deletion
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=database_metadata_table_name,
                IndexName='DatabaseIdIndex',
                KeyConditionExpression='databaseId = :dbId',
                ExpressionAttributeValues={':dbId': {'S': database_id}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_metadata[deserialized['metadataKey']] = {
                    'metadataValue': deserialized['metadataValue'],
                    'metadataValueType': deserialized['metadataValueType']
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='databaseMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                error_message = "Deletion validation failed: " + "; ".join(validation_errors)
                raise VAMSGeneralErrorResponse(error_message)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error during deletion validation: {e}")
            # Continue without validation if it fails
        
        successful_items = []
        failed_items = []
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if metadata exists
                existing_response = database_metadata_table.get_item(
                    Key={
                        'metadataKey': metadata_key,
                        'databaseId': database_id
                    }
                )
                
                if 'Item' not in existing_response:
                    failed_items.append({'key': metadata_key, 'error': 'Metadata key not found'})
                    continue
                
                # Prepare item for batch delete
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': metadata_key},
                            'databaseId': {'S': database_id}
                        }
                    }
                })
                successful_items.append(metadata_key)
            except Exception as e:
                failed_items.append({'key': metadata_key, 'error': str(e)})
        
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    dynamodb_client.batch_write_item(RequestItems={database_metadata_table_name: batch})
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    for item in batch:
                        key = item['DeleteRequest']['Key']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch delete failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadataKeys),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {len(successful_items)} of {len(request_model.metadataKeys)} metadata items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - Database Metadata
#######################

def handle_database_metadata_get(event):
    """Handle GET requests for database metadata"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        query_request_model = parse(query_parameters, model=GetDatabaseMetadataRequestModel)
        query_params = {'pageSize': query_request_model.pageSize, 'startingToken': query_request_model.startingToken}
        
        response = get_database_metadata(path_request_model.databaseId, query_params, claims_and_roles)
        return success(body=response.dict())
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_database_metadata_post(event):
    """Handle POST requests to create database metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        request_model = parse(body, model=CreateDatabaseMetadataRequestModel)
        response = create_database_metadata(path_request_model.databaseId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_database_metadata_put(event):
    """Handle PUT requests to update database metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        request_model = parse(body, model=UpdateDatabaseMetadataRequestModel)
        response = update_database_metadata(path_request_model.databaseId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_database_metadata_delete(event):
    """Handle DELETE requests to delete database metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        request_model = parse(body, model=DeleteDatabaseMetadataRequestModel)
        response = delete_database_metadata(path_request_model.databaseId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for centralized metadata service"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        
        if not method_allowed_on_api:
            return authorization_error()
        
        # Route to appropriate handler based on path
        # Asset Link Metadata Routes
        if '/asset-links/' in path and '/metadata' in path:
            if method == 'GET':
                return handle_asset_link_metadata_get(event)
            elif method == 'POST':
                return handle_asset_link_metadata_post(event)
            elif method == 'PUT':
                return handle_asset_link_metadata_put(event)
            elif method == 'DELETE':
                return handle_asset_link_metadata_delete(event)
        
        # File Metadata/Attribute Routes
        elif '/database/' in path and '/assets/' in path and '/metadata/file' in path:
            if method == 'GET':
                return handle_file_metadata_get(event)
            elif method == 'POST':
                return handle_file_metadata_post(event)
            elif method == 'PUT':
                return handle_file_metadata_put(event)
            elif method == 'DELETE':
                return handle_file_metadata_delete(event)
        
        # Asset Metadata Routes (not file metadata)
        elif '/database/' in path and '/assets/' in path and '/metadata' in path:
            if method == 'GET':
                return handle_asset_metadata_get(event)
            elif method == 'POST':
                return handle_asset_metadata_post(event)
            elif method == 'PUT':
                return handle_asset_metadata_put(event)
            elif method == 'DELETE':
                return handle_asset_metadata_delete(event)
        
        # Database Metadata Routes
        elif '/database/' in path and '/metadata' in path and '/assets/' not in path:
            if method == 'GET':
                return handle_database_metadata_get(event)
            elif method == 'POST':
                return handle_database_metadata_post(event)
            elif method == 'PUT':
                return handle_database_metadata_put(event)
            elif method == 'DELETE':
                return handle_database_metadata_delete(event)
        
        # If no route matched
        return validation_error(body={'message': "Route not found"}, event=event)
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)