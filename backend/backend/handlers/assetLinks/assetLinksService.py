# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from typing import Dict, List, Set, Optional
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
    GetAssetLinksRequestModel,
    GetAssetLinksResponseModel, 
    GetAssetLinksTreeViewResponseModel,
    GetSingleAssetLinkRequestModel,
    GetSingleAssetLinkResponseModel,
    UpdateAssetLinkRequestModel,
    UpdateAssetLinkResponseModel,
    DeleteAssetLinkRequestModel,
    DeleteAssetLinkResponseModel,
    AssetNodeModel, 
    AssetTreeNodeModel, 
    AssetLinkModel,
    UnauthorizedCountsModel,
    RelationshipType
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
logger = safeLogger(service_name="AssetLinksService")

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

def get_asset_details(asset_id: str, database_id: str) -> Optional[Dict]:
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

def batch_get_asset_details(asset_keys: List[tuple]) -> Dict[str, Dict]:
    """Batch get asset details for multiple assets"""
    asset_details = {}
    
    # Process in batches of 100 (DynamoDB batch_get_item limit)
    batch_size = 100
    for i in range(0, len(asset_keys), batch_size):
        batch = asset_keys[i:i + batch_size]
        
        try:
            request_items = {
                asset_storage_table_name: {
                    'Keys': [
                        {
                            'databaseId': database_id,
                            'assetId': asset_id
                        }
                        for database_id, asset_id  in batch
                    ]
                }
            }
            
            response = dynamodb.batch_get_item(RequestItems=request_items)
            
            for item in response.get('Responses', {}).get(asset_storage_table_name, []):
                key = f"{item['databaseId']}:{item['assetId']}"
                asset_details[key] = item
                
        except Exception as e:
            logger.exception(f"Error in batch get asset details: {e}")
            # Fall back to individual gets for this batch
            for database_id, asset_id  in batch:
                asset = get_asset_details(asset_id, database_id)
                if asset:
                    key = f"{database_id}:{asset_id}"
                    asset_details[key] = asset
    
    return asset_details

def check_asset_permission(asset: Dict, claims_and_roles: Dict, action: str = "GET") -> bool:
    """Check if user has permission to access an asset"""
    try:
        asset_copy = asset.copy()
        asset_copy.update({"object__type": "asset"})
        
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            return casbin_enforcer.enforce(asset_copy, action)
            
        return False
        
    except Exception as e:
        logger.exception(f"Error checking asset permission: {e}")
        return False

def delete_asset_link_metadata(asset_link_id: str):
    """Delete all metadata associated with an asset link"""
    try:
        # Query all metadata for this asset link
        response = asset_links_metadata_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('assetLinkId').eq(asset_link_id)
        )
        
        # Delete all metadata items
        with asset_links_metadata_table.batch_writer() as batch:
            for item in response.get('Items', []):
                batch.delete_item(
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
# GET Operations
#######################

def get_single_asset_link(asset_link_id: str, claims_and_roles: Dict) -> GetSingleAssetLinkResponseModel:
    """Get a single asset link by ID"""
    try:
        # Get the asset link
        response = asset_links_table.get_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        if 'Item' not in response:
            raise ValueError(f"Asset link {asset_link_id} not found")
            
        link_item = response['Item']
        
        # Check permissions for both assets
        from_asset = get_asset_details(link_item['fromAssetId'], link_item['fromAssetDatabaseId'])
        to_asset = get_asset_details(link_item['toAssetId'], link_item['toAssetDatabaseId'])
        
        if not from_asset or not to_asset:
            raise ValueError("One or both linked assets no longer exist")
            
        # Check permissions
        if not (check_asset_permission(from_asset, claims_and_roles) and 
                check_asset_permission(to_asset, claims_and_roles)):
            raise PermissionError("Not authorized to view this asset link")
        
        # Create response model
        asset_link = AssetLinkModel(
            assetLinkId=link_item['assetLinkId'],
            fromAssetId=link_item['fromAssetId'],
            fromAssetDatabaseId=link_item['fromAssetDatabaseId'],
            toAssetId=link_item['toAssetId'],
            toAssetDatabaseId=link_item['toAssetDatabaseId'],
            relationshipType=link_item['relationshipType'],
            tags=link_item.get('tags', [])
        )
        
        return GetSingleAssetLinkResponseModel(assetLink=asset_link)
        
    except Exception as e:
        logger.exception(f"Error getting single asset link: {e}")
        raise

def get_asset_links_for_asset(asset_id: str, database_id: str, child_tree_view: bool, claims_and_roles: Dict):
    """Get all asset links for a specific asset"""
    try:
        asset_key = f"{database_id}:{asset_id}"
        
        # Verify the target asset exists and user has permission
        target_asset = get_asset_details(asset_id, database_id)
        if not target_asset:
            raise ValueError(f"Asset {asset_id} not found in database {database_id}")
            
        if not check_asset_permission(target_asset, claims_and_roles):
            raise PermissionError("Not authorized to view links for this asset")
        
        # Get all links where this asset is the 'from' asset (children/related going out)
        from_response = asset_links_table.query(
            IndexName='fromAssetGSI',
            KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(asset_key)
        )
        
        # Get all links where this asset is the 'to' asset (parents/related coming in)
        to_response = asset_links_table.query(
            IndexName='toAssetGSI',
            KeyConditionExpression=Key('toAssetDatabaseId:toAssetId').eq(asset_key)
        )
        
        all_links = from_response.get('Items', []) + to_response.get('Items', [])
        
        # Collect all unique asset keys for batch retrieval
        asset_keys = set()
        for link in all_links:
            from_key = (link['fromAssetDatabaseId'], link['fromAssetId'])
            to_key = (link['toAssetDatabaseId'], link['toAssetId'])
            asset_keys.add(from_key)
            asset_keys.add(to_key)
        
        # Batch get asset details
        asset_details = batch_get_asset_details(list(asset_keys))
        
        # Organize relationships
        related_assets = []
        parent_assets = []
        child_assets = []
        
        unauthorized_counts = UnauthorizedCountsModel()
        
        for link in all_links:
            from_key = f"{link['fromAssetDatabaseId']}:{link['fromAssetId']}"
            to_key = f"{link['toAssetDatabaseId']}:{link['toAssetId']}"
            
            if link['relationshipType'] == RelationshipType.RELATED:
                # For related links, the other asset (not the queried one) is what we want
                if from_key == asset_key:
                    # This asset is the 'from', so the 'to' asset is related
                    other_asset = asset_details.get(to_key)
                    if other_asset and check_asset_permission(other_asset, claims_and_roles):
                        related_assets.append(AssetNodeModel(
                            assetId=link['toAssetId'],
                            assetName=other_asset.get('assetName', ''),
                            databaseId=link['toAssetDatabaseId'],
                            assetLinkId=link['assetLinkId']
                        ))
                    else:
                        unauthorized_counts.related += 1
                else:
                    # This asset is the 'to', so the 'from' asset is related
                    other_asset = asset_details.get(from_key)
                    if other_asset and check_asset_permission(other_asset, claims_and_roles):
                        related_assets.append(AssetNodeModel(
                            assetId=link['fromAssetId'],
                            assetName=other_asset.get('assetName', ''),
                            databaseId=link['fromAssetDatabaseId'],
                            assetLinkId=link['assetLinkId']
                        ))
                    else:
                        unauthorized_counts.related += 1
                        
            elif link['relationshipType'] == RelationshipType.PARENT_CHILD:
                if to_key == asset_key:
                    # This asset is the child, so the 'from' asset is the parent
                    parent_asset = asset_details.get(from_key)
                    if parent_asset and check_asset_permission(parent_asset, claims_and_roles):
                        parent_assets.append(AssetNodeModel(
                            assetId=link['fromAssetId'],
                            assetName=parent_asset.get('assetName', ''),
                            databaseId=link['fromAssetDatabaseId'],
                            assetLinkId=link['assetLinkId']
                        ))
                    else:
                        unauthorized_counts.parents += 1
                        
                elif from_key == asset_key:
                    # This asset is the parent, so the 'to' asset is the child
                    child_asset = asset_details.get(to_key)
                    if child_asset and check_asset_permission(child_asset, claims_and_roles):
                        child_assets.append(AssetNodeModel(
                            assetId=link['toAssetId'],
                            assetName=child_asset.get('assetName', ''),
                            databaseId=link['toAssetDatabaseId'],
                            assetLinkId=link['assetLinkId']
                        ))
                    else:
                        unauthorized_counts.children += 1
        
        # If tree view is requested, build the tree structure for children
        if child_tree_view:
            tree_children = build_child_tree(asset_id, database_id, claims_and_roles, unauthorized_counts)
            return GetAssetLinksTreeViewResponseModel(
                related=related_assets,
                parents=parent_assets,
                children=tree_children,
                unauthorizedCounts=unauthorized_counts
            )
        else:
            return GetAssetLinksResponseModel(
                related=related_assets,
                parents=parent_assets,
                children=child_assets,
                unauthorizedCounts=unauthorized_counts
            )
            
    except Exception as e:
        logger.exception(f"Error getting asset links: {e}")
        raise

def build_child_tree(root_asset_id: str, root_database_id: str, claims_and_roles: Dict, unauthorized_counts: UnauthorizedCountsModel) -> List[AssetTreeNodeModel]:
    """Build a recursive tree structure of child assets"""
    try:
        visited = set()
        
        def build_tree_recursive(asset_id: str, database_id: str) -> List[Dict]:
            """Recursively build tree nodes as dictionaries"""
            asset_key = f"{database_id}:{asset_id}"
            
            if asset_key in visited:
                return []  # Prevent infinite loops
                
            visited.add(asset_key)
            
            # Get all children of this asset
            response = asset_links_table.query(
                IndexName='fromAssetGSI',
                KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(asset_key),
                FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq(RelationshipType.PARENT_CHILD)
            )
            
            tree_nodes = []
            
            for link in response.get('Items', []):
                child_asset = get_asset_details(link['toAssetId'], link['toAssetDatabaseId'])
                
                if child_asset and check_asset_permission(child_asset, claims_and_roles):
                    # Recursively get children of this child
                    grandchildren = build_tree_recursive(link['toAssetId'], link['toAssetDatabaseId'])
                    
                    # Create a dictionary representation of the node
                    tree_node = {
                        "assetId": link['toAssetId'],
                        "assetName": child_asset.get('assetName', ''),
                        "databaseId": link['toAssetDatabaseId'],
                        "assetLinkId": link['assetLinkId'],
                        "children": grandchildren
                    }
                    tree_nodes.append(tree_node)
                else:
                    unauthorized_counts.children += 1
            
            return tree_nodes
        
        # Get the tree as dictionaries
        tree_dicts = build_tree_recursive(root_asset_id, root_database_id)
        
        # Convert to AssetTreeNodeModel objects
        def dict_to_model(node_dict):
            children_models = [dict_to_model(child) for child in node_dict["children"]]
            return AssetTreeNodeModel(
                assetId=node_dict["assetId"],
                assetName=node_dict["assetName"],
                databaseId=node_dict["databaseId"],
                assetLinkId=node_dict["assetLinkId"],
                children=children_models
            )
        
        return [dict_to_model(node) for node in tree_dicts]
        
    except Exception as e:
        logger.exception(f"Error building child tree: {e}")
        return []

#######################
# PUT Operations
#######################

def update_asset_link(asset_link_id: str, request_model: UpdateAssetLinkRequestModel, claims_and_roles: dict) -> UpdateAssetLinkResponseModel:
    """Update an asset link (currently only tags can be updated)"""
    try:
        # Get the asset link first
        response = asset_links_table.get_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        if 'Item' not in response:
            raise ValueError(f"Asset link {asset_link_id} not found")
            
        link_item = response['Item']
        
        # Check permissions for both assets
        from_asset = get_asset_details(link_item['fromAssetId'], link_item['fromAssetDatabaseId'])
        to_asset = get_asset_details(link_item['toAssetId'], link_item['toAssetDatabaseId'])
        
        if not from_asset or not to_asset:
            raise ValueError("One or both linked assets no longer exist")
            
        # Check permissions - user must have PUT permission on both assets
        if not (check_asset_permission(from_asset, claims_and_roles, "PUT") and 
                check_asset_permission(to_asset, claims_and_roles, "PUT")):
            raise PermissionError("Not authorized to update this asset link")
        
        # Update the asset link tags
        asset_links_table.update_item(
            Key={'assetLinkId': asset_link_id},
            UpdateExpression='SET tags = :tags',
            ExpressionAttributeValues={
                ':tags': request_model.tags
            }
        )
        
        logger.info(f"Updated asset link {asset_link_id} tags to {request_model.tags}")
        
        return UpdateAssetLinkResponseModel(
            message="Asset link updated successfully"
        )
        
    except Exception as e:
        logger.exception(f"Error updating asset link: {e}")
        raise

#######################
# DELETE Operations
#######################

def delete_asset_link(asset_link_id: str, claims_and_roles: dict) -> DeleteAssetLinkResponseModel:
    """Delete an asset link by ID"""
    try:
        # Get the asset link first
        response = asset_links_table.get_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        if 'Item' not in response:
            raise ValueError(f"Asset link {asset_link_id} not found")
            
        link_item = response['Item']
        
        # Check permissions for both assets
        from_asset = get_asset_details(link_item['fromAssetId'], link_item['fromAssetDatabaseId'])
        to_asset = get_asset_details(link_item['toAssetId'], link_item['toAssetDatabaseId'])
        
        if not from_asset or not to_asset:
            raise ValueError("One or both linked assets no longer exist")
            
        # Check permissions - user must have DELETE permission on both assets
        if not (check_asset_permission(from_asset, claims_and_roles, "DELETE") and 
                check_asset_permission(to_asset, claims_and_roles, "DELETE")):
            raise PermissionError("Not authorized to delete this asset link")
        
        # Delete associated metadata first
        delete_asset_link_metadata(asset_link_id)
        
        # Delete the asset link
        asset_links_table.delete_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        logger.info(f"Deleted asset link {asset_link_id} between {link_item['fromAssetId']} and {link_item['toAssetId']}")
        
        return DeleteAssetLinkResponseModel(
            message="Asset link deleted successfully"
        )
        
    except Exception as e:
        logger.exception(f"Error deleting asset link: {e}")
        raise

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for asset links"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Case 1: Get a single asset link
        if 'assetLinkId' in path_parameters:
            logger.info(f"Getting single asset link {path_parameters['assetLinkId']}")
            
            # Validate path parameters using request model
            try:
                request_model = parse(path_parameters, model=GetSingleAssetLinkRequestModel)
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
            
            # Get the single asset link
            response = get_single_asset_link(request_model.assetLinkId, claims_and_roles)
            return success(body=response.dict())
        
        # Case 2: Get asset links for a specific asset
        elif 'assetId' in path_parameters and 'databaseId' in path_parameters:
            logger.info(f"Getting asset links for asset {path_parameters['assetId']} in database {path_parameters['databaseId']}")
            
            # Validate path parameters
            (valid, message) = validate({
                'assetId': {
                    'value': path_parameters['assetId'],
                    'validator': 'ASSET_ID'
                },
                'databaseId': {
                    'value': path_parameters['databaseId'],
                    'validator': 'ID'
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message})
            
            # Parse and validate parameters using request model
            try:
                # Combine path and query parameters for the request model
                combined_params = {
                    'assetId': path_parameters['assetId'],
                    'databaseId': path_parameters['databaseId'],
                    'childTreeView': query_parameters.get('childTreeView', '').lower() == 'true'
                }
                
                request_model = parse(combined_params, model=GetAssetLinksRequestModel)
            except ValidationError as v:
                logger.exception(f"Validation error in parameters: {v}")
                return validation_error(body={'message': str(v)})
            
            # Get asset links
            response = get_asset_links_for_asset(
                request_model.assetId, 
                request_model.databaseId, 
                request_model.childTreeView, 
                claims_and_roles
            )
            return success(body=response.dict())
            
        else:
            return validation_error(body={'message': 'Asset ID, Database ID, or Asset Link ID is required'})
            
    except ValueError as v:
        logger.warning(f"Validation error in asset links retrieval: {v}")
        return validation_error(body={'message': str(v)})
    except PermissionError as p:
        logger.warning(f"Permission error in asset links retrieval: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()

def handle_put_request(event):
    """Handle PUT requests to update asset links"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Validate required path parameters
        if 'assetLinkId' not in path_parameters:
            return validation_error(body={'message': "Asset link ID is required"})
        
        # Parse and validate path parameters using request model
        try:
            path_request_model = parse(path_parameters, model=GetSingleAssetLinkRequestModel)
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
        
        logger.info(f"Updating asset link {path_request_model.assetLinkId}")
        
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
        request_model = parse(body, model=UpdateAssetLinkRequestModel)
        
        # Update the asset link
        response = update_asset_link(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValueError as v:
        logger.warning(f"Validation error in asset link update: {v}")
        return validation_error(body={'message': str(v)})
    except PermissionError as p:
        logger.warning(f"Permission error in asset link update: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error()

def handle_delete_request(event):
    """Handle DELETE requests for asset links"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Validate required path parameters
        if 'relationId' not in path_parameters:
            return validation_error(body={'message': "Asset link ID (relationId) is required"})
        
        # Parse and validate path parameters using request model
        try:
            request_model = parse(path_parameters, model=DeleteAssetLinkRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': str(v)})
        
        # Validate relation ID
        (valid, message) = validate({
            'relationId': {
                'value': request_model.relationId,
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        logger.info(f"Deleting asset link {request_model.relationId}")
        
        # Delete the asset link
        response = delete_asset_link(request_model.relationId, claims_and_roles)
        return success(body=response.dict())
        
    except ValueError as v:
        logger.warning(f"Validation error in asset link deletion: {v}")
        return validation_error(body={'message': str(v)})
    except PermissionError as p:
        logger.warning(f"Permission error in asset link deletion: {p}")
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
    """Lambda handler for asset links operations (GET, PUT, and DELETE)"""
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
