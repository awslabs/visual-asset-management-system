# Backend Development

This guide covers development patterns for the VAMS Python Lambda backend, including handler structure, Pydantic model definitions, two-tier authorization, Amazon DynamoDB access patterns, and error handling.

## Technology Stack

| Component     | Details                                               |
| ------------- | ----------------------------------------------------- |
| Runtime       | Python 3.12 (AWS Lambda)                              |
| Validation    | Pydantic 1.10.7 (v1 only) via `aws-lambda-powertools` |
| Authorization | Casbin ABAC/RBAC with Amazon DynamoDB policy storage  |
| AWS SDK       | boto3 1.34.84                                         |
| Search        | OpenSearch (opensearch-py 2.5.0)                      |
| Logging       | AWS Lambda Powertools Logger with custom redaction    |
| Testing       | pytest 8.3.4, moto 5.1.0                              |

:::warning[Pydantic v1 Only]
VAMS uses Pydantic **1.10.7**. Never use Pydantic v2 syntax (`model_validator`, `model_dump`, `ConfigDict`). Import `BaseModel` from `aws_lambda_powertools.utilities.parser`, not from `pydantic` directly. Violations cause import failures in Lambda.
:::

## Project Structure

```
backend/
  backend/
    common/                          # Shared utilities
      constants.py                   # ABAC policy, allowed values, file blocklists
      dynamodb.py                    # DynamoDB helpers (to_update_expr, get_asset_object_from_id)
      validators.py                  # Input validation regex patterns and validate() dispatcher
      s3.py                          # S3 file validation (extension + MIME type checks)
    customLogging/
      auditLogging.py                # CloudWatch audit logging (9 event types)
      logger.py                      # safeLogger wrapper with sensitive data redaction
    handlers/                        # Lambda handlers (one folder per domain)
      assets/assetService.py         # Gold standard handler
      auth/                          # Auth handlers (authorizer, constraints, cognito)
      authz/__init__.py              # Casbin ABAC/RBAC enforcer (CasbinEnforcer)
      databases/                     # Database CRUD
      metadata/                      # Metadata CRUD
      pipelines/                     # Pipeline management
      workflows/                     # Step Functions workflow management
      ...                            # Additional handler domains
    models/                          # Pydantic v1 models
      assetsV3.py                    # Gold standard model file
      common.py                      # Response helpers, error functions
      pipelines.py                   # Pipeline models
      workflows.py                   # Workflow models
      ...                            # Domain-specific models
  tests/                             # Test suite
    mocks/                           # Mock modules replacing real imports
```

## Gold Standard Handler Pattern

Every new Lambda handler must follow the structure demonstrated in `backend/handlers/assets/assetService.py`. The pattern consists of five layers.

### 1. Module-Level Setup

Set up imports, AWS clients, logger, and environment variables at the module level. This code executes once during Lambda cold start.

```python
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
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.yourDomain import YourRequestModel

# Configure AWS clients with retry configuration
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="YourServiceName")

claims_and_roles = {}

try:
    your_table_name = os.environ["YOUR_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

your_table = dynamodb.Table(your_table_name)
```

:::note[Environment Variable Loading]
All environment variables must be loaded at module level inside a `try/except` block. Use `os.environ["KEY"]` for required variables and `os.environ.get("KEY")` for optional ones. Never load environment variables inside handler functions.
:::

### 2. Lambda Handler Entry Point

The entry point extracts claims, performs API-level authorization, and routes to method handlers.

```python
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
```

### 3. Method Handlers

Route HTTP methods to specific business logic functions based on the request path.

```python
def handle_get(event):
    path = event['requestContext']['http']['path']
    query_params = event.get('queryStringParameters', {}) or {}

    if '/items/' in path:
        item_id = path.split('/items/')[-1]
        return get_single_item(event, item_id)
    else:
        return get_all_items(event, query_params)
```

### 4. Business Logic Functions

Each business logic function follows a four-step pattern: validate, query, authorize, respond.

```python
def get_single_item(event, item_id):
    # Step 1: Validate input parameters
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

| Exception                    | Response Function       | HTTP Status |
| ---------------------------- | ----------------------- | ----------- |
| `ValidationError` (Pydantic) | `validation_error()`    | 400         |
| `VAMSGeneralErrorResponse`   | `general_error()`       | 400         |
| Authorization failure        | `authorization_error()` | 403         |
| `Exception` (catch-all)      | `internal_error()`      | 500         |

All response functions accept an optional `event=` parameter for audit logging. Always pass the event when available.

## Two-Tier Authorization

VAMS enforces authorization at two levels. Both levels must allow access for a request to succeed.

### Tier 1: API-Level Authorization

Controls which API routes a role can access. Performed in the `lambda_handler` using `enforceAPI()`.

```python
casbin_enforcer = CasbinEnforcer(claims_and_roles)
if not casbin_enforcer.enforceAPI(event):
    return authorization_error()
```

### Tier 2: Object-Level Authorization

Controls which specific data entities a role can access. Performed in business logic functions using `enforce()`.

```python
# MUST annotate the object type before calling enforce()
item['object__type'] = 'asset'
casbin_enforcer = CasbinEnforcer(claims_and_roles)
if not casbin_enforcer.enforce(event, item):
    return authorization_error()
```

:::warning[Object Type Annotation]
You must add `object__type` to the item dictionary before calling `enforce()`. Valid object types include: `database`, `asset`, `api`, `web`, `tag`, `tagType`, `role`, `userRole`, `pipeline`, `workflow`, `metadataSchema`, `apiKey`.
:::

### Key Authorization Concepts

-   **CasbinEnforcer** uses a 60-second policy cache TTL per user
-   Policy is stored in Amazon DynamoDB (`ConstraintsStorageTable`)
-   Claims are extracted via `request_to_claims(event)` which returns user tokens, roles, and MFA status
-   Roles with `mfaRequired=True` are only active when `mfaEnabled=True` in claims

## Pydantic v1 Model Patterns

Reference file: `backend/models/assetsV3.py`

### Correct Model Definition

```python
from typing import Dict, List, Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import (
    BaseModel, root_validator, validator, ValidationError
)
from common.validators import validate, id_pattern, object_name_pattern

class CreateItemRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new item"""
    databaseId: str = Field(
        min_length=4, max_length=256,
        strip_whitespace=True, pattern=id_pattern
    )
    itemName: str = Field(
        min_length=1, max_length=256,
        strip_whitespace=True, pattern=object_name_pattern
    )
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    tags: Optional[list[str]] = []

    @root_validator
    def validate_fields(cls, values):
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

### Common Mistakes to Avoid

```python
# WRONG: Importing from pydantic directly
from pydantic import BaseModel

# WRONG: Using Pydantic v2 syntax
class MyModel(BaseModel):
    model_config = ConfigDict(extra='ignore')    # v2 syntax

# WRONG: Missing extra='ignore'
class MyModel(BaseModel):
    pass

# WRONG: Using model_validate or model_dump (v2)
item = MyModel.model_validate(data)
data = item.model_dump()

# CORRECT alternatives:
from aws_lambda_powertools.utilities.parser import parse
item = parse(body, model=MyModel)
data = item.dict()
```

### Parsing Request Bodies

```python
from aws_lambda_powertools.utilities.parser import parse

body = json.loads(event.get('body', '{}'))
request = parse(body, model=CreateItemRequestModel)
```

## Amazon DynamoDB Patterns

### Table Initialization

```python
# Module-level: resource API for high-level operations
dynamodb = boto3.resource('dynamodb', config=retry_config)
your_table = dynamodb.Table(os.environ["YOUR_STORAGE_TABLE_NAME"])

# Module-level: client API for low-level operations
dynamodb_client = boto3.client('dynamodb', config=retry_config)
```

### Common Operations

```python
# Query with key condition
from boto3.dynamodb.conditions import Key

response = your_table.query(
    KeyConditionExpression=(
        Key('databaseId').eq(database_id) & Key('assetId').eq(asset_id)
    ),
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

# Update item using the to_update_expr helper
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

VAMS uses Base64-encoded `NextToken` pagination:

```python
import base64

def get_paginated_items(event, query_params):
    max_items = int(query_params.get('maxItems', '100'))
    next_token = query_params.get('NextToken')

    scan_kwargs = {'Limit': max_items}

    if next_token:
        decoded = json.loads(base64.b64decode(next_token).decode('utf-8'))
        scan_kwargs['ExclusiveStartKey'] = decoded

    response = your_table.scan(**scan_kwargs)
    items = response.get('Items', [])

    result = {'Items': items}
    if 'LastEvaluatedKey' in response:
        result['NextToken'] = base64.b64encode(
            json.dumps(response['LastEvaluatedKey']).encode('utf-8')
        ).decode('utf-8')

    return success(body=result)
```

## Input Validation

Use the `validate()` dispatcher from `common.validators` for all input validation, both in `@root_validator` methods and in handler code.

```python
from common.validators import validate

(valid, message) = validate({
    'databaseId': {'value': database_id, 'validator': 'ID'},
    'assetId': {'value': asset_id, 'validator': 'ASSET_ID'},
    'tags': {
        'value': tag_list,
        'validator': 'STRING_256_ARRAY',
        'optional': True
    }
})
if not valid:
    return validation_error(body={'message': message}, event=event)
```

### Available Validators

| Validator          | Pattern                      | Use For                |
| ------------------ | ---------------------------- | ---------------------- |
| `ID`               | `^[-_a-zA-Z0-9]{3,63}$`      | databaseId, pipelineId |
| `ASSET_ID`         | filename pattern, max 256    | assetId                |
| `UUID`             | Standard UUID format         | Unique identifiers     |
| `OBJECT_NAME`      | `^[a-zA-Z0-9\-._\s]{1,256}$` | assetName, dbName      |
| `EMAIL`            | Email regex                  | Email addresses        |
| `USERID`           | `^[\w\-\.\+\@]{3,256}$`      | User identifiers       |
| `FILE_NAME`        | No special characters        | File names             |
| `STRING_256`       | Max 256 chars                | Medium strings         |
| `ID_ARRAY`         | Array of IDs                 | Multiple IDs           |
| `STRING_256_ARRAY` | Array of max-256 strings     | Tags, lists            |

### Regex Patterns for Pydantic Fields

```python
from common.validators import (
    id_pattern,                  # r'^[-_a-zA-Z0-9]{3,63}$'
    filename_pattern,            # For asset IDs and file names
    object_name_pattern,         # r'^[a-zA-Z0-9\-._\s]{1,256}$'
    relative_file_path_pattern,  # r'^\/.*$'
)
```

## Logging

### safeLogger

Use `safeLogger` from `customLogging.logger` for all logging. Never use `print()` or `logging.getLogger()`.

```python
from customLogging.logger import safeLogger

logger = safeLogger(service_name="YourServiceName")

logger.info("Processing request")
logger.error(f"Failed to process: {error_message}")
logger.exception(f"Unexpected error: {e}")   # Includes stack trace
logger.warning(f"Potential issue: {details}")
```

The logger automatically redacts sensitive fields at all nesting levels:

-   `authorization`
-   `idJwtToken`
-   `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`

### Audit Logging

Nine dedicated Amazon CloudWatch log groups capture security-sensitive operations. See the [Audit Logging](audit-logging.md) guide for details.

## Response Functions

All handlers must use the standardized response functions from `models/common.py`:

```python
from models.common import (
    success,              # 200
    validation_error,     # 400 -- validation failures
    general_error,        # 400 -- business logic errors
    authorization_error,  # 403 -- access denied
    internal_error,       # 500 -- unexpected errors
    VAMSGeneralErrorResponse  # Exception class for business logic
)

# Raise in business logic:
raise VAMSGeneralErrorResponse("Error getting bucket details.")

# Return from handlers:
return success(body={'items': items})
return validation_error(body={'message': 'Invalid ID format'}, event=event)
```

## Adding a New API Endpoint

Adding a new endpoint requires coordinated changes across multiple files.

### Checklist

| Step | File                                                         | Action                                     |
| ---- | ------------------------------------------------------------ | ------------------------------------------ |
| 1    | `backend/backend/handlers/{domain}/{handler}.py`             | Implement Lambda handler                   |
| 2    | `backend/backend/models/{domain}.py`                         | Define Pydantic v1 models                  |
| 3    | `infra/lib/lambdaBuilder/{domain}Functions.ts`               | Build Lambda with env vars and permissions |
| 4    | `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` | Attach Lambda to API Gateway route         |
| 5    | `web/src/services/APIService.ts`                             | Add API call function                      |

:::warning[All Steps Required]
A handler without an API Gateway route is dead code. A route without a handler returns HTTP 500. Always complete all steps when adding a new endpoint.
:::

### Handler Template

```python
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
```

## Custom Authentication Hooks

VAMS provides two customization points for organizations to extend authentication behavior without modifying core code. Both files are located in `backend/backend/customConfigCommon/`.

### Login Profile Customization

The file `customAuthLoginProfile.py` controls how user profile information is updated when a user authenticates. Override the `customAuthProfileLoginWriteOverride()` function to customize profile data.

**Default behavior:** Extracts the `email` claim from the JWT token and writes it to the user's VAMS profile. The login profile is updated via an authenticated POST call to `/api/auth/loginProfile/\{userId\}` from the web UI on each login.

**Common customizations:**

-   Fetching additional user attributes from an external identity provider API
-   Populating the `name` field from directory services
-   Enriching the profile with organizational metadata

```python
# backend/backend/customConfigCommon/customAuthLoginProfile.py
def customAuthProfileLoginWriteOverride(userProfile, lambdaRequestEvent):
    # Default: override email from JWT claims
    claims = ...  # extracted from request context
    if 'email' in claims:
        userProfile["email"] = claims['email']

    # Add custom logic here (e.g., fetch from external IDP userinfo endpoint)
    return userProfile
```

:::note[Email Fallback]
The email field is used by systems that send notifications to the user. If the email is blank or not in a valid email format, VAMS falls back to using the `userId` as the notification address.
:::

### MFA and Claims Check Customization

The file `customAuthClaimsCheck.py` controls how authentication claims are verified, including Multi-Factor Authentication (MFA) status.

**Default behavior for Amazon Cognito:** Calls the Cognito `get_user` API with the access token to check if MFA is enabled for the authenticated user. Results are cached per user based on `auth_time` to reduce external API calls.

**Default behavior for external OAuth IDP:** Sets `mfaEnabled` to `false`. Organizations must implement their own MFA verification logic for external identity providers.

```python
# backend/backend/customConfigCommon/customAuthClaimsCheck.py
def customMFATokenScopeCheckOverride(user, lambdaRequest):
    # For Cognito: checks UserMFASettingList via get_user API
    # For external IDP: returns False by default
    # Override with your organization's MFA verification logic
    return mfaLoginEnabled

def customAuthClaimsCheckOverride(claims_and_roles, lambdaRequest):
    # Calls customMFATokenScopeCheckOverride and sets mfaEnabled flag
    # Add additional claims validation logic here
    return claims_and_roles
```

:::warning[Performance Consideration]
The `customAuthClaimsCheck` functions are called frequently during VAMS API authorization checks. Use caching (the default implementation caches by `auth_time`) and minimize external API calls to avoid performance impacts.
:::

## Anti-Patterns

Avoid these common mistakes in backend development.

| Anti-Pattern                                   | Correct Approach                                               |
| ---------------------------------------------- | -------------------------------------------------------------- |
| `from pydantic import BaseModel`               | `from aws_lambda_powertools.utilities.parser import BaseModel` |
| Raw dict responses `{'statusCode': 200, ...}`  | Use `success()`, `validation_error()`, etc.                    |
| `print()` for logging                          | Use `logger.info()`, `logger.error()`                          |
| Creating boto3 clients inside functions        | Create at module level with `retry_config`                     |
| Skipping `enforceAPI()` in handler             | Always check both auth tiers                                   |
| Missing `object__type` before `enforce()`      | Annotate item before object-level auth                         |
| Inline regex validation                        | Use `validate()` dispatcher                                    |
| Swallowing exceptions with bare `except: pass` | Log errors and raise `VAMSGeneralErrorResponse`                |

## Next Steps

-   [CDK Infrastructure](cdk.md) -- Lambda builder patterns and API route wiring
-   [Frontend Development](frontend.md) -- Consuming backend APIs from the React frontend
-   [Audit Logging](audit-logging.md) -- Understanding the audit trail system
