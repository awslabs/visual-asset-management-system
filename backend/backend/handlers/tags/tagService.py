#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tag service handler for VAMS API."""

import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.validators import validate
from common.dynamodb import validate_pagination_info
from common.constants import STANDARD_JSON_RESPONSE
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.tag import (
    GetTagsRequestModel, TagResponseModel, TagOperationResponseModel
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="TagService")

# Global variables for claims and roles
claims_and_roles = {}

deserializer = TypeDeserializer()
paginator = dynamodb_client.get_paginator('scan')

# Load environment variables with error handling
try:
    tag_db_table_name = os.environ["TAGS_STORAGE_TABLE_NAME"]
    tag_type_db_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
tag_table = dynamodb.Table(tag_db_table_name)
tag_type_table = dynamodb.Table(tag_type_db_table_name)

#######################
# Business Logic Functions
#######################

def get_tag_types():
    """Get all tag types from DynamoDB
    
    Returns:
        List of tag type dictionaries with tagTypeName and required fields
    """
    try:
        raw_tag_type_items = []
        page_iterator_tag_types = paginator.paginate(
            TableName=tag_type_db_table_name,
            PaginationConfig={
                'MaxItems': 1000,
                'PageSize': 1000,
            }
        ).build_full_result()
        
        if len(page_iterator_tag_types["Items"]) > 0:
            raw_tag_type_items.extend(page_iterator_tag_types["Items"])
            while "NextToken" in page_iterator_tag_types:
                page_iterator_tag_types = paginator.paginate(
                    TableName=tag_type_db_table_name,
                    PaginationConfig={
                        'MaxItems': 1000,
                        'PageSize': 1000,
                        'StartingToken': page_iterator_tag_types["NextToken"]
                    }
                ).build_full_result()
                if len(page_iterator_tag_types["Items"]) > 0:
                    raw_tag_type_items.extend(page_iterator_tag_types["Items"])
        
        tag_type_results = []
        for tag_type_result in raw_tag_type_items:
            deserialized_document = {k: deserializer.deserialize(v) for k, v in tag_type_result.items()}
            
            tag_type = {
                "tagTypeName": deserialized_document["tagTypeName"],
                "required": deserialized_document.get("required", "False"),
            }
            
            tag_type_results.append(tag_type)
        
        return tag_type_results
        
    except Exception as e:
        logger.exception(f"Error getting tag types: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving tag types")

def get_tags(query_params):
    """Get all tags with pagination and authorization filtering
    
    Args:
        query_params: Dictionary with pagination parameters (maxItems, pageSize, startingToken)
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    try:
        # Get tag types for required tags designation
        tag_types = get_tag_types()
        
        # Query tags with pagination
        page_iterator_tags = paginator.paginate(
            TableName=tag_db_table_name,
            PaginationConfig={
                'MaxItems': int(query_params['maxItems']),
                'PageSize': int(query_params['pageSize']),
                'StartingToken': query_params.get('startingToken')
            }
        ).build_full_result()
        
        authorized_tags = []
        
        for tag in page_iterator_tags["Items"]:
            deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}
            
            # For each tag type coming back from tags, add "[R]" to the end if it matches to a required tag type
            for tag_type in tag_types:
                if deserialized_document["tagTypeName"] == tag_type["tagTypeName"]:
                    if tag_type["required"] == "True":
                        deserialized_document["tagTypeName"] = deserialized_document["tagTypeName"] + " [R]"
                    break
            
            # Add Casbin Enforcer to check if the current user has permissions to GET the Tag
            deserialized_document.update({"object__type": "tag"})
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_document, "GET"):
                    authorized_tags.append(deserialized_document)
        
        result = {"Items": authorized_tags}
        if 'NextToken' in page_iterator_tags:
            result['NextToken'] = page_iterator_tags['NextToken']
        
        return result
        
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting tags: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving tags")

def delete_tag(tag_name, claims_and_roles):
    """Delete a tag
    
    Args:
        tag_name: The tag name to delete
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        TagOperationResponseModel with operation result
    """
    try:
        # Validate tag name
        (valid, message) = validate({
            'tagName': {
                'value': tag_name,
                'validator': 'OBJECT_NAME'
            }
        })
        
        if not valid:
            logger.error(message)
            raise VAMSGeneralErrorResponse(message)
        
        # Get the tag
        tag_response = tag_table.get_item(Key={'tagName': tag_name})
        tag = tag_response.get("Item", {})
        
        if not tag:
            raise VAMSGeneralErrorResponse("Tag not found")
        
        # Check authorization
        tag.update({"object__type": "tag"})
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(tag, "DELETE"):
                raise authorization_error()
        
        # Delete the tag
        logger.info(f"Deleting tag: {tag_name}")
        tag_table.delete_item(
            Key={'tagName': tag_name},
            ConditionExpression='attribute_exists(tagName)'
        )
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return TagOperationResponseModel(
            success=True,
            message=f"Tag {tag_name} deleted successfully",
            tagName=tag_name,
            operation="delete",
            timestamp=now
        )
        
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting tag: {e}")
        raise VAMSGeneralErrorResponse("Error deleting tag")

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for tags
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate query parameters using GetTagsRequestModel
        try:
            request_model = parse(query_parameters, model=GetTagsRequestModel)
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
        
        # Get the tags
        tags_result = get_tags(query_params)
        
        # Convert to TagResponseModel instances
        formatted_items = []
        for item in tags_result.get('Items', []):
            try:
                tag_model = TagResponseModel(**item)
                formatted_items.append(tag_model.dict())
            except ValidationError:
                # Fall back to raw item if conversion fails
                formatted_items.append(item)
        
        # Build response with formatted items
        response = {"Items": formatted_items}
        if 'NextToken' in tags_result:
            response['NextToken'] = tags_result['NextToken']
        
        # Wrap in "message" for backwards compatibility with tagTypeService format
        return success(body={"message": response})
        
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)

def handle_delete_request(event):
    """Handle DELETE requests for tags
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Get tag name from path parameters
        tag_name = path_parameters.get("tagId")
        
        if not tag_name or len(tag_name) == 0:
            return validation_error(body={'message': "Tag name is required"}, event=event)
        
        # Delete the tag
        result = delete_tag(tag_name, claims_and_roles)
        
        # Return success response
        return success(body=result.dict())
        
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for tag service APIs"""
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
        elif method == 'DELETE':
            return handle_delete_request(event)
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
