# CLAUDE.md -- VAMS Python Lambda Backend

> Auto-loaded when Claude Code works within `backend/`. This is the authoritative
> guide for all backend Lambda handler development, Pydantic model creation,
> authorization enforcement, DynamoDB access, and testing patterns.

---

## Quick Reference

| Item                  | Value                                                  |
| --------------------- | ------------------------------------------------------ |
| Runtime               | Python 3.13+                                           |
| Framework             | AWS Lambda + API Gateway v2 (HTTP API)                 |
| Validation            | Pydantic **1.10.7** (NOT v2) via aws-lambda-powertools |
| Auth                  | Casbin ABAC/RBAC with DynamoDB policy storage          |
| ORM                   | boto3 DynamoDB resource + client APIs                  |
| Search                | OpenSearch (opensearch-py 2.5.0)                       |
| Logging               | aws-lambda-powertools Logger with custom redaction     |
| Tests                 | pytest 8.3.4 + moto 5.1.0 for AWS mocks                |
| Gold Standard Handler | `backend/handlers/assets/assetService.py`              |
| Gold Standard Model   | `backend/models/assetsV3.py`                           |

---

## Directory Structure

> **Maintenance note:** Update this tree when adding new handler domains, model files, or test directories. See root `CLAUDE.md` Rule 11.

```
backend/
├── conftest.py                          # Root test config: sys.path, env vars, mock imports
├── pytest.ini                           # Markers: unit, integration, slow, aws
├── requirements.txt                     # Python 3.13+ production deps
├── requirements-dev.txt                 # Dev/test deps (moto, pytest, mypy, flake8)
├── backend/
│   ├── common/                          # Shared utilities
│   │   ├── constants.py                 # ABAC policy, allowed values, file blocklists
│   │   ├── dynamodb.py                  # DynamoDB helpers (to_update_expr, get_asset_object_from_id)
│   │   ├── validators.py                # Input validation regex patterns and validate() dispatcher
│   │   ├── s3.py                        # S3 file validation (extension + MIME type checks)
│   │   └── stepfunctions_builder.py     # ASL builder for workflows (builder pattern:
│   │                                    #   TaskStateBuilder, LambdaTaskBuilder,
│   │                                    #   SqsTaskBuilder, EventBridgeTaskBuilder)
│   ├── customLogging/
│   │   ├── auditLogging.py              # CloudWatch audit logging (9 event types, silent failure)
│   │   └── logger.py                    # safeLogger wrapper with sensitive data redaction
│   ├── handlers/                        # Lambda handlers (one folder per domain)
│   │   ├── assets/assetService.py       # GOLD STANDARD handler -- follow this pattern
│   │   ├── assets/assetVersions.py     # Asset version CRUD + archive/unarchive + update (versionAlias)
│   │   ├── auth/                        # Auth handlers (authorizer, constraints, cognito, preTokenGen, apiKeyService)
│   │   ├── authz/__init__.py            # Casbin ABAC/RBAC enforcer (CasbinEnforcer proxy)
│   │   ├── assetLinks/                  # Asset relationship management
│   │   ├── comments/                    # Comment CRUD
│   │   ├── config/                      # System configuration
│   │   ├── databases/                   # Database CRUD
│   │   ├── indexing/                    # OpenSearch indexing (DynamoDB/S3 streams)
│   │   ├── metadata/                    # Metadata CRUD
│   │   ├── metadataschema/              # Metadata schema management
│   │   ├── pipelines/                   # Pipeline management (modernized: Pydantic models,
│   │                                    #   supports Lambda/SQS/EventBridge execution types)
│   │   ├── roles/                       # Role CRUD
│   │   ├── search/                      # OpenSearch search handlers
│   │   ├── sendEmail/                   # Email notification Lambda
│   │   ├── subscription/                # Asset subscription management
│   │   ├── tags/                        # Tag CRUD
│   │   ├── tagTypes/                    # Tag type management
│   │   ├── userRoles/                   # User-role assignment
│   │   ├── workflows/                   # Step Functions workflow management (modernized:
│   │                                    #   Pydantic models, builder pattern for ASL generation)
│   │   └── [handlerType]/               # Handler-type specific handlers
│   └── models/                          # Pydantic models
│       ├── assetsV3.py                  # GOLD STANDARD model file -- follow this pattern
│       │                                #   Includes UpdateAssetVersionRequestModel (versionAlias, comment)
│       │                                #   AssetVersionListItemModel/CurrentVersionModel have versionAlias, isArchived
│       ├── apiKeys.py                   # API key request/response models
│       ├── pipelines.py                 # Pipeline models (PipelineExecutionType enum, SQS/EventBridge fields)
│       ├── workflows.py                 # Workflow models (Step Functions ASL generation)
│       ├── common.py                    # Response helpers, error functions, APIGatewayProxyResponseV2
│       └── [domain].py                  # Domain-specific models
├── lambdaLayers/                        # Lambda layer definitions
└── tests/                               # Test suite with mocks
    ├── mocks/                           # Mock modules replacing real imports
    │   ├── common/                      # Mock common utilities
    │   ├── customLogging/               # Mock logging
    │   ├── customConfigCommon/          # Mock config
    │   └── handlers/                    # Mock handlers
    └── [domain]/                        # Per-domain test files
```

---

## Critical Rules

1. **ALWAYS use Pydantic v1 syntax.** This project uses `pydantic==1.10.7`. Never use
   Pydantic v2 APIs (`model_validate`, `model_dump`, `ConfigDict`). Use `@root_validator`,
   `@validator`, `Field(...)`, `extra='ignore'`.

2. **ALWAYS import BaseModel from aws_lambda_powertools**, not from pydantic directly.

    ```python
    from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
    ```

3. **ALWAYS use the validate() dispatcher** from `common.validators` for complex validation
   in `@root_validator` methods. Never write raw regex validation inline.

4. **ALWAYS enforce two-level authorization**: `enforceAPI()` for route access, then
   `enforce()` for object-level access inside method handlers.

5. **ALWAYS use safeLogger** from `customLogging.logger`. Never use `print()` or raw
   `logging.getLogger()`.

6. **ALWAYS wrap AWS clients with retry config** at module level:

    ```python
    retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
    ```

7. **ALWAYS raise VAMSGeneralErrorResponse** for business logic errors. Never return
   raw dicts with status codes.

8. **ALWAYS use `extra='ignore'`** on all Pydantic model classes to silently drop
   unexpected fields.

9. **NEVER log sensitive data.** The safeLogger auto-redacts `authorization`,
   `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`.
   Do not circumvent this.

10. **NEVER use `os.environ["KEY"]` outside of the module-level try/except block.**
    All environment variable loading happens once at cold start.

---

## Gold Standard Handler Pattern

Reference: `backend/handlers/assets/assetService.py`

Every new Lambda handler MUST follow this exact structure:

### 1. Module-Level Setup (Lines 1-87 of assetService.py)

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.yourDomain import YourRequestModel, YourResponseModel

# Configure AWS clients with retry configuration
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="YourServiceName")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables
try:
    your_table_name = os.environ["YOUR_STORAGE_TABLE_NAME"]
    # Required: os.environ["KEY"] -- raises KeyError on missing
    # Optional: os.environ.get("KEY") -- returns None on missing
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
your_table = dynamodb.Table(your_table_name)
```

### 2. Lambda Handler Entry Point

```python
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for your service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        # API-level authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Route to method handler
        if method == 'GET':
            return handle_get_request(event)
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
```

### 3. Method Handlers

```python
def handle_get_request(event):
    """Route GET requests to specific handlers"""
    path = event['requestContext']['http']['path']
    query_params = event.get('queryStringParameters', {}) or {}

    if '/items/' in path:
        item_id = path.split('/items/')[-1]
        return get_single_item(event, item_id)
    else:
        return get_all_items(event, query_params)
```

### 4. Business Logic Functions

```python
def get_single_item(event, item_id):
    """Get a single item -- validate, query, authorize, respond"""
    # Step 1: Validate params
    (valid, message) = validate({
        'itemId': {'value': item_id, 'validator': 'ID'}
    })
    if not valid:
        return validation_error(body={'message': message}, event=event)

    # Step 2: Query DynamoDB
    response = your_table.get_item(Key={'itemId': item_id})
    item = response.get('Item')
    if not item:
        return general_error(body={'message': 'Item not found'}, event=event)

    # Step 3: Object-level authorization
    item['object__type'] = 'yourObjectType'
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforce(event, item):
        return authorization_error()

    # Step 4: Return response
    return success(body=item)
```

### 5. Error Handling Hierarchy

| Exception                    | Response Function       | Status Code |
| ---------------------------- | ----------------------- | ----------- |
| `ValidationError` (Pydantic) | `validation_error()`    | 400         |
| `VAMSGeneralErrorResponse`   | `general_error()`       | 400         |
| `Exception` (catch-all)      | `internal_error()`      | 500         |
| Authorization failure        | `authorization_error()` | 403         |

All response functions accept an optional `event=` parameter for audit logging.

---

## Pydantic v1 Model Patterns

Reference: `backend/models/assetsV3.py`

### CORRECT Model Definition

```python
from typing import Dict, List, Optional, Literal, Union, Any
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from common.validators import validate, id_pattern, object_name_pattern, filename_pattern

class CreateItemRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new item"""
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    itemName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    isDistributable: bool
    tags: Optional[list[str]] = []

    @root_validator
    def validate_fields(cls, values):
        """Validate fields requiring custom logic beyond basic type checks"""
        # Use the validate() dispatcher for complex validation
        (valid, message) = validate({
            'tags': {
                'value': values.get('tags'),
                'validator': 'STRING_256_ARRAY',
                'optional': True
            }
        })
        if not valid:
            raise ValueError(message)
        return values
```

### INCORRECT -- Common Mistakes

```python
# WRONG: Importing from pydantic directly
from pydantic import BaseModel  # WRONG

# WRONG: Using Pydantic v2 syntax
class MyModel(BaseModel):
    model_config = ConfigDict(extra='ignore')  # WRONG -- v2 syntax

# WRONG: Missing extra='ignore'
class MyModel(BaseModel):  # WRONG -- must have extra='ignore'
    pass

# WRONG: Using model_validate (v2)
item = MyModel.model_validate(data)  # WRONG
# CORRECT:
item = parse(body, model=MyModel)  # from aws_lambda_powertools.utilities.parser

# WRONG: Using @field_validator (v2)
@field_validator('name')  # WRONG
# CORRECT:
@validator('name')  # Pydantic v1

# WRONG: Using model_dump (v2)
data = item.model_dump()  # WRONG
# CORRECT:
data = item.dict()  # Pydantic v1
```

### Field Validation Patterns

```python
# String with regex pattern (from common.validators)
databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)

# Optional field with default
tags: Optional[list[str]] = []
bucketExistingKey: Optional[str] = None

# Numeric constraints
file_size: Optional[int] = Field(None, ge=0)
num_parts: Optional[int] = Field(None, ge=0, le=10000)

# Nested model
currentVersion: Optional[CurrentVersionModel] = None
assetLocation: Optional[AssetLocationModel] = None
```

### Parsing Request Bodies

```python
from aws_lambda_powertools.utilities.parser import parse

# In handler code:
body = json.loads(event.get('body', '{}'))
request = parse(body, model=CreateItemRequestModel)
```

---

## Authorization System (Casbin ABAC/RBAC)

Reference: `backend/handlers/authz/__init__.py`

### Two-Level Enforcement

```python
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims

# Level 1: API-level authorization (in lambda_handler)
claims_and_roles = request_to_claims(event)
casbin_enforcer = CasbinEnforcer(claims_and_roles)
if not casbin_enforcer.enforceAPI(event):
    return authorization_error()

# Level 2: Object-level authorization (in method handlers)
item['object__type'] = 'asset'  # MUST annotate object type before enforce()
if not casbin_enforcer.enforce(event, item):
    return authorization_error()
```

### Key Concepts

-   **CasbinEnforcer** is a proxy with **60-second policy cache TTL** per user
-   Policy is stored in DynamoDB (`ConstraintsStorageTable`)
-   Claims are extracted via `request_to_claims(event)` which returns:
    ```python
    {
        "tokens": ["userId", ...],
        "roles": ["role1", "role2"],
        "mfaEnabled": True/False
    }
    ```
-   **MFA-aware**: Roles with `mfaRequired=True` are only active when `mfaEnabled=True` in claims
-   **Object annotation**: You MUST add `object__type` field to the item dict before calling `enforce()`
-   Valid object types: `database`, `asset`, `api`, `web`, `tag`, `tagType`, `role`, `userRole`, `pipeline`, `workflow`, `metadataSchema`, `apiKey`

### Casbin Policy Model (from constants.py)

```
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj_rule, act, eft

[role_definition]
g = _, _

[policy_effect]
e = some(where (p.eft == allow)) && !some(where (p.eft == deny))

[matchers]
m = g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act
```

### Constraint Fields (PERMISSION_CONSTRAINT_FIELDS)

These are the fields that can be used in ABAC policy rules:

```python
"databaseId", "assetName", "assetType", "tags",
"tagName", "tagTypeName", "roleName", "userId",
"pipelineId", "pipelineType", "pipelineExecutionType",
"workflowId", "metadataSchemaName", "metadataSchemaEntityType",
"object__type", "route__path"
```

---

## Validators (common/validators.py)

### validate() Dispatcher

The `validate()` function is the standard way to validate inputs in both `@root_validator`
methods and handler code:

```python
from common.validators import validate

(valid, message) = validate({
    'databaseId': {
        'value': database_id,
        'validator': 'ID'
    },
    'assetId': {
        'value': asset_id,
        'validator': 'ASSET_ID'
    },
    'tags': {
        'value': tag_list,
        'validator': 'STRING_256_ARRAY',
        'optional': True          # Skip validation if None or empty
    },
    'databaseId': {
        'value': db_id,
        'validator': 'ID',
        'allowGlobalKeyword': True  # Allow "GLOBAL" as valid value
    }
})
if not valid:
    raise ValueError(message)  # In @root_validator
    # OR
    return validation_error(body={'message': message}, event=event)  # In handler
```

### Available Validator Types

| Validator             | Pattern/Rule                        | Use For                      |
| --------------------- | ----------------------------------- | ---------------------------- | ---------- |
| `ID`                  | `^[-_a-zA-Z0-9]{3,63}$`             | databaseId, pipelineId, etc. |
| `ASSET_ID`            | filename_pattern, max 256 chars     | assetId                      |
| `UUID`                | Standard UUID format                | Unique identifiers           |
| `FILE_NAME`           | `^(?!.\*[<>:"\/\\                   | ?\*])...`                    | File names |
| `OBJECT_NAME`         | `^[a-zA-Z0-9\-._\s]{1,256}$`        | assetName, dbName, etc.      |
| `EMAIL`               | Email regex                         | Email addresses              |
| `USERID`              | `^[\w\-\.\+\@]{3,256}$`             | User identifiers             |
| `REGEX`               | Valid regex                         | Regex patterns               |
| `NUMBER`              | Numeric string                      | Number strings               |
| `BOOL`                | Boolean string                      | Boolean strings              |
| `RELATIVE_FILE_PATH`  | `^\/.*$`                            | S3 relative paths            |
| `ASSET_PATH`          | `^.+\/.+$`                          | Asset S3 paths               |
| `ASSET_PATH_PIPELINE` | `^pipelines\/.+\/.+\/output\/.+\/$` | Pipeline output paths        |
| `STRING_30`           | Max 30 chars                        | Short strings                |
| `STRING_256`          | Max 256 chars                       | Medium strings               |
| `STRING_JSON`         | Valid JSON                          | JSON strings                 |
| `FILE_EXTENSION`      | `^[\\.]([a-zA-Z0-9]){1,7}$`         | File extensions              |
| `ID_ARRAY`            | Array of IDs                        | Multiple IDs                 |
| `UUID_ARRAY`          | Array of UUIDs                      | Multiple UUIDs               |
| `STRING_256_ARRAY`    | Array of max-256 strings            | Tags, lists                  |
| `EMAIL_ARRAY`         | Array of emails                     | Multiple emails              |
| `USERID_ARRAY`        | Array of userIds                    | Multiple users               |
| `OBJECT_NAME_ARRAY`   | Array of object names               | Multiple names               |

### Importing Regex Patterns for Pydantic Fields

```python
from common.validators import (
    id_pattern,              # r'^[-_a-zA-Z0-9]{3,63}$'
    filename_pattern,        # For asset IDs and file names
    object_name_pattern,     # r'^[a-zA-Z0-9\-._\s]{1,256}$'
    relative_file_path_pattern,  # r'^\/.*$'
)
```

---

## DynamoDB Patterns

### Table Initialization

```python
# Module-level: resource API for high-level operations
dynamodb = boto3.resource('dynamodb', config=retry_config)
your_table = dynamodb.Table(os.environ["YOUR_STORAGE_TABLE_NAME"])

# Module-level: client API for low-level operations (scans, pagination)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
```

### Common Operations

```python
# Query with key condition
response = your_table.query(
    KeyConditionExpression=Key('databaseId').eq(database_id) & Key('assetId').eq(asset_id),
    ScanIndexForward=False
)

# Get single item
response = your_table.get_item(Key={'itemId': item_id})
item = response.get('Item')

# Put item with condition
your_table.put_item(
    Item=item_dict,
    ConditionExpression='attribute_not_exists(databaseId) and attribute_not_exists(itemId)'
)

# Update item (use to_update_expr helper)
from common.dynamodb import to_update_expr
keys_map, values_map, expr = to_update_expr(update_dict)
your_table.update_item(
    Key={'itemId': item_id},
    UpdateExpression=expr,
    ExpressionAttributeNames=keys_map,
    ExpressionAttributeValues=values_map
)
```

### Pagination Pattern

```python
import base64
import json
from common.dynamodb import validate_pagination_info

def get_paginated_items(event, query_params):
    """Standard pagination with Base64-encoded NextToken"""
    # Parse pagination params
    max_items = int(query_params.get('maxItems', '100'))
    next_token = query_params.get('NextToken')

    scan_kwargs = {'Limit': max_items}

    # Decode continuation token
    if next_token:
        decoded = json.loads(base64.b64decode(next_token).decode('utf-8'))
        scan_kwargs['ExclusiveStartKey'] = decoded

    response = your_table.scan(**scan_kwargs)
    items = response.get('Items', [])

    # Encode next page token
    result = {'Items': items}
    if 'LastEvaluatedKey' in response:
        result['NextToken'] = base64.b64encode(
            json.dumps(response['LastEvaluatedKey']).encode('utf-8')
        ).decode('utf-8')

    return success(body=result)
```

### Archived Assets Pattern

Archived assets use a `databaseId + "#deleted"` partition key suffix:

```python
# Archive: Update partition key to mark as deleted
archived_key = f"{database_id}#deleted"

# Query archived assets
response = your_table.query(
    KeyConditionExpression=Key('databaseId').eq(f"{database_id}#deleted")
)
```

### TypeDeserializer for Low-Level Responses

```python
from boto3.dynamodb.types import TypeDeserializer

deserializer = TypeDeserializer()

# Convert low-level DynamoDB response to Python dict
def deserialize_item(item):
    return {k: deserializer.deserialize(v) for k, v in item.items()}
```

---

## Logging

### safeLogger

```python
from customLogging.logger import safeLogger

logger = safeLogger(service_name="YourServiceName")

# Standard usage
logger.info("Processing request")
logger.error(f"Failed to process: {error_message}")
logger.exception(f"Unexpected error: {e}")  # Includes stack trace
logger.warning(f"Potential issue: {details}")
logger.debug(f"Debug info: {data}")
```

### Auto-Redacted Fields

The `safeLogger` automatically redacts these keys at all nesting levels:

-   `authorization`
-   `idJwtToken`
-   `Credentials`
-   `AccessKeyId`
-   `SecretAccessKey`
-   `SessionToken`

### Audit Logging

Reference: `backend/customLogging/auditLogging.py`

```python
from customLogging.auditLogging import (
    log_authentication,
    log_authorization,
    log_authorization_api,
    log_file_upload,
    log_file_download,
    log_errors,
    # ... other event types
)
```

-   **9 CloudWatch log groups** for different event types
-   **Silent failure pattern**: If audit logging fails, the error is logged locally but Lambda execution continues
-   Log group names come from `AUDIT_LOG_*` environment variables
-   All audit functions extract user context from the event via `request_to_claims(event)`

---

## Response Functions (models/common.py)

```python
from models.common import (
    APIGatewayProxyResponseV2,
    success,               # 200 -- success(body={'items': items})
    validation_error,      # 400 -- validation_error(body={'message': 'Bad input'}, event=event)
    general_error,         # 400 -- general_error(body={'message': 'Business error'}, event=event)
    authorization_error,   # 403 -- authorization_error()
    internal_error,        # 500 -- internal_error(event=event)
    VAMSGeneralErrorResponse  # Exception class for business logic errors
)
```

### VAMSGeneralErrorResponse

```python
# Raise in business logic functions:
raise VAMSGeneralErrorResponse("Error getting bucket details.")

# Caught in lambda_handler try/except:
except VAMSGeneralErrorResponse as v:
    return general_error(body={'message': str(v)}, event=event)
```

### Response Format

All responses follow `APIGatewayProxyResponseV2`:

```python
{
    "isBase64Encoded": False,
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache, no-store"
    },
    "body": "{...}"  # JSON string
}
```

---

## Environment Variables

### Loading Pattern

```python
# At module level, inside try/except:
try:
    # Required -- raises KeyError if missing
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]

    # Optional -- returns None if missing
    asset_upload_table_name = os.environ.get("ASSET_UPLOAD_TABLE_NAME")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Conditional table init for optional vars
asset_upload_table = dynamodb.Table(asset_upload_table_name) if asset_upload_table_name else None
```

### Common Environment Variables

| Variable                            | Required | Description                        |
| ----------------------------------- | -------- | ---------------------------------- |
| `ASSET_STORAGE_TABLE_NAME`          | Yes      | Assets DynamoDB table              |
| `DATABASE_STORAGE_TABLE_NAME`       | Yes      | Databases DynamoDB table           |
| `S3_ASSET_*_BUCKET`                 | Yes      | S3 buckets for asset storage       |
| `S3_ASSET_AUXILIARY_BUCKET`         | Yes      | S3 bucket for auxiliary/temp files |
| `ASSET_VERSIONS_STORAGE_TABLE_NAME` | Yes      | Asset versions DynamoDB table      |
| `*_STORAGE_TABLE_NAME`              | Varies   | Per-domain DynamoDB tables         |
| `AUDIT_LOG_*`                       | Yes      | CloudWatch log group names         |
| `COGNITO_AUTH_ENABLED`              | Yes      | Enable/disable Cognito auth        |
| `AWS_REGION`                        | Auto     | AWS region (set by Lambda runtime) |
| `SUBSCRIPTIONS_STORAGE_TABLE_NAME`  | Yes      | Subscriptions table                |
| `SEND_EMAIL_FUNCTION_NAME`          | Yes      | Email notification Lambda name     |

---

## File Security (common/constants.py + common/s3.py)

### Blocked File Extensions

```python
UNALLOWED_FILE_EXTENSION_LIST = [
    ".jar", ".java", ".com", ".php", ".reg", ".pif", ".bak",
    ".dll", ".exe", ".nat", ".cmd", ".lnk", ".docm", ".vbs", ".bat"
]
```

### Blocked MIME Types

```python
UNALLOWED_MIME_LIST = [
    "application/java-archive", "application/x-msdownload",
    "application/x-sh", "application/javascript",
    "application/x-powershell", "application/vbscript",
    # ... and more
]
```

Always validate files against both lists before accepting uploads.

---

## Testing

### Running Tests

```bash
# From backend/ directory
cd backend
pip install -r requirements-dev.txt
pytest                            # Run all tests
pytest -m unit                    # Unit tests only
pytest -m integration             # Integration tests only
pytest -m "not slow"              # Skip slow tests
pytest tests/test_specific.py     # Single file
pytest -v --strict-markers        # Verbose with strict markers
```

### Test Configuration (pytest.ini)

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
xfail_strict = true
addopts = -v --strict-markers
markers =
    unit: marks tests as unit tests
    integration: marks tests as integration tests
    slow: marks tests as slow (skipped by default)
    aws: marks tests that interact with AWS services
```

### Root conftest.py

The root `conftest.py` performs critical setup:

1. **sys.path manipulation** -- adds `backend/`, `backend/backend/`, `tests/mocks/` to Python path
2. **Environment variables** -- sets test values for all required env vars
3. **Mock imports** -- replaces real modules in `sys.modules` with mocks from `tests/mocks/`
4. **autouse fixture** `setup_mock_imports()` -- runs before every test to set up mock module hierarchy

### Import Pattern in Tests

```python
# Tests import from backend.backend path
from backend.backend.handlers.assets.assetService import lambda_handler

# Or for models
from backend.backend.models.assetsV3 import CreateAssetRequestModel
```

### Writing New Tests

```python
import pytest
from unittest.mock import MagicMock, patch
import json

@pytest.mark.unit
class TestYourHandler:
    """Tests for your handler"""

    def test_get_item_success(self):
        """Test successful item retrieval"""
        event = {
            'requestContext': {
                'http': {
                    'method': 'GET',
                    'path': '/items/test-item-id'
                }
            },
            'queryStringParameters': {},
            'headers': {
                'authorization': 'Bearer test-token'
            }
        }
        context = MagicMock()

        # Mock DynamoDB, auth, etc. as needed
        with patch('backend.backend.handlers.your.handler.your_table') as mock_table:
            mock_table.get_item.return_value = {
                'Item': {'itemId': 'test-item-id', 'name': 'Test'}
            }
            response = lambda_handler(event, context)

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['itemId'] == 'test-item-id'

    def test_get_item_not_found(self):
        """Test item not found returns 400"""
        # ... similar pattern
```

### Per-Handler conftest.py

Create handler-specific `conftest.py` files for environment variables unique to that handler:

```python
# tests/your_domain/conftest.py
import os

os.environ['YOUR_SPECIFIC_TABLE'] = 'test-table'
os.environ['YOUR_SPECIFIC_BUCKET'] = 'test-bucket'
```

---

## Key Dependencies

| Package               | Version    | Purpose                                      |
| --------------------- | ---------- | -------------------------------------------- |
| aws-lambda-powertools | 2.36.0     | Logger, Parser, BaseModel, typing            |
| boto3                 | 1.34.84    | AWS SDK                                      |
| botocore              | 1.34.162   | Low-level AWS SDK                            |
| casbin                | 1.33.0     | ABAC/RBAC policy engine                      |
| pydantic              | 1.10.7     | Data validation (v1 ONLY)                    |
| opensearch-py         | 2.5.0      | OpenSearch client                            |
| simpleeval            | 1.0.3      | Safe expression evaluation (Casbin matchers) |
| locked-dict           | 2023.10.22 | Thread-safe dict for Casbin cache            |
| moto                  | 5.1.0      | AWS service mocking (dev only)               |
| pytest                | 8.3.4      | Test framework (dev only)                    |
| mypy                  | 1.0.0      | Type checking (dev only)                     |
| flake8                | 6.0.0      | Linting (dev only)                           |

---

## Anti-Patterns -- What NOT to Do

### 1. Raw dict responses

```python
# WRONG
return {
    'statusCode': 200,
    'body': json.dumps({'message': 'ok'})
}

# CORRECT
return success(body={'message': 'ok'})
```

### 2. Missing API-level auth check

```python
# WRONG -- skipping enforceAPI
def lambda_handler(event, context):
    return handle_get(event)

# CORRECT -- always check API auth first
def lambda_handler(event, context):
    claims_and_roles = request_to_claims(event)
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforceAPI(event):
        return authorization_error()
    return handle_get(event)
```

### 3. Missing object\_\_type annotation

```python
# WRONG -- enforce() without object type
casbin_enforcer.enforce(event, item)

# CORRECT -- annotate before enforce
item['object__type'] = 'asset'
casbin_enforcer.enforce(event, item)
```

### 4. Inline regex validation

```python
# WRONG -- writing regex inline
import re
if not re.match(r'^[-_a-zA-Z0-9]{3,63}$', value):
    return validation_error(...)

# CORRECT -- use the validate() dispatcher
(valid, message) = validate({
    'field': {'value': value, 'validator': 'ID'}
})
```

### 5. print() for logging

```python
# WRONG
print(f"Processing item: {item_id}")

# CORRECT
logger.info(f"Processing item: {item_id}")
```

### 6. Creating boto3 clients inside functions

```python
# WRONG -- creates new client on every invocation
def my_function():
    client = boto3.client('dynamodb')

# CORRECT -- module-level with retry config
dynamodb_client = boto3.client('dynamodb', config=retry_config)
```

### 7. Pydantic v2 imports or syntax

```python
# WRONG
from pydantic import BaseModel, ConfigDict, field_validator
class MyModel(BaseModel):
    model_config = ConfigDict(extra='ignore')

# CORRECT
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
class MyModel(BaseModel, extra='ignore'):
    pass
```

### 8. Environment variables loaded inside handler functions

```python
# WRONG
def lambda_handler(event, context):
    table_name = os.environ["MY_TABLE"]  # Cold start penalty on every invocation

# CORRECT -- at module level
try:
    table_name = os.environ["MY_TABLE"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e
```

### 9. Swallowing exceptions silently

```python
# WRONG
try:
    do_something()
except Exception:
    pass

# CORRECT -- log the error, then decide
try:
    do_something()
except Exception as e:
    logger.exception(f"Error in do_something: {e}")
    raise VAMSGeneralErrorResponse("Operation failed")
```

### 10. Returning raw error strings from handlers

```python
# WRONG
return {'statusCode': 400, 'body': 'bad request'}

# CORRECT
return validation_error(body={'message': 'Specific error description'}, event=event)
```

---

## Copy-Paste Templates

### New Handler Template

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
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
    table_name = os.environ["CHANGE_ME_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
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

### New Pydantic Model Template

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

### New Test Template

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
        """Helper to build API Gateway v2 event"""
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

---

## Development Checklist

When creating or modifying a handler:

-   [ ] Imports follow the standard order (stdlib, boto3, powertools, common, handlers, models)
-   [ ] AWS clients created at module level with `retry_config`
-   [ ] `safeLogger` used with descriptive `service_name`
-   [ ] Environment variables loaded in module-level `try/except`
-   [ ] DynamoDB tables initialized at module level
-   [ ] `lambda_handler` extracts claims via `request_to_claims(event)`
-   [ ] API-level auth checked via `casbin_enforcer.enforceAPI(event)`
-   [ ] Routes dispatch to dedicated method handlers
-   [ ] Request bodies parsed with `parse(body, model=ModelClass)`
-   [ ] Input params validated with `validate()` dispatcher
-   [ ] Object-level auth checked via `casbin_enforcer.enforce(event, item)` with `object__type` set
-   [ ] Business logic errors raise `VAMSGeneralErrorResponse`
-   [ ] Error handling: ValidationError -> 400, VAMSGeneralErrorResponse -> 400, Exception -> 500
-   [ ] All response functions use `event=event` for audit logging where applicable
-   [ ] No `print()` statements -- only `logger.*`
-   [ ] No Pydantic v2 syntax
-   [ ] Models use `extra='ignore'`
-   [ ] Tests exist with proper mocking via `conftest.py`
