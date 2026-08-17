#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tag creation and update handler for VAMS API."""

import boto3
import json
from datetime import datetime
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate
from common.tagScope import GLOBAL_SCOPE, normalize_scope, verify_database_exists, name_used_by_any_database

TAG_NAME_INDEX = "tagNameIndex"
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse, validation_error_message
from models.tag import (
    CreateTagRequestModel, UpdateTagRequestModel, TagOperationResponseModel
)
from handlers.auth import request_to_claims

# Advisory returned when a GLOBAL entry is created over a name a database already uses. Kept
# generic on purpose: Rule 11 forbids naming another database in a client-facing message.
DUPLICATE_SCOPE_WARNING_TAG = (
    "This name is also used by a database-specific tag."
    " Asset forms will list both entries until the database-specific tag is removed."
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

try:
    tag_db_table_name = get_table_name(ResourceKeys.TAG_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving tags table name")
    tag_db_table_name = None

try:
    tag_type_db_table_name = get_table_name(ResourceKeys.TAG_TYPE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving tag types table name")
    tag_type_db_table_name = None

try:
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving database table name")
    database_table_name = None

tag_table = dynamodb.Table(tag_db_table_name) if tag_db_table_name else None
tag_type_table = dynamodb.Table(tag_type_db_table_name) if tag_type_db_table_name else None
database_table = dynamodb.Table(database_table_name) if database_table_name else None

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
        database_id = normalize_scope(tag_data.get('databaseId'))

        # Check authorization (scope-aware)
        tag_obj = {
            "object__type": "tag",
            "tagName": tag_name,
            "databaseId": database_id,
        }
        if len(claims_and_roles["tokens"]) == 0:
            return authorization_error()
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(tag_obj, "POST"):
            return authorization_error()

        # Scope-conflict rule, asymmetric by design:
        #   GLOBAL  over a database-specific name -> ALLOWED, with a warning. Promoting a name to the
        #           shared vocabulary is the normal direction of travel, and blocking it would force an
        #           admin to delete every database copy before the shared entry could exist at all.
        #   database-specific over a GLOBAL name  -> REJECTED. A database cannot shadow the shared
        #           vocabulary, because an asset stores a bare name and could no longer be read.
        warnings = []
        try:
            if database_id == GLOBAL_SCOPE:
                if name_used_by_any_database(tag_table, TAG_NAME_INDEX, 'tagName', tag_name):
                    logger.warning(
                        f"Global tag {tag_name} created while a database-specific tag of that name exists"
                    )
                    warnings.append(DUPLICATE_SCOPE_WARNING_TAG)
            else:
                # Creating a database tag: reject if a GLOBAL tag of this name exists.
                global_tag = tag_table.get_item(
                    Key={'databaseId': GLOBAL_SCOPE, 'tagName': tag_name}
                )
                if 'Item' in global_tag:
                    raise VAMSGeneralErrorResponse(
                        "A global tag already uses this name."
                    )
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking tag name conflicts: {e}")
                raise VAMSGeneralErrorResponse("Error checking tag existence")
            raise

        # Check if tag already exists within this scope
        try:
            existing_tag = tag_table.get_item(Key={'databaseId': database_id, 'tagName': tag_name})
            if 'Item' in existing_tag:
                raise VAMSGeneralErrorResponse("Tag already exists in this scope.")
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking existing tag: {e}")
                raise VAMSGeneralErrorResponse("Error checking tag existence")
            raise

        # Referenced database must exist (skipped for GLOBAL)
        verify_database_exists(database_id, database_table)

        # TagType <-> Tag scope coupling: a tag's type must live in the tag's OWN scope.
        #   global tag  -> tag type must exist as GLOBAL
        #   scoped tag  -> tag type must exist in that same database; a GLOBAL type is NOT accepted,
        #                  so a database's tags are described only by that database's categories.
        try:
            tag_type_name = tag_data['tagTypeName']
            type_in_scope = tag_type_table.get_item(
                Key={'databaseId': database_id, 'tagTypeName': tag_type_name}
            )
            if 'Item' not in type_in_scope:
                raise VAMSGeneralErrorResponse(
                    "Invalid tag type specified. A tag type in the same scope as the tag is required."
                )
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking tag type: {e}")
                raise VAMSGeneralErrorResponse("Error validating tag type")
            raise

        # Create the tag
        logger.info(f"Creating tag {tag_name}")
        tag_table.put_item(
            Item={
                'databaseId': database_id,
                'tagName': tag_name,
                'description': tag_data['description'],
                'tagTypeName': tag_data['tagTypeName']
            },
            ConditionExpression='attribute_not_exists(databaseId) AND attribute_not_exists(tagName)'
        )
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return TagOperationResponseModel(
            success=True,
            message=f"Tag {tag_name} created successfully",
            tagName=tag_name,
            operation="create",
            timestamp=now,
            warnings=warnings or None
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
        requested_scope = normalize_scope(tag_data.get('databaseId'))

        # Load existing tag first (needed for stored-scope auth + immutability check).
        # Scope is immutable, so the tag lives under its request-supplied scope partition.
        tag_response = tag_table.get_item(Key={'databaseId': requested_scope, 'tagName': tag_name})
        if 'Item' not in tag_response:
            raise VAMSGeneralErrorResponse("Tag not found")
        existing_tag = tag_response['Item']
        stored_scope = normalize_scope(existing_tag.get('databaseId'))

        # Authorization uses the STORED scope (a DB-X admin cannot edit a global/DB-Y tag)
        tag_obj = {"object__type": "tag", "tagName": tag_name, "databaseId": stored_scope}
        if len(claims_and_roles["tokens"]) == 0:
            return authorization_error()
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(tag_obj, "PUT"):
            return authorization_error()

        # Scope is immutable: reject any attempt to change databaseId
        if tag_data.get('databaseId') is not None and normalize_scope(tag_data['databaseId']) != stored_scope:
            raise VAMSGeneralErrorResponse(
                "Tag scope cannot be changed. Delete and recreate the tag to change its scope."
            )

        # Same coupling on update, against the stored (immutable) scope: the new tag type must live in
        # the tag's own scope, so an edit cannot move a scoped tag onto a GLOBAL category.
        try:
            tag_type_name = tag_data['tagTypeName']
            type_in_scope = tag_type_table.get_item(
                Key={'databaseId': stored_scope, 'tagTypeName': tag_type_name}
            )
            if 'Item' not in type_in_scope:
                raise VAMSGeneralErrorResponse(
                    "Invalid tag type specified. A tag type in the same scope as the tag is required."
                )
        except Exception as e:
            if not isinstance(e, VAMSGeneralErrorResponse):
                logger.exception(f"Error checking tag type: {e}")
                raise VAMSGeneralErrorResponse("Error validating tag type")
            raise

        # Update the tag
        logger.info(f"Updating tag {tag_name}")
        tag_table.update_item(
            Key={'databaseId': stored_scope, 'tagName': tag_name},
            UpdateExpression='SET tagTypeName = :tag_type, description = :desc',
            ExpressionAttributeValues={
                ':tag_type': tag_data['tagTypeName'],
                ':desc': tag_data['description']
            },
            ConditionExpression='attribute_exists(databaseId) AND attribute_exists(tagName)'
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
        return validation_error(body={'message': validation_error_message(v)}, event=event)
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
        return validation_error(body={'message': validation_error_message(v)}, event=event)
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
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
