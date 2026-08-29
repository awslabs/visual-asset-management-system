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
    query_params = event.get('queryStringParameters', {}) or {}
    item_id = query_params.get('itemId')

    (valid, message) = validate({'itemId': {'value': item_id, 'validator': 'ID'}})
    if not valid:
        return validation_error(body={'message': message}, event=event)

    item = table.get_item(Key={'itemId': item_id}).get('Item')
    if not item:
        return general_error(body={'message': 'Item not found'}, event=event)

    # Tier-2 authorization. The empty-token guard is its own statement BEFORE enforce().
    # Wrapping the enforce() in `if len(tokens) > 0:` instead would skip authorization
    # entirely on an empty token list and fall through to the success below.
    item['object__type'] = 'CHANGE_ME_objectType'
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not CasbinEnforcer(claims_and_roles).enforce(event, item):
        return authorization_error()

    return success(body=item)


def handle_list(event):
    """External paging: one page plus an opaque NextToken (see backend/CLAUDE.md Rule 15).

    The token is Base64 so it is a string the request model can accept back, and opaque so
    callers cannot depend on its interior. Returning the raw LastEvaluatedKey dict here caps
    the listing at page one with no error anywhere.
    """
    query_params = event.get('queryStringParameters', {}) or {}
    max_items = int(query_params.get('maxItems', str(DEFAULT_PAGE_SIZE)))
    starting_token = query_params.get('startingToken')

    query_kwargs = {
        'KeyConditionExpression': Key('databaseId').eq(query_params['databaseId']),
        'Limit': max_items,
    }
    if starting_token:
        query_kwargs['ExclusiveStartKey'] = json.loads(
            base64.b64decode(starting_token).decode('utf-8')
        )

    response = table.query(**query_kwargs)

    allowed = []
    for item in response.get('Items', []):
        # List filtering is the one Tier-2 shape that may test tokens as a condition:
        # it appends on success, so empty tokens produce an empty list, not an unfiltered one.
        item['object__type'] = 'CHANGE_ME_objectType'
        if len(claims_and_roles["tokens"]) > 0 and CasbinEnforcer(claims_and_roles).enforce(event, item):
            allowed.append(item)

    result = {'Items': allowed}
    if 'LastEvaluatedKey' in response:
        result['NextToken'] = base64.b64encode(
            json.dumps(response['LastEvaluatedKey']).encode('utf-8')
        ).decode('utf-8')
    return success(body=result)


def _all_rows_for_database(database_id):
    """Read to exhaustion: for when THIS HANDLER needs every row (cascade delete, cycle
    check, existence test) rather than the caller.

    A single query returns at most 1 MB, and a FilterExpression is applied only to what
    that call already read — so one un-looped call is neither a complete listing nor a
    valid existence check (backend/CLAUDE.md Rule 14). Terminate on key PRESENCE: against
    a MagicMock, `response.get('LastEvaluatedKey')` is truthy forever and the loop never
    ends, while `in` resolves False and exits.
    """
    rows = []
    query_kwargs = {'KeyConditionExpression': Key('databaseId').eq(database_id)}
    while True:
        response = table.query(**query_kwargs)
        rows.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            break
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    return rows


def handle_put(event):
    # TODO: Implement — same Tier-2 shape as handle_get before any write
    pass


def handle_delete(event):
    # TODO: Implement — same Tier-2 shape as handle_get before any write
    pass
```

The `handle_list` and `_all_rows_for_database` helpers above need these imports added to the
header block: `import base64`, `import json`, `from boto3.dynamodb.conditions import Key`, and a
`DEFAULT_PAGE_SIZE` module constant.

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
    # `regex=` is the Pydantic v1 spelling. `pattern=` is v2 and is SILENTLY SWALLOWED into
    # FieldInfo.extra — the model imports cleanly, every test passes, and the field is
    # unconstrained. Same for `strip_whitespace=` on Field(): it is a Config/constr option,
    # not a field constraint, so use the Config below when a value must be stripped.
    databaseId: str = Field(min_length=4, max_length=256, regex=id_pattern)
    itemName: str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    description: str = Field(min_length=4, max_length=256)

    class Config:
        anystr_strip_whitespace = True

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
    itemName: Optional[str] = Field(None, min_length=1, max_length=256, regex=object_name_pattern)
    description: Optional[str] = Field(None, min_length=4, max_length=256)

    class Config:
        anystr_strip_whitespace = True
```

`tests/models/test_no_dead_field_kwargs.py` fails on any swallowed `pattern=`, so scaffolding a
model with the v2 spelling breaks the suite rather than shipping an unconstrained field. That test
also holds a hard count of the pre-existing inert `strip_whitespace=` declarations, so adding one
more from a template would fail it too — which is why the template no longer carries any.

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
