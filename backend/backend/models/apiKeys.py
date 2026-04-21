# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime
from typing import Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from customLogging.logger import safeLogger
from common.validators import validate, object_name_pattern

logger = safeLogger(service_name="ApiKeyModels")


def _validate_iso8601_date(value):
    """Validate that a string is a valid ISO 8601 date/datetime."""
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
            raise ValueError(f"Invalid date format: '{value}'. Use ISO 8601 format (e.g. 2026-12-31 or 2026-12-31T23:59:59Z)")
    return value


class CreateApiKeyRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new API key"""
    apiKeyName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    userId: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    expiresAt: Optional[str] = Field(None, max_length=30, strip_whitespace=True)

    @root_validator
    def validate_fields(cls, values):
        logger.info("Validating API key creation parameters")
        validation_map = {
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
    isActive: Optional[str] = Field(None, pattern=r'^(true|false)$')

    @root_validator
    def validate_at_least_one_field(cls, values):
        if values.get('description') is None and values.get('expiresAt') is None and values.get('isActive') is None:
            raise ValueError("At least one of 'description', 'expiresAt', or 'isActive' must be provided")

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
