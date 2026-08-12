# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate
from customLogging.logger import safeLogger

logger = safeLogger(service_name="AuthLoginProfileModels")


class UpdateLoginProfileRequestModel(BaseModel, extra='ignore'):
    """Optional request body for POST /auth/loginProfile/{userId}."""
    email: Optional[str] = Field(None, max_length=256, strip_whitespace=True)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'email': {
                'value': values.get('email'),
                'validator': 'EMAIL',
                'optional': True
            }
        })
        if not valid:
            raise ValueError(message)
        return values


class LoginProfileResponseModel(BaseModel, extra='ignore'):
    """Login profile entity returned by the loginProfile endpoints.

    The handler returns the stored profile dict directly so organization-specific
    fields added by customAuthProfileLoginWriteOverride are preserved; this model
    documents the guaranteed fields.
    """
    userId: str
    email: Optional[str] = None
