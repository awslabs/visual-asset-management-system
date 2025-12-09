"""Role service handler for VAMS API."""

import os
import boto3
from datetime import datetime
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import (
    APIGatewayProxyResponseV2, 
    internal_error, 
    success, 
    validation_error, 
    authorization_error,
    VAMSGeneralErrorResponse
)
from models.roleConstraints import (
    GetRolesRequestModel,
    RoleResponseModel,
    GetRolesResponseModel
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
logger = safeLogger(service_name="RoleService")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
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
# Business Logic Functions
#######################

def get_role_details(role_name):
    """Get role details from DynamoDB
    
    Args:
        role_name: The role name
        
    Returns:
        The role details or None if not found
    """
    try:
        response = roles_table.get_item(Key={'roleName': role_name})
        return response.get('Item')
    except Exception as e:
        logger.exception(f"Error getting role details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving resource")


def get_all_roles(query_params):
    """Get all roles with authorization filtering
    
    Args:
        query_params: Query parameters for pagination
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    deserializer = TypeDeserializer()
    
    try:
        paginator = dynamodb_client.get_paginator('scan')
        page_iterator = paginator.paginate(
            TableName=roles_table_name,
            PaginationConfig={
                'MaxItems': int(query_params['maxItems']),
                'PageSize': int(query_params['pageSize']),
                'StartingToken': query_params.get('startingToken')
            }
        ).build_full_result()
        
        authorized_roles = []
        
        for role in page_iterator.get('Items', []):
            deserialized_document = {k: deserializer.deserialize(v) for k, v in role.items()}
            
            # Add object type for Casbin enforcement
            deserialized_document.update({"object__type": "role"})
            
            # Check if user has permission to GET the role
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_document, "GET"):
                    authorized_roles.append(deserialized_document)
        
        result = {"Items": authorized_roles}
        
        if 'NextToken' in page_iterator:
            result['NextToken'] = page_iterator['NextToken']
        
        return result
        
    except Exception as e:
        logger.exception(f"Error scanning roles: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving roles")


#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for roles
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate query parameters
        try:
            request_model = parse(query_parameters, model=GetRolesRequestModel)
            query_params = {
                'maxItems': request_model.maxItems,
                'pageSize': request_model.pageSize,
                'startingToken': request_model.startingToken
            }
        except ValidationError as v:
            logger.exception(f"Validation error in query parameters: {v}")
            validate_pagination_info(query_parameters)
            query_params = query_parameters
        
        # Get all roles
        roles_result = get_all_roles(query_params)
        
        # Convert to response models
        formatted_items = []
        for item in roles_result.get('Items', []):
            try:
                role_model = RoleResponseModel(**item)
                formatted_items.append(role_model)
            except ValidationError as v:
                logger.warning(f"Could not convert role to response model: {v}")
                # Fall back to raw item if conversion fails
                formatted_items.append(RoleResponseModel(**{
                    'roleName': item.get('roleName', ''),
                    'description': item.get('description', ''),
                    'id': item.get('id'),
                    'createdOn': item.get('createdOn'),
                    'source': item.get('source'),
                    'sourceIdentifier': item.get('sourceIdentifier'),
                    'mfaRequired': item.get('mfaRequired', False)
                }))
        
        # Create the response model
        try:
            response_model = GetRolesResponseModel(
                Items=formatted_items,
                NextToken=roles_result.get('NextToken')
            )
            # Wrap in message field for backward compatibility with legacy format
            return success(body={"message": response_model.dict()})
        except ValidationError as v:
            logger.exception(f"Error creating GetRolesResponseModel: {v}")
            # Fall back to raw response if model creation fails
            response_data = {"Items": [item.dict() if hasattr(item, 'dict') else item for item in formatted_items]}
            if 'NextToken' in roles_result:
                response_data['NextToken'] = roles_result['NextToken']
            return success(body={"message": response_data})
        
    except VAMSGeneralErrorResponse as e:
        return validation_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()


def handle_delete_request(event):
    """Handle DELETE requests for roles
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Get role name from path
        role_name = path_parameters.get('roleId')
        
        if not role_name or len(role_name) == 0:
            return validation_error(body={'message': "Role name is required"})
        
        # Validate role name
        (valid, message) = validate({
            'roleName': {
                'value': role_name,
                'validator': 'OBJECT_NAME'
            }
        })
        
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        # Check authorization
        role_object = {
            'roleName': role_name,
            'object__type': 'role'
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(role_object, "DELETE"):
                return authorization_error()
        
        # Delete the role and associated user role entries
        try:
            # First, delete all user role entries for this role
            # Query all users that have this role (scan since roleName is sort key)
            logger.info(f"Deleting user role entries for role {role_name}")
            
            try:
                # Scan the user_roles_table to find all entries with this roleName
                scan_response = user_roles_table.scan(
                    FilterExpression='roleName = :roleName',
                    ExpressionAttributeValues={':roleName': role_name}
                )
                
                # Delete each user role entry
                for item in scan_response.get('Items', []):
                    user_id = item.get('userId')
                    if user_id:
                        logger.info(f"Deleting user role entry for user {user_id} and role {role_name}")
                        user_roles_table.delete_item(
                            Key={
                                'userId': user_id,
                                'roleName': role_name
                            }
                        )
            except Exception as e:
                logger.warning(f"Error deleting user role entries: {e}")
                # Continue with role deletion even if user role cleanup fails
            
            # Delete the role from roles table
            roles_table.delete_item(
                Key={'roleName': role_name},
                ConditionExpression='attribute_exists(roleName)'
            )
            
            return success(body={"message": "success"})
            
        except dynamodb_client.exceptions.ConditionalCheckFailedException:
            return validation_error(body={"message": "Role does not exist"})
        
    except VAMSGeneralErrorResponse as e:
        return validation_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error()


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for role service APIs"""
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
        elif method == 'DELETE':
            return handle_delete_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"})
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return validation_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
