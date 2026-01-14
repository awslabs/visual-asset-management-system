"""Create/Update role handler for VAMS API."""

import os
import boto3
import uuid
from datetime import datetime
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_auth_changes
from models.common import (
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    authorization_error,
    VAMSGeneralErrorResponse
)
from models.roleConstraints import (
    CreateRoleRequestModel,
    UpdateRoleRequestModel,
    RoleOperationResponseModel
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="CreateRole")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
try:
    roles_table_name = os.environ["ROLES_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
roles_table = dynamodb.Table(roles_table_name)

#######################
# Business Logic Functions
#######################

def create_role(role_data, claims_and_roles):
    """Create a new role
    
    Args:
        role_data: Dictionary with role creation data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        RoleOperationResponseModel with operation result
    """
    try:
        # Check authorization
        role_object = {
            'roleName': role_data['roleName'],
            'object__type': 'role'
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(role_object, "POST"):
                raise authorization_error()
        
        # Create the role item
        logger.info(f"Creating role {role_data['roleName']}")
        
        item = {
            'id': str(uuid.uuid4()),
            'roleName': role_data['roleName'],
            'description': role_data['description'],
            'createdOn': datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            'source': role_data.get('source'),
            'sourceIdentifier': role_data.get('sourceIdentifier'),
            'mfaRequired': role_data.get('mfaRequired', False)
        }
        
        # Save to database
        roles_table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(roleName)'
        )
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return RoleOperationResponseModel(
            success=True,
            message=f"Role {role_data['roleName']} created successfully",
            roleName=role_data['roleName'],
            operation="create",
            timestamp=now
        )
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ConditionalCheckFailedException':
            raise VAMSGeneralErrorResponse("Role already exists")
        elif error_code == 'ValidationException':
            raise VAMSGeneralErrorResponse("Invalid request parameters")
        else:
            logger.exception(f"DynamoDB error: {error_code}")
            raise VAMSGeneralErrorResponse("Error creating role")
    except Exception as e:
        logger.exception(f"Error creating role: {e}")
        raise VAMSGeneralErrorResponse("Error creating role")


def update_role(role_data, claims_and_roles):
    """Update an existing role
    
    Args:
        role_data: Dictionary with role update data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        RoleOperationResponseModel with operation result
    """
    try:
        # Check authorization
        role_object = {
            'roleName': role_data['roleName'],
            'object__type': 'role'
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(role_object, "PUT"):
                raise authorization_error()
        
        # Update the role
        logger.info(f"Updating role {role_data['roleName']}")
        
        roles_table.update_item(
            Key={'roleName': role_data['roleName']},
            UpdateExpression='SET description = :desc, #source = :source, sourceIdentifier = :sourceIdentifier, mfaRequired = :mfaRequired',
            ExpressionAttributeNames={
                '#source': 'source'
            },
            ExpressionAttributeValues={
                ':desc': role_data['description'],
                ':source': role_data.get('source'),
                ':sourceIdentifier': role_data.get('sourceIdentifier'),
                ':mfaRequired': role_data.get('mfaRequired', False)
            },
            ConditionExpression='attribute_exists(roleName)'
        )
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return RoleOperationResponseModel(
            success=True,
            message=f"Role {role_data['roleName']} updated successfully",
            roleName=role_data['roleName'],
            operation="update",
            timestamp=now
        )
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ConditionalCheckFailedException':
            raise VAMSGeneralErrorResponse("Role does not exist")
        elif error_code == 'ValidationException':
            raise VAMSGeneralErrorResponse("Invalid request parameters")
        else:
            logger.exception(f"DynamoDB error: {error_code}")
            raise VAMSGeneralErrorResponse("Error updating role")
    except Exception as e:
        logger.exception(f"Error updating role: {e}")
        raise VAMSGeneralErrorResponse("Error updating role")


#######################
# Request Handlers
#######################

def handle_post_request(event):
    """Handle POST requests to create roles
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                import json
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Validate required fields
        if 'roleName' not in body or 'description' not in body:
            return validation_error(body={'message': "roleName and description are required"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateRoleRequestModel)
        
        # Create the role
        result = create_role(
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # AUDIT LOG: Role created
        log_auth_changes(event, "roleCreate", {
            "roleName": result.roleName,
            "operation": "create",
            "description": request_model.description,
            "mfaRequired": request_model.mfaRequired
        })
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_put_request(event):
    """Handle PUT requests to update roles
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                import json
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Validate required fields
        if 'roleName' not in body or 'description' not in body:
            return validation_error(body={'message': "roleName and description are required"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateRoleRequestModel)
        
        # Update the role
        result = update_role(
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # AUDIT LOG: Role updated
        log_auth_changes(event, "roleUpdate", {
            "roleName": result.roleName,
            "operation": "update",
            "description": request_model.description,
            "mfaRequired": request_model.mfaRequired
        })
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for create/update role APIs"""
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
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)