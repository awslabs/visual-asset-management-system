# CLAUDE.md -- VAMS Python Lambda Backend

> Auto-loaded when Claude Code works within `backend/`. Authoritative guide for Lambda handler development, Pydantic model creation, authorization enforcement, DynamoDB access, and validators. Testing details: `backend/tests/CLAUDE.md`. Copy-paste skeletons for a new handler / model / test file: `backend/HANDLER_TEMPLATES.md`.

---

## Quick Reference

-   **Runtime**: Python 3.12 (AWS Lambda); Python 3.13+ (local dev/tests)
-   **Framework**: AWS Lambda + API Gateway REST API (v1)
-   **Validation**: Pydantic **1.10.13** (NOT v2) via aws-lambda-powertools
-   **Auth**: Casbin ABAC/RBAC with DynamoDB policy storage
-   **ORM**: boto3 DynamoDB resource + client APIs
-   **Search**: OpenSearch (opensearch-py 2.5.0)
-   **Logging**: aws-lambda-powertools Logger with custom redaction
-   **Tests**: pytest 9.0.3 + moto 5.1.0 (see `backend/tests/CLAUDE.md`)
-   **Gold Standard**: `backend/handlers/assets/assetService.py` (handler), `backend/models/assetsV3.py` (model)

---

## Directory Structure

> Update this tree when adding new handler domains, model files, or test directories (root `CLAUDE.md` Rule 11).

```
backend/
├── conftest.py, pytest.ini              # See backend/tests/CLAUDE.md
├── pyproject.toml, poetry.lock          # Poetry-managed deps
├── requirements.txt, requirements-dev.txt  # Exported from poetry.lock
├── backend/
│   ├── common/                                     # Shared utilities (no AWS bootstrap)
│   │   ├── auth/apiEvent.py                        #   normalize_event: REST→HTTP-API-v2 shape
│   │   ├── auth/authorizerCore.py                  #   JWT/API-key/IP validation
│   │   ├── auth/clientIp.py                        #   True client-IP resolution
│   │   ├── apiRoutes.py                            # MASTER API route registry: ApiRoute constants,
│   │   │                                           #   category group arrays, ALL_API_ROUTES.
│   │   │                                           #   Handlers dispatch via ApiRoute.matches().
│   │   ├── constants.py                            # ABAC policy, allowed values, file blocklists
│   │   ├── dynamodb.py                             # to_update_expr, get_asset_object_from_id
│   │   ├── resourceNames.py                        # SSM resource-name resolver + ResourceKeys
│   │   ├── s3.py                                   # S3 file validation + paged list helpers
│   │   ├── s3MetadataKeys.py, s3PathPatterns.py    # Canonical S3 keys, .previewFile. patterns (mirror web/src/common/constants/fileFormats.ts)
│   │   ├── dynamoDbMetadataKeys.py                 # Reserved DynamoDB metadata keys
│   │   ├── assetHistory.py, syncTracking.py        # Best-effort history / outbound-sync writers
│   │   ├── stepfunctions_builder.py                # ASL builder (Lambda/SQS/EventBridge tasks)
│   │   └── validators.py                           # validate() dispatcher + regex patterns
│   ├── customLogging/
│   │   ├── auditLogging.py                         # CloudWatch audit (9 event types, silent-fail)
│   │   └── logger.py                               # safeLogger with sensitive-data redaction
│   ├── handlers/                                   # Lambda handlers, one folder per domain
│   │   ├── assets/                                 # assetService.py (GOLD STANDARD),
│   │   │                                           #   assetVersions, assetHistory, etc.
│   │   ├── auth/                                   # apiGatewayAuthorizerRest, apiKeyService,
│   │   │                                           #   constraints, cognito, preTokenGen;
│   │   │                                           #   __init__: request_to_claims() → claims
│   │   ├── authz/__init__.py                       # CasbinEnforcer proxy (ABAC/RBAC)
│   │   ├── pipelines/, workflows/                  # Pipeline CRUD; Step Functions workflow mgmt
│   │   ├── addon/garnetFramework/                  # Garnet NGSI-LD indexer Lambdas
│   │   ├── addon/physna/                           # Physna Sync Lambdas (physnaCommon.py shared)
│   │   └── assetLinks, comments, config, databases, indexing, metadata,
│   │       metadataschema, roles, search, sendEmail, subscription, tags,
│   │       tagTypes, userRoles                     # Domain handlers (folder per domain)
│   └── models/                                     # Pydantic v1 models, one file per domain
│       ├── assetsV3.py                             # GOLD STANDARD model file
│       ├── common.py                               # Response helpers, APIGatewayProxyResponseV2
│       └── apiKeys, pipelines, workflows, assetHistory, [domain].py
├── lambdaLayers/                                   # Lambda layer definitions
└── tests/                                          # See backend/tests/CLAUDE.md
```

---

## Critical Rules

1.  **ALWAYS use Pydantic v1 syntax.** This project uses `pydantic==1.10.13`. Never call
    v2 APIs (`model_validate`, `model_dump`, `ConfigDict`). Use `@root_validator`,
    `@validator`, `Field(...)`, `extra='ignore'`.

2.  **ALWAYS import BaseModel from aws_lambda_powertools**, not from pydantic directly:
    `from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError`.

3.  **ALWAYS use the `validate()` dispatcher** from `common.validators` for complex validation
    in `@root_validator` methods. Never write raw regex validation inline.

4.  **ALWAYS enforce two-level authorization**: `enforceAPI()` for route access, then
    `enforce()` for object-level access inside method handlers. **Fail closed on an empty
    token list** — empty `claims_and_roles["tokens"]` means no authenticated identity, so
    authorization cannot be evaluated and must deny.

    -   **Tier 1**: pre-set `method_allowed_on_api = False`, then only flip it inside
        `if len(tokens) > 0: ...enforceAPI()...`; deny if the flag is still `False`. The
        empty-token case naturally denies.
    -   **Tier 2, single resource**: guard with an explicit
        `if len(claims_and_roles["tokens"]) == 0: return authorization_error()` **before** the
        `enforce()` call. **Never** wrap a single-resource `enforce()` in `if len(tokens) > 0:`
        without an `else` that denies — that silently skips authorization and falls through to
        the response/mutation when tokens are empty.
    -   **Tier 2, list filtering**: the exception. Handlers that _append_ an item only when
        `enforce()` passes are fail-closed by construction (empty tokens → empty result).

5.  **ALWAYS use safeLogger** from `customLogging.logger`. Never `print()` or raw `logging.getLogger()`.

6.  **ALWAYS wrap AWS clients with retry config** at module level:
    `retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})`.

7.  **ALWAYS raise `VAMSGeneralErrorResponse`** for business logic errors. Never return raw dicts with status codes.

8.  **ALWAYS use `extra='ignore'`** on every Pydantic model class to silently drop unexpected fields.

9.  **NEVER log sensitive data.** `safeLogger` auto-redacts `authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`. Do not circumvent this.

10. **ALWAYS resolve resource names at module level** via `get_table_name()`, `get_bucket_name()`, or `get_log_group_name()` from `common.resourceNames` inside a `try/except`. Never read `os.environ["TABLE_NAME"]` for resource names in non-pipeline handlers — SSM resolution provides centralized name management with env-var overrides for testing.

11. **NEVER echo request input into error messages returned to the client.** Keep response
    messages generic — no user-supplied values (IDs, names, paths, etc.) and no internal
    details (other databases' IDs, ARNs, stack traces). Log specifics via `logger` for
    debugging; the caller gets a generic message. Example: `logger.info(f"pipelineId {pipeline_id} conflicts with database {other_db}")` before
    `return validation_error(body={'message': "Pipeline ID is already in use by another database. Choose a different ID."}, event=event)`.

12. **ALWAYS dispatch API requests via the master route constants.** Handlers routing on
    `event['requestContext']['http']['path']` must match against the `ApiRoute` constants
    from `common/apiRoutes.py` (e.g. `API_LIST_FILES.matches(path)`), never against
    hard-coded path fragments (`path.endswith('/listFiles')`). When adding or renaming an
    API path, define the `ApiRoute` constant in `common/apiRoutes.py` AND add it to the
    matching category group array (`ASSET_FILE_ROUTES`, `AUTH_ROUTES`, …) so it is included
    in `ALL_API_ROUTES` and served by the `GET /auth/routes/api` listing. Keep route
    templates in sync with the routes attached in the CDK api builder stacks.

13. **ALWAYS use a leading `/` for normalized asset-relative file paths.** Normalized file
    paths (DynamoDB composite keys like `databaseId:assetId:filePath`, the `filePath`
    attribute, file-path provenance values) are asset-relative and begin with a single `/`
    (e.g. `/folder/file.txt`). Normalize inputs before storing or comparing:
    `file_path = "/" + raw_path.lstrip("/")`.

14. **NEVER read only the first page of an S3 or DynamoDB listing when the full set is
    needed.** S3 `list_object_versions` / `list_objects_v2` and DynamoDB queries cap a
    single call (`MaxKeys`, `Limit`, one page). When completeness matters, page to
    exhaustion via the shared helpers `common.s3.list_all_object_versions()` /
    `list_all_objects()` (page-size constants `S3_VERSIONS_PAGE_SIZE` /
    `S3_OBJECTS_PAGE_SIZE`; both accept an optional `max_keys` / `max_objects` cap for
    best-effort sampling). A bare `list_object_versions(..., MaxKeys=N)` silently drops
    versions beyond `N` (wrong archive status, truncated history). Existence-only checks
    (`MaxKeys=1`) are the allowed exception.

            **To check whether a single key or specific `versionId` is archived, do NOT list
            versions** — use `common.s3.is_object_version_archived(bucket, key, version_id,

        client=...)`. It issues one `HeadObject`(delete-marker → 405, live → 200, missing →

    404), O(1) regardless of version count. Handler-local`is_file_archived` helpers must
    delegate to it, never re-implement a version scan.

15. **Paginate large GET responses; never return an unbounded in-memory set.** A response
    that can exceed the AWS Lambda synchronous response limit (6 MB) must page externally:
    accept `maxItems`/`pageSize`/`startingToken` and return `NextToken`, defaulting sizes
    to named constants (mirror the asset-listing and metadata-listing handlers). Do not
    use DynamoDB `paginator.build_full_result()` to accumulate every record for a
    user-facing GET. When response ordering or enrichment requires the full set first
    (e.g. metadata-schema injection/ordering), enrich the full set, then offset-slice to
    the page. Limits that bound response size or protect Lambda runtime (e.g.
    `MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST`, worker-pool caps) stay as named constants with
    a rationale comment — keep them.

16. **Normalize the event before reading `requestContext['http']`, `pathParameters`, or
    `queryStringParameters`.** The REST API (v1) proxy event differs from the HTTP API v2
    layout handlers are written against in two ways that `normalize_event(event)` (from
    `common.auth.apiEvent`) reconciles — it mutates the event in place, is idempotent, and
    is a no-op for internal `lambdaCrossCall` events:

    1.  **`requestContext.http` block.** Handlers read `event['requestContext']['http']['path']` / `['method']` / `['sourceIp']`, but the REST event exposes these as top-level `path` / `httpMethod` and `requestContext.identity.sourceIp`. `normalize_event` injects the v2-style block.
    2.  **Null `pathParameters` / `queryStringParameters`.** The REST event sends explicit JSON `null` when there are none, so `event.get('pathParameters', {})` returns `None` (the default applies only when the key is **absent**, not when it is present-but-`null`). A handler that then does `path_params['id']` crashes with `TypeError` → 500. `normalize_event` coerces present-but-`null` to `{}`.

    `request_to_claims(event)` calls `normalize_event(event)` internally, so a handler whose **first** event access is `request_to_claims(event)` — the Gold Standard pattern — is already covered. A handler that reads `requestContext['http']`, `pathParameters`, or `queryStringParameters` **before** calling `request_to_claims` MUST call `normalize_event(event)` as the first statement of `lambda_handler`. Skipping it makes the handler 500 on a real REST request — a failure invisible to CDK synth and to unit tests that hand-build a v2-shaped event. Cover the REST-shaped event, including `null` params, in tests.

    ```python
    # ✅ Gold Standard — claims first; normalize is implicit
    def lambda_handler(event, context):
        claims_and_roles = request_to_claims(event)      # normalizes internally
        path = event['requestContext']['http']['path']   # safe — already normalized

    # ✅ When http MUST be read before claims — call normalize_event first
    from common.auth.apiEvent import normalize_event
    def lambda_handler(event, context):
        normalize_event(event)
        path = event['requestContext']['http']['path']
        claims_and_roles = request_to_claims(event)
    ```

---

## Gold Standard Handler Pattern

Reference: `backend/handlers/assets/assetService.py`

Every new Lambda handler MUST follow this exact structure. For a fill-in-the-blanks skeleton, see `backend/HANDLER_TEMPLATES.md`.

### 1. Module-Level Setup

Instantiate AWS clients (with `retry_config`), the logger, and every DynamoDB table at
import time — never inside the request path. Resolve resource names in one `try/except`
block, wrapping optional resources in inner `try/except KeyError`. Handler-specific env
vars read directly from `os.environ`. Full boilerplate lives in `backend/HANDLER_TEMPLATES.md`; the module-load contract is:

```python
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="YourServiceName")
claims_and_roles = {}

try:
    your_table_name = get_table_name(ResourceKeys.YOUR_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

your_table = dynamodb.Table(your_table_name)
```

### 2. Lambda Handler Entry Point

`lambda_handler` extracts claims (which also normalizes the event, Rule 16), runs Tier-1 auth, then dispatches by HTTP method inside a `try/except` that maps error types to the response functions in the table below. The Tier-1 block below is load-bearing: the pre-set `method_allowed_on_api = False` ensures the empty-token case denies (Rule 4).

```python
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    try:
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()

        if method == 'GET':    return handle_get_request(event)
        elif method == 'PUT':  return handle_put_request(event)
        elif method == 'DELETE': return handle_delete_request(event)
        else: return validation_error(body={'message': "Method not allowed"}, event=event)

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

Route by path (via `ApiRoute.matches()`) and delegate to per-operation functions. Use `event.get('queryStringParameters', {}) or {}` to defensively coerce a REST `null` (Rule 16).

### 4. Business Logic Functions

Each per-operation function runs the same four steps: (1) validate params with the
`validate()` dispatcher; (2) query DynamoDB; (3) annotate `object__type`, fail-close on
empty tokens, then run Tier-2 `casbin_enforcer.enforce(event, item)`; (4)
`return success(body=...)`.

```python
def get_single_item(event, item_id):
    (valid, message) = validate({'itemId': {'value': item_id, 'validator': 'ID'}})
    if not valid:
        return validation_error(body={'message': message}, event=event)

    item = your_table.get_item(Key={'itemId': item_id}).get('Item')
    if not item:
        return general_error(body={'message': 'Item not found'}, event=event)

    item['object__type'] = 'yourObjectType'
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not CasbinEnforcer(claims_and_roles).enforce(event, item):
        return authorization_error()
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

Import `BaseModel` from `aws_lambda_powertools.utilities.parser`; declare `extra='ignore'` on the class; use `Field(...)` with `pattern=` (loaded from `common.validators`); attach a `@root_validator` for cross-field logic that calls the `validate()` dispatcher.

```python
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, ValidationError
from pydantic import Field
from common.validators import validate, id_pattern, object_name_pattern

class CreateItemRequestModel(BaseModel, extra='ignore'):
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    itemName:   str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    tags: Optional[list[str]] = []

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'tags': {'value': values.get('tags'), 'validator': 'STRING_256_ARRAY', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        return values
```

### v1 vs v2 API Mapping

| v2 (WRONG)                               | v1 (CORRECT)                                                         |
| ---------------------------------------- | -------------------------------------------------------------------- |
| `from pydantic import BaseModel`         | `from aws_lambda_powertools.utilities.parser import BaseModel`       |
| `model_config = ConfigDict(extra='...')` | `class MyModel(BaseModel, extra='ignore'):`                          |
| `MyModel.model_validate(data)`           | `parse(body, model=MyModel)` (from `aws_lambda_powertools...parser`) |
| `@field_validator('name')`               | `@validator('name')`                                                 |
| `item.model_dump()`                      | `item.dict()`                                                        |

Every model class must declare `extra='ignore'`.

### Field Validation Patterns

Common shapes:

-   String with regex: `Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)`
-   Optional with default: `Optional[list[str]] = []`, `Optional[str] = None`
-   Numeric constraints: `Field(None, ge=0)`, `Field(None, ge=0, le=10000)`
-   Nested models: `Optional[CurrentVersionModel] = None`

### Parsing Request Bodies

```python
from aws_lambda_powertools.utilities.parser import parse
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

# Tier 1: API-level authorization (in lambda_handler)
claims_and_roles = request_to_claims(event)
casbin_enforcer = CasbinEnforcer(claims_and_roles)
if not casbin_enforcer.enforceAPI(event):
    return authorization_error()

# Tier 2: Object-level authorization (in method handlers)
item['object__type'] = 'asset'  # MUST annotate object type before enforce()
if not casbin_enforcer.enforce(event, item):
    return authorization_error()
```

### Key Concepts

-   **CasbinEnforcer** is a proxy with a **60-second policy cache TTL** per user; policy is stored in DynamoDB (`ConstraintsStorageTable`).
-   `request_to_claims(event)` returns `{"tokens": ["userId", ...], "roles": [...], "mfaEnabled": bool}`.
-   **MFA-aware**: roles with `mfaRequired=True` are only active when `mfaEnabled=True` in claims.
-   **Object annotation**: set `item['object__type']` before every `enforce()` call.
-   Valid object types: `database`, `asset`, `api`, `web`, `tag`, `tagType`, `role`, `userRole`, `pipeline`, `workflow`, `metadataSchema`, `apiKey`.

### System User (`SYSTEM_USER`)

`SYSTEM_USER` is the **only** valid user ID for system-process actions — never `SYSTEM`, `system`, or any other variant. It is seeded into the user and user-roles tables during CDK deployment and assigned to the `admin` role, so actions attributed to it pass Casbin authorization. Use it for: Lambda cross-calls (`{'lambdaCrossCall': {'userName': 'SYSTEM_USER'}}` — also the default in `request_to_claims()` when `userName` is omitted); username fallbacks (`claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]`); provenance/audit values (`createdBy`, `modifiedBy`, `changeUserId` fallbacks); and identity comparisons (`skip_schema_validation = (username == "SYSTEM_USER")` in `metadataService.py`, the pipeline-execution bypass in `processWorkflowExecutionOutput.py`).

Handlers compare against this exact string, so a mismatched variant silently fails the comparison (or attributes records to a user ID with no admin role). IAM permissions on direct Lambda invocation are the security boundary for who can inject a `lambdaCrossCall` event.

### Casbin Policy Model

Defined in `common/constants.py`. Request: `(sub, obj, act)`; policy line: `(sub, obj_rule, act, eft)`; role mapping: `g = _, _`; effect: `some(allow) && !some(deny)`; matcher: `g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act`.

### Constraint Fields (`PERMISSION_CONSTRAINT_FIELDS`)

Fields that can be referenced in ABAC policy rules: `databaseId`, `assetName`, `assetType`, `tags`, `tagName`, `tagTypeName`, `roleName`, `userId`, `pipelineId`, `pipelineType`, `pipelineExecutionType`, `workflowId`, `metadataSchemaName`, `metadataSchemaEntityType`, `object__type`, `route__path`.

---

## Validators (`common/validators.py`)

### `validate()` Dispatcher

The `validate()` function is the standard way to validate inputs in both `@root_validator`
methods and handler code. Per-field entries take `value`, `validator` (from the table
below), and optional `optional: True` (skip when `None`/empty) and
`allowGlobalKeyword: True` (accept the literal `"GLOBAL"`):

```python
from common.validators import validate

(valid, message) = validate({
    'databaseId': {'value': database_id, 'validator': 'ID', 'allowGlobalKeyword': True},
    'assetId':    {'value': asset_id,    'validator': 'ASSET_ID'},
    'tags':       {'value': tag_list,    'validator': 'STRING_256_ARRAY', 'optional': True},
})
if not valid:
    raise ValueError(message)                                    # In @root_validator
    # OR
    return validation_error(body={'message': message}, event=event)  # In handler
```

### Available Validator Types

Scalar validators: `ID` (`^[-_a-zA-Z0-9]{3,63}$` — databaseId, pipelineId, etc.),
`ASSET_ID` (filename pattern, max 256 chars), `UUID`, `FILE_NAME`,
`OBJECT_NAME` (`^[a-zA-Z0-9\-._\s]{1,256}$` — assetName, dbName, etc.),
`EMAIL`, `USERID` (`^[\w\-\.\+\@]{3,256}$`), `REGEX`, `NUMBER`, `BOOL`,
`RELATIVE_FILE_PATH` (`^\/.*$`), `ASSET_PATH` (`^.+\/.+$`),
`ASSET_PATH_PIPELINE` (`^pipelines\/.+\/.+\/output\/.+\/$`),
`STRING_30`, `STRING_256`, `STRING_JSON`,
`FILE_EXTENSION` (`^[\\.]([a-zA-Z0-9]){1,7}$`).

Array validators (each element runs the scalar rule): `ID_ARRAY`, `UUID_ARRAY`,
`STRING_256_ARRAY`, `EMAIL_ARRAY`, `USERID_ARRAY`, `OBJECT_NAME_ARRAY`.

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

Resource + client APIs at module level, table names resolved via SSM:

```python
from backend.common.resourceNames import get_table_name, ResourceKeys

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)   # for low-level scans/pagination
your_table = dynamodb.Table(get_table_name(ResourceKeys.YOUR_STORAGE_TABLE))
```

### Common Operations

Standard boto3 resource-API calls (`query` with `KeyConditionExpression`, `get_item`,
`put_item` with `ConditionExpression`, `update_item`). For updates, build the update
expression via `common.dynamodb.to_update_expr(update_dict)`:

```python
from common.dynamodb import to_update_expr
keys_map, values_map, expr = to_update_expr(update_dict)
your_table.update_item(
    Key={'itemId': item_id},
    UpdateExpression=expr,
    ExpressionAttributeNames=keys_map,
    ExpressionAttributeValues=values_map,
)
```

### Pagination Pattern

Use Base64-encoded `NextToken` around `LastEvaluatedKey` (see Rule 15 for the wider rule
against unbounded in-memory sets):

```python
import base64, json
from common.dynamodb import validate_pagination_info

max_items = int(query_params.get('maxItems', '100'))
next_token = query_params.get('NextToken')

scan_kwargs = {'Limit': max_items}
if next_token:
    scan_kwargs['ExclusiveStartKey'] = json.loads(base64.b64decode(next_token).decode('utf-8'))

response = your_table.scan(**scan_kwargs)
result = {'Items': response.get('Items', [])}
if 'LastEvaluatedKey' in response:
    result['NextToken'] = base64.b64encode(
        json.dumps(response['LastEvaluatedKey']).encode('utf-8')
    ).decode('utf-8')
return success(body=result)
```

### Archived Assets Pattern

Archived assets live under a `databaseId + "#deleted"` partition-key suffix. Archive =
rewrite items with the suffixed partition key; query archived items with
`KeyConditionExpression=Key('databaseId').eq(f"{database_id}#deleted")`.

### TypeDeserializer for Low-Level Responses

Low-level client responses use DynamoDB's typed dict shape. Convert with
`boto3.dynamodb.types.TypeDeserializer`:

```python
from boto3.dynamodb.types import TypeDeserializer
deserializer = TypeDeserializer()
python_dict = {k: deserializer.deserialize(v) for k, v in item.items()}
```

---

## Logging

### safeLogger

```python
from customLogging.logger import safeLogger
logger = safeLogger(service_name="YourServiceName")
logger.info(...); logger.warning(...); logger.error(...); logger.exception(...)  # exception adds stack trace
```

`safeLogger` auto-redacts these keys at every nesting level: `authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`.

### Audit Logging

`backend/customLogging/auditLogging.py` exposes `log_authentication`, `log_authorization`, `log_authorization_api`, `log_file_upload`, `log_file_download`, `log_errors`, and other event-type functions — writing to **9 CloudWatch log groups** whose names resolve from SSM via `get_log_group_name(ResourceKeys.*)`. All audit functions extract user context via `request_to_claims(event)`. **Silent failure**: a failed audit write is logged locally, and Lambda execution continues.

---

## Response Functions (`models/common.py`)

-   `success(body=...)` → 200
-   `validation_error(body={'message': ...}, event=event)` → 400
-   `general_error(body={'message': ...}, event=event)` → 400
-   `authorization_error()` → 403
-   `internal_error(event=event)` → 500
-   `VAMSGeneralErrorResponse(...)` — exception class raised by business logic; caught in the handler `try/except` and re-emitted via `general_error(body={'message': str(v)}, event=event)`.

Pass `event=event` where accepted so the audit-logging hook can capture the caller. All responses follow `APIGatewayProxyResponseV2`: `isBase64Encoded=False`, `statusCode`, `headers` (`Content-Type: application/json`, `Cache-Control: no-cache, no-store`), and a JSON-string `body`.

---

## Environment Variables

### Loading Pattern

Resolve resource names at module load inside one `try/except`; wrap optional resources in an inner `try/except KeyError`; read handler-specific env vars directly from `os.environ`:

```python
try:
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    auxiliary_bucket = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    try:
        optional_table_name = get_table_name(ResourceKeys.OPTIONAL_TABLE)
    except KeyError:
        optional_table_name = None
    send_email_function = os.environ.get("SEND_EMAIL_FUNCTION_NAME")
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

asset_table = dynamodb.Table(asset_table_name)
```

**Resolution order:** `get_table_name(ResourceKeys.*)` first checks for legacy env-var overrides (e.g. `ASSET_STORAGE_TABLE_NAME`), then a 60-minute in-module cache, then fetches all resource-name parameters from SSM via one paginated `GetParametersByPath` call. Pytest and local utilities can inject names as env vars while deployed handlers use SSM. **Pipeline handlers** in `backendPipelines/` still use legacy env vars and do not call `get_table_name()`.

### Common Environment Variables

`VAMS_RESOURCE_PARAM_PREFIX` (required, non-pipeline handlers): SSM parameter prefix for resource-name resolution. `PRESIGNED_URL_TIMEOUT_SECONDS` (required): S3 presigned URL TTL. `AWS_REGION` (auto, set by Lambda runtime). `COGNITO_AUTH_ENABLED` (authorizer Lambda only): whether the Cognito MFA-preference check is reachable. Handler-specific vars like `SEND_EMAIL_FUNCTION_NAME` are read directly from `os.environ`.

**Legacy env-var overrides** (for pipeline handlers and testing): `ASSET_STORAGE_TABLE_NAME`, `DATABASE_STORAGE_TABLE_NAME`, `S3_ASSET_AUXILIARY_BUCKET`, `AUDIT_LOG_*`, etc. Non-pipeline handlers resolve these via SSM unless the legacy env var is explicitly set.

---

## File Security

Uploads must be validated against **both** `UNALLOWED_FILE_EXTENSION_LIST` (`.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`) and `UNALLOWED_MIME_LIST` (Java archives, MS-executable, shell/JS/PowerShell/VBScript, etc.). Both lists live in `common/constants.py`; extend them there, never bypass.

---

## Testing

Run `pytest` from `backend/` after `pip install -r requirements-dev.txt`. Tests live under `backend/tests/[domain]/`; markers are `unit`, `integration`, `slow`, `aws`. Full configuration, mock module hierarchy, `conftest.py` layering, and event-shape conventions: see `backend/tests/CLAUDE.md`.

## Templates

New-handler / model / test skeletons: `backend/HANDLER_TEMPLATES.md`. Gold Standard: `backend/handlers/assets/assetService.py`.

---

## Key Dependencies

Runtime: `aws-lambda-powertools` 2.36.0 (Logger, Parser, BaseModel, typing), `boto3` 1.34.84 / `botocore` 1.34.162, `casbin` 1.33.0 (ABAC/RBAC), `pydantic` 1.10.13 (v1 ONLY), `opensearch-py` 2.5.0, `simpleeval` 1.0.7 (safe expression evaluation in Casbin matchers), `locked-dict` 2023.10.22 (thread-safe Casbin cache).

Dev only: `moto` 5.1.0 (AWS mocks), `pytest` 9.0.3, `mypy` 1.0.0, `flake8` 6.0.0.

---

## Updating Python Dependencies (Poetry-Managed)

Wherever a `pyproject.toml` sits next to a `requirements*.txt`, the requirements file is a **generated artifact** exported from `poetry.lock` — never edit it by hand. Poetry-managed projects: `backend/`, `backend/lambdaLayers/base/`, `backend/lambdaLayers/authorizer/`, and `backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`.

To change a dependency version:

1. Edit the constraint in `pyproject.toml` only if the current constraint excludes the target version (exact pins like `urllib3 = "2.6.3"` must be edited; ranges like `^2.12.1` that already admit the target need no edit).
2. Re-resolve without installing: `poetry update --lock <package> [<package>...]`
3. Re-export the requirements file(s):

    ```bash
    # Lambda layers and pipeline layers (single requirements.txt):
    poetry export --without-hashes -f requirements.txt -o requirements.txt

    # backend/ (split main vs dev):
    poetry export --only main --without-hashes -f requirements.txt -o requirements.txt
    poetry export --with dev --without-hashes -f requirements.txt -o requirements-dev.txt
    ```

4. Commit `pyproject.toml`, `poetry.lock`, and the exported requirements file(s) together — a requirements file that drifts from its lock will be silently overwritten by the next export, and the layer bundling build installs from the exported file.

Requirements files with **no** side-by-side `pyproject.toml` (e.g. `backendPipelines/multi/rapidPipelineEKS/lambda/requirements.txt`, `infra/lib/nestedStacks/pipelines/multi/rapidPipelineEKS/constructs/requirements.txt`) are hand-maintained pip files, edited directly.

---

## Anti-Patterns -- What NOT to Do

Most anti-patterns are the inverse of a Critical Rule above. The compact list cites the rule each violates:

-   Raw dict responses with status codes — Rule 7. Use `success` / `validation_error` / `general_error` / `internal_error`.
-   Skipping the `enforceAPI()` check in `lambda_handler` — Rule 4.
-   Missing `object__type` annotation before `enforce()` — Rule 4.
-   Gating a single-resource `enforce()` on `if len(tokens) > 0:` without an `else` that denies — Rule 4. Fails open on empty tokens; the list-filtering "append only when `enforce()` passes" shape is the one exception (fail-closed by construction).
-   Inline regex validation — Rule 3. Use the `validate()` dispatcher.
-   `print()` for logging — Rule 5.
-   Creating boto3 clients inside functions — Rule 6.
-   Pydantic v2 imports or syntax (`ConfigDict`, `field_validator`, `model_validate`, `model_dump`, or `from pydantic import BaseModel`) — Rules 1 and 2.
-   Resolving resource names inside handler functions — Rule 10.
-   Returning raw error strings (`{'statusCode': 400, 'body': 'bad request'}`) — Rules 7 and 11.
-   Echoing user input or internal details in client error messages — Rule 11.

### Swallowing exceptions silently

A bare `except Exception: pass` hides real failures — no log line, no metric, no re-raise. Log the error, then decide whether to raise, translate to a `VAMSGeneralErrorResponse`, or continue with a documented best-effort behavior:

```python
try:
    do_something()
except Exception as e:
    logger.exception(f"Error in do_something: {e}")
    raise VAMSGeneralErrorResponse("Operation failed")
```

The best-effort helpers `common.assetHistory` and `common.syncTracking` are the deliberate exceptions: they catch and log their own failures so a history-write failure never breaks the primary operation. New handlers should not replicate that shape without an equally explicit rationale.

---

## Development Checklist

When creating or modifying a handler:

-   [ ] Imports in standard order (stdlib, boto3, powertools, common, handlers, models)
-   [ ] Module-level: AWS clients with `retry_config`; `safeLogger(service_name=...)`; resource names via `get_table_name(ResourceKeys.*)` inside one `try/except`; DynamoDB tables initialized from resolved names
-   [ ] `lambda_handler` extracts claims via `request_to_claims(event)` first
-   [ ] Tier-1 auth via `casbin_enforcer.enforceAPI(event)` with the fail-closed pre-set flag pattern
-   [ ] Routes dispatch via `ApiRoute.matches()` (not hardcoded fragments)
-   [ ] Request bodies parsed with `parse(body, model=ModelClass)`; params validated with the `validate()` dispatcher
-   [ ] Tier-2 auth via `casbin_enforcer.enforce(event, item)` with `object__type` set; empty-token case fails closed with an explicit `authorization_error()` before any single-resource `enforce()`
-   [ ] Business logic errors raise `VAMSGeneralErrorResponse`; error handling maps ValidationError→400, VAMSGeneralErrorResponse→400, Exception→500
-   [ ] Client error messages are generic (no echoed input or internal details); response functions pass `event=event` for audit logging
-   [ ] No `print()`, no Pydantic v2 syntax; models declare `extra='ignore'`
-   [ ] Tests exist with proper mocking via `conftest.py` (see `backend/tests/CLAUDE.md`)
