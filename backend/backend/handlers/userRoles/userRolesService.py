# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import (
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

# Load environment variables
try:
    roles_table_name = os.environ["ROLES_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
roles_table = dynamodb.Table(roles_table_name)
user_roles_table = dynamodb.Table(user_roles_table_name)


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
            raise ValueError(f"Role '{role_name}' does not exist in the system")
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


#######################
# Business Logic Functions
#######################

def create_user_roles(request_model: CreateUserRolesRequestModel, claims_and_roles):
    """Create new user roles
    
    Args:
        request_model: Validated CreateUserRolesRequestModel
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        UserRoleOperationResponseModel with operation result
    """
    user_id = request_model.userId
    role_names = request_model.roleName
    
    # Validate that all roles exist before proceeding
    try:
        validate_roles_exist_strict(role_names)
    except ValueError as e:
        raise VAMSGeneralErrorResponse(str(e))
    
    # Check for existing user roles
    existing_items = get_all_roles_for_user(user_id)
    if is_any_user_role_already_existing(existing_items, user_id, role_names):
        raise VAMSGeneralErrorResponse("One or more roles already exist for this user")
    
    # Prepare items to insert with authorization checks
    items_to_insert = []
    for role in role_names:
        user_role = {
            'userId': user_id,
            'roleName': role,
            'createdOn': datetime.datetime.utcnow().isoformat()
        }
        
        # Add Casbin Enforcer to check if the current user has permissions to POST the User Role
        user_role_check = user_role.copy()
        user_role_check.update({"object__type": "userRole"})
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(user_role_check, "POST"):
                raise authorization_error()
        
        items_to_insert.append(user_role)
    
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
    
    Args:
        request_model: Validated UpdateUserRolesRequestModel
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        UserRoleOperationResponseModel with operation result
    """
    user_id = request_model.userId
    new_role_names = request_model.roleName
    
    # Get existing roles
    items = get_all_roles_for_user(user_id)
    existing_roles = [item["roleName"]['S'] for item in items]
    
    # Calculate differential
    roles_to_delete = list(set(existing_roles) - set(new_role_names))
    roles_to_create = list(set(new_role_names) - set(existing_roles))
    
    # Validate new roles exist
    if roles_to_create:
        try:
            validate_roles_exist_strict(roles_to_create)
        except ValueError as e:
            raise VAMSGeneralErrorResponse(str(e))
    
    # Prepare roles to create with authorization checks
    user_roles_to_create = []
    for role in roles_to_create:
        create_user_role = {
            'userId': user_id,
            'roleName': role,
            'createdOn': datetime.datetime.utcnow().isoformat()
        }
        
        # Add Casbin Enforcer to check if the current user has permissions to POST the User Role
        create_user_role_check = create_user_role.copy()
        create_user_role_check.update({"object__type": "userRole"})
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(create_user_role_check, "POST"):
                raise authorization_error()
        
        user_roles_to_create.append(create_user_role)
    
    # Prepare roles to delete with authorization checks
    user_roles_to_delete = []
    for role in roles_to_delete:
        delete_user_role = {
            'userId': user_id,
            'roleName': role
        }
        
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the User Role
        delete_user_role_check = delete_user_role.copy()
        delete_user_role_check.update({"object__type": "userRole"})
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(delete_user_role_check, "DELETE"):
                raise authorization_error()
        
        user_roles_to_delete.append(delete_user_role)
    
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
    
    Args:
        request_model: Validated DeleteUserRolesRequestModel
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        UserRoleOperationResponseModel with operation result
    """
    user_id = request_model.userId
    
    # Get all roles for the user
    items = get_all_roles_for_user(user_id)
    
    # Prepare items to delete with authorization checks
    items_to_delete = []
    for role in items:
        user_role = {
            'userId': user_id,
            'roleName': role['roleName']['S']
        }
        
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the User Role
        user_role_check = user_role.copy()
        user_role_check.update({"object__type": "userRole"})
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(user_role_check, "DELETE"):
                raise authorization_error()
        
        items_to_delete.append(user_role)
    
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
                            "createdOn": deserialized_document["createdOn"]
                        })
        
        # Sort the list results by createdOn for pagination
        grouped_data["Items"].sort(key=lambda x: x["createdOn"])
        
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
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()


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
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateUserRolesRequestModel)
        
        # Create user roles
        result = create_user_roles(request_model, claims_and_roles)
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error()


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
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateUserRolesRequestModel)
        
        # Update user roles
        result = update_user_roles(request_model, claims_and_roles)
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error()


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
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        
        # Parse and validate the request model
        request_model = parse(body, model=DeleteUserRolesRequestModel)
        
        # Delete user roles
        result = delete_user_roles(request_model, claims_and_roles)
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error()


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
