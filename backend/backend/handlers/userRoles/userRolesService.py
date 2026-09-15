# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.resourceNames import get_table_name, ResourceKeys
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_auth_changes
from models.common import (
    validation_error_message,
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    general_error,
    authorization_error,
    VAMSGeneralErrorResponse
)
from models.roleConstraints import (
    GetUserRolesRequestModel,
    CreateUserRolesRequestModel,
    UpdateUserRolesRequestModel,
    DeleteUserRolesRequestModel,
    UserRoleResponseModel,
    GetUserRolesResponseModel,
    UserRoleOperationResponseModel
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
logger = safeLogger(service="UserRolesService")

# Global variables for claims and roles
claims_and_roles = {}

try:
    roles_table_name = get_table_name(ResourceKeys.ROLES_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving roles table name")
    roles_table_name = None

try:
    user_roles_table_name = get_table_name(ResourceKeys.USER_ROLES_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving user roles table name")
    user_roles_table_name = None

roles_table = dynamodb.Table(roles_table_name) if roles_table_name else None
user_roles_table = dynamodb.Table(user_roles_table_name) if user_roles_table_name else None


#: Message a differential update returns when the user already holds exactly the named roles.
#: The request handler compares against it so the audit record can record a no-op instead of a
#: role change that did not happen.
USER_ROLE_UPDATE_NO_CHANGES_MESSAGE = "No user role changes were required"

#: Message a delete-all returns when the user holds no role assignments. Same purpose as the
#: update message above: the operation is authorized, nothing is written, and neither the
#: response nor the audit record claims a deletion that did not happen.
USER_ROLE_DELETE_NO_CHANGES_MESSAGE = "No user role assignments to delete"

#: Message returned when a request names a role that does not exist. Rule 11: the name the
#: caller submitted stays in the log, and the response says only that a named role is unknown.
ROLE_DOES_NOT_EXIST_MESSAGE = "One or more of the named roles does not exist in the system"


class AuthorizationDenied(Exception):
    """Object-level authorization refused the caller.

    Raised rather than returned so a business-logic function has one return type -- its
    response model -- and a denial cannot be mistaken for data by its caller. Translated to
    authorization_error() by the request handler.
    """


#######################
# Utility Functions
#######################

def get_all_roles_for_user(user_id):
    """Get all roles for a specific user
    
    Args:
        user_id: The user ID
        
    Returns:
        List of role items from DynamoDB
    """
    try:
        resp = dynamodb_client.query(
            TableName=user_roles_table_name,
            KeyConditionExpression='userId = :id',
            ExpressionAttributeValues={':id': {'S': user_id}}
        )
        return resp.get('Items', [])
    except Exception as e:
        logger.exception(f"Error getting roles for user {user_id}: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving user roles.")


def get_role(role_name):
    """Get a specific role by name
    
    Args:
        role_name: The role name
        
    Returns:
        List of role items from DynamoDB
    """
    try:
        resp = dynamodb_client.query(
            TableName=roles_table_name,
            KeyConditionExpression='roleName = :roleName',
            ExpressionAttributeValues={':roleName': {'S': role_name}}
        )
        return resp.get('Items', [])
    except Exception as e:
        logger.exception(f"Error getting role {role_name}: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving role.")


def validate_roles_exist_strict(role_names):
    """Strictly validate that all role names exist
    
    Args:
        role_names: List of role names to validate
        
    Returns:
        True if all roles exist
        
    Raises:
        ValueError: If any role does not exist
    """
    for role_name in role_names:
        role_items = get_role(role_name)
        if not role_items or len(role_items) == 0:
            logger.warning(f"Rejected user role request naming an unknown role: {role_name}")
            raise ValueError(ROLE_DOES_NOT_EXIST_MESSAGE)
    return True


def is_any_user_role_already_existing(items, user_id, role_names):
    """Check if any user role combination already exists
    
    Args:
        items: Existing user role items
        user_id: The user ID
        role_names: List of role names to check
        
    Returns:
        True if any combination already exists, False otherwise
    """
    existing_roles = [f"{item['userId']['S']}---{item['roleName']['S']}" for item in items]
    new_roles = [f"{user_id}---{role}" for role in role_names]
    
    logger.info(f"Existing roles: {existing_roles}")
    logger.info(f"New roles: {new_roles}")
    
    for role in new_roles:
        if role in existing_roles:
            return True

    return False


def enforce_user_role(authorization_object, action, claims_and_roles):
    """Evaluate one userRole object for `action`, raising AuthorizationDenied on a refusal.

    Args:
        authorization_object: The userRole fields to evaluate (userId, and roleName for a
            single assignment). Annotated with its object type here so no call site can omit it
        action: The Casbin action for the operation (POST / PUT / DELETE)
        claims_and_roles: User claims and roles for authorization

    Raises:
        AuthorizationDenied: If the caller is not authorized for this object and action
    """
    check_object = dict(authorization_object, object__type="userRole")
    if not CasbinEnforcer(claims_and_roles).enforce(check_object, action):
        raise AuthorizationDenied()


def require_authenticated_identity(claims_and_roles):
    """Fail closed when the request carries no authenticated identity.

    An empty token list means no identity is available, so authorization cannot be evaluated
    and must deny. Called by every entry point below before its first enforce(), including the
    ones whose only object-level checks sit inside a loop.

    Raises:
        AuthorizationDenied: If the request carries no tokens
    """
    if len(claims_and_roles["tokens"]) == 0:
        raise AuthorizationDenied()


def authorize_user_role_set_operation(user_id, action, claims_and_roles):
    """Authorize an operation on one user's role set AS A WHOLE, before any of it is read.

    Used by the two operations that can change rows the request does not name: PUT replaces the
    whole set, so it deletes every assignment absent from the body, and DELETE removes all of
    them. For those the subject of the decision is the user's role set rather than an individual
    assignment, and the verdict must not depend on what the target currently holds -- the
    delete-all body carries no role names at all, so a target holding nothing has no row to
    evaluate and per-row authorization alone would answer 200 to a caller authorized for no
    userRole row, a membership oracle in the status code.

    The object evaluated therefore carries only the userId the request names. It comes from the
    request body, so the verdict is a function of the request alone.

    A constraint scoped to specific role names does not match this object (`roleName` is absent,
    so the enforcer supplies its empty placeholder). That is the intended reach: such a caller
    governs the individual assignments they are scoped to -- which POST grants them -- not a
    user's role set as a whole. The seeded administrator constraint is `roleName contains '.*'`,
    which does match an empty role name -- see
    tests/handlers/userRoles/test_userRolesService_operation_authz.py.

    POST deliberately does NOT use this check. Its body must name at least one role
    (`CreateUserRolesRequestModel.roleName` carries `min_items=1`), so its per-named-row loop
    always evaluates at least one object regardless of the target's membership, and POST can
    only create the rows it names. A set-level check there would close no oracle the loop has
    not already closed, while denying a roleName-scoped constraint the very assignments it is
    scoped to -- see tests/handlers/userRoles/test_userRolesService_post_scoped_grant.py.

    Args:
        user_id: The target user ID from the request
        action: The Casbin action for the operation (PUT / DELETE)
        claims_and_roles: User claims and roles for authorization

    Raises:
        AuthorizationDenied: If the caller is not authorized for the operation
    """
    require_authenticated_identity(claims_and_roles)

    enforce_user_role({'userId': user_id}, action, claims_and_roles)


#######################
# Business Logic Functions
#######################

def create_user_roles(request_model: CreateUserRolesRequestModel, claims_and_roles):
    """Create new user roles

    Authorization precedes every answer that depends on state the caller cannot see.
    `is_any_user_role_already_existing` rejects an assignment the user already holds, so
    deciding it before the verdicts would let a caller authorized for no userRole row read exact
    membership off the status code -- 400 for a role the user holds, 403 for one they do not.
    Role existence is the same kind of answer for a role the caller cannot manage. Both are
    therefore decided only after every named row is authorized, and none of those verdicts
    depends on what the target user currently holds.

    POST can only create the rows it names, and the body must name at least one, so the loop
    below is the whole authorization: it always evaluates at least one object regardless of the
    target's membership. That is why there is no set-level check on top -- adding one would deny
    a roleName-scoped constraint the assignments it is scoped to (see
    authorize_user_role_set_operation).

    Args:
        request_model: Validated CreateUserRolesRequestModel
        claims_and_roles: User claims and roles for authorization

    Returns:
        UserRoleOperationResponseModel with operation result

    Raises:
        AuthorizationDenied: If the request carries no identity or the caller is not authorized
            for one of the assignments
    """
    user_id = request_model.userId
    # De-duplicated, request-ordered so the rows evaluated and written are deterministic.
    role_names = list(dict.fromkeys(request_model.roleName))

    # Authorize every assignment the request names, before anything about the target is read
    require_authenticated_identity(claims_and_roles)

    for role in role_names:
        enforce_user_role({'userId': user_id, 'roleName': role}, "POST", claims_and_roles)

    # Validate that all roles exist before proceeding
    try:
        validate_roles_exist_strict(role_names)
    except ValueError as e:
        raise VAMSGeneralErrorResponse(str(e))

    # Check for existing user roles
    existing_items = get_all_roles_for_user(user_id)
    if is_any_user_role_already_existing(existing_items, user_id, role_names):
        raise VAMSGeneralErrorResponse("One or more roles already exist for this user")

    items_to_insert = [
        {
            'userId': user_id,
            'roleName': role,
            'createdOn': datetime.datetime.utcnow().isoformat()
        }
        for role in role_names
    ]

    # Insert all user roles
    try:
        with user_roles_table.batch_writer() as batch:
            for item in items_to_insert:
                batch.put_item(Item=item)
        
        timestamp = datetime.datetime.utcnow().isoformat()
        return UserRoleOperationResponseModel(
            success=True,
            message="User roles created successfully",
            userId=user_id,
            operation="create",
            timestamp=timestamp
        )
    except Exception as e:
        logger.exception(f"Error creating user roles: {e}")
        raise VAMSGeneralErrorResponse(f"Error creating user roles.")


def update_user_roles(request_model: UpdateUserRolesRequestModel, claims_and_roles):
    """Update user roles (differential update - add new, remove old)

    Authorization covers the operation and every userRole row the request NAMES, not only the
    rows the differential changes. Authorizing the change set alone makes the set of evaluated
    objects depend on the target user's current membership: a request whose computed change set
    is empty evaluates nothing, so a caller authorized for no userRole row at all is answered
    200 when the named roles are exactly the roles the user already holds and 403 for any other
    guess -- a membership oracle. The operation is therefore authorized first, from the userId
    alone. Naming a role is then a request to grant it (the same body grants it when the user
    does not already hold it), so each named row is evaluated for POST regardless of whether it
    happens to exist, and each row the request drops is evaluated for DELETE.

    ROLE EXISTENCE. Only the roles this request would ADD have to exist:

        a named role must resolve in the roles table unless the target already holds it.

    A role row can be deleted while user assignments still reference it (roleService's delete
    cascade is single-page and swallows its own failures), so orphaned assignments exist on any
    upgraded deployment. This endpoint is a whole-set replace and the Edit form pre-populates
    from the user's current roles, so requiring every NAMED role to exist would 400 every edit
    of such a user -- including the edit that removes the orphan -- leaving the assignment
    unremovable. A role the target already holds is therefore accepted whether the request
    retains it (it stays, unvalidated) or drops it (it is deleted, which is the repair).

    The existence check is reached only after the set-level verdict and every per-named-row
    verdict, neither of which depends on the target's membership, so a caller those checks
    refuse never sees the 400 and cannot read membership off it. (The per-DROPPED-row DELETE
    verdicts do depend on membership -- a caller not authorized to remove a row the request
    drops is refused -- but that refusal is the correct answer to what they asked for, and it
    sits before the existence check rather than being reachable around it.)

    Args:
        request_model: Validated UpdateUserRolesRequestModel
        claims_and_roles: User claims and roles for authorization

    Returns:
        UserRoleOperationResponseModel with operation result

    Raises:
        AuthorizationDenied: If the caller is not authorized for the operation or one of the
            named rows
    """
    user_id = request_model.userId
    # De-duplicated, request-ordered so the roles evaluated and written are deterministic.
    named_role_names = list(dict.fromkeys(request_model.roleName))

    # Authorize the operation before the target's membership is read at all
    authorize_user_role_set_operation(user_id, "PUT", claims_and_roles)

    # Get existing roles
    items = get_all_roles_for_user(user_id)
    existing_roles = list(dict.fromkeys(item["roleName"]['S'] for item in items))

    # Calculate differential
    named_role_set = set(named_role_names)
    existing_role_set = set(existing_roles)
    roles_to_delete = [role for role in existing_roles if role not in named_role_set]
    roles_to_create = [role for role in named_role_names if role not in existing_role_set]

    # Authorize every named row, collecting the ones that do not yet exist for the write
    roles_to_create_set = set(roles_to_create)
    user_roles_to_create = []
    for role in named_role_names:
        named_user_role = {
            'userId': user_id,
            'roleName': role
        }

        # Check if the current user has permissions to POST the User Role
        enforce_user_role(named_user_role, "POST", claims_and_roles)

        if role in roles_to_create_set:
            user_roles_to_create.append(dict(
                named_user_role,
                createdOn=datetime.datetime.utcnow().isoformat()
            ))

    # Prepare roles to delete with authorization checks
    user_roles_to_delete = []
    for role in roles_to_delete:
        delete_user_role = {
            'userId': user_id,
            'roleName': role
        }

        # Check if the current user has permissions to DELETE the User Role
        enforce_user_role(delete_user_role, "DELETE", claims_and_roles)

        user_roles_to_delete.append(delete_user_role)

    # Only the roles this request would ADD have to exist. A named role the target already
    # holds is left alone whether it still resolves or not, so an orphaned assignment -- one
    # whose roles-table row was deleted -- can be retained by a full-list edit and removed by
    # dropping it from the list. Reached only after every authorization verdict above.
    try:
        validate_roles_exist_strict(roles_to_create)
    except ValueError as e:
        raise VAMSGeneralErrorResponse(str(e))

    # Nothing to write: the user already holds exactly the named roles. Reported as a no-op so
    # neither the response nor the audit record claims a role change that did not happen.
    if not user_roles_to_create and not user_roles_to_delete:
        logger.info(f"No user role changes required for user {user_id}")
        return UserRoleOperationResponseModel(
            success=True,
            message=USER_ROLE_UPDATE_NO_CHANGES_MESSAGE,
            userId=user_id,
            operation="update",
            timestamp=datetime.datetime.utcnow().isoformat()
        )

    # Perform batch operations
    try:
        with user_roles_table.batch_writer() as batch:
            for item in user_roles_to_create:
                batch.put_item(Item=item)
            for keys in user_roles_to_delete:
                batch.delete_item(Key=keys)

        timestamp = datetime.datetime.utcnow().isoformat()
        return UserRoleOperationResponseModel(
            success=True,
            message="User roles updated successfully",
            userId=user_id,
            operation="update",
            timestamp=timestamp
        )
    except Exception as e:
        logger.exception(f"Error updating user roles: {e}")
        raise VAMSGeneralErrorResponse(f"Error updating user roles.")


def delete_user_roles(request_model: DeleteUserRolesRequestModel, claims_and_roles):
    """Delete all roles for a user

    The request body carries no role names, so the loop below iterates the assignments that
    EXIST -- zero times for a target holding none. Per-row authorization alone therefore
    evaluates nothing for such a target, which would answer a caller authorized for no userRole
    row and tell them the user holds no roles. The operation is authorized first, from the
    userId alone, so the verdict does not depend on what the target holds; and a request that
    removes nothing reports a no-op rather than a deletion, in the response and in the audit
    record alike.

    Args:
        request_model: Validated DeleteUserRolesRequestModel
        claims_and_roles: User claims and roles for authorization

    Returns:
        UserRoleOperationResponseModel with operation result

    Raises:
        AuthorizationDenied: If the caller is not authorized for the operation or one of the
            assignments
    """
    user_id = request_model.userId

    # Authorize the delete-all before the target's assignments are read at all
    authorize_user_role_set_operation(user_id, "DELETE", claims_and_roles)

    # Get all roles for the user
    items = get_all_roles_for_user(user_id)

    items_to_delete = []
    for role in items:
        user_role = {
            'userId': user_id,
            'roleName': role['roleName']['S']
        }

        # Check if the current user has permissions to DELETE the User Role
        enforce_user_role(user_role, "DELETE", claims_and_roles)

        items_to_delete.append(user_role)

    # Nothing to remove: the user holds no assignments. Reported as a no-op so neither the
    # response nor the audit record claims a deletion that did not happen.
    if not items_to_delete:
        logger.info(f"No user role assignments to delete for user {user_id}")
        return UserRoleOperationResponseModel(
            success=True,
            message=USER_ROLE_DELETE_NO_CHANGES_MESSAGE,
            userId=user_id,
            operation="delete",
            timestamp=datetime.datetime.utcnow().isoformat()
        )

    # Delete all user roles
    try:
        with user_roles_table.batch_writer() as batch:
            for keys in items_to_delete:
                batch.delete_item(Key=keys)
        
        timestamp = datetime.datetime.utcnow().isoformat()
        return UserRoleOperationResponseModel(
            success=True,
            message="User roles deleted successfully",
            userId=user_id,
            operation="delete",
            timestamp=timestamp
        )
    except Exception as e:
        logger.exception(f"Error deleting user roles: {e}")
        raise VAMSGeneralErrorResponse(f"Error deleting user roles.")


def get_user_roles(query_params):
    """Get all user roles with pagination
    
    Args:
        query_params: Query parameters for pagination
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    deserializer = TypeDeserializer()
    paginator = dynamodb_client.get_paginator('scan')
    
    # Scan all user roles
    try:
        raw_user_roles = []
        page_iterator = paginator.paginate(
            TableName=user_roles_table_name,
            PaginationConfig={
                'MaxItems': 1000,
                'PageSize': 1000,
            }
        ).build_full_result()
        
        if len(page_iterator["Items"]) > 0:
            raw_user_roles.extend(page_iterator["Items"])
            while "NextToken" in page_iterator:
                page_iterator = paginator.paginate(
                    TableName=user_roles_table_name,
                    PaginationConfig={
                        'MaxItems': 1000,
                        'PageSize': 1000,
                        'StartingToken': page_iterator["NextToken"]
                    }
                ).build_full_result()
                if len(page_iterator["Items"]) > 0:
                    raw_user_roles.extend(page_iterator["Items"])
        
        # Group by userId
        grouped_data = {"Items": []}
        
        for user_role in raw_user_roles:
            deserialized_document = {k: deserializer.deserialize(v) for k, v in user_role.items()}
            
            # Add Casbin Enforcer to check if the current user has permissions to GET the User Roles
            deserialized_document.update({"object__type": "userRole"})
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_document, "GET"):
                    user_id_exists = False
                    for item in grouped_data["Items"]:
                        if item["userId"] == deserialized_document["userId"]:
                            # Found record so just add the roleName to the existing record
                            item["roleName"].append(deserialized_document["roleName"])
                            user_id_exists = True
                            break
                    
                    if not user_id_exists:
                        grouped_data["Items"].append({
                            "userId": deserialized_document["userId"],
                            "roleName": [deserialized_document["roleName"]],
                            # createdOn is also the pagination token below, so a row stored
                            # without it takes a CONSTANT default. A computed one (a timestamp
                            # of now) differs on every request, so the token a client received
                            # for one page would match nothing on the next and the page would
                            # come back empty.
                            "createdOn": deserialized_document.get("createdOn") or ""
                        })

        # Sort the list results by createdOn for pagination. createdOn is not unique -- the
        # grouping keeps the first row's value per userId -- so userId breaks the tie and makes
        # the order total, which is what keeps the page boundaries identical between requests.
        grouped_data["Items"].sort(key=lambda x: (x["createdOn"], x["userId"]))
        
        # Custom pagination
        if "startingToken" in query_params and query_params["startingToken"]:
            for item in grouped_data["Items"][:]:
                if item["createdOn"] != query_params["startingToken"]:
                    grouped_data["Items"].remove(item)
                else:
                    break
        
        # Prepare records for next page
        next_is_token = False
        start_removing_records = False
        record_count = 0
        for item in grouped_data["Items"][:]:
            record_count += 1
            if next_is_token:
                grouped_data['NextToken'] = item["createdOn"]
                next_is_token = False
                start_removing_records = True
            if start_removing_records:
                grouped_data["Items"].remove(item)
            if record_count == int(query_params["maxItems"]):
                next_is_token = True
        
        return grouped_data
        
    except Exception as e:
        logger.exception(f"Error getting user roles: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving user roles.")


#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for user roles
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate query parameters
        request_model = parse(query_parameters, model=GetUserRolesRequestModel)
        
        # Extract validated parameters for the query
        query_params = {
            'maxItems': request_model.maxItems,
            'pageSize': request_model.pageSize,
            'startingToken': request_model.startingToken
        }
        
        # Get user roles
        user_roles_result = get_user_roles(query_params)
        
        # Return success response
        return success(body={"message": user_roles_result})
        
    except ValidationError as v:
        logger.exception(f"Validation error in query parameters: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_post_request(event):
    """Handle POST requests to create user roles
    
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
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateUserRolesRequestModel)
        
        # Create user roles
        result = create_user_roles(request_model, claims_and_roles)
        
        # AUDIT LOG: User roles created
        log_auth_changes(event, "userRoleCreate", {
            "userId": result.userId,
            "operation": "create",
            "roleNames": request_model.roleName
        })
        
        # Return success response
        return success(body=result.dict())

    except AuthorizationDenied:
        return authorization_error()
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
    """Handle PUT requests to update user roles
    
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
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateUserRolesRequestModel)
        
        # Update user roles
        result = update_user_roles(request_model, claims_and_roles)
        
        # AUDIT LOG: User roles updated. A request that resolved to no changes is still
        # recorded, with changed=False. The trail's subject is the authorized attempt -- who
        # asked to rewrite whose role set, and when -- which is what a reviewer needs in order
        # to see repeated probing of a user's membership; `changed` is the honest signal for
        # whether anything was written, so the record cannot be read as a role change that never
        # happened. handle_delete_request records the same way, deliberately: an operation that
        # writes nothing must not be invisible on one endpoint and visible on the other.
        log_auth_changes(event, "userRoleUpdate", {
            "userId": result.userId,
            "operation": "update",
            "roleNames": request_model.roleName,
            "changed": result.message != USER_ROLE_UPDATE_NO_CHANGES_MESSAGE
        })
        
        # Return success response
        return success(body=result.dict())

    except AuthorizationDenied:
        return authorization_error()
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_delete_request(event):
    """Handle DELETE requests for user roles
    
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
        
        # Parse and validate the request model
        request_model = parse(body, model=DeleteUserRolesRequestModel)
        
        # Delete user roles
        result = delete_user_roles(request_model, claims_and_roles)
        
        # AUDIT LOG: User roles deleted. A delete-all against a user holding no assignments is
        # recorded with changed=False, matching handle_put_request: the record is of the
        # authorized attempt, and `changed` says whether anything was removed. Suppressing the
        # record instead would make a delete-all against an empty role set the one user-role
        # write that leaves no trail -- and it is exactly the probe worth seeing.
        log_auth_changes(event, "userRoleDelete", {
            "userId": result.userId,
            "operation": "delete",
            "changed": result.message != USER_ROLE_DELETE_NO_CHANGES_MESSAGE
        })
        
        # Return success response
        return success(body=result.dict())

    except AuthorizationDenied:
        return authorization_error()
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for user roles service APIs"""
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
        if method == 'GET':
            return handle_get_request(event)
        elif method == 'POST':
            return handle_post_request(event)
        elif method == 'PUT':
            return handle_put_request(event)
        elif method == 'DELETE':
            return handle_delete_request(event)
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