# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime, timezone
from typing import Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from customLogging.logger import safeLogger
from common.validators import validate, object_name_pattern

logger = safeLogger(service_name="ApiKeyModels")

# Maximum lifetime of user-level (self-service) API keys, measured from key
# creation. User-created keys must expire, and neither creation nor later
# edits may set an expiration beyond this window; after it elapses the user
# must rotate (create a new key).
USER_API_KEY_MAX_EXPIRATION_DAYS = 365

# The stored values the authorizer accepts for an API key's active flag. The
# authorizer compares exactly (`isActive != 'true'` denies), so the literals are
# case-sensitive: 'True' would store a key the authorizer treats as disabled.
ALLOWED_API_KEY_ACTIVE_VALUES = ('true', 'false')


def _validate_iso8601_date(value):
    """Validate that a string is a valid ISO 8601 date/datetime.

    The rejection message names the expected format only. Pydantic validation
    errors are surfaced to the caller verbatim, so the submitted value is logged
    rather than echoed back.
    """
    if value is None:
        return value
    try:
        # Try full ISO 8601 datetime (e.g. 2026-12-31T23:59:59Z)
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        try:
            # Try date-only format (e.g. 2026-12-31)
            datetime.strptime(value, '%Y-%m-%d')
        except (ValueError, TypeError):
            logger.error(f"Rejected expiration value: {value}")
            raise ValueError("Invalid date format. Use ISO 8601 format (e.g. 2026-12-31 or 2026-12-31T23:59:59Z)")
    return value


def parse_iso8601_datetime(value):
    """Parse an ISO 8601 date/datetime string to a timezone-aware datetime (UTC assumed when naive)."""
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        parsed = datetime.strptime(value, '%Y-%m-%d')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class CreateApiKeyRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new API key"""
    apiKeyName: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
    userId: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    expiresAt: Optional[str] = Field(None, max_length=30, strip_whitespace=True)

    @root_validator
    def validate_fields(cls, values):
        logger.info("Validating API key creation parameters")
        validation_map = {
            'apiKeyName': {
                'value': values.get('apiKeyName'),
                'validator': 'OBJECT_NAME'
            },
            'userId': {
                'value': values.get('userId'),
                'validator': 'USERID'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
        }
        (valid, message) = validate(validation_map)
        if not valid:
            logger.error(message)
            raise ValueError(message)

        # Validate expiresAt date format
        if values.get('expiresAt'):
            _validate_iso8601_date(values.get('expiresAt'))

        return values


class UpdateApiKeyRequestModel(BaseModel, extra='ignore'):
    """Request model for updating an API key"""
    description: Optional[str] = Field(None, max_length=256, strip_whitespace=True)
    expiresAt: Optional[str] = Field(None, max_length=30, strip_whitespace=True)
    isActive: Optional[str] = Field(None, regex=r'^(true|false)$')

    @root_validator
    def validate_at_least_one_field(cls, values):
        if values.get('description') is None and values.get('expiresAt') is None and values.get('isActive') is None:
            raise ValueError("At least one of 'description', 'expiresAt', or 'isActive' must be provided")

        if values.get('isActive') is not None and values.get('isActive') not in ALLOWED_API_KEY_ACTIVE_VALUES:
            raise ValueError(f"isActive must be one of: {', '.join(ALLOWED_API_KEY_ACTIVE_VALUES)}")

        # Validate description with STRING_256 if provided
        if values.get('description') is not None:
            (valid, message) = validate({
                'description': {
                    'value': values.get('description'),
                    'validator': 'STRING_256',
                    'optional': True
                }
            })
            if not valid:
                raise ValueError(message)

        # Validate expiresAt date format
        if values.get('expiresAt'):
            _validate_iso8601_date(values.get('expiresAt'))

        return values


class CreateUserApiKeyRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a user-level (self-service) API key.

    The key is always tied to the requesting user (no userId field), and an
    expiration date is required. The handler enforces the maximum expiration
    window (USER_API_KEY_MAX_EXPIRATION_DAYS from creation).
    """
    apiKeyName: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    expiresAt: str = Field(min_length=1, max_length=30, strip_whitespace=True)

    @root_validator
    def validate_fields(cls, values):
        logger.info("Validating user API key creation parameters")
        (valid, message) = validate({
            'apiKeyName': {
                'value': values.get('apiKeyName'),
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)

        if values.get('expiresAt'):
            _validate_iso8601_date(values.get('expiresAt'))

        return values


class UpdateUserApiKeyRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a user-level (self-service) API key.

    Unlike the admin update model, the expiration cannot be cleared -- when
    provided it must be a non-empty valid date. The handler enforces ownership
    and the maximum expiration window from the key's original creation.
    """
    description: Optional[str] = Field(None, max_length=256, strip_whitespace=True)
    expiresAt: Optional[str] = Field(None, min_length=1, max_length=30, strip_whitespace=True)
    isActive: Optional[str] = Field(None, regex=r'^(true|false)$')

    @root_validator
    def validate_fields(cls, values):
        if values.get('description') is None and values.get('expiresAt') is None and values.get('isActive') is None:
            raise ValueError("At least one of 'description', 'expiresAt', or 'isActive' must be provided")

        if values.get('isActive') is not None and values.get('isActive') not in ALLOWED_API_KEY_ACTIVE_VALUES:
            raise ValueError(f"isActive must be one of: {', '.join(ALLOWED_API_KEY_ACTIVE_VALUES)}")

        if values.get('description') is not None:
            (valid, message) = validate({
                'description': {
                    'value': values.get('description'),
                    'validator': 'STRING_256',
                    'optional': True
                }
            })
            if not valid:
                raise ValueError(message)

        if values.get('expiresAt') is not None:
            if not values.get('expiresAt'):
                raise ValueError("User API keys require an expiration date; it cannot be cleared")
            _validate_iso8601_date(values.get('expiresAt'))

        return values
