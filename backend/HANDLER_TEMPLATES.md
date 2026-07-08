# Backend Copy-Paste Templates

Skeletons for a new Lambda handler, its Pydantic model file, and its unit-test file. Follow the rules in `backend/CLAUDE.md` when filling these in; the gold-standard reference is `backend/handlers/assets/assetService.py`.

Replace `CHANGE_ME` and TODOs with domain-specific values.

---

## New Handler Template

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from backend.common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)

# Configure AWS clients with retry configuration
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="CHANGE_ME")

claims_and_roles = {}

try:
    table_name = get_table_name(ResourceKeys.CHANGE_ME_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

table = dynamodb.Table(table_name)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if method == 'GET':
            return handle_get(event)
        elif method == 'PUT':
            return handle_put(event)
        elif method == 'DELETE':
            return handle_delete(event)
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


def handle_get(event):
    # TODO: Implement
    pass


def handle_put(event):
    # TODO: Implement
    pass


def handle_delete(event):
    # TODO: Implement
    pass
```

---

## New Pydantic Model Template

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, List, Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from customLogging.logger import safeLogger
from common.validators import validate, id_pattern, object_name_pattern

logger = safeLogger(service_name="CHANGE_ME_Models")


class CreateItemRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new item"""
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    itemName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)

    @root_validator
    def validate_fields(cls, values):
        logger.info("Validating custom parameters")
        # Add custom validation here
        return values


class ItemResponseModel(BaseModel, extra='ignore'):
    """Response model for item data"""
    itemId: str
    itemName: str
    description: str = ""


class UpdateItemRequestModel(BaseModel, extra='ignore'):
    """Request model for updating an item"""
    itemName: Optional[str] = Field(None, min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: Optional[str] = Field(None, min_length=4, max_length=256, strip_whitespace=True)
```

---

## New Test Template

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
import json
from unittest.mock import MagicMock, patch


@pytest.mark.unit
class TestYourHandler:
    """Unit tests for your handler"""

    def _make_event(self, method='GET', path='/your-path', body=None, query_params=None):
        """Helper to build API Gateway REST API event"""
        event = {
            'requestContext': {
                'http': {
                    'method': method,
                    'path': path
                }
            },
            'queryStringParameters': query_params or {},
            'headers': {
                'authorization': 'Bearer test-token'
            }
        }
        if body:
            event['body'] = json.dumps(body)
        return event

    def test_placeholder(self):
        """Replace with real tests"""
        assert True
```
