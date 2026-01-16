#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tag creation and update handler for VAMS API."""

import os
import boto3
import json
from datetime import datetime
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.tag import (
    CreateTagRequestModel, UpdateTagRequestModel, TagOperationResponseModel
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="CreateTag")

# Global variables for claims and roles
claims_and_roles = {}

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

def create_tag(tag_data, claims_and_roles):
    """Create a new tag
    
    Args:
        tag_data: Dictionary with tag creation data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        TagOperationResponseModel with operation result
    """
    try:
        tag_name = tag_data['tagName']
        
        # Check authorization
        tag_obj = {
            "object__type": "tag",
            "tagName": tag_name
        }
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(tag_obj, "POST"):
                raise authorization_error()
        
        # Check if tag already exists
        try:
            existing_tag = tag_table.get_item(Key={'tagName': tag_name})
            if 'Item' in existing_tag:
                raise VAMSGeneralErrorResponse("Tag already exists. Tags are unique across tag types.")
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking existing tag: {e}")
                raise VAMSGeneralErrorResponse("Error checking tag existence")
            raise
        
        # Check if tag type exists
        try:
            tag_type_response = tag_type_table.get_item(
                Key={'tagTypeName': tag_data['tagTypeName']}
            )
            
            if 'Item' not in tag_type_response:
                raise VAMSGeneralErrorResponse("Invalid tag type specified.")
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking tag type: {e}")
                raise VAMSGeneralErrorResponse("Error validating tag type")
            raise
        
        # Create the tag
        logger.info(f"Creating tag {tag_name}")
        tag_table.put_item(
            Item={
                'tagName': tag_name,
                'description': tag_data['description'],
                'tagTypeName': tag_data['tagTypeName']
            },
            ConditionExpression='attribute_not_exists(tagName)'
        )
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return TagOperationResponseModel(
            success=True,
            message=f"Tag {tag_name} created successfully",
            tagName=tag_name,
            operation="create",
            timestamp=now
        )
        
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating tag: {e}")
        raise VAMSGeneralErrorResponse("Error creating tag")

def update_tag(tag_data, claims_and_roles):
    """Update an existing tag
    
    Args:
        tag_data: Dictionary with tag update data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        TagOperationResponseModel with operation result
    """
    try:
        tag_name = tag_data['tagName']
        
        # Check authorization
        tag_obj = {
            "object__type": "tag",
            "tagName": tag_name
        }
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(tag_obj, "PUT"):
                raise authorization_error()
        
        # Check if tag exists
        try:
            tag_response = tag_table.query(
                KeyConditionExpression='tagName = :tag',
                ExpressionAttributeValues={':tag': tag_name}
            )
            
            if 'Items' not in tag_response or len(tag_response['Items']) == 0:
                raise VAMSGeneralErrorResponse("Tag not found")
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking tag existence: {e}")
                raise VAMSGeneralErrorResponse("Error validating tag")
            raise
        
        # Check if tag type exists
        try:
            tag_type_response = tag_type_table.get_item(
                Key={'tagTypeName': tag_data['tagTypeName']}
            )
            
            if 'Item' not in tag_type_response:
                raise VAMSGeneralErrorResponse("Invalid tag type specified.")
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking tag type: {e}")
                raise VAMSGeneralErrorResponse("Error validating tag type")
            raise
        
        # Update the tag
        logger.info(f"Updating tag {tag_name}")
        tag_table.update_item(
            Key={'tagName': tag_name},
            UpdateExpression='SET tagTypeName = :tag_type, description = :desc',
            ExpressionAttributeValues={
                ':tag_type': tag_data['tagTypeName'],
                ':desc': tag_data['description']
            },
            ConditionExpression='attribute_exists(tagName)'
        )
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return TagOperationResponseModel(
            success=True,
            message=f"Tag {tag_name} updated successfully",
            tagName=tag_name,
            operation="update",
            timestamp=now
        )
        
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating tag: {e}")
        raise VAMSGeneralErrorResponse("Error updating tag")

#######################
# Request Handlers
#######################

def handle_post_request(event):
    """Handle POST requests to create tags
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body with enhanced error handling (Pattern 1: Required Body)
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
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateTagRequestModel)
        
        # Create the tag
        result = create_tag(
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)

def handle_put_request(event):
    """Handle PUT requests to update tags
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body with enhanced error handling (Pattern 1: Required Body)
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
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateTagRequestModel)
        
        # Update the tag
        result = update_tag(
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for tag creation and update APIs"""
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
        if method == 'POST':
            return handle_post_request(event)
        elif method == 'PUT':
            return handle_put_request(event)
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
