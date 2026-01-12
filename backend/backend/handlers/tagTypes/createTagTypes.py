# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from datetime import datetime
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from botocore.config import Config

from common.constants import STANDARD_JSON_RESPONSE
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
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
    CreateTagTypeRequestModel,
    UpdateTagTypeRequestModel,
    TagTypeOperationResponseModel
)

# Configure retry
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="CreateTagType")

# Global variables
claims_and_roles = {}

# Load environment variables
try:
    tag_type_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB table
tag_type_table = dynamodb.Table(tag_type_table_name)

#######################
# Business Logic Functions
#######################

def create_tag_type(request_model: CreateTagTypeRequestModel, claims_and_roles: dict) -> TagTypeOperationResponseModel:
    """Create a new tag type
    
    Args:
        request_model: Validated request model with tag type data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        TagTypeOperationResponseModel with operation result
        
    Raises:
        VAMSGeneralErrorResponse: If tag type already exists or creation fails
    """
    try:
        # Check if tag type already exists
        existing = tag_type_table.get_item(Key={'tagTypeName': request_model.tagTypeName})
        if 'Item' in existing:
            raise VAMSGeneralErrorResponse("Tag type already exists", status_code=400)
        
        # Create item
        item = {
            "tagTypeName": request_model.tagTypeName,
            "description": request_model.description,
            "required": request_model.required
        }
        
        # Check authorization
        item.update({"object__type": "tagType"})
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(item, "POST"):
                raise VAMSGeneralErrorResponse("Not authorized to create tag type", status_code=403)
        
        # Save to DynamoDB
        tag_type_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(tagTypeName)"
        )
        
        logger.info(f"Created tag type: {request_model.tagTypeName}")
        
        # Return success response
        timestamp = datetime.utcnow().isoformat()
        return TagTypeOperationResponseModel(
            success=True,
            message=f"Tag type '{request_model.tagTypeName}' created successfully",
            tagTypeName=request_model.tagTypeName,
            operation="create",
            timestamp=timestamp
        )
        
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating tag type: {e}")
        if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            raise VAMSGeneralErrorResponse("Tag type already exists", status_code=400)
        raise VAMSGeneralErrorResponse(f"Error creating tag type: {str(e)}")

def update_tag_type(request_model: UpdateTagTypeRequestModel, claims_and_roles: dict) -> TagTypeOperationResponseModel:
    """Update an existing tag type
    
    Args:
        request_model: Validated request model with updated tag type data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        TagTypeOperationResponseModel with operation result
        
    Raises:
        VAMSGeneralErrorResponse: If tag type not found or update fails
    """
    try:
        # Check if tag type exists
        existing = tag_type_table.get_item(Key={'tagTypeName': request_model.tagTypeName})
        if 'Item' not in existing:
            raise VAMSGeneralErrorResponse("Tag type not found", status_code=404)
        
        # Check authorization
        tag_type = existing['Item']
        tag_type.update({"object__type": "tagType"})
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(tag_type, "PUT"):
                raise VAMSGeneralErrorResponse("Not authorized to update tag type", status_code=403)
        
        # Update in DynamoDB
        tag_type_table.update_item(
            Key={'tagTypeName': request_model.tagTypeName},
            UpdateExpression='SET description = :desc, required = :req',
            ExpressionAttributeValues={
                ':desc': request_model.description,
                ':req': request_model.required
            },
            ConditionExpression='attribute_exists(tagTypeName)'
        )
        
        logger.info(f"Updated tag type: {request_model.tagTypeName}")
        
        # Return success response
        timestamp = datetime.utcnow().isoformat()
        return TagTypeOperationResponseModel(
            success=True,
            message=f"Tag type '{request_model.tagTypeName}' updated successfully",
            tagTypeName=request_model.tagTypeName,
            operation="update",
            timestamp=timestamp
        )
        
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating tag type: {e}")
        if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            raise VAMSGeneralErrorResponse("Tag type not found", status_code=404)
        raise VAMSGeneralErrorResponse(f"Error updating tag type: {str(e)}")

#######################
# Request Handlers
#######################

def handle_post_request(event):
    """Handle POST requests to create tag types
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body
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
        
        # Parse and validate with Pydantic
        request_model = parse(body, model=CreateTagTypeRequestModel)
        
        # Create tag type
        result = create_tag_type(request_model, claims_and_roles)
        
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error()

def handle_put_request(event):
    """Handle PUT requests to update tag types
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body
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
        
        # Parse and validate with Pydantic
        request_model = parse(body, model=UpdateTagTypeRequestModel)
        
        # Update tag type
        result = update_tag_type(request_model, claims_and_roles)
        
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error()

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for tag type create/update operations"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
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
