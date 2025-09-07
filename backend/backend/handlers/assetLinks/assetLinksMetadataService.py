# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, authorization_error, general_error, VAMSGeneralErrorResponse
from models.assetLinks import (
    GetAssetLinkMetadataRequestModel,
    CreateAssetLinkMetadataRequestModel,
    CreateAssetLinkMetadataResponseModel,
    UpdateAssetLinkMetadataPathRequestModel,
    UpdateAssetLinkMetadataRequestModel,
    UpdateAssetLinkMetadataResponseModel,
    GetAssetLinkMetadataResponseModel,
    DeleteAssetLinkMetadataPathRequestModel,
    DeleteAssetLinkMetadataResponseModel,
    AssetLinkMetadataModel
)

# Configure AWS clients
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="AssetLinksMetadataService")

# Load environment variables
try:
    asset_links_table_v2_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    asset_links_metadata_table_name = os.environ["ASSET_LINKS_METADATA_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    auth_table_name = os.environ["AUTH_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
    roles_table_name = os.environ["ROLES_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_links_table = dynamodb.Table(asset_links_table_v2_name)
asset_links_metadata_table = dynamodb.Table(asset_links_metadata_table_name)
asset_storage_table = dynamodb.Table(asset_storage_table_name)

#######################
# Utility Functions
#######################

def get_asset_details(asset_id: str, database_id: str):
    """Get asset details from the asset storage table"""
    try:
        response = asset_storage_table.get_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            }
        )
        return response.get('Item')
    except Exception as e:
        logger.exception(f"Error getting asset details for {asset_id}: {e}")
        return None

def check_asset_permission(asset, claims_and_roles: dict, action: str) -> bool:
    """Check if user has permission to perform action on asset"""
    try:
        if not asset:
            return False
            
        asset_copy = asset.copy()
        asset_copy.update({"object__type": "asset"})
        
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            return casbin_enforcer.enforce(asset_copy, action)
            
        return False
        
    except Exception as e:
        logger.exception(f"Error checking asset permission: {e}")
        return False

def validate_asset_link_access(asset_link_id: str, claims_and_roles: dict, action: str = "GET") -> bool:
    """Validate that user has access to the asset link"""
    try:
        # Get the asset link
        response = asset_links_table.get_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        if 'Item' not in response:
            return False
            
        link_item = response['Item']
        
        # Check permissions for both assets
        from_asset = get_asset_details(link_item['fromAssetId'], link_item['fromAssetDatabaseId'])
        to_asset = get_asset_details(link_item['toAssetId'], link_item['toAssetDatabaseId'])
        
        if not from_asset or not to_asset:
            return False
            
        # User must have permission on both assets
        return (check_asset_permission(from_asset, claims_and_roles, action) and 
                check_asset_permission(to_asset, claims_and_roles, action))
        
    except Exception as e:
        logger.exception(f"Error validating asset link access: {e}")
        return False

#######################
# API Implementation
#######################

def create_asset_link_metadata(asset_link_id: str, request_model: CreateAssetLinkMetadataRequestModel, claims_and_roles: dict) -> CreateAssetLinkMetadataResponseModel:
    """Create metadata for an asset link"""
    
    # Validate access to the asset link
    if not validate_asset_link_access(asset_link_id, claims_and_roles, "POST"):
        raise PermissionError("Not authorized to create metadata for this asset link")
    
    # Check if metadata key already exists
    try:
        existing_response = asset_links_metadata_table.get_item(
            Key={
                'assetLinkId': asset_link_id,
                'metadataKey': request_model.metadataKey
            }
        )
        
        if 'Item' in existing_response:
            raise ValueError("Metadata key already exists for this asset link")
            
    except Exception as e:
        if "already exists" in str(e):
            raise
        logger.exception(f"Error checking existing metadata: {e}")
        raise RuntimeError("Error validating metadata key")
    
    # Create the metadata item
    metadata_item = {
        'assetLinkId': asset_link_id,
        'metadataKey': request_model.metadataKey,
        'metadataValue': request_model.metadataValue,
        'metadataValueType': request_model.metadataValueType
    }
    
    try:
        asset_links_metadata_table.put_item(Item=metadata_item)
        logger.info(f"Created metadata {request_model.metadataKey} for asset link {asset_link_id}")
        
        return CreateAssetLinkMetadataResponseModel(
            message="Asset link metadata created successfully"
        )
        
    except Exception as e:
        logger.exception(f"Error creating asset link metadata: {e}")
        raise RuntimeError(f"Failed to create metadata: {str(e)}")

def get_asset_link_metadata(asset_link_id: str, claims_and_roles: dict) -> GetAssetLinkMetadataResponseModel:
    """Get all metadata for an asset link"""
    
    # Validate access to the asset link
    if not validate_asset_link_access(asset_link_id, claims_and_roles, "GET"):
        raise PermissionError("Not authorized to view metadata for this asset link")
    
    try:
        # Query all metadata for this asset link
        response = asset_links_metadata_table.query(
            KeyConditionExpression=Key('assetLinkId').eq(asset_link_id)
        )
        
        metadata_list = []
        for item in response.get('Items', []):
            metadata_list.append(AssetLinkMetadataModel(
                assetLinkId=item['assetLinkId'],
                metadataKey=item['metadataKey'],
                metadataValue=item['metadataValue'],
                metadataValueType=item['metadataValueType']
            ))
        
        return GetAssetLinkMetadataResponseModel(metadata=metadata_list)
        
    except Exception as e:
        logger.exception(f"Error getting asset link metadata: {e}")
        raise RuntimeError(f"Failed to get metadata: {str(e)}")

def update_asset_link_metadata(asset_link_id: str, metadata_key: str, request_model: UpdateAssetLinkMetadataRequestModel, claims_and_roles: dict) -> UpdateAssetLinkMetadataResponseModel:
    """Update metadata for an asset link"""
    
    # Validate access to the asset link
    if not validate_asset_link_access(asset_link_id, claims_and_roles, "PUT"):
        raise PermissionError("Not authorized to update metadata for this asset link")
    
    try:
        # Check if metadata exists
        existing_response = asset_links_metadata_table.get_item(
            Key={
                'assetLinkId': asset_link_id,
                'metadataKey': metadata_key
            }
        )
        
        if 'Item' not in existing_response:
            raise ValueError("Metadata key not found for this asset link")
        
        # Update the metadata
        asset_links_metadata_table.update_item(
            Key={
                'assetLinkId': asset_link_id,
                'metadataKey': metadata_key
            },
            UpdateExpression='SET metadataValue = :val, metadataValueType = :type',
            ExpressionAttributeValues={
                ':val': request_model.metadataValue,
                ':type': request_model.metadataValueType
            }
        )
        
        logger.info(f"Updated metadata {metadata_key} for asset link {asset_link_id}")
        
        return UpdateAssetLinkMetadataResponseModel(
            message="Asset link metadata updated successfully"
        )
        
    except Exception as e:
        logger.exception(f"Error updating asset link metadata: {e}")
        raise RuntimeError(f"Failed to update metadata: {str(e)}")

def delete_asset_link_metadata(asset_link_id: str, metadata_key: str, claims_and_roles: dict) -> DeleteAssetLinkMetadataResponseModel:
    """Delete metadata for an asset link"""
    
    # Validate access to the asset link
    if not validate_asset_link_access(asset_link_id, claims_and_roles, "DELETE"):
        raise PermissionError("Not authorized to delete metadata for this asset link")
    
    try:
        # Check if metadata exists
        existing_response = asset_links_metadata_table.get_item(
            Key={
                'assetLinkId': asset_link_id,
                'metadataKey': metadata_key
            }
        )
        
        if 'Item' not in existing_response:
            raise ValueError("Metadata key not found for this asset link")
        
        # Delete the metadata
        asset_links_metadata_table.delete_item(
            Key={
                'assetLinkId': asset_link_id,
                'metadataKey': metadata_key
            }
        )
        
        logger.info(f"Deleted metadata {metadata_key} for asset link {asset_link_id}")
        
        return DeleteAssetLinkMetadataResponseModel(
            message="Asset link metadata deleted successfully"
        )
        
    except Exception as e:
        logger.exception(f"Error deleting asset link metadata: {e}")
        raise RuntimeError(f"Failed to delete metadata: {str(e)}")

#######################
# Request Handlers
#######################

def handle_post_request(event):
    """Handle POST requests to create asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Validate path parameters using request model
        try:
            path_request_model = parse(path_parameters, model=GetAssetLinkMetadataRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)})
        
        # Validate asset link ID
        (valid, message) = validate({
            'assetLinkId': {
                'value': path_request_model.assetLinkId,
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        logger.info(f"Creating metadata for asset link {path_request_model.assetLinkId}")
        
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
            
        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetLinkMetadataRequestModel)
        
        # Create the metadata
        response = create_asset_link_metadata(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValueError as v:
        logger.warning(f"Validation error in asset link metadata creation: {v}")
        return validation_error(body={'message': str(v)})
    except PermissionError as p:
        logger.warning(f"Permission error in asset link metadata creation: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error()

def handle_get_request(event):
    """Handle GET requests to retrieve asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Validate path parameters using request model
        try:
            request_model = parse(path_parameters, model=GetAssetLinkMetadataRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)})
        
        # Validate asset link ID
        (valid, message) = validate({
            'assetLinkId': {
                'value': request_model.assetLinkId,
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        logger.info(f"Getting metadata for asset link {request_model.assetLinkId}")
        
        # Get the metadata
        response = get_asset_link_metadata(request_model.assetLinkId, claims_and_roles)
        return success(body=response.dict())
        
    except ValueError as v:
        logger.warning(f"Validation error in asset link metadata retrieval: {v}")
        return validation_error(body={'message': str(v)})
    except PermissionError as p:
        logger.warning(f"Permission error in asset link metadata retrieval: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()

def handle_put_request(event):
    """Handle PUT requests to update asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Validate path parameters using request model
        try:
            path_request_model = parse(path_parameters, model=UpdateAssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)})
        
        # Validate asset link ID
        (valid, message) = validate({
            'assetLinkId': {
                'value': path_request_model.assetLinkId,
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        logger.info(f"Updating metadata {path_request_model.metadataKey} for asset link {path_request_model.assetLinkId}")
        
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
            
        # Parse and validate the request model
        request_model = parse(body, model=UpdateAssetLinkMetadataRequestModel)
        
        # Update the metadata
        response = update_asset_link_metadata(
            path_request_model.assetLinkId, 
            path_request_model.metadataKey, 
            request_model, 
            claims_and_roles
        )
        return success(body=response.dict())
        
    except ValueError as v:
        logger.warning(f"Validation error in asset link metadata update: {v}")
        return validation_error(body={'message': str(v)})
    except PermissionError as p:
        logger.warning(f"Permission error in asset link metadata update: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error()

def handle_delete_request(event):
    """Handle DELETE requests to delete asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Validate path parameters using request model
        try:
            request_model = parse(path_parameters, model=DeleteAssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)})
        
        # Validate asset link ID
        (valid, message) = validate({
            'assetLinkId': {
                'value': request_model.assetLinkId,
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        logger.info(f"Deleting metadata {request_model.metadataKey} for asset link {request_model.assetLinkId}")
        
        # Delete the metadata
        response = delete_asset_link_metadata(
            request_model.assetLinkId, 
            request_model.metadataKey, 
            claims_and_roles
        )
        return success(body=response.dict())
        
    except ValueError as v:
        logger.warning(f"Validation error in asset link metadata deletion: {v}")
        return validation_error(body={'message': str(v)})
    except PermissionError as p:
        logger.warning(f"Permission error in asset link metadata deletion: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error()

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset link metadata operations"""
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
        
        # Route to appropriate handler
        if method == 'POST':
            return handle_post_request(event)
        elif method == 'GET':
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
