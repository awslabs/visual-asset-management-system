# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import json
from datetime import datetime
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from botocore.config import Config

from common.constants import STANDARD_JSON_RESPONSE
from common.resourceNames import get_table_name, ResourceKeys
from common.tagScope import GLOBAL_SCOPE, normalize_scope, verify_database_exists, name_used_by_any_database

TAG_TYPE_NAME_INDEX = "tagTypeNameIndex"
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import (
    validation_error_message,
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
from handlers.auth import request_to_claims

# Advisory returned when a GLOBAL entry is created over a name a database already uses. Kept
# generic on purpose: Rule 11 forbids naming another database in a client-facing message.
DUPLICATE_SCOPE_WARNING_TAG_TYPE = (
    "This name is also used by a database-specific tag type."
    " Asset forms will list both entries until the database-specific tag type is removed."
)

# Configure retry
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="CreateTagType")

# Global variables
claims_and_roles = {}

try:
    tag_type_table_name = get_table_name(ResourceKeys.TAG_TYPE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving tag types table name")
    tag_type_table_name = None

try:
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving database table name")
    database_table_name = None

tag_type_table = dynamodb.Table(tag_type_table_name) if tag_type_table_name else None
database_table = dynamodb.Table(database_table_name) if database_table_name else None

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
        tag_type_name = request_model.tagTypeName
        database_id = normalize_scope(getattr(request_model, "databaseId", None))

        # Create item
        item = {
            "tagTypeName": tag_type_name,
            "description": request_model.description,
            "required": request_model.required,
            "databaseId": database_id
        }

        # Check authorization (scope-aware)
        auth_obj = dict(item)
        auth_obj.update({"object__type": "tagType"})
        if len(claims_and_roles["tokens"]) == 0:
            raise VAMSGeneralErrorResponse("Not authorized to create tag type", status_code=403)
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(auth_obj, "POST"):
            raise VAMSGeneralErrorResponse("Not authorized to create tag type", status_code=403)

        # Scope-conflict rule, asymmetric by design:
        #   GLOBAL  over a database-specific name -> ALLOWED, with a warning. Promoting a name to
        #           the shared vocabulary is the normal direction of travel, and blocking it would
        #           force an admin to delete every database copy before the shared entry existed.
        #   database-specific over a GLOBAL name  -> REJECTED. A database cannot shadow the shared
        #           vocabulary, because an asset stores a bare name and could no longer be read.
        warnings = []
        if database_id == GLOBAL_SCOPE:
            if name_used_by_any_database(tag_type_table, TAG_TYPE_NAME_INDEX, 'tagTypeName', tag_type_name):
                logger.warning(
                    f"Global tag type {tag_type_name} created while a database-specific tag type of "
                    "that name exists"
                )
                warnings.append(DUPLICATE_SCOPE_WARNING_TAG_TYPE)
        else:
            global_type = tag_type_table.get_item(
                Key={'databaseId': GLOBAL_SCOPE, 'tagTypeName': tag_type_name}
            )
            if 'Item' in global_type:
                raise VAMSGeneralErrorResponse(
                    "A global tag type already uses this name.", status_code=400
                )

        # Check if tag type already exists within this scope
        existing = tag_type_table.get_item(Key={'databaseId': database_id, 'tagTypeName': tag_type_name})
        if 'Item' in existing:
            raise VAMSGeneralErrorResponse("Tag type already exists", status_code=400)

        # Referenced database must exist (skipped for GLOBAL)
        verify_database_exists(database_id, database_table)

        # Save to DynamoDB
        tag_type_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(databaseId) AND attribute_not_exists(tagTypeName)"
        )
        
        logger.info(f"Created tag type: {request_model.tagTypeName}")
        
        # Return success response
        timestamp = datetime.utcnow().isoformat()
        return TagTypeOperationResponseModel(
            success=True,
            message=f"Tag type '{request_model.tagTypeName}' created successfully",
            tagTypeName=request_model.tagTypeName,
            operation="create",
            timestamp=timestamp,
            warnings=warnings or None
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
        tag_type_name = request_model.tagTypeName
        requested_scope = normalize_scope(getattr(request_model, "databaseId", None))

        # Check if tag type exists (composite key; scope is immutable so it lives
        # under its request-supplied scope partition)
        existing = tag_type_table.get_item(Key={'databaseId': requested_scope, 'tagTypeName': tag_type_name})
        if 'Item' not in existing:
            raise VAMSGeneralErrorResponse("Tag type not found", status_code=404)

        # Authorization uses the STORED scope (a DB-X admin cannot edit a global/DB-Y type)
        tag_type = existing['Item']
        stored_scope = normalize_scope(tag_type.get('databaseId'))
        tag_type.update({"object__type": "tagType", "databaseId": stored_scope})
        if len(claims_and_roles["tokens"]) == 0:
            raise VAMSGeneralErrorResponse("Not authorized to update tag type", status_code=403)
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(tag_type, "PUT"):
            raise VAMSGeneralErrorResponse("Not authorized to update tag type", status_code=403)

        # Scope is immutable: reject any attempt to change databaseId
        requested = getattr(request_model, "databaseId", None)
        if requested is not None and normalize_scope(requested) != stored_scope:
            raise VAMSGeneralErrorResponse(
                "Tag type scope cannot be changed. Delete and recreate to change scope."
            )

        # Update in DynamoDB
        tag_type_table.update_item(
            Key={'databaseId': stored_scope, 'tagTypeName': tag_type_name},
            UpdateExpression='SET description = :desc, required = :req',
            ExpressionAttributeValues={
                ':desc': request_model.description,
                ':req': request_model.required
            },
            ConditionExpression='attribute_exists(databaseId) AND attribute_exists(tagTypeName)'
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
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse and validate with Pydantic
        request_model = parse(body, model=CreateTagTypeRequestModel)
        
        # Create tag type
        result = create_tag_type(request_model, claims_and_roles)
        
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)

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
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse and validate with Pydantic
        request_model = parse(body, model=UpdateTagTypeRequestModel)
        
        # Update tag type
        result = update_tag_type(request_model, claims_and_roles)
        
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)

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
            return validation_error(body={'message': "Method not allowed"}, event=event)
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
