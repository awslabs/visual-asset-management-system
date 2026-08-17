# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from botocore.config import Config

from common.resourceNames import get_table_name, ResourceKeys
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from common.tagScope import GLOBAL_SCOPE, normalize_scope

# GSI on the tag-type table's name attribute, used for cross-scope name lookups.
TAG_TYPE_NAME_INDEX = "tagTypeNameIndex"
from common.constants import STANDARD_JSON_RESPONSE
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

try:
    tag_table_name = get_table_name(ResourceKeys.TAG_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving tags table name")
    tag_table_name = None

try:
    tag_type_table_name = get_table_name(ResourceKeys.TAG_TYPE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving tag types table name")
    tag_type_table_name = None

tag_table = dynamodb.Table(tag_table_name) if tag_table_name else None
tag_type_table = dynamodb.Table(tag_type_table_name) if tag_type_table_name else None

#######################
# Business Logic Functions
#######################


def _query_all_in_partition(table, scope):
    """Every item in one partition of a composite tag/tag-type table.

    A single query returns at most 1MB, so a scoped listing has to follow LastEvaluatedKey or it
    silently truncates and reports the short list as complete. The asset-side lookup in
    createAsset.py pages the same way.
    """
    items = []
    query_kwargs = {'KeyConditionExpression': Key('databaseId').eq(scope)}
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get('Items', []))
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            return items
        query_kwargs['ExclusiveStartKey'] = last_key


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
        requested_database_id = query_params.get('databaseId')
        scope = query_params.get('scope')  # 'global' | 'all' | None

        deserializer = TypeDeserializer()
        paginator = dynamodb_client.get_paginator('scan')

        # Select tag types by scope:
        #   ?databaseId=X -> single-partition query on that database
        #   ?scope=global -> single-partition query on the GLOBAL partition
        #   ?scope=all / none -> full scan (bounded vocabulary)
        tag_type_next_token = None
        if requested_database_id:
            raw_tag_type_items = _query_all_in_partition(tag_type_table, requested_database_id)
        elif scope == 'global':
            raw_tag_type_items = _query_all_in_partition(tag_type_table, GLOBAL_SCOPE)
        else:
            page_iterator_tag_types = paginator.paginate(
                TableName=tag_type_table_name,
                PaginationConfig={
                    'MaxItems': int(query_params['maxItems']),
                    'PageSize': int(query_params['pageSize']),
                    'StartingToken': query_params.get('startingToken')
                }
            ).build_full_result()
            raw_tag_type_items = [
                {k: deserializer.deserialize(v) for k, v in tt.items()}
                for tt in page_iterator_tag_types.get("Items", [])
            ]
            if 'NextToken' in page_iterator_tag_types:
                tag_type_next_token = page_iterator_tag_types['NextToken']

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
                # Keyed by SCOPE + name. The same name may exist as a GLOBAL entry and as a
                # database-specific one; keying by name alone would list one scope's tags
                # under the other scope's tag type.
                key = (normalize_scope(deserialized_tag.get("databaseId")), tag_type_name)
                formatted_tag_results.setdefault(key, []).append(tag_name)
        
        # Process tag types and check authorization
        formatted_tag_type_results = []
        for deserialized_tag_type in raw_tag_type_items:
            tag_type = {
                "tagTypeName": deserialized_tag_type.get("tagTypeName"),
                "description": deserialized_tag_type.get("description"),
                "required": deserialized_tag_type.get("required", "False"),
                "databaseId": normalize_scope(deserialized_tag_type.get("databaseId")),
                "tags": formatted_tag_results.get(
                    (normalize_scope(deserialized_tag_type.get("databaseId")),
                     deserialized_tag_type.get("tagTypeName")),
                    [],
                )
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
        if tag_type_next_token:
            result['NextToken'] = tag_type_next_token

        return result
        
    except Exception as e:
        logger.exception(f"Error getting tag types: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving tag types: {str(e)}")

def delete_tag_type(tag_type_name: str, claims_and_roles: dict, database_id: str = None) -> TagTypeOperationResponseModel:
    """Delete a tag type

    Args:
        tag_type_name: Name of the tag type to delete
        claims_and_roles: User claims and roles for authorization
        database_id: The scope (databaseId) the tag type lives in; defaults to GLOBAL

    Returns:
        TagTypeOperationResponseModel with operation result

    Raises:
        VAMSGeneralErrorResponse: If tag type not found, in use, or deletion fails
    """
    try:
        scope = normalize_scope(database_id)

        # Get the tag type (composite key)
        tag_type_response = tag_type_table.get_item(Key={'databaseId': scope, 'tagTypeName': tag_type_name})
        tag_type = tag_type_response.get("Item")

        if not tag_type:
            raise VAMSGeneralErrorResponse("Tag type not found", status_code=404)

        stored_scope = normalize_scope(tag_type.get("databaseId"))

        # Check if tag type is in use by any referencing tag. A GLOBAL tag type can be
        # A tag resolves its tag type within its OWN scope, so a reference blocks the delete only
        # when the tag resolves to THIS type:
        #   database-scoped type -> only same-database tags can reference it.
        #   GLOBAL type          -> GLOBAL tags, plus a tag in a database that has no tag type of
        #                           this name of its own (such a tag can only mean the shared one).
        # The second clause matters because the same name may exist as a GLOBAL type and as a
        # database-specific type: that database's tags belong to ITS type and must not block the
        # shared one from being deleted.
        scopes_with_own_type = set()
        if stored_scope == GLOBAL_SCOPE:
            own_type_rows = tag_type_table.query(
                IndexName=TAG_TYPE_NAME_INDEX,
                KeyConditionExpression=Key('tagTypeName').eq(tag_type_name),
            )
            for row in own_type_rows.get('Items', []):
                row_scope = normalize_scope(row.get("databaseId"))
                if row_scope != GLOBAL_SCOPE:
                    scopes_with_own_type.add(row_scope)

        # Page the scan to exhaustion so references beyond the first 1MB page are seen.
        last_evaluated_key = None
        while True:
            scan_kwargs = {}
            if last_evaluated_key:
                scan_kwargs['ExclusiveStartKey'] = last_evaluated_key
            scan_response = tag_table.scan(**scan_kwargs)
            for tag in scan_response.get('Items', []):
                if tag.get("tagTypeName") != tag_type_name:
                    continue
                tag_scope = normalize_scope(tag.get("databaseId"))
                if stored_scope == GLOBAL_SCOPE:
                    references_this_type = (
                        tag_scope == GLOBAL_SCOPE or tag_scope not in scopes_with_own_type
                    )
                else:
                    references_this_type = tag_scope == stored_scope
                if references_this_type:
                    raise VAMSGeneralErrorResponse(
                        "Cannot delete tag type that is currently in use by a tag",
                        status_code=400
                    )
            last_evaluated_key = scan_response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break

        # Check authorization (scope-aware: auth against the type's stored scope)
        tag_type.update({
            "object__type": "tagType",
            "databaseId": stored_scope,
        })
        if len(claims_and_roles["tokens"]) == 0:
            raise VAMSGeneralErrorResponse("Not authorized to delete tag type", status_code=403)
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(tag_type, "DELETE"):
            raise VAMSGeneralErrorResponse("Not authorized to delete tag type", status_code=403)

        # Delete the tag type
        logger.info(f"Deleting tag type: {tag_type_name}")
        tag_type_table.delete_item(
            Key={'databaseId': stored_scope, 'tagTypeName': tag_type_name},
            ConditionExpression='attribute_exists(databaseId) AND attribute_exists(tagTypeName)'
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
                'startingToken': request_model.startingToken,
                'databaseId': query_parameters.get('databaseId'),
                'scope': query_parameters.get('scope')
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
        return general_error(body={'message': str(v)}, status_code=v.status_code, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)

def handle_delete_request(event):
    """Handle DELETE requests to delete tag types
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {}) or {}
    query_parameters = event.get('queryStringParameters', {}) or {}

    try:
        # Validate path parameters
        tag_type_name = path_parameters.get("tagTypeId")

        if not tag_type_name or len(tag_type_name) == 0:
            return validation_error(body={'message': "TagTypeName is a required path parameter"}, event=event)

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
            return validation_error(body={'message': message}, event=event)

        # Optional scope: defaults to GLOBAL when the databaseId query param is absent
        database_id = query_parameters.get("databaseId")

        # Delete tag type
        result = delete_tag_type(tag_type_name, claims_and_roles, database_id)
        
        return success(body=result.dict())
        
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, status_code=v.status_code, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)

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
