# Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auth Constraints service handler for VAMS API."""

import base64
import boto3
import json
import uuid
from datetime import datetime
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import (
    STANDARD_JSON_RESPONSE,
    ALLOWED_CONSTRAINT_PERMISSIONS,
    ALLOWED_CONSTRAINT_PERMISSION_TYPES,
    ALLOWED_CONSTRAINT_OBJECT_TYPES,
    ALLOWED_CONSTRAINT_OPERATORS,
    CONSTRAINT_OBJECT_TYPE_FIELDS,
    CONSTRAINT_OPERATOR_LABELS,
    CONSTRAINT_PERMISSION_LABELS,
    CONSTRAINT_PERMISSION_TYPE_LABELS,
)
from common.apiRoutes import API_AUTH_CONSTRAINT_PERMISSION_OBJECTS
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_auth_changes
from common.dynamodb import validate_pagination_info
from models.common import (
    validation_error_message,
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.roleConstraints import (
    GetConstraintsRequestModel, CreateConstraintRequestModel,
    ConstraintResponseModel, ConstraintOperationResponseModel,
    GetConstraintPermissionObjectsResponseModel,
    MAX_CONSTRAINT_LIST_PAGE_SIZE, MAX_LIST_MAX_ITEMS,
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="AuthConstraintsService")

# Global variables for claims and roles
claims_and_roles = {}

try:
    constraints_table_name = get_table_name(ResourceKeys.CONSTRAINTS_STORAGE_TABLE)
    roles_table_name = get_table_name(ResourceKeys.ROLES_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

constraints_table = dynamodb.Table(constraints_table_name)
roles_table = dynamodb.Table(roles_table_name)

#######################
# Helper Functions for New Table Format
#######################

def _constraint_id_filter(base_constraint_id):
    """Build the scan filter that addresses exactly one constraint

    Matches the constraint's own items - the base constraintId and its
    `#group#`/`#user#` denormalized items - and nothing belonging to another
    constraint whose ID merely starts with the same characters.

    Args:
        base_constraint_id: The base constraint ID (without #group# or #user# suffix)

    Returns:
        A boto3 condition expression scoped to that constraint's items
    """
    return (
        Attr('constraintId').eq(base_constraint_id)
        | Attr('constraintId').begins_with(f"{base_constraint_id}#")
    )


def _transform_to_denormalized_format(constraint_data):
    """Transform constraint data to denormalized table format
    Creates one item per UNIQUE group/user for efficient GSI queries
    
    Args:
        constraint_data: Constraint data in API format
        
    Returns:
        List of items formatted for denormalized ConstraintsStorageTable
    """
    items = []
    base_constraint_id = constraint_data['identifier']
    
    # Base constraint data shared across all denormalized items
    base_item = {
        'name': constraint_data.get('name', ''),
        'description': constraint_data.get('description', ''),
        'objectType': constraint_data.get('objectType', ''),
        # Store complex data as JSON strings
        'criteriaAnd': json.dumps(constraint_data.get('criteriaAnd', [])),
        'criteriaOr': json.dumps(constraint_data.get('criteriaOr', [])),
        'groupPermissions': json.dumps(constraint_data.get('groupPermissions', [])),
        'userPermissions': json.dumps(constraint_data.get('userPermissions', [])),
        # Metadata
        'dateCreated': constraint_data.get('dateCreated', datetime.utcnow().isoformat()),
        'dateModified': datetime.utcnow().isoformat(),
        'createdBy': constraint_data.get('createdBy', 'SYSTEM_USER'),
        'modifiedBy': constraint_data.get('modifiedBy', 'SYSTEM_USER'),
    }
    
    # Create one item per UNIQUE groupId (not per permission)
    # Multiple permissions for same group are stored in the groupPermissions JSON
    group_permissions = constraint_data.get('groupPermissions', [])
    unique_group_ids = set()
    for group_perm in group_permissions:
        group_id = group_perm.get('groupId')
        if group_id and group_id not in unique_group_ids:
            unique_group_ids.add(group_id)
            item = base_item.copy()
            item['constraintId'] = f"{base_constraint_id}#group#{group_id}"
            item['groupId'] = group_id  # For GroupPermissionsIndex GSI
            items.append(item)
    
    # Create one item per UNIQUE userId (not per permission)
    # Multiple permissions for same user are stored in the userPermissions JSON
    user_permissions = constraint_data.get('userPermissions', [])
    unique_user_ids = set()
    for user_perm in user_permissions:
        user_id = user_perm.get('userId')
        if user_id and user_id not in unique_user_ids:
            unique_user_ids.add(user_id)
            item = base_item.copy()
            item['constraintId'] = f"{base_constraint_id}#user#{user_id}"
            item['userId'] = user_id  # For UserPermissionsIndex GSI
            items.append(item)
    
    # Safety: If no permissions exist, create one base item (shouldn't happen in practice)
    if len(items) == 0:
        item = base_item.copy()
        item['constraintId'] = base_constraint_id
        items.append(item)
    
    return items


def _transform_from_new_format(item):
    """Transform new table format back to API response format
    
    Args:
        item: Item from new ConstraintsStorageTable
        
    Returns:
        Dictionary in API response format with base constraintId
    """
    # Helper function to parse field that might be JSON string or already parsed
    def parse_field(value, default=[]):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return default
        elif isinstance(value, list):
            return value
        return default
    
    # Extract base constraintId (remove #group# or #user# suffix for API response)
    full_constraint_id = item.get('constraintId', '')
    base_constraint_id = full_constraint_id.split('#group#')[0].split('#user#')[0]
    
    # Parse JSON strings back to objects (handle both string and already-parsed cases)
    constraint = {
        'constraintId': base_constraint_id,  # Return base ID without denormalization suffix
        'name': item.get('name', ''),
        'description': item.get('description', ''),
        'objectType': item.get('objectType', ''),
        'criteriaAnd': parse_field(item.get('criteriaAnd'), []),
        'criteriaOr': parse_field(item.get('criteriaOr'), []),
        'groupPermissions': parse_field(item.get('groupPermissions'), []),
        'userPermissions': parse_field(item.get('userPermissions'), []),
    }
    
    # Add metadata if present
    if 'dateCreated' in item:
        constraint['dateCreated'] = item['dateCreated']
    if 'dateModified' in item:
        constraint['dateModified'] = item['dateModified']
    if 'createdBy' in item:
        constraint['createdBy'] = item['createdBy']
    if 'modifiedBy' in item:
        constraint['modifiedBy'] = item['modifiedBy']
    
    return constraint


#######################
# Business Logic Functions
#######################

def validate_constraint_role_exists(group_id):
    """Validate that a role/group exists in the roles table
    
    Args:
        group_id: The role/group ID to validate
        
    Returns:
        True only when the lookup succeeds and the role exists; False when the role
        is absent or the lookup could not be completed
    """
    try:
        role_response = roles_table.get_item(Key={'roleName': group_id})
        return 'Item' in role_response
    except Exception as e:
        logger.exception(f"Could not validate groupId '{group_id}': {e}")
        return False


def get_constraint_details(constraint_id):
    """Get constraint details from denormalized DynamoDB table
    Scans for any item with the base constraintId (may have #group# or #user# suffix)
    
    Args:
        constraint_id: The base constraint ID (without #group# or #user# suffix)
        
    Returns:
        The constraint details or None if not found
    """
    try:
        # Scan for the constraint's own items: the base constraintId and the
        # denormalized IDs carrying a #group#/#user# suffix
        logger.info(f"Scanning for constraint with ID: {constraint_id}")

        response = constraints_table.scan(
            FilterExpression=_constraint_id_filter(constraint_id)
        )
        
        items = response.get('Items', [])
        logger.info(f"Scan found {len(items)} items for constraint {constraint_id}")
        
        if items:
            logger.debug(f"Retrieved constraint {constraint_id}")
            return _transform_from_new_format(items[0])
        
        logger.warning(f"No items found for constraint {constraint_id}")
        return None
    except Exception as e:
        logger.exception(f"Error getting constraint details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving constraint")


def _decode_constraint_offset(starting_token):
    """Decode a listing startingToken into the row offset the page starts at

    Args:
        starting_token: The opaque base64 NextToken handed back by a previous page

    Returns:
        The zero-based offset into the deduplicated constraint list

    Raises:
        VAMSGeneralErrorResponse: The token is not one this listing emitted
    """
    if not starting_token:
        return 0

    # base64.b64decode discards characters outside the alphabet, so a value like
    # "[object Object]" decodes without raising and only fails at int()
    try:
        offset = int(base64.b64decode(starting_token).decode('utf-8'))
    except (ValueError, base64.binascii.Error, UnicodeDecodeError, TypeError) as e:
        logger.exception(f"Invalid startingToken format: {e}")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

    if offset < 0:
        logger.warning("Rejected a startingToken that decoded to a negative offset")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

    return offset


def get_all_constraints(query_params):
    """Get all constraints with pagination from denormalized table
    Deduplicates by base constraintId to return unique constraints

    A constraint's `#group#`/`#user#` items each carry their own partition key, so they are
    spread through scan order rather than sitting together: the deduplication has to see the
    whole table before it can say how many distinct constraints there are, and the page is an
    offset slice of that deduplicated, ordered list. pageSize therefore counts constraints.
    The ordering is what makes the offset stable, since scan order is not contractual and every
    page re-reads the table.

    A layout that pages at the cursor instead would store one base item per constraint and keep
    the `#group#`/`#user#` items as GSI projections only, so the base items could be paged
    directly; moving to it requires migrating the stored items.

    Args:
        query_params: Query parameters for pagination

    Returns:
        Dictionary with Items and optional NextToken
    """
    try:
        # The page served is the smallest of the slice asked for, the ceiling allowed, and the
        # payload bound for whole-constraint items
        page_size = min(
            int(query_params.get('pageSize') or MAX_CONSTRAINT_LIST_PAGE_SIZE),
            int(query_params.get('maxItems') or MAX_LIST_MAX_ITEMS),
            MAX_CONSTRAINT_LIST_PAGE_SIZE,
        )
        start = _decode_constraint_offset(query_params.get('startingToken'))

        # Deduplicate by base constraintId (remove #group# or #user# suffixes) across the whole
        # table, so a constraint whose items straddle a scan page is counted once
        unique_constraints = {}
        row_count = 0
        scan_kwargs = {}
        while True:
            response = constraints_table.scan(**scan_kwargs)
            items = response.get('Items', [])
            row_count += len(items)
            for item in items:
                full_constraint_id = item.get('constraintId', '')
                base_constraint_id = full_constraint_id.split('#group#')[0].split('#user#')[0]

                # Only keep the first occurrence of each base constraintId
                if base_constraint_id not in unique_constraints:
                    unique_constraints[base_constraint_id] = item

            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

        ordered_ids = sorted(unique_constraints)
        page = [unique_constraints[base_id] for base_id in ordered_ids[start:start + page_size]]

        # Transform items from new format to API format
        formatted_items = [_transform_from_new_format(item) for item in page]

        result = {'Items': formatted_items}

        # Handle pagination token - base64 of the next offset so it survives query-string
        # round tripping as an opaque string
        next_offset = start + page_size
        if next_offset < len(ordered_ids):
            result['NextToken'] = base64.b64encode(
                str(next_offset).encode('utf-8')
            ).decode('utf-8')

        logger.debug(
            f"Retrieved {len(formatted_items)} of {len(ordered_ids)} unique constraints "
            f"from {row_count} denormalized items"
        )
        return result
    except Exception as e:
        logger.exception(f"Error getting all constraints: {e}")
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        raise VAMSGeneralErrorResponse("Error retrieving constraints")


def create_or_update_constraint(constraint_data, claims_and_roles):
    """Create or update a constraint in denormalized format
    Creates/updates multiple items (one per group/user permission)
    
    Args:
        constraint_data: Dictionary with constraint data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        ConstraintOperationResponseModel with operation result
    """
    try:
        # Generate unique constraintId if not provided or empty
        constraint_id = constraint_data.get('identifier', '').strip()
        if not constraint_id:
            constraint_id = str(uuid.uuid4())
            constraint_data['identifier'] = constraint_id
            logger.info(f"Generated new constraintId: {constraint_id}")
        
        # Note: Constraints don't have object-level authorization in Casbin
        # Only API-level authorization is checked in the lambda_handler
        # This is because constraints are configuration objects, not data entities
        
        # Validate role existence for groupPermissions
        for group_perm in constraint_data.get('groupPermissions', []):
            if not validate_constraint_role_exists(group_perm['groupId']):
                raise VAMSGeneralErrorResponse(f"Group/Role does not exist")
        
        logger.info(f"Creating/updating constraint {constraint_id}")
        
        # Add metadata
        now = datetime.utcnow().isoformat()
        username = claims_and_roles["tokens"][0] if claims_and_roles.get("tokens") else "SYSTEM_USER"
        constraint_data['dateModified'] = now
        constraint_data['modifiedBy'] = username
        if 'dateCreated' not in constraint_data:
            constraint_data['dateCreated'] = now
            constraint_data['createdBy'] = username
        
        # Delete existing denormalized items for this constraint (if updating)
        try:
            _delete_denormalized_items(constraint_id)
        except Exception as delete_error:
            logger.warning(f"Error deleting old denormalized items: {delete_error}")
        
        # Transform to denormalized format (returns array of items)
        denormalized_items = _transform_to_denormalized_format(constraint_data)
        
        # Write all denormalized items using batch write for efficiency
        with constraints_table.batch_writer() as batch:
            for item in denormalized_items:
                batch.put_item(Item=item)
        
        logger.info(f"Successfully wrote {len(denormalized_items)} denormalized items for constraint {constraint_id}")
        
        # Determine if this was a create or update operation
        operation = "update" if 'dateCreated' in constraint_data and constraint_data['dateCreated'] != now else "create"
        
        # Return success response
        return ConstraintOperationResponseModel(
            success=True,
            message=f"Constraint {constraint_id} created/updated successfully",
            constraintId=constraint_id,
            operation=operation,
            timestamp=now
        )
    except Exception as e:
        logger.exception(f"Error creating/updating constraint: {e}")
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        raise VAMSGeneralErrorResponse("Error creating/updating constraint")


def _delete_denormalized_items(base_constraint_id):
    """Delete all denormalized items for a constraint
    
    Args:
        base_constraint_id: The base constraint ID (without #group# or #user# suffix)
    """
    try:
        logger.info(f"Scanning for items to delete for constraint: {base_constraint_id}")

        # Scan for the constraint's own items only - the base constraintId and its
        # #group#/#user# denormalized items
        response = constraints_table.scan(
            FilterExpression=_constraint_id_filter(base_constraint_id)
        )
        
        items_to_delete = response.get('Items', [])
        logger.info(f"Found {len(items_to_delete)} items to delete for constraint {base_constraint_id}")
        
        # Handle pagination if there are many items
        while 'LastEvaluatedKey' in response:
            response = constraints_table.scan(
                FilterExpression=_constraint_id_filter(base_constraint_id),
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items_to_delete.extend(response.get('Items', []))
        
        # Delete all denormalized items using batch write
        if items_to_delete:
            with constraints_table.batch_writer() as batch:
                for item in items_to_delete:
                    logger.debug(f"Deleting item: {item['constraintId']}")
                    batch.delete_item(Key={'constraintId': item['constraintId']})
            logger.info(f"Successfully deleted {len(items_to_delete)} denormalized items for constraint {base_constraint_id}")
        else:
            logger.warning(f"No items found to delete for constraint {base_constraint_id}")
    except Exception as e:
        logger.exception(f"Error deleting denormalized items: {e}")
        # Don't raise - allow delete to continue even if cleanup fails


def delete_constraint(constraint_id, claims_and_roles):
    """Delete a constraint and all its denormalized items
    
    Args:
        constraint_id: The base constraint ID
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        ConstraintOperationResponseModel with operation result
    """
    try:
        # Note: Constraints don't have object-level authorization in Casbin
        # Only API-level authorization is checked in the lambda_handler
        # This is because constraints are configuration objects, not data entities
        
        logger.info(f"Deleting constraint {constraint_id} and all denormalized items")
        
        # Delete all denormalized items for this constraint
        # Don't check existence first - just try to delete
        # This handles eventual consistency issues and is more efficient
        _delete_denormalized_items(constraint_id)
        
        # Check if any items were actually deleted by doing a quick scan
        check_response = constraints_table.scan(
            FilterExpression=_constraint_id_filter(constraint_id),
            Limit=1
        )
        
        if len(check_response.get('Items', [])) > 0:
            # Items still exist, deletion may have failed
            logger.warning(f"Items still exist after deletion attempt for constraint {constraint_id}")
            raise VAMSGeneralErrorResponse("Error deleting constraint - items may still exist")
        
        logger.info(f"Successfully deleted all items for constraint {constraint_id}")
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return ConstraintOperationResponseModel(
            success=True,
            message=f"Constraint {constraint_id} deleted successfully",
            constraintId=constraint_id,
            operation="delete",
            timestamp=now
        )
    except Exception as e:
        logger.exception(f"Error deleting constraint: {e}")
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        raise VAMSGeneralErrorResponse("Error deleting constraint")


#######################
# Request Handlers
#######################

def get_constraint_permission_objects(event):
    """Return the constraint permission objects: object types (with their valid
    fields), operators, permissions, and permission types."""
    object_types = [
        {
            "label": CONSTRAINT_OBJECT_TYPE_FIELDS[object_type]["label"],
            "value": object_type,
            "fields": CONSTRAINT_OBJECT_TYPE_FIELDS[object_type]["fields"],
        }
        for object_type in ALLOWED_CONSTRAINT_OBJECT_TYPES
        if object_type in CONSTRAINT_OBJECT_TYPE_FIELDS
    ]
    response_model = GetConstraintPermissionObjectsResponseModel(
        objectTypes=object_types,
        operators=CONSTRAINT_OPERATOR_LABELS,
        permissions=CONSTRAINT_PERMISSION_LABELS,
        permissionTypes=CONSTRAINT_PERMISSION_TYPE_LABELS,
    )
    return success(body=response_model.dict())


def handle_get_request(event):
    """Handle GET requests for constraints

    Args:
        event: API Gateway event

    Returns:
        APIGatewayProxyResponseV2 response
    """
    path = event['requestContext']['http']['path']
    # The permissionObjects listing must be matched before the constraint list / by-id
    # logic, since /auth/constraints/permissionObjects also matches the {constraintId} template.
    if API_AUTH_CONSTRAINT_PERMISSION_OBJECTS.matches(path):
        return get_constraint_permission_objects(event)

    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Case 1: Get a specific constraint
        if 'constraintId' in path_parameters:
            constraint_id = path_parameters['constraintId']
            logger.info(f"Getting constraint {constraint_id}")
            
            # Validate constraintId
            (valid, message) = validate({
                'constraintId': {
                    'value': constraint_id,
                    'validator': 'OBJECT_NAME'
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)
            
            # Get the constraint
            constraint = get_constraint_details(constraint_id)
            
            # Check if constraint exists
            # Note: Constraints don't have object-level authorization in Casbin
            # Only API-level authorization is checked in the lambda_handler
            if constraint:
                # Convert to ConstraintResponseModel for consistent response format
                try:
                    response_model = ConstraintResponseModel(**constraint)
                    return success(body={"constraint": response_model.dict()})
                except ValidationError as v:
                    logger.exception(f"Error converting constraint to response model: {v}")
                    # Fall back to raw response if conversion fails
                    return success(body={"constraint": constraint})
            else:
                return general_error(body={"message": "Constraint not found"}, status_code=404, event=event)
        
        # Case 2: List all constraints
        else:
            logger.info("Listing all constraints")
            
            # Parse and validate query parameters
            try:
                request_model = parse(query_parameters, model=GetConstraintsRequestModel)
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
            
            # Get all constraints
            constraints_result = get_all_constraints(query_params)
            
            # Convert to ConstraintResponseModel instances
            formatted_items = []
            for item in constraints_result.get('Items', []):
                try:
                    constraint_model = ConstraintResponseModel(**item)
                    formatted_items.append(constraint_model.dict())
                except ValidationError:
                    # Fall back to raw item if conversion fails
                    formatted_items.append(item)
            
            # Build response
            response = {"message": {"Items": formatted_items}}
            if 'NextToken' in constraints_result:
                response['message']['NextToken'] = constraints_result['NextToken']
            
            return success(body=response)
    
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_post_request(event):
    """Handle POST requests to create/update constraints
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
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
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # If constraintId is in path, use it; otherwise use identifier from body
        if 'constraintId' in path_parameters:
            constraint_id = path_parameters['constraintId']
            # Validate constraintId from path
            (valid, message) = validate({
                'constraintId': {
                    'value': constraint_id,
                    'validator': 'OBJECT_NAME'
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)
            
            # Override identifier in body with path parameter
            body['identifier'] = constraint_id
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateConstraintRequestModel)
        
        # Create/update the constraint
        result = create_or_update_constraint(
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # AUDIT LOG: Constraint created/updated
        log_auth_changes(event, "constraintCreateUpdate", {
            "constraintId": result.constraintId,
            "operation": result.operation,
            "objectType": request_model.objectType,
            "groupPermissions": request_model.groupPermissions,
            "userPermissions": request_model.userPermissions
        })
        
        # Return success response with constraint data
        response_body = result.dict()
        response_body['constraint'] = json.dumps(request_model.dict(exclude_unset=True))
        
        return success(body=response_body)
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_delete_request(event):
    """Handle DELETE requests for constraints
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    # Validate required path parameters
    if 'constraintId' not in path_parameters:
        return validation_error(body={'message': "No constraint ID in API Call"}, event=event)
    
    constraint_id = path_parameters['constraintId']
    
    # Validate constraintId
    (valid, message) = validate({
        'constraintId': {
            'value': constraint_id,
            'validator': 'OBJECT_NAME'
        }
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)
    
    try:
        # Delete the constraint
        result = delete_constraint(constraint_id, claims_and_roles)
        
        # AUDIT LOG: Constraint deleted
        log_auth_changes(event, "constraintDelete", {
            "constraintId": result.constraintId,
            "operation": "delete"
        })
        
        # Return success response
        return success(body=result.dict())
    
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for auth constraints service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        logger.info(f"Processing {method} request for path: {path}")
        
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
        elif method == 'POST':
            return handle_post_request(event)
        elif method == 'DELETE':
            return handle_delete_request(event)
        elif method == 'PUT':
            # PUT is treated the same as POST for constraints
            return handle_post_request(event)
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