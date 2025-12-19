# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from botocore.config import Config

from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from common.constants import STANDARD_JSON_RESPONSE
from models.common import (
    APIGatewayProxyResponseV2,
    success,
    validation_error,
    authorization_error,
    general_error,
    internal_error,
    VAMSGeneralErrorResponse
)
from models.tag import (
    GetTagTypesRequestModel,
    DeleteTagTypeRequestModel,
    TagTypeResponseModel,
    TagTypeOperationResponseModel
)

# Configure retry
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="TagTypeService")

# Global variables
claims_and_roles = {}

# Load environment variables
try:
    tag_table_name = os.environ["TAGS_STORAGE_TABLE_NAME"]
    tag_type_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
tag_table = dynamodb.Table(tag_table_name)
tag_type_table = dynamodb.Table(tag_type_table_name)

#######################
# Business Logic Functions
#######################

def get_tag_types(query_params: dict, claims_and_roles: dict) -> dict:
    """Get all tag types with their associated tags
    
    Args:
        query_params: Pagination parameters (maxItems, pageSize, startingToken)
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        Dictionary with Items (list of tag types) and optional NextToken
        
    Raises:
        VAMSGeneralErrorResponse: If retrieval fails
    """
    try:
        deserializer = TypeDeserializer()
        paginator = dynamodb_client.get_paginator('scan')
        
        # Get tag types with pagination
        page_iterator_tag_types = paginator.paginate(
            TableName=tag_type_table_name,
            PaginationConfig={
                'MaxItems': int(query_params['maxItems']),
                'PageSize': int(query_params['pageSize']),
                'StartingToken': query_params.get('startingToken')
            }
        ).build_full_result()
        
        # Get all tags (no pagination needed for tags lookup)
        raw_tag_items = []
        page_iterator_tags = paginator.paginate(
            TableName=tag_table_name,
            PaginationConfig={
                'MaxItems': 1000,
                'PageSize': 1000,
            }
        ).build_full_result()
        
        if len(page_iterator_tags.get("Items", [])) > 0:
            raw_tag_items.extend(page_iterator_tags["Items"])
            
            # Continue fetching if there are more tags
            while "NextToken" in page_iterator_tags:
                page_iterator_tags = paginator.paginate(
                    TableName=tag_table_name,
                    PaginationConfig={
                        'MaxItems': 1000,
                        'PageSize': 1000,
                        'StartingToken': page_iterator_tags["NextToken"]
                    }
                ).build_full_result()
                if len(page_iterator_tags.get("Items", [])) > 0:
                    raw_tag_items.extend(page_iterator_tags["Items"])
        
        # Deserialize and organize tags by tag type
        formatted_tag_results = {}
        for tag in raw_tag_items:
            deserialized_tag = {k: deserializer.deserialize(v) for k, v in tag.items()}
            tag_name = deserialized_tag.get("tagName")
            tag_type_name = deserialized_tag.get("tagTypeName")
            
            if tag_type_name and tag_name:
                if tag_type_name not in formatted_tag_results:
                    formatted_tag_results[tag_type_name] = [tag_name]
                else:
                    formatted_tag_results[tag_type_name].append(tag_name)
        
        # Process tag types and check authorization
        formatted_tag_type_results = []
        for tag_type_item in page_iterator_tag_types.get("Items", []):
            deserialized_tag_type = {k: deserializer.deserialize(v) for k, v in tag_type_item.items()}
            
            tag_type = {
                "tagTypeName": deserialized_tag_type.get("tagTypeName"),
                "description": deserialized_tag_type.get("description"),
                "required": deserialized_tag_type.get("required", "False"),
                "tags": formatted_tag_results.get(deserialized_tag_type.get("tagTypeName"), [])
            }
            
            # Check authorization
            tag_type.update({"object__type": "tagType"})
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(tag_type, "GET"):
                    # Remove object__type before adding to results
                    tag_type.pop("object__type", None)
                    formatted_tag_type_results.append(tag_type)
            else:
                # No authorization required, add all
                tag_type.pop("object__type", None)
                formatted_tag_type_results.append(tag_type)
        
        # Build response
        result = {"Items": formatted_tag_type_results}
        if 'NextToken' in page_iterator_tag_types:
            result['NextToken'] = page_iterator_tag_types['NextToken']
        
        return result
        
    except Exception as e:
        logger.exception(f"Error getting tag types: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving tag types: {str(e)}")

def delete_tag_type(tag_type_name: str, claims_and_roles: dict) -> TagTypeOperationResponseModel:
    """Delete a tag type
    
    Args:
        tag_type_name: Name of the tag type to delete
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        TagTypeOperationResponseModel with operation result
        
    Raises:
        VAMSGeneralErrorResponse: If tag type not found, in use, or deletion fails
    """
    try:
        # Get the tag type
        tag_type_response = tag_type_table.get_item(Key={'tagTypeName': tag_type_name})
        tag_type = tag_type_response.get("Item")
        
        if not tag_type:
            raise VAMSGeneralErrorResponse("Tag type not found", status_code=404)
        
        # Check if tag type is in use by any tags
        tag_results = tag_table.scan().get('Items', [])
        for tag in tag_results:
            if tag.get("tagTypeName") == tag_type_name:
                raise VAMSGeneralErrorResponse(
                    "Cannot delete tag type that is currently in use by a tag",
                    status_code=400
                )
        
        # Check authorization
        tag_type.update({"object__type": "tagType"})
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(tag_type, "DELETE"):
                raise VAMSGeneralErrorResponse("Not authorized to delete tag type", status_code=403)
        
        # Delete the tag type
        logger.info(f"Deleting tag type: {tag_type_name}")
        tag_type_table.delete_item(
            Key={'tagTypeName': tag_type_name},
            ConditionExpression='attribute_exists(tagTypeName)'
        )
        
        # Return success response
        timestamp = datetime.utcnow().isoformat()
        return TagTypeOperationResponseModel(
            success=True,
            message=f"Tag type '{tag_type_name}' deleted successfully",
            tagTypeName=tag_type_name,
            operation="delete",
            timestamp=timestamp
        )
        
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting tag type: {e}")
        if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            raise VAMSGeneralErrorResponse("Tag type not found", status_code=404)
        raise VAMSGeneralErrorResponse(f"Error deleting tag type: {str(e)}")

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests to list tag types
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate query parameters using GetTagTypesRequestModel
        try:
            request_model = parse(query_parameters, model=GetTagTypesRequestModel)
            # Extract validated parameters for the query
            query_params = {
                'maxItems': request_model.maxItems,
                'pageSize': request_model.pageSize,
                'startingToken': request_model.startingToken
            }
        except ValidationError as v:
            logger.exception(f"Validation error in query parameters: {v}")
            # Fall back to default pagination with validation
            validate_pagination_info(query_parameters)
            query_params = query_parameters
        
        # Get tag types
        result = get_tag_types(query_params, claims_and_roles)
        
        # Convert to response models
        formatted_items = []
        for item in result.get('Items', []):
            try:
                tag_type_model = TagTypeResponseModel(**item)
                formatted_items.append(tag_type_model.dict())
            except ValidationError:
                # Fall back to raw item if conversion fails
                formatted_items.append(item)
        
        # Build response
        response = {"Items": formatted_items}
        if 'NextToken' in result:
            response['NextToken'] = result['NextToken']
        
        return success(body={"message": response})
        
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()

def handle_delete_request(event):
    """Handle DELETE requests to delete tag types
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Validate path parameters
        tag_type_name = path_parameters.get("tagTypeId")
        
        if not tag_type_name or len(tag_type_name) == 0:
            return validation_error(body={'message': "TagTypeName is a required path parameter"})
        
        # Validate tag type name format
        from common.validators import validate
        (valid, message) = validate({
            'tagTypeName': {
                'value': tag_type_name,
                'validator': 'OBJECT_NAME'
            }
        })
        
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        # Delete tag type
        result = delete_tag_type(tag_type_name, claims_and_roles)
        
        return success(body=result.dict())
        
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error()

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for tag type service operations (GET, DELETE)"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
        method = event['requestContext']['http']['method']
        
        # Validate pagination info for GET requests
        if method == 'GET':
            query_parameters = event.get('queryStringParameters', {})
            validate_pagination_info(query_parameters)
        
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
        elif method == 'DELETE':
            return handle_delete_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"})
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
