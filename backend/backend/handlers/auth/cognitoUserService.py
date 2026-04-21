# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cognito User service handler for VAMS API."""

import os
import boto3
import json
from datetime import datetime
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_auth_changes
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.user import (
    ListCognitoUsersRequestModel, CreateCognitoUserRequestModel,
    UpdateCognitoUserRequestModel, ResetPasswordRequestModel,
    CognitoUserResponseModel, CognitoUserOperationResponseModel
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

cognito_client = boto3.client('cognito-idp', config=retry_config)
logger = safeLogger(service_name="CognitoUserService")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
try:
    cognito_enabled = os.environ.get("COGNITO_ENABLED", "false").lower() == "true"
    user_pool_id = os.environ.get("USER_POOL_ID", "")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

#######################
# Helper Functions
#######################

def check_cognito_enabled():
    """Check if Cognito is enabled in the solution
    
    Raises:
        VAMSGeneralErrorResponse: If Cognito is not enabled
    """
    if not cognito_enabled:
        logger.error("Cognito is not enabled in this deployment")
        raise VAMSGeneralErrorResponse("Cognito user management is not available")
    
    if not user_pool_id:
        logger.error("User pool ID not configured")
        raise VAMSGeneralErrorResponse("Cognito configuration error")


def extract_user_attributes(user):
    """Extract user attributes from Cognito user object
    
    Args:
        user: Cognito user object
        
    Returns:
        Dictionary with extracted attributes
    """
    attributes = {}
    for attr in user.get('Attributes', []):
        attributes[attr['Name']] = attr['Value']
    
    return {
        'userId': user.get('Username', ''),
        'email': attributes.get('email', ''),
        'phone': attributes.get('phone_number'),
        'userStatus': user.get('UserStatus', ''),
        'enabled': user.get('Enabled', True),
        'userCreateDate': user.get('UserCreateDate').isoformat() if user.get('UserCreateDate') else None,
        'userLastModifiedDate': user.get('UserLastModifiedDate').isoformat() if user.get('UserLastModifiedDate') else None,
        'mfaEnabled': attributes.get('mfa_enabled', 'false') == 'true'
    }


#######################
# Business Logic Functions
#######################

def list_cognito_users(starting_token=None, max_results=60):
    """List all Cognito users in the user pool
    
    Args:
        starting_token: Cognito pagination token for next page (VAMS standard: startingToken)
        max_results: Maximum number of users to return
        
    Returns:
        Dictionary with users list and optional NextToken (VAMS standard)
    """
    try:
        check_cognito_enabled()
        
        logger.info(f"Listing Cognito users from pool {user_pool_id}")
        
        # Build request parameters
        params = {
            'UserPoolId': user_pool_id,
            'Limit': max_results
        }
        
        if starting_token:
            params['PaginationToken'] = starting_token
        
        # List users
        response = cognito_client.list_users(**params)
        
        # Extract user data
        users = []
        for user in response.get('Users', []):
            try:
                user_data = extract_user_attributes(user)
                users.append(user_data)
            except Exception as e:
                logger.warning(f"Error extracting user data: {e}")
                continue
        
        result = {'users': users}
        
        # Add NextToken if present (VAMS standard)
        if 'PaginationToken' in response:
            result['NextToken'] = response['PaginationToken']
        
        logger.info(f"Retrieved {len(users)} Cognito users")
        return result
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.exception(f"Cognito error listing users: {error_code}")
        if error_code == 'ResourceNotFoundException':
            raise VAMSGeneralErrorResponse("User pool not found")
        elif error_code == 'InvalidParameterException':
            raise VAMSGeneralErrorResponse("Invalid request parameters")
        else:
            raise VAMSGeneralErrorResponse("Error listing users")
    except Exception as e:
        logger.exception(f"Error listing Cognito users: {e}")
        raise VAMSGeneralErrorResponse("Error listing users")


def create_cognito_user(user_data, claims_and_roles):
    """Create a new Cognito user
    
    Uses Cognito's admin_create_user which auto-generates a temporary password
    and sends a welcome email to the user. This is more secure as we don't handle passwords.
    
    Args:
        user_data: Dictionary with user creation data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        CognitoUserOperationResponseModel with operation result
    """
    try:
        check_cognito_enabled()
        
        user_id = user_data['userId']
        email = user_data['email']
        # Handle both 'phone' and 'phoneNumber' keys for compatibility
        phone = user_data.get('phone') or user_data.get('phoneNumber')
        
        logger.info(f"Creating Cognito user {user_id} with email {email} and phone {phone}")
        
        # Build user attributes
        user_attributes = [
            {'Name': 'email', 'Value': email},
            {'Name': 'email_verified', 'Value': 'true'}
        ]
        
        if phone:
            user_attributes.extend([
                {'Name': 'phone_number', 'Value': phone},
                {'Name': 'phone_number_verified', 'Value': 'true'}
            ])
            logger.info(f"Adding phone number attribute: {phone}")
        
        # Create user - Cognito will auto-generate password and send welcome email
        response = cognito_client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=user_id,
            UserAttributes=user_attributes,
            DesiredDeliveryMediums=['EMAIL']
            # MessageAction not specified - Cognito will send welcome email with temporary password
        )
        
        logger.info(f"Successfully created Cognito user {user_id}")
        
        now = datetime.utcnow().isoformat()
        return CognitoUserOperationResponseModel(
            success=True,
            message=f"User {user_id} created successfully. A welcome email with temporary password has been sent to their email.",
            userId=user_id,
            operation="create",
            timestamp=now
        )
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.exception(f"Cognito error creating user: {error_code}")
        if error_code == 'UsernameExistsException':
            raise VAMSGeneralErrorResponse("User already exists")
        elif error_code == 'InvalidParameterException':
            raise VAMSGeneralErrorResponse("Invalid user parameters")
        else:
            raise VAMSGeneralErrorResponse("Error creating user")
    except Exception as e:
        logger.exception(f"Error creating Cognito user: {e}")
        raise VAMSGeneralErrorResponse("Error creating user")


def update_cognito_user(user_id, update_data, claims_and_roles):
    """Update an existing Cognito user's email and/or phone
    
    Args:
        user_id: The user ID to update
        update_data: Dictionary with update data (email and/or phone)
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        CognitoUserOperationResponseModel with operation result
    """
    try:
        check_cognito_enabled()
        
        # Handle both 'phone' and 'phoneNumber' keys for compatibility
        phone = update_data.get('phone') or update_data.get('phoneNumber')
        
        logger.info(f"Updating Cognito user {user_id} with email {update_data.get('email')} and phone {phone}")
        
        # Build user attributes to update
        user_attributes = []
        
        if 'email' in update_data and update_data['email']:
            user_attributes.extend([
                {'Name': 'email', 'Value': update_data['email']},
                {'Name': 'email_verified', 'Value': 'true'}
            ])
        
        # Handle phone number - if phone has a value, set it; otherwise clear it
        if phone:
            # Phone number provided - update it and mark as verified
            user_attributes.extend([
                {'Name': 'phone_number', 'Value': phone},
                {'Name': 'phone_number_verified', 'Value': 'true'}
            ])
            logger.info(f"Adding phone number attribute: {phone}")
        else:
            # Phone number not provided or empty - set to empty and mark as not verified
            user_attributes.extend([
                {'Name': 'phone_number', 'Value': ''},
                {'Name': 'phone_number_verified', 'Value': 'false'}
            ])
            logger.info("Clearing phone number (setting to empty)")
        
        # Update user attributes
        if user_attributes:
            cognito_client.admin_update_user_attributes(
                UserPoolId=user_pool_id,
                Username=user_id,
                UserAttributes=user_attributes
            )
        
        logger.info(f"Successfully updated Cognito user {user_id}")
        
        now = datetime.utcnow().isoformat()
        return CognitoUserOperationResponseModel(
            success=True,
            message=f"User {user_id} updated successfully",
            userId=user_id,
            operation="update",
            timestamp=now
        )
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.exception(f"Cognito error updating user: {error_code}")
        if error_code == 'UserNotFoundException':
            raise VAMSGeneralErrorResponse("User not found")
        elif error_code == 'InvalidParameterException':
            raise VAMSGeneralErrorResponse("Invalid update parameters")
        else:
            raise VAMSGeneralErrorResponse("Error updating user")
    except Exception as e:
        logger.exception(f"Error updating Cognito user: {e}")
        raise VAMSGeneralErrorResponse("Error updating user")


def delete_cognito_user(user_id, claims_and_roles):
    """Delete a Cognito user
    
    Args:
        user_id: The user ID to delete
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        CognitoUserOperationResponseModel with operation result
    """
    try:
        check_cognito_enabled()
        
        logger.info(f"Deleting Cognito user {user_id}")
        
        # Delete user
        cognito_client.admin_delete_user(
            UserPoolId=user_pool_id,
            Username=user_id
        )
        
        logger.info(f"Successfully deleted Cognito user {user_id}")
        
        now = datetime.utcnow().isoformat()
        return CognitoUserOperationResponseModel(
            success=True,
            message=f"User {user_id} deleted successfully",
            userId=user_id,
            operation="delete",
            timestamp=now
        )
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.exception(f"Cognito error deleting user: {error_code}")
        if error_code == 'UserNotFoundException':
            raise VAMSGeneralErrorResponse("User not found")
        else:
            raise VAMSGeneralErrorResponse("Error deleting user")
    except Exception as e:
        logger.exception(f"Error deleting Cognito user: {e}")
        raise VAMSGeneralErrorResponse("Error deleting user")


def reset_user_password(user_id, claims_and_roles):
    """Reset a Cognito user's password by deleting and recreating the user
    
    This approach ensures password reset works regardless of user state (CONFIRMED, FORCE_CHANGE_PASSWORD, etc.)
    by deleting the user and recreating them with the same email and phone number.
    Cognito will send a new welcome email with a temporary password.
    
    Args:
        user_id: The user ID to reset password for
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        CognitoUserOperationResponseModel with operation result
    """
    try:
        check_cognito_enabled()
        
        logger.info(f"Resetting password for Cognito user {user_id} via delete/recreate")
        
        # Step 1: Get current user attributes to preserve email and phone
        try:
            user_response = cognito_client.admin_get_user(
                UserPoolId=user_pool_id,
                Username=user_id
            )
            
            # Extract current attributes
            current_attributes = {}
            for attr in user_response.get('UserAttributes', []):
                current_attributes[attr['Name']] = attr['Value']
            
            email = current_attributes.get('email')
            phone = current_attributes.get('phone_number')
            
            logger.info(f"Retrieved user attributes - email: {email}, phone: {phone}")
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'UserNotFoundException':
                raise VAMSGeneralErrorResponse("User not found")
            raise
        
        # Step 2: Delete the user
        try:
            cognito_client.admin_delete_user(
                UserPoolId=user_pool_id,
                Username=user_id
            )
            logger.info(f"Deleted user {user_id}")
        except ClientError as e:
            logger.exception(f"Error deleting user during reset: {e}")
            raise VAMSGeneralErrorResponse("Error resetting password - could not delete user")
        
        # Step 3: Recreate the user with same email and phone
        try:
            user_attributes = [
                {'Name': 'email', 'Value': email},
                {'Name': 'email_verified', 'Value': 'true'}
            ]
            
            if phone:
                user_attributes.extend([
                    {'Name': 'phone_number', 'Value': phone},
                    {'Name': 'phone_number_verified', 'Value': 'true'}
                ])
            
            cognito_client.admin_create_user(
                UserPoolId=user_pool_id,
                Username=user_id,
                UserAttributes=user_attributes,
                DesiredDeliveryMediums=['EMAIL']
            )
            logger.info(f"Recreated user {user_id} with new temporary password")
            
        except ClientError as e:
            logger.exception(f"Error recreating user during reset: {e}")
            raise VAMSGeneralErrorResponse("Error resetting password - could not recreate user")
        
        logger.info(f"Successfully reset password for Cognito user {user_id}")
        
        now = datetime.utcnow().isoformat()
        return CognitoUserOperationResponseModel(
            success=True,
            message=f"Password reset successfully for user {user_id}. A new temporary password has been sent to their email.",
            userId=user_id,
            operation="resetPassword",
            timestamp=now
        )
        
    except VAMSGeneralErrorResponse:
        # Re-raise VAMS errors
        raise
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.exception(f"Cognito error resetting password: {error_code}")
        if error_code == 'UserNotFoundException':
            raise VAMSGeneralErrorResponse("User not found")
        elif error_code == 'InvalidParameterException':
            raise VAMSGeneralErrorResponse("Invalid reset parameters")
        else:
            raise VAMSGeneralErrorResponse("Error resetting password")
    except Exception as e:
        logger.exception(f"Error resetting user password: {e}")
        raise VAMSGeneralErrorResponse("Error resetting password")


#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests to list Cognito users
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate query parameters
        try:
            request_model = parse(query_parameters, model=ListCognitoUsersRequestModel)
            query_params = {
                'starting_token': request_model.startingToken,
                'max_results': request_model.pageSize or request_model.maxItems
            }
        except ValidationError as v:
            logger.exception(f"Validation error in query parameters: {v}")
            query_params = {
                'starting_token': query_parameters.get('startingToken'),
                'max_results': int(query_parameters.get('pageSize', 60))
            }
        
        # List users
        result = list_cognito_users(
            starting_token=query_params.get('starting_token'),
            max_results=query_params.get('max_results', 60)
        )
        
        # Convert to response models
        formatted_users = []
        for user in result.get('users', []):
            try:
                user_model = CognitoUserResponseModel(**user)
                formatted_users.append(user_model.dict())
            except ValidationError:
                # Fall back to raw user data if conversion fails
                formatted_users.append(user)
        
        response = {'users': formatted_users}
        if 'NextToken' in result:
            response['NextToken'] = result['NextToken']
        
        return success(body=response)
        
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_post_request(event):
    """Handle POST requests to create user or reset password
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    path = event['requestContext']['http']['path']
    
    try:
        # Check if this is a password reset request
        if 'resetPassword' in path:
            # Password reset request
            user_id = path_parameters.get('userId')
            if not user_id:
                return validation_error(body={'message': "userId is required"}, event=event)
            
            # Validate userId
            (valid, message) = validate({
                'userId': {
                    'value': user_id,
                    'validator': 'USERID'
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)
            
            # Parse request body for confirmation
            body = event.get('body')
            if body:
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except json.JSONDecodeError as e:
                        logger.exception(f"Invalid JSON in request body: {e}")
                        return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
                
                # Parse and validate reset request
                try:
                    request_model = parse(body, model=ResetPasswordRequestModel)
                except ValidationError as v:
                    logger.exception(f"Validation error: {v}")
                    return validation_error(body={'message': str(v)}, event=event)
            
            # Reset password
            result = reset_user_password(user_id, claims_and_roles)
            
            # AUDIT LOG: Password reset
            log_auth_changes(event, "cognitoUserPasswordReset", {
                "userId": result.userId,
                "operation": "resetPassword"
            })
            
            return success(body=result.dict())
        
        else:
            # Create user request
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
            request_model = parse(body, model=CreateCognitoUserRequestModel)
            
            # Create the user
            result = create_cognito_user(
                request_model.dict(exclude_unset=True),
                claims_and_roles
            )
            
            # AUDIT LOG: User created
            log_auth_changes(event, "cognitoUserCreate", {
                "userId": result.userId,
                "operation": "create",
                "email": request_model.email,
                "phone": request_model.phone
            })
            
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
    """Handle PUT requests to update user
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Extract userId from path
        user_id = path_parameters.get('userId')
        if not user_id:
            return validation_error(body={'message': "userId is required"}, event=event)
        
        # Validate userId
        (valid, message) = validate({
            'userId': {
                'value': user_id,
                'validator': 'USERID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)
        
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
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateCognitoUserRequestModel)
        
        # Update the user
        result = update_cognito_user(
            user_id,
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # AUDIT LOG: User updated
        log_auth_changes(event, "cognitoUserUpdate", {
            "userId": result.userId,
            "operation": "update",
            "email": request_model.email,
            "phone": request_model.phone
        })
        
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


def handle_delete_request(event):
    """Handle DELETE requests to delete user
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Extract userId from path
        user_id = path_parameters.get('userId')
        if not user_id:
            return validation_error(body={'message': "userId is required"}, event=event)
        
        # Validate userId
        (valid, message) = validate({
            'userId': {
                'value': user_id,
                'validator': 'USERID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)
        
        # Delete the user
        result = delete_cognito_user(user_id, claims_and_roles)
        
        # AUDIT LOG: User deleted
        log_auth_changes(event, "cognitoUserDelete", {
            "userId": result.userId,
            "operation": "delete"
        })
        
        return success(body=result.dict())
    
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for Cognito user service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Check Cognito is enabled first
        check_cognito_enabled()
        
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        logger.info(f"Processing {method} request for path: {path}")
        
        # Check API authorization (API-level only, no data-level checks)
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
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)