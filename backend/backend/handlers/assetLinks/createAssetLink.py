# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from typing import Set, List
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
from models.assetLinks import CreateAssetLinkRequestModel, CreateAssetLinkResponseModel, RelationshipType

# Configure AWS clients
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="CreateAssetLink")

# Load environment variables
try:
    asset_links_table_v2_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_links_table = dynamodb.Table(asset_links_table_v2_name)
asset_storage_table = dynamodb.Table(asset_storage_table_name)

#######################
# Utility Functions
#######################

def validate_assets_exist(from_asset_id: str, from_database_id: str, to_asset_id: str, to_database_id: str) -> bool:
    """Validate that both assets exist in their respective databases"""
    try:
        # Check from asset
        from_response = asset_storage_table.get_item(
            Key={
                'databaseId': from_database_id,
                'assetId': from_asset_id
            }
        )
        
        # Check to asset
        to_response = asset_storage_table.get_item(
            Key={
                'databaseId': to_database_id,
                'assetId': to_asset_id
            }
        )
        
        return 'Item' in from_response and 'Item' in to_response
    except Exception as e:
        logger.exception(f"Error validating assets exist: {e}")
        return False

def check_existing_relationship(from_asset_id: str, from_database_id: str, to_asset_id: str, to_database_id: str, relationship_type: str, alias_id: str = None) -> bool:
    """Check if a relationship already exists between two assets with the same aliasId"""
    try:
        from_key = f"{from_database_id}:{from_asset_id}"
        to_key = f"{to_database_id}:{to_asset_id}"
        
        # For 'related' relationships, check both directions (aliases not applicable)
        if relationship_type == RelationshipType.RELATED:
            # Check from -> to
            response1 = asset_links_table.query(
                IndexName='fromAssetGSI',
                KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(from_key) & 
                                     Key('toAssetDatabaseId:toAssetId').eq(to_key),
                FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq(RelationshipType.RELATED)
            )
            
            # Check to -> from
            response2 = asset_links_table.query(
                IndexName='fromAssetGSI',
                KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(to_key) & 
                                     Key('toAssetDatabaseId:toAssetId').eq(from_key),
                FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq(RelationshipType.RELATED)
            )
            
            return len(response1.get('Items', [])) > 0 or len(response2.get('Items', [])) > 0
        
        # For 'parentChild' relationships, use simplified approach
        else:
            # Normalize alias_id to empty string if None
            normalized_alias = alias_id if alias_id else ''
            
            # Step 1: Get ALL parent->child relationships for this parent-child pair using fromAssetGSI
            response = asset_links_table.query(
                IndexName='fromAssetGSI',
                KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(from_key) & 
                                     Key('toAssetDatabaseId:toAssetId').eq(to_key),
                FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq(RelationshipType.PARENT_CHILD)
            )
            
            existing_links = response.get('Items', [])
            
            # Step 2: Check if any of these links have the same aliasId (or both have no alias)
            for link in existing_links:
                existing_alias = link.get('assetLinkAliasId')
                # Both have no alias (None or missing)
                if not existing_alias and not alias_id:
                    return True
                # Both have the same alias value
                if existing_alias and alias_id and existing_alias == alias_id:
                    return True
            
            # Step 3: Check for reverse relationship (child->parent) - not allowed regardless of alias
            reverse_response = asset_links_table.query(
                IndexName='fromAssetGSI',
                KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(to_key) & 
                                     Key('toAssetDatabaseId:toAssetId').eq(from_key),
                FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq(RelationshipType.PARENT_CHILD)
            )
            
            # If any reverse relationships exist, it's bidirectional (not allowed)
            if len(reverse_response.get('Items', [])) > 0:
                return True
            
            return False
            
    except Exception as e:
        logger.exception(f"Error checking existing relationship: {e}")
        return True  # Err on the side of caution

def detect_cycle_in_parent_child(from_asset_id: str, from_database_id: str, to_asset_id: str, to_database_id: str) -> bool:
    """
    Detect if creating a parent-child relationship would create a cycle.
    This checks if the 'to' asset has any downstream children that eventually lead back to the 'from' asset.
    Note: Cycles are checked across ALL aliases - a cycle exists if ANY path exists regardless of aliasId.
    """
    try:
        visited: Set[str] = set()
        
        def has_path_to_asset(current_asset_id: str, current_database_id: str, target_asset_id: str, target_database_id: str) -> bool:
            """Recursively check if there's a path from current asset to target asset"""
            current_key = f"{current_database_id}:{current_asset_id}"
            target_key = f"{target_database_id}:{target_asset_id}"
            
            if current_key == target_key:
                return True
                
            if current_key in visited:
                return False
                
            visited.add(current_key)
            
            # Get all children of current asset (where current asset is the 'from' in parentChild relationships)
            # This will return multiple items if there are multiple aliases for the same parent-child pair
            try:
                response = asset_links_table.query(
                    IndexName='fromAssetGSI',
                    KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(current_key),
                    FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq(RelationshipType.PARENT_CHILD)
                )
                
                # Process all items (may include multiple with different aliases)
                for item in response.get('Items', []):
                    child_key = item['toAssetDatabaseId:toAssetId']
                    child_database_id, child_asset_id = child_key.split(':', 1)
                    
                    if has_path_to_asset(child_asset_id, child_database_id, target_asset_id, target_database_id):
                        return True
                        
                return False
                
            except Exception as e:
                logger.exception(f"Error in cycle detection recursive call: {e}")
                return True  # Err on the side of caution
        
        # Check if the 'to' asset has any path back to the 'from' asset
        return has_path_to_asset(to_asset_id, to_database_id, from_asset_id, from_database_id)
        
    except Exception as e:
        logger.exception(f"Error in cycle detection: {e}")
        return True  # Err on the side of caution

def check_asset_permissions(asset_id: str, database_id: str, claims_and_roles: dict, action: str) -> bool:
    """Check if user has permissions for the specified asset and action"""
    try:
        # Get asset details
        response = asset_storage_table.get_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            }
        )
        
        if 'Item' not in response:
            return False
            
        asset = response['Item']
        asset.update({"object__type": "asset"})
        
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            return casbin_enforcer.enforce(asset, action)
            
        return False
        
    except Exception as e:
        logger.exception(f"Error checking asset permissions: {e}")
        return False

#######################
# API Implementation
#######################

def create_asset_link(request_model: CreateAssetLinkRequestModel, claims_and_roles: dict) -> CreateAssetLinkResponseModel:
    """Create a new asset link with validation and cycle detection"""
    
    # Validate that assets are not the same
    if (request_model.fromAssetId == request_model.toAssetId and 
        request_model.fromAssetDatabaseId == request_model.toAssetDatabaseId):
        raise ValueError("Cannot create asset link to the same asset")
    
    # Validate that both assets exist
    if not validate_assets_exist(
        request_model.fromAssetId, 
        request_model.fromAssetDatabaseId,
        request_model.toAssetId, 
        request_model.toAssetDatabaseId
    ):
        raise ValueError("One or both assets do not exist")
    
    # Check permissions for both assets
    if not check_asset_permissions(request_model.fromAssetId, request_model.fromAssetDatabaseId, claims_and_roles, "POST"):
        raise PermissionError("Not authorized to create links for the source asset")
        
    if not check_asset_permissions(request_model.toAssetId, request_model.toAssetDatabaseId, claims_and_roles, "POST"):
        raise PermissionError("Not authorized to create links for the target asset")
    
    # Check for existing relationships with the same aliasId
    if check_existing_relationship(
        request_model.fromAssetId,
        request_model.fromAssetDatabaseId,
        request_model.toAssetId,
        request_model.toAssetDatabaseId,
        request_model.relationshipType,
        request_model.assetLinkAliasId
    ):
        if request_model.relationshipType == RelationshipType.PARENT_CHILD:
            if request_model.assetLinkAliasId:
                raise ValueError(f"Validation Error: A parent-child relationship already exists between these assets with provided alias")
            else:
                raise ValueError("Validation Error: A parent-child relationship already exists between these assets with provided alias")
        else:
            raise ValueError("Validation Error: A relationship already exists between these assets")
    
    # For parentChild relationships, check for cycles
    if request_model.relationshipType == RelationshipType.PARENT_CHILD:
        if detect_cycle_in_parent_child(
            request_model.fromAssetId,
            request_model.fromAssetDatabaseId,
            request_model.toAssetId,
            request_model.toAssetDatabaseId
        ):
            raise ValueError("Validation Error: Creating this parent-child relationship would create a cycle")
    
    # Generate new asset link ID
    asset_link_id = str(uuid.uuid4())
    
    # Create the asset link record
    asset_link_item = {
        'assetLinkId': asset_link_id,
        'fromAssetDatabaseId:fromAssetId': f"{request_model.fromAssetDatabaseId}:{request_model.fromAssetId}",
        'fromAssetDatabaseId': request_model.fromAssetDatabaseId,
        'fromAssetId': request_model.fromAssetId,
        'toAssetDatabaseId:toAssetId': f"{request_model.toAssetDatabaseId}:{request_model.toAssetId}",
        'toAssetDatabaseId': request_model.toAssetDatabaseId,
        'toAssetId': request_model.toAssetId,
        'relationshipType': request_model.relationshipType,
        'tags': request_model.tags or []
    }
    
    # Only add assetLinkAliasId if it has a value (DynamoDB GSI doesn't support empty strings)
    if request_model.assetLinkAliasId:
        asset_link_item['assetLinkAliasId'] = request_model.assetLinkAliasId
    
    try:
        # Save to DynamoDB
        asset_links_table.put_item(Item=asset_link_item)
        logger.info(f"Created asset link {asset_link_id} between {request_model.fromAssetId} and {request_model.toAssetId}")
        
        return CreateAssetLinkResponseModel(
            assetLinkId=asset_link_id,
            message="Asset link created successfully"
        )
        
    except Exception as e:
        logger.exception(f"Error saving asset link: {e}")
        raise RuntimeError(f"Failed to create asset link.")

#######################
# Request Handlers
#######################

def handle_post_request(event):
    """Handle POST requests to create asset links"""
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
        
        # Validate required fields
        required_fields = ['fromAssetId', 'fromAssetDatabaseId', 'toAssetId', 'toAssetDatabaseId', 'relationshipType']
        for field in required_fields:
            if field not in body:
                return validation_error(body={'message': f"Missing required field: {field}"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetLinkRequestModel)
        
        # Validate asset IDs and database IDs
        (valid, message) = validate({
            'fromAssetId': {
                'value': request_model.fromAssetId,
                'validator': 'ASSET_ID'
            },
            'fromAssetDatabaseId': {
                'value': request_model.fromAssetDatabaseId,
                'validator': 'ID'
            },
            'toAssetId': {
                'value': request_model.toAssetId,
                'validator': 'ASSET_ID'
            },
            'toAssetDatabaseId': {
                'value': request_model.toAssetDatabaseId,
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)
        
        logger.info(f"Creating asset link from {request_model.fromAssetId} to {request_model.toAssetId}")
        
        # Create the asset link
        response = create_asset_link(request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValueError as v:
        logger.warning(f"Validation error in asset link creation: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error in asset link creation: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset link creation API"""
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
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
