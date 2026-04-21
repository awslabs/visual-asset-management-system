# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""User API models for VAMS - Cognito User Management."""

from typing import Optional, Literal
from pydantic import Field, validator
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate
from customLogging.logger import safeLogger

logger = safeLogger(service_name="UserModels")

######################## Cognito User Management API Models ##########################

class ListCognitoUsersRequestModel(BaseModel, extra='ignore'):
    """Request model for listing Cognito users"""
    maxItems: Optional[int] = Field(default=60, ge=1, le=60)
    pageSize: Optional[int] = Field(default=60, ge=1, le=60)
    startingToken: Optional[str] = None


class CreateCognitoUserRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a Cognito user"""
    userId: str = Field(min_length=3, max_length=256, strip_whitespace=True)
    email: str = Field(min_length=3, max_length=256, strip_whitespace=True)
    phone: Optional[str] = Field(None, min_length=10, max_length=20)

    @root_validator
    def validate_fields(cls, values):
        """Validate userId, email, and phone format"""
        # Validate userId
        (valid, message) = validate({
            'userId': {
                'value': values.get('userId'),
                'validator': 'USERID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate email
        (valid, message) = validate({
            'email': {
                'value': values.get('email'),
                'validator': 'EMAIL'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate phone if provided (E.164 format: +12345678900)
        if values.get('phone'):
            phone = values.get('phone')
            # Basic E.164 validation: starts with +, followed by 10-15 digits
            if not phone.startswith('+'):
                raise ValueError("phone must be in E.164 format (e.g., +12345678900)")
            
            # Remove the + and check if remaining characters are digits
            phone_digits = phone[1:]
            if not phone_digits.isdigit():
                raise ValueError("phone must contain only digits after the + sign")
            
            if len(phone_digits) < 10 or len(phone_digits) > 15:
                raise ValueError("phone must have 10-15 digits after the + sign")
        
        return values


class UpdateCognitoUserRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a Cognito user"""
    email: Optional[str] = Field(None, min_length=3, max_length=256)
    phone: Optional[str] = Field(None, min_length=10, max_length=20)

    @root_validator
    def validate_fields(cls, values):
        """Validate that at least one field is provided and formats are correct"""
        email = values.get('email')
        phone = values.get('phone')
        
        # Ensure at least one field is provided
        if not email and not phone:
            raise ValueError("At least one field (email or phone) must be provided for update")
        
        # Validate email if provided
        if email:
            (valid, message) = validate({
                'email': {
                    'value': email,
                    'validator': 'EMAIL'
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        # Validate phone if provided (E.164 format)
        if phone:
            if not phone.startswith('+'):
                raise ValueError("phone must be in E.164 format (e.g., +12345678900)")
            
            phone_digits = phone[1:]
            if not phone_digits.isdigit():
                raise ValueError("phone must contain only digits after the + sign")
            
            if len(phone_digits) < 10 or len(phone_digits) > 15:
                raise ValueError("phone must have 10-15 digits after the + sign")
        
        return values


class ResetPasswordRequestModel(BaseModel, extra='ignore'):
    """Request model for resetting a Cognito user's password"""
    confirmReset: bool = Field(default=False)

    @validator('confirmReset')
    def validate_confirmation(cls, v):
        """Ensure confirmation is provided for password reset"""
        if not v:
            raise ValueError("confirmReset must be true to reset password")
        return v


class CognitoUserResponseModel(BaseModel, extra='ignore'):
    """Response model for Cognito user data"""
    userId: str
    email: str
    phone: Optional[str] = None
    userStatus: str
    enabled: bool
    userCreateDate: Optional[str] = None
    userLastModifiedDate: Optional[str] = None
    mfaEnabled: Optional[bool] = False


class CognitoUserOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for Cognito user operations (create, update, delete, resetPassword)"""
    success: bool
    message: str
    userId: str
    operation: Literal["create", "update", "delete", "resetPassword"]
    timestamp: str