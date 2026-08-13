# VAMS Backend + CDK Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending VAMS backend APIs and CDK infrastructure. Follow these rules to ensure consistency, quality, and maintainability across all backend and infrastructure implementations.

> **Steering Document Sync (bidirectional):** This document mirrors the Claude Code steering in `backend/CLAUDE.md` and `infra/CLAUDE.md` (and cross-cutting rules in the root `CLAUDE.md`). Whenever you change a rule, pattern, or convention here, make the equivalent change in the matching `CLAUDE.md` file(s) in the same change — and whenever those `CLAUDE.md` files change, reflect it back here. Keep the two sets of documents saying the same thing.

## 🏗️ **Architecture Overview**

### **File Structure Standards**

```
backend/
├── backend/
│   ├── handlers/                # Lambda function handlers (one per API domain)
│   │   ├── assets/             # Asset-related handlers
│   │   │   ├── assetService.py # GOLD STANDARD implementation
│   │   │   ├── createAsset.py  # Asset creation handler
│   │   │   └── uploadFile.py   # File upload handler
│   │   ├── databases/          # Database-related handlers
│   │   └── [domain]/           # Other domain handlers
│   ├── models/                 # Pydantic request/response models
│   │   ├── assetsV3.py        # Asset API models (GOLD STANDARD)
│   │   ├── common.py          # Common response models
│   │   └── [domain].py        # Domain-specific models
│   ├── common/                # Shared utilities
│   │   ├── constants.py       # Constants and configuration
│   │   ├── validators.py      # Input validation functions
│   │   └── dynamodb.py        # DynamoDB utilities
│   └── customLogging/         # Logging utilities
├── tests/                     # Test files (mirror handler structure)
│   ├── handlers/              # Handler tests
│   ├── models/                # Model tests
│   └── conftest.py           # Test configuration
└── requirements.txt          # Python dependencies

infra/
├── lib/
│   ├── nestedStacks/
│   │   ├── apiLambda/         # API Gateway and Lambda definitions
│   │   │   ├── apiBuilder-nestedStack.ts  # API route definitions
│   │   │   └── constructs/    # Custom constructs
│   │   └── storage/           # Storage resource definitions
│   │       └── storageBuilder-nestedStack.ts  # DynamoDB, S3, SNS
│   ├── lambdaBuilder/         # Lambda function builders
│   │   ├── assetFunctions.ts  # Asset lambda builders
│   │   └── [domain]Functions.ts  # Domain lambda builders
│   └── helper/                # CDK helper utilities
└── config/                   # Configuration files
```

### **Handler Domains (`backend/backend/handlers/`)**

One folder per domain. The current domains:

-   `assets/` — Asset handlers (`assetService.py` is the GOLD STANDARD; `assetVersions.py` covers version CRUD + archive/unarchive + update; `assetHistory.py` serves the paged asset lifecycle history lookup)
-   `auth/` — Auth handlers (authorizer, constraints, cognito, preTokenGen, apiKeyService)
-   `authz/` — Casbin ABAC/RBAC enforcer (`CasbinEnforcer` proxy)
-   `assetLinks/` — Asset relationship management
-   `comments/` — Comment CRUD
-   `config/` — System configuration
-   `databases/` — Database CRUD
-   `indexing/` — OpenSearch indexing (DynamoDB/S3 streams)
-   `metadata/` — Metadata CRUD
-   `metadataschema/` — Metadata schema management
-   `pipelines/` — Pipeline management (Pydantic models; Lambda/SQS/EventBridge execution types)
-   `roles/` — Role CRUD
-   `search/` — OpenSearch search handlers
-   `sendEmail/` — Email notification Lambda
-   `subscription/` — Asset subscription management
-   `tags/` — Tag CRUD
-   `tagTypes/` — Tag type management
-   `userRoles/` — User-role assignment
-   `workflows/` — Step Functions workflow management (Pydantic models, builder pattern for ASL generation: Lambda/SQS/EventBridge/DeadlineCloud task states; `sfn/deadlineCloudJobCallback` resolves Deadline Cloud job task tokens from default-bus events)
-   `addon/` — Add-on integrations (`garnetFramework/` Garnet NGSI-LD indexer Lambdas; `physna/` Physna Sync Lambdas: physnaFileSync, physnaAssetSync, physnaViewer; physnaCommon.py holds shared client/auth helpers)

#### **Workflow Execution Storage**

Workflow executions are workflow-keyed: the `executionId` is a VAMS GUID passed as the Step Functions execution name, so `$$.Execution.Name == executionId`. Asset/database linkage is not on the main row — it lives in `WorkflowExecutionInputsStorageTable`, queried via the `WorkflowExecInputsByAssetGSI` GSI for the asset-scoped execution listing. `executeWorkflow` writes the V2 main execution row plus the workflow inputs/configuration rows, one `PipelineExecutions` row per pipeline in the workflow, and the first-pipeline input rows (files/metadata/configuration). `processWorkflowExecutionOutput` writes the end-state pipeline's output/metadata/log rows and the completion status back to the main row. The pure record-building logic (key construction, S3 prefix derivation, record-dict builders, text truncation) lives in `common/workflows/executionRecords.py` and is unit-tested in isolation.

## 📋 **Development Workflow Checklist**

### **Phase 1: Pre-Implementation**

-   [ ] **Analyze Requirements**: Understand the new API/feature requirements
-   [ ] **Review Gold Standard**: Study `assetService.py` for implementation patterns
-   [ ] **Plan API Design**: Design request/response models and endpoints
-   [ ] **Plan CDK Changes**: Identify required infrastructure changes
-   [ ] **Plan Authorization**: Determine permission requirements and object types
-   [ ] **Plan Frontend Integration**: Identify frontend service changes needed
-   [ ] **Plan CLI Integration**: Identify CLI command changes needed
-   [ ] **Plan Documentation**: Identify documentation updates required

### **Phase 2: Implementation**

#### **Step 1: Backend Models (Pydantic)**

-   [ ] **Create Request Models**: Add Pydantic models in `models/[domain].py`
-   [ ] **Create Response Models**: Add response models with proper typing
-   [ ] **Add Validation Logic**: Include `@root_validator` for complex validation
-   [ ] **Follow Gold Standard**: Use `assetsV3.py` patterns for validation
-   [ ] **Import in Models**: Add new models to appropriate `__init__.py`

#### **Step 2: Backend Handler Implementation**

-   [ ] **Create Handler File**: Add handler in `handlers/[domain]/[handler].py`
-   [ ] **Follow Gold Standard**: Use `assetService.py` patterns for structure
-   [ ] **Normalize the REST event**: Call `request_to_claims(event)` as the first event access (it normalizes internally). Only if the handler reads `requestContext['http']` _before_ claims, `import normalize_event` from `common.auth.apiEvent` and call it as the first statement of `lambda_handler` (see Rule 1)
-   [ ] **Implement Error Handling**: Use comprehensive try/catch with proper exceptions
-   [ ] **Add Authorization**: Include Casbin enforcement with object-type checking
-   [ ] **Add Logging**: Use `safeLogger` for structured logging. It redacts credential keys (`authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`) and caller-authored content keys (`configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions`), at every nesting level in dicts, lists, and tuples, and inside a request `body` that arrives as a JSON string. Redaction is key-driven, so an f-string interpolating a payload value bypasses it — log identifiers and counts, never rendered template bodies or tag values
-   [ ] **Resolve Resource Names**: Use `get_table_name(ResourceKeys.*)`, `get_bucket_name(ResourceKeys.*)` from `common.resourceNames` at module level in try/except
-   [ ] **Add AWS Clients**: Configure AWS clients with retry configuration
-   [ ] **Implement Business Logic**: Separate business logic from request handling
-   [ ] **Add Response Enhancement**: Include version info and bucket details where applicable

#### **Step 3: CDK Infrastructure**

-   [ ] **Update Storage Resources**: Add new DynamoDB tables/S3 buckets in `storageBuilder-nestedStack.ts`
-   [ ] **Register Resource Names**: Add constants to `infra/common/resourceParamKeys.ts`, `backend/backend/common/resourceNames.py`, AND `infra/deploymentDataMigration/tools/ssm_resource_lookup.py` (data-migration scripts resolve table/log-group names from these SSM parameters); register descriptor in `resourceNameRegistry`
-   [ ] **Create Lambda Builder**: Add lambda function builder in `lambdaBuilder/[domain]Functions.ts`
-   [ ] **Configure Environment Variables**: Add handler-specific env vars only (resource names resolved from SSM via `globalLambdaEnvironmentsAndPermissions`)
-   [ ] **Configure Permissions**: Grant appropriate DynamoDB/S3/SNS permissions
-   [ ] **Configure VPC**: Add VPC/subnet configuration based on config flags
-   [ ] **Add KMS Permissions**: Include KMS key permissions for encryption
-   [ ] **Add API Routes**: Register routes in `apiBuilder2-nestedStack.ts` (preferred; `apiBuilder-nestedStack.ts` is near the CloudFormation per-stack resource limit)
-   [ ] **Follow Naming Conventions**: Use consistent naming patterns

#### **Step 4: API Gateway Integration**

-   [ ] **Add Route Definitions**: Use `attachFunctionToApi` for route registration
-   [ ] **Configure HTTP Methods**: Set appropriate HTTP methods for each endpoint
-   [ ] **Add Security**: Confirm the route resolves through the custom VAMS Lambda authorizer (never a built-in CDK authorizer); set `allowAnonymous` only for a deliberately unauthenticated path
-   [ ] **Test Route Paths**: Verify route paths match API documentation

### **Phase 3: Quality Assurance**

#### **Step 5: Testing**

-   [ ] **Write Unit Tests**: Create tests in `tests/handlers/[domain]/`
-   [ ] **Test Success Cases**: Test normal operation flows
-   [ ] **Test Error Cases**: Test all error scenarios and exception handling
-   [ ] **Test Authorization**: Test Casbin enforcement scenarios
-   [ ] **Test Validation**: Test Pydantic model validation
-   [ ] **Mock AWS Services**: Use proper mocking for DynamoDB, S3, SNS
-   [ ] **Run All Tests**: Ensure `pytest` passes with coverage

#### **Step 6: Frontend Integration**

-   [ ] **Update API Service**: Add methods to `web/src/services/APIService.js` (or update existing API Paths that may not be always in this file)
-   [ ] **Follow Frontend Patterns**: Use boolean/message return patterns
-   [ ] **Handle Response Formats**: Support both legacy and new response formats
-   [ ] **Add Error Handling**: Include proper error message extraction
-   [ ] **Test Frontend Integration**: Verify frontend can consume new APIs

#### **Step 7: CLI Integration**

-   [ ] **Update API Client**: Add methods to `tools/VamsCLI/vamscli/utils/api_client.py`
-   [ ] **Add Constants**: Add API endpoints to `constants.py`
-   [ ] **Add Exceptions**: Create specific exceptions for new error scenarios
-   [ ] **Add Commands**: Create CLI commands if needed
-   [ ] **Test CLI Integration**: Verify CLI can consume new APIs

#### **Step 8: Documentation Updates**

-   [ ] **Update API docs in BOTH places**: API documentation lives in two independent sources that must be kept in sync — (1) the OpenAPI spec `documentation/VAMS_API.yaml` (paths + component schemas), and (2) the Docusaurus reference page `documentation/docusaurus-site/docs/api/{domain}.md` (e.g. `api/auth.md` for `/auth/*`). Add/rename/change the endpoint in **both**; updating only one leaves the docs inconsistent.
-   [ ] **Update Docusaurus developer docs (`documentation/docusaurus-site/docs/developer/`)**: Add architecture and usage information
-   [ ] **Update Docusaurus permissions docs (`documentation/docusaurus-site/docs/concepts/permissions-model.md`)**: Add authorization mappings for new endpoints
-   [ ] **Update README**: Update overview if major features added
-   [ ] **Add Code Examples**: Include usage examples in documentation

#### **Step 9: Code Quality**

-   [ ] **Run Black**: Format code with `black backend/`
-   [ ] **Run MyPy**: Type check backend code
-   [ ] **Run Flake8**: Lint backend code
-   [ ] **Check CDK Lint**: Run CDK linting on infrastructure code
-   [ ] **Review Error Messages**: Ensure user-friendly error messages
-   [ ] **Review Logging**: Ensure proper structured logging

## 🚨 **Mandatory Rules**

### **Rule 1: Follow Gold Standard Implementation (assetService.py)**

### **Rule 2: Page S3 and DynamoDB listings to exhaustion when the full set is needed**

S3 `list_object_versions` / `list_objects_v2` and DynamoDB queries cap a single
call (`MaxKeys`, `Limit`, one page). When the result must be complete, page to
exhaustion — a bare `list_object_versions(..., MaxKeys=N)` silently drops versions
beyond `N`, producing wrong archive status and truncated history. For S3
versions/objects use the shared helpers `common.s3.list_all_object_versions()` /
`list_all_objects()` (page-size constants `S3_VERSIONS_PAGE_SIZE` /
`S3_OBJECTS_PAGE_SIZE`; both accept an optional `max_keys` / `max_objects` cap for
best-effort sampling). Existence-only checks (`MaxKeys=1`) are the allowed
exception.

To check whether a single key or a specific `versionId` is archived, do **not**
list versions — use `common.s3.is_object_version_archived()`, which issues one
`HeadObject` (405 MethodNotAllowed = delete marker, 200 = live, 404 = missing) and
is O(1) regardless of version count. Handler-local `is_file_archived` helpers must
delegate to it.

### **Rule 3: Paginate large GET responses; never return an unbounded in-memory set**

A response that can exceed the AWS Lambda synchronous response limit (6 MB) must
page externally: accept `maxItems`/`pageSize`/`startingToken` and return
`NextToken`, defaulting the sizes to named constants (mirror the asset-listing and
metadata-listing handlers). Do not use DynamoDB `paginator.build_full_result()` to
accumulate every record for a user-facing GET. When ordering or enrichment requires
the full set first (e.g. metadata schema injection/ordering), enrich the full set,
then offset-slice to the page. Limits that bound response size or protect Lambda
runtime (e.g. `MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST`, worker-pool caps) stay as named
constants with a rationale comment — keep them. CLI and web clients that consume a
paginated GET must follow `NextToken` to retrieve the complete set.

### **Rule 4: Keep handlers portable across AWS partitions**

Handlers run in `aws`, `aws-us-gov`, `aws-eusc` (EU Sovereign, region
`eusc-de-east-1`), and potentially `aws-cn` / `aws-iso*`. A partition defect is
invisible in commercial tests and surfaces as a runtime 500 or a validation
rejection that reproduces **only** in the affected partition. There is deliberately
no central partition helper in the backend — these four rules are the contract.

1. **Never build an ARN from a hardcoded partition.** Either parameterize it
   (`common/workflows/stepfunctions_builder.py` threads a `partition` argument
   through every state-machine integration ARN, sourced from the `AWS_PARTITION`
   Lambda env var read in `common/workflows/workflowAsl.py`), or parse an ARN you
   already hold (`handlers/workflows/executionService.py` splits a resource ARN,
   falling back to the execution log-group ARN, to recover
   partition/region/account). Prefer parsing when an ARN is in hand — no env var,
   cannot drift. `AWS_PARTITION` is set on the `workflowService` Lambda only, so a
   handler needing it must have its CDK builder updated or derive it from an ARN.

2. **Never hardcode an endpoint hostname or DNS suffix.** Suffixes differ per
   partition — `amazonaws.com`, `amazonaws.com.cn`, **`amazonaws.eu`** (EU
   Sovereign), `c2s.ic.gov`, `sc2s.sgov.gov`, `cloud.adc-e.uk`, `csp.hci.ic.gov`.
   Construct plain boto3 clients with **no `endpoint_url`** and let the SDK resolve
   per region; read service endpoints (e.g. the OpenSearch host) from SSM. Region
   comes from `os.environ["AWS_REGION"]` — avoid a `"us-east-1"` default, which
   silently points at the wrong partition if the variable is ever missing.

3. **New ARN/URL validators must accept every partition.** Compose them from
   `aws_partition_group` (`aws`, `-us-gov`, `-cn`, `-eusc`, `-iso[-x]`) and
   `aws_dns_suffix_group` in `common/validators.py`; never inline `arn:aws:`. A
   commercial-only pattern passes every commercial test and rejects legitimate
   input only in the affected partition — the hardest failure to attribute.
   `infra/lib/helper/const.ts` (`SERVICE_LOOKUP`) is the authoritative partition +
   suffix list; keep these groups in step with it.

4. **A service or model may not exist everywhere.** Bedrock model ids differ (both
   restricted config templates pin an older Sonnet), OpenSearch engine versions
   differ, and some services are absent. Take such values from configuration the
   CDK layer supplies per partition rather than hard-coding them.

The `GOVCLOUD` feature switch is present in `featuresEnabled` for **both** GovCloud
and EU Sovereign (both templates set `app.govCloud.enabled: true`), so treat it as
"restricted partition" rather than literally GovCloud. The CDK-side rules —
including the `AWS::Lambda::EventSourceMapping` tag restriction that fails a
GovCloud deploy outright — are in `.kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md`.

## 🔐 **Security Guidelines for Exception Handling**

### **Critical Security Rule: Secure Exception Handling**

**ALL inner raises of custom exception types (not the final catch wrappers) MUST NOT contain:**

-   Input parameters that reveal system internals
-   `str(e)` exception information from caught exceptions
-   Function names, file paths, or AWS resource details
-   Database schema, table names, or configuration details
-   Lambda function names or internal service identifiers
-   S3 bucket names, keys, or path structures
-   Any information that could aid in system reconnaissance

### **Secure Exception Patterns**

#### **✅ SECURE Pattern - Inner Business Logic:**

```python
# Inner business logic exceptions - Generic messages only
if not bucket_name or not base_assets_prefix:
    raise VAMSGeneralErrorResponse("Database configuration invalid")

if not asset:
    raise VAMSGeneralErrorResponse("Resource not found")

if not casbin_enforcer.enforce(resource, "GET"):
    raise VAMSGeneralErrorResponse("Access denied")

# Validation failures - No input parameter details
if len(asset_id) < 3:
    raise VAMSGeneralErrorResponse("Invalid resource identifier")
```

#### **✅ SECURE Pattern - Final Catch Wrappers:**

```python
# Final catch wrappers - Log details internally, return generic messages
try:
    # Business logic here
    result = process_asset(asset_data)
    return result
except VAMSGeneralErrorResponse as e:
    # Re-raise VAMS exceptions as-is (already secure)
    raise e
except Exception as e:
    # Log full details for debugging (internal only)
    logger.exception(f"Error processing asset {asset_id}: {e}")
    # Return generic message to user
    raise VAMSGeneralErrorResponse("Error processing request")
```

#### **❌ INSECURE Patterns - DO NOT USE:**

```python
# ❌ NEVER expose internal exception details
raise VAMSGeneralErrorResponse(f"Error getting bucket details: {e}")

# ❌ NEVER expose input parameters in exceptions
raise ValueError(f"Database {database_id} does not exist")

# ❌ NEVER expose function or service names
raise Exception(f"Error invoking lambda function {function_name}: {e}")

# ❌ NEVER expose file paths or system details
raise Exception(f"Error getting database default bucket details: missing bucket_name")

# ❌ NEVER expose AWS resource details
raise VAMSGeneralErrorResponse(f"S3 bucket {bucket_name} access denied: {str(e)}")
```

### **Validation and Model Guidelines**

#### **Validator Integration**

-   **Validators Path**: `backend/backend/common/validators.py`
-   **Models Path**: `backend/backend/models/`
-   **Use existing validators** where a validation type exists (see available types below)
-   **Add new validator types** for repetitive validations instead of duplicating logic
-   **Request model verification** for complex business logic that cannot be handled by simple validators

#### **Available Validator Types** (from `backend/backend/common/validators.py`):

```python
# Identity and Reference Validators
'ID'                    # General IDs (3-63 chars, alphanumeric + hyphens/underscores)
'ASSET_ID'              # Asset identifiers (up to 256 chars)
'UUID'                  # Standard UUID format
'EMAIL'                 # Email address format
'USERID'                # User identifier format

# String Length Validators
'STRING_30'             # Max 30 characters
'STRING_256'            # Max 256 characters
'STRING_256_ARRAY'      # Array of strings, each max 256 chars

# File and Path Validators
'FILE_NAME'             # Valid filename format
'FILE_EXTENSION'        # File extension format (.ext)
'RELATIVE_FILE_PATH'    # Relative file path format
'ASSET_PATH'            # Asset path format (with isFolder option)
'ASSET_PATH_PIPELINE'   # Pipeline-specific asset paths
'ASSET_AUXILIARYPREVIEW_PATH'  # Auxiliary preview paths

# Object and Content Validators
'OBJECT_NAME'           # Object name format
'OBJECT_NAME_ARRAY'     # Array of object names
'STRING_JSON'           # Valid JSON string format
'REGEX'                 # Valid regex pattern
'NUMBER'                # Numeric value
'BOOL'                  # Boolean value

# Array Validators
'ID_ARRAY'              # Array of IDs
'UUID_ARRAY'            # Array of UUIDs
'EMAIL_ARRAY'           # Array of email addresses
'USERID_ARRAY'          # Array of user IDs

# Specialized Validators
'SAGEMAKER_NOTEBOOK_ID' # SageMaker notebook naming
```

#### **Request/Response Model Strategy**:

1. **Use validators first** where a validation type exists
2. **Add custom `@root_validator`** for complex business logic validation
3. **Create new validator types** for repetitive validation patterns
4. **Keep validation logic in models**, not in business logic functions

#### **Example: Secure Model with Validator Integration**

```python
class CreateAssetRequestModel(BaseModel, extra='ignore'):
    """Secure request model with proper validation"""
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace=True, regex=id_pattern)
    assetName: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, regex=id_pattern)

    @root_validator
    def validate_fields(cls, values):
        # Use existing validators where possible
        (valid, message) = validate({
            'assetId': {
                'value': values.get('assetId'),
                'validator': 'ASSET_ID'
            },
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID'
            },
            'assetName': {
                'value': values.get('assetName'),
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            # Validator messages are safe to return - they explain restrictions without exposing input values
            logger.error(message)
            raise ValueError(message)
        return values
```

## 🚨 **Mandatory Rules**

### **Rule 1: Follow Gold Standard Implementation (assetService.py)**

All backend handlers MUST follow the patterns established in `assetService.py`:

```python
# ✅ CORRECT - Follow assetService.py patterns
import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from backend.common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.[domain] import [RequestModel], [ResponseModel]

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="[ServiceName]")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    # Resolve DynamoDB table names from SSM Parameter Store
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    required_bucket = get_bucket_name(ResourceKeys.REQUIRED_BUCKET)
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for [service] APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        # Check API authorization
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
        # ... other methods

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

**Event normalization (`normalize_event`):** The REST API (v1) proxy event differs from the
HTTP API v2 layout handlers are written against in **two** ways that `normalize_event(event)`
(from `common.auth.apiEvent`) reconciles. It mutates in place, is idempotent, and no-ops on
`lambdaCrossCall` events.

1.  **`requestContext.http` block.** Handlers read
    `event['requestContext']['http']['path']` / `['method']` / `['sourceIp']`; the REST event
    exposes these as top-level `path` / `httpMethod` and `requestContext.identity.sourceIp`.
    `normalize_event` injects the v2-style `requestContext.http` block.
2.  **Null `pathParameters` / `queryStringParameters`.** The REST event sends these as an
    explicit JSON `null` when empty (HTTP API v2 omitted them), so `event.get('pathParameters', {})`
    / `event.get('queryStringParameters', {})` returns `None` — the default applies only when
    the **key is absent**, not present-but-`null`. A handler that then does `params['id']`,
    `'id' in params`, or `int(params['maxItems'])` crashes with `TypeError: 'NoneType' object
is not subscriptable/iterable` → **500**. `normalize_event` coerces a present-but-`null`
    value of either key to `{}`.

-   **`request_to_claims(event)` calls `normalize_event(event)` internally** (first line),
    so the Gold Standard order above — `request_to_claims(event)` as the handler's first
    event access, _then_ read `path`/`method`/params — is already covered for both
    normalizations and needs **no** import or explicit call.
-   **Only when a handler must read `requestContext['http']`, `pathParameters`, or
    `queryStringParameters` _before_ `request_to_claims`** does it
    `from common.auth.apiEvent import normalize_event` and call `normalize_event(event)` as the
    first statement of `lambda_handler`. Reading those before normalization raises
    `KeyError`/`TypeError` → 500 on a real REST request — a failure invisible to CDK synth and
    to unit tests that hand-build a v2-shaped event, so cover the REST-shaped event (including
    `null` params) in tests.
-   **`claims_and_roles["roles"]` comes from the `vams:roles` authorizer context value**, which
    the authorizer (`common/auth/authorizerCore.py`) resolves from the user roles table with a
    60-second per-user cache. Resolving it there — rather than in the Cognito
    pre-token-generation trigger, which only runs for Cognito — is what makes roles available
    for every auth mode (Cognito, external OAuth IDP, API key) and lets a role change take
    effect without re-issuing a token. The value is informational for handlers and audit logs:
    `CasbinEnforcer` re-reads a user's roles from DynamoDB when it builds policy, so
    authorization does not depend on it. Do not reintroduce a role lookup in `preTokenGen`.

### **Rule 2: Pydantic Models MUST Follow assetsV3.py Patterns**

```python
# ✅ CORRECT - Follow assetsV3.py patterns
from typing import Dict, List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern, object_name_pattern

class [Domain]RequestModel(BaseModel, extra='ignore'):
    """Request model for [operation] [domain]"""
    requiredField: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=id_pattern)
    optionalField: Optional[str] = Field(None, min_length=1, max_length=256)

    @root_validator
    def validate_fields(cls, values):
        # Custom validation logic
        (valid, message) = validate({
            'optionalField': {
                'value': values.get('optionalField'),
                'validator': 'STRING_256',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        return values

class [Domain]ResponseModel(BaseModel, extra='ignore'):
    """Response model for [domain] data"""
    id: str
    name: str
    status: Optional[str] = "active"
    timestamp: str
```

### **Rule 3: CDK Lambda Functions MUST Follow assetFunctions.ts Patterns**

```typescript
// ✅ CORRECT - Follow assetFunctions.ts patterns
export function build[Domain]Service(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "[domainService]";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas ? vpc : undefined,
        vpcSubnets: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas ? { subnets: subnets } : undefined,
        environment: {
            // Handler-specific env vars only (resource names resolved from SSM)
            REQUIRED_SETTING: config.app.requiredSetting,
        },
    });

    // Grant permissions
    storageResources.dynamo.requiredTable.grantReadWriteData(fun);
    storageResources.s3.requiredBucket.grantReadWrite(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

### **Rule 4: Authorization MUST Include Casbin Enforcement**

```python
# ✅ CORRECT - Include proper authorization checks
def handle_get_request(event):
    """Handle GET requests with proper authorization"""
    path_parameters = event.get('pathParameters', {})

    try:
        # Validate parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_parameters['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_parameters['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)

        # Get the resource
        resource = get_resource_details(path_parameters['databaseId'], path_parameters['assetId'])

        # Check authorization
        if resource:
            resource.update({"object__type": "[objectType]"})
            # Fail closed: an empty token list means no authenticated identity, so deny
            # rather than fall through to returning the resource. Never gate the enforce
            # inside `if len(tokens) > 0` without an else that denies.
            if len(claims_and_roles["tokens"]) == 0:
                return authorization_error()
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(resource, "GET"):
                return authorization_error()

            # Convert to response model
            try:
                response_model = [Domain]ResponseModel(**resource)
                return success(body=response_model.dict())
            except ValidationError as v:
                logger.exception(f"Error converting to response model: {v}")
                return success(body={"message": resource})
        else:
            return general_error(body={"message": "Resource not found"}, status_code=404, event=event)

    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)
```

#### **System User (`SYSTEM_USER`)**

`SYSTEM_USER` is the **only** valid user ID for system-process actions — never use `SYSTEM`, `system`, or any other variant. It is seeded into the user and user-roles tables during CDK deployment and assigned to the `admin` role, so actions attributed to it pass Casbin authorization. Use it consistently for:

-   **Lambda cross-calls**: `{'lambdaCrossCall': {'userName': 'SYSTEM_USER'}}` — and it is the default in `request_to_claims()` when a cross-call omits `userName`
-   **Username fallbacks**: `claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]` when no user context exists
-   **Provenance / audit values**: `createdBy`, `modifiedBy`, `changeUserId` fallbacks (`user_id or "SYSTEM_USER"`)
-   **Identity comparisons**: e.g. `skip_schema_validation = (username == "SYSTEM_USER")` in `metadataService.py`, and the pipeline-execution bypass in `processWorkflowExecutionOutput.py`

Because handlers compare against this exact string, a mismatched variant silently fails the comparison (or attributes records to a user ID that has no admin role). IAM permissions on direct Lambda invocation are the security boundary for who can inject a `lambdaCrossCall` event.

### **Rule 5: Storage Resources MUST Be Added to storageBuilder-nestedStack.ts**

```typescript
// ✅ CORRECT - Add new storage resources
export interface storageResources {
    // ... existing resources
    dynamo: {
        // ... existing tables
        [newDomain]StorageTable: dynamodb.Table;
    };
    s3: {
        // ... existing buckets
        [newDomain]Bucket?: s3.Bucket;
    };
}

// In storageResourcesBuilder function:
const [newDomain]StorageTable = new dynamodb.Table(scope, "[NewDomain]StorageTable", {
    ...dynamodbDefaultProps,
    partitionKey: {
        name: "primaryKey",
        type: dynamodb.AttributeType.STRING,
    },
    sortKey: {
        name: "sortKey",
        type: dynamodb.AttributeType.STRING,
    },
});

// Add GSI if needed
[newDomain]StorageTable.addGlobalSecondaryIndex({
    indexName: "RequiredGSI",
    partitionKey: {
        name: "gsiPartitionKey",
        type: dynamodb.AttributeType.STRING,
    },
});

// Return in storageResources
return {
    // ... existing resources
    dynamo: {
        // ... existing tables
        [newDomain]StorageTable: [newDomain]StorageTable,
    },
};
```

### **Rule 6: API Routes MUST Be Registered in an apiBuilder Nested Stack**

Prefer `apiBuilder2-nestedStack.ts` for new endpoints — the primary `apiBuilder-nestedStack.ts` is near the CloudFormation per-stack resource limit. Place a function in `apiBuilder` only when it must share a directly-referenced function instance defined there. `attachFunctionToApi` records a descriptor in the cross-stack `RouteRegistry` (passed as `registry`) and creates no API resource itself; the API implementation, built last, renders the whole registry into one OpenAPI document. Registering the same method + path twice throws at synth.

```typescript
// ✅ CORRECT - Register API routes
const [domain]Service = build[Domain]Service(
    scope,
    lambdaCommonBaseLayer,
    storageResources,
    config,
    vpc,
    subnets
);

// Attach routes following existing patterns
attachFunctionToApi(this, [domain]Service, {
    routePath: "/[domain]",
    method: apigateway.HttpMethod.GET,
    registry: registry,
});

attachFunctionToApi(this, [domain]Service, {
    routePath: "/[domain]/{[domain]Id}",
    method: apigateway.HttpMethod.GET,
    registry: registry,
});

attachFunctionToApi(this, [domain]Service, {
    routePath: "/[domain]",
    method: apigateway.HttpMethod.POST,
    registry: registry,
});
```

### **Rule 7: Frontend Integration MUST Follow APIService.js Patterns**

```javascript
// ✅ CORRECT - Add to web/src/services/APIService.js
/**
 * [Operation description]
 * @param {Object} params - Parameters object
 * @param {string} params.requiredParam - Required parameter description
 * @param {boolean} params.optionalParam - Optional parameter description
 * @returns {Promise<boolean|{message}|any>}
 */
export const [operationName] = async (
    { requiredParam, optionalParam = false },
    api = API
) => {
    try {
        if (!requiredParam) {
            return [false, "Required parameter is missing"];
        }

        const response = await api.[method]("api", `[endpoint]`, {
            body: {
                requiredParam,
                optionalParam,
            },
        });

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("[Operation] error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else if (response.success !== undefined) {
            // New API response format
            return [response.success, response.message || "Operation completed"];
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error in [operationName]:", error);
        return [false, error?.message || "Failed to [operation]"];
    }
};
```

### **Rule 8: CLI Integration MUST Follow api_client.py Patterns**

```python
# ✅ CORRECT - Add to tools/VamsCLI/vamscli/utils/api_client.py

# First add constants to constants.py
API_[DOMAIN] = "/[domain]"
API_[DOMAIN]_BY_ID = "/[domain]/{[domain]Id}"

# Then add exceptions to exceptions.py
class [Domain]NotFoundError(VamsCLIError):
    """Raised when [domain] is not found."""
    pass

class [Domain]AlreadyExistsError(VamsCLIError):
    """Raised when [domain] already exists."""
    pass

# Then add API methods to api_client.py
def [operation_name](self, [params]) -> Dict[str, Any]:
    """
    [Operation description] using the [endpoint] [method] endpoint.

    Args:
        [param]: [Description]

    Returns:
        API response data with [description]

    Raises:
        [Domain]NotFoundError: When [domain] is not found
        Invalid[Domain]DataError: When [domain] data is invalid
        APIError: When API call fails
    """
    try:
        endpoint = API_[DOMAIN].format([param]=[param])
        response = self.[method](endpoint, data=[data], include_auth=True)
        return response.json()

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400:
            error_data = e.response.json() if e.response.content else {}
            error_message = error_data.get('message', str(e))
            raise Invalid[Domain]DataError(f"Invalid [domain] data: {error_message}")

        elif e.response.status_code == 404:
            raise [Domain]NotFoundError(f"[Domain] not found")
        elif e.response.status_code in [401, 403]:
            raise AuthenticationError(f"Authentication failed: {e}")
        else:
            raise APIError(f"[Operation] failed: {e}")

    except Exception as e:
        raise APIError(f"Failed to [operation]: {e}")
```

### **Rule 9: Documentation MUST Be Updated Across All Files**

When making API changes, update the appropriate documentation files:

#### **Documentation File Mapping:**

-   **API changes** → Update `VAMS_API.yaml` with new endpoints, schemas, responses
-   **Authorization changes** → Update Docusaurus permissions docs with new permission mappings
-   **Architecture changes** → Update Docusaurus developer docs with component information
-   **Major features** → Update main `README.md`

#### **Comment & Documentation Style (Match Surrounding Code):**

Comments and documentation must be commensurate with the surrounding material — match the level of detail, density, and tone of the file you are editing.

-   **Code comments**: Match the comment density and style already present in the file (the CDK stacks use brief single-line `//` notes and short `/** ... */` section headers). Describe **what** a piece of code is, not the history of why it was added.
-   **No changelog/process narration in code**: Never write comments that reference "upgrades", "new in vX", "added for", migrations, or the change request that prompted the edit. Changelog narration belongs in `CHANGELOG.md` and the docs revision history, not in source comments.

#### **VAMS_API.yaml Update Pattern:**

```yaml
# ✅ CORRECT - Add comprehensive API documentation
/[domain]/{[domain]Id}:
    get:
        summary: "Get a [domain]."
        responses:
            "200":
                description: OK
                content:
                    application/json:
                        schema:
                            $ref: "#/components/schemas/[domain]Response"
            "400":
                description: Invalid parameters.
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
            "403":
                description: Not authorized to access [domain].
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
            "404":
                description: [Domain] not found.
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
            "500":
                description: Error processing request.
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
        parameters:
            - name: "[domain]Id"
              in: "path"
              description: "Unique identifier for [domain]."
              required: true
              schema:
                  $ref: '#/components/schemas/id_regex'
        security:
            - DefaultCognitoAuthorizer: []

components:
    schemas:
        [domain]Request:
            type: object
            properties:
                requiredField:
                    $ref: '#/components/schemas/id_regex'
                optionalField:
                    $ref: '#/components/schemas/string256Param'
            required:
                - requiredField

        [domain]Response:
            type: object
            properties:
                id:
                    $ref: '#/components/schemas/id_regex'
                name:
                    type: string
                status:
                    type: string
                timestamp:
                    type: string
                    format: date-time
            required:
                - id
                - name
                - timestamp
```

#### **Permissions Documentation Update Pattern (`documentation/docusaurus-site/docs/concepts/permissions-model.md`):**

```markdown
# ✅ CORRECT - Add authorization mapping

-   `/[domain]` - GET/POST
    -   `[Domain]` ([domainId], [field1], [field2]) - GET (api: GET)
    -   `[Domain]` ([domainId], [field1], [field2]) - POST (api: POST)
-   `/[domain]/{[domain]Id}` - GET/PUT/DELETE
    -   `[Domain]` ([domainId], [field1], [field2]) - GET (api: GET)
    -   `[Domain]` ([domainId], [field1], [field2]) - PUT (api: PUT)
    -   `[Domain]` ([domainId], [field1], [field2]) - DELETE (api: DELETE)
```

#### **Docusaurus Documentation Updates:**

When making backend or CDK changes, update the corresponding Docusaurus documentation pages at `documentation/docusaurus-site/docs/`:

-   **New or changed API endpoint (incl. path renames)** → Update **both** the OpenAPI spec `VAMS_API.yaml` **and** the matching Docusaurus reference page under `api/` (e.g. `api/auth.md`) — two separate sources of truth that must stay in sync — plus the CLI command reference if applicable
-   **New config option** → Update `deployment/configuration-reference.md`
-   **New config option** → Also mirror it into the interactive **ConfigBuilder** component (`documentation/docusaurus-site/src/components/ConfigBuilder/`) so the config generator stays in sync — see the component `README.md` for which files to touch (`schema.ts`, `defaults.ts`, `validation.ts`), then confirm the `infra/test/configBuilderSync.test.ts` drift check passes. The drift check only verifies `schema.ts` fields and `defaults.ts` presets — it does **not** cover `validation.ts`, so new/changed `getConfig()` validation logic must be hand-ported into `validation.ts` and kept in sync by review, not by the test. A missing rule leaves the ConfigBuilder approving a config that then fails `cdk synth`, which is worse than no validation because the operator was told it was valid. Two exclusions: rules reading a value the browser cannot see are out of scope — notably the `app.iamRoleConfig` checks, which validate the contents of `infra/config/policy/iamRoleConfig.json`. When checking the port, compare the config FIELD PATHS each rule references; the two files word the same rule differently, so matching on message text under-reports drift.
-   **New pipeline** → Create page in `pipelines/`, update `pipelines/overview.md`, update `overview/features.md`, update `sidebars.ts`
-   **New DynamoDB table** → Update `architecture/aws-resources.md`, `architecture/data-model.md`
-   **Permission changes** → Update `concepts/permissions-model.md`, `user-guide/permissions.md`

#### **Cross-Steering File Updates:**

When making changes that affect development standards, architecture patterns, or quality requirements:

1. Update **all** affected CLAUDE.md files (root, web/, backend/, infra/, tools/VamsCLI/, documentation/)
2. Update the `.kiro/steering/` version of this file
3. If the change affects frontend patterns, also update `WEB_DEVELOPMENT_WORKFLOW.md` and `WEB_FRONTEND.md`
4. If the change affects documentation standards, also update `DOCUMENTATION_WORKFLOW.md`
5. Update any Claude Code skills in `.claude/commands/` that scaffold or reference the changed rule, pattern, checklist, or file path (see root `CLAUDE.md` Rule 12 for the skill-to-steering mapping) — a stale skill actively scaffolds outdated code

### **Rule 10: Tests MUST Follow Comprehensive Patterns**

```python
# ✅ CORRECT - Comprehensive test coverage
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from moto import mock_dynamodb, mock_s3
from handlers.[domain].[handler] import lambda_handler
from models.[domain] import [RequestModel], [ResponseModel]

@pytest.fixture
def mock_environment():
    """Mock environment variables"""
    with patch.dict('os.environ', {
        'VAMS_RESOURCE_PARAM_PREFIX': '/test/resourceNames',
        'REQUIRED_STORAGE_TABLE_NAME': 'test-table',  # Env var override for testing
        'REQUIRED_BUCKET_NAME': 'test-bucket',
    }):
        yield

@pytest.fixture
def mock_claims_and_roles():
    """Mock claims and roles for authorization"""
    return {
        "tokens": ["test-user@example.com"],
        "roles": ["test-role"],
        "username": "test-user@example.com"
    }

class Test[Domain]Handler:
    """Test [domain] handler functionality."""

    @mock_dynamodb
    @mock_s3
    def test_[operation]_success(self, mock_environment, mock_claims_and_roles):
        """Test successful [operation] execution."""
        # Setup mocks
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer_instance.enforce.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                # Create test event
                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/test-id',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'test-id'
                    }
                }

                # Execute handler
                response = lambda_handler(event, {})

                # Verify response
                assert response['statusCode'] == 200
                body = json.loads(response['body'])
                assert 'message' in body or '[expectedField]' in body

    def test_[operation]_validation_error(self, mock_environment, mock_claims_and_roles, event=event):
        """Test [operation] with validation error."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            # Create invalid event
            event = {
                'requestContext': {
                    'http': {
                        'path': '/[domain]/invalid-id',
                        'method': 'GET'
                    }
                },
                'pathParameters': {
                    '[domain]Id': 'invalid'  # Too short for ID validation
                }
            }

            response = lambda_handler(event, {})

            assert response['statusCode'] == 400
            body = json.loads(response['body'])
            assert 'message' in body

    def test_[operation]_authorization_error(self, mock_environment, mock_claims_and_roles):
        """Test [operation] with authorization error."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = False
                mock_enforcer.return_value = mock_enforcer_instance

                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/test-id',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'test-id'
                    }
                }

                response = lambda_handler(event, {})

                assert response['statusCode'] == 403
```

### **Rule 11: Poetry-Managed Requirements Files Are Generated — Never Edit Directly**

Wherever a `pyproject.toml` sits next to a `requirements*.txt`, the requirements file is a **generated artifact** exported from `poetry.lock` — never edit it by hand. Poetry-managed projects: `backend/`, `backend/lambdaLayers/base/`, `backend/lambdaLayers/authorizer/`, and `backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`.

To change a dependency version:

1. Edit the constraint in `pyproject.toml` only if the current constraint excludes the target version (exact pins like `urllib3 = "2.6.3"` must be edited; ranges like `^2.12.1` that already admit the target need no edit).
2. Re-resolve the lock without installing: `poetry update --lock <package> [<package>...]`
3. Re-export the requirements file(s):

    ```bash
    # Lambda layers and pipeline layers (single requirements.txt):
    poetry export --without-hashes -f requirements.txt -o requirements.txt

    # backend/ (split main vs dev):
    poetry export --only main --without-hashes -f requirements.txt -o requirements.txt
    poetry export --with dev --without-hashes -f requirements.txt -o requirements-dev.txt
    ```

4. Commit `pyproject.toml`, `poetry.lock`, and the exported requirements file(s) together — a requirements file that drifts from its lock will be silently overwritten by the next export, and the layer bundling build installs from the exported file.

Requirements files with **no** side-by-side `pyproject.toml` (e.g. `backendPipelines/multi/rapidPipelineEKS/lambda/requirements.txt`, `infra/lib/nestedStacks/pipelines/multi/rapidPipelineEKS/constructs/requirements.txt`) are hand-maintained pip files and are edited directly.

## 📝 **Development Templates**

### **New Backend Handler Template**

```python
"""[Domain] service handler for VAMS API."""

import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.[domain] import (
    [RequestModel], [ResponseModel], [OperationResponseModel]
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="[ServiceName]")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    # Resolve DynamoDB table names from SSM Parameter Store
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    required_bucket_name = get_bucket_name(ResourceKeys.REQUIRED_BUCKET)
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

# Initialize resources
required_table = dynamodb.Table(required_table_name)

#######################
# Business Logic Functions
#######################

def get_[domain]_details([domain]_id):
    """Get [domain] details from DynamoDB

    Args:
        [domain]_id: The [domain] ID

    Returns:
        The [domain] details or None if not found
    """
    try:
        response = required_table.get_item(Key={'[domain]Id': [domain]_id})
        return response.get('Item')
    except Exception as e:
        logger.exception(f"Error getting [domain] details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving resource")

def create_[domain]([domain]_data, claims_and_roles):
    """Create a new [domain]

    Args:
        [domain]_data: Dictionary with [domain] creation data
        claims_and_roles: User claims and roles for authorization

    Returns:
        Created [domain] data
    """
    try:
        # Check authorization
        [domain]_data.update({"object__type": "[domain]"})
        # Fail closed: an empty token list means no authenticated identity, so deny
        # rather than fall through to the mutation. Never gate the enforce inside
        # `if len(tokens) > 0` without an else that denies.
        if len(claims_and_roles["tokens"]) == 0:
            return authorization_error()
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce([domain]_data, "POST"):
            return authorization_error()

        # Create the [domain]
        logger.info(f"Creating [domain] {[domain]_data['[domain]Id']}")

        # Add metadata
        now = datetime.utcnow().isoformat()
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        [domain]_data['dateCreated'] = now
        [domain]_data['createdBy'] = username

        # Save to database
        required_table.put_item(Item=[domain]_data)

        # Return success response
        return [Domain]OperationResponseModel(
            success=True,
            message=f"[Domain] {[domain]_data['[domain]Id']} created successfully",
            [domain]Id=[domain]_data['[domain]Id'],
            operation="create",
            timestamp=now
        )
    except Exception as e:
        logger.exception(f"Error creating [domain]: {e}")
        raise VAMSGeneralErrorResponse("Error creating resource")

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for [domain]

    Args:
        event: API Gateway event

    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}

    try:
        # Get body from event with default empty dict (Pattern 2: Optional Body)
        body = event.get('body', {})

        # If body exists, parse it safely
        if body:
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

        # Case 1: Get a specific [domain]
        if '[domain]Id' in path_parameters:
            logger.info(f"Getting [domain] {path_parameters['[domain]Id']}")

            # Validate parameters
            (valid, message) = validate({
                '[domain]Id': {
                    'value': path_parameters['[domain]Id'],
                    'validator': 'ID'
                },
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)

            # Parse query parameters if needed
            try:
                request_model = parse(query_parameters, model=[Domain]RequestModel)
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                return validation_error(body={'message': str(v)}, event=event)

            # Get the [domain]
            [domain] = get_[domain]_details(path_parameters['[domain]Id'])

            # Check if [domain] exists and user has permission
            if [domain]:
                [domain].update({"object__type": "[domain]"})
                # Fail closed: an empty token list means no authenticated identity, so
                # deny rather than fall through to returning the resource. Never gate the
                # enforce inside `if len(tokens) > 0` without an else that denies.
                if len(claims_and_roles["tokens"]) == 0:
                    return authorization_error()
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not casbin_enforcer.enforce([domain], "GET"):
                    return authorization_error()

                # Convert to response model
                try:
                    response_model = [Domain]ResponseModel(**[domain])
                    return success(body=response_model.dict())
                except ValidationError as v:
                    logger.exception(f"Error converting [domain] to response model: {v}")
                    return success(body={"message": [domain]})
            else:
                return general_error(body={"message": "[Domain] not found"}, status_code=404, event=event)

        # Case 2: List all [domain]s
        else:
            logger.info("Listing all [domain]s")

            # Parse and validate query parameters
            try:
                request_model = parse(query_parameters, model=[Domain]ListRequestModel)
                query_params = {
                    'maxItems': request_model.maxItems,
                    'pageSize': request_model.pageSize,
                    'startingToken': request_model.startingToken
                }
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                validate_pagination_info(query_parameters)
                query_params = query_parameters

            # Get all [domain]s with authorization filtering
            [domain]s_result = get_all_[domain]s(query_params)

            # Convert to response models
            formatted_items = []
            for item in [domain]s_result.get('Items', []):
                try:
                    [domain]_model = [Domain]ResponseModel(**item)
                    formatted_items.append([domain]_model.dict())
                except ValidationError:
                    formatted_items.append(item)

            response = {"Items": formatted_items}
            if 'NextToken' in [domain]s_result:
                response['NextToken'] = [domain]s_result['NextToken']

            return success(body=response)

    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)

def handle_post_request(event):
    """Handle POST requests to create [domain]

    Args:
        event: API Gateway event

    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body with enhanced error handling (Pattern 1: Required Body)
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
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)

        # Parse and validate the request model
        request_model = parse(body, model=[Domain]CreateRequestModel)

        # Create the [domain]
        result = create_[domain](
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )

        # Return success response
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

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for [domain] service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        # Check API authorization
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
```

### **New Pydantic Models Template**

```python
"""[Domain] API models for VAMS."""

from typing import Dict, List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="[Domain]Models")

######################## [Domain] API Models ##########################

class [Domain]RequestModel(BaseModel, extra='ignore'):
    """Request model for getting a [domain]"""
    includeDeleted: Optional[bool] = False

class [Domain]ListRequestModel(BaseModel, extra='ignore'):
    """Request model for listing [domain]s"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None
    includeDeleted: Optional[bool] = False

class [Domain]CreateRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a [domain]"""
    [domain]Id: str = Field(min_length=4, max_length=256, strip_whitespace=True, regex=id_pattern)
    [domain]Name: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    tags: Optional[List[str]] = []

    @root_validator
    def validate_fields(cls, values):
        # Validate tags if provided
        if values.get('tags'):
            logger.info("Validating tags")
            (valid, message) = validate({
                'tags': {
                    'value': values.get('tags'),
                    'validator': 'STRING_256_ARRAY',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        return values

class [Domain]UpdateRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a [domain]"""
    [domain]Name: Optional[str] = Field(None, min_length=1, max_length=256, regex=object_name_pattern)
    description: Optional[str] = Field(None, min_length=4, max_length=256)
    tags: Optional[List[str]] = None

    @root_validator
    def validate_fields(cls, values):
        # Validate tags if provided
        if values.get('tags') is not None:
            logger.info("Validating tags")
            (valid, message) = validate({
                'tags': {
                    'value': values.get('tags'),
                    'validator': 'STRING_256_ARRAY',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)

        # Ensure at least one field is provided for update
        if not any(values.get(field) is not None for field in ['[domain]Name', 'description', 'tags']):
            raise ValueError("At least one field must be provided for update")

        return values

class [Domain]DeleteRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting a [domain]"""
    confirmDelete: bool = Field(default=False)
    reason: Optional[str] = Field(None, max_length=256)

    @validator('confirmDelete')
    def validate_confirmation(cls, v):
        """Ensure confirmation is provided for deletion"""
        if not v:
            raise ValueError("confirmDelete must be true for deletion")
        return v

class [Domain]ResponseModel(BaseModel, extra='ignore'):
    """Response model for [domain] data"""
    [domain]Id: str
    [domain]Name: str
    description: str
    tags: Optional[List[str]] = []
    status: Optional[str] = "active"
    dateCreated: Optional[str] = None
    createdBy: Optional[str] = None

class [Domain]OperationResponseModel(BaseModel, extra='ignore'):
    """Response model for [domain] operations (create, update, delete)"""
    success: bool
    message: str
    [domain]Id: str
    operation: Literal["create", "update", "delete"]
    timestamp: str
```

### **New CDK Lambda Builder Template**

```typescript
/*
 * [Domain] Lambda functions for VAMS CDK infrastructure.
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";

export function build[Domain]Service(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "[domain]Service";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,

        environment: {
            // Handler-specific env vars only (resource names resolved from SSM)
            PRESIGNED_URL_TIMEOUT_SECONDS: config.app.presignedUrlTimeoutSeconds.toString(),
        },
    });

    // Grant permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    // SSM resource name parameters grant via globalLambdaEnvironmentsAndPermissions

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildCreate[Domain]Function(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "create[Domain]";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,

        environment: {
            // Handler-specific env vars only (resource names resolved from SSM)
            PRESIGNED_URL_TIMEOUT_SECONDS: config.app.presignedUrlTimeoutSeconds.toString(),
        },
    });

    // Grant permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    // SSM resource name parameters grant via globalLambdaEnvironmentsAndPermissions

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

### **New Test Template**

```python
"""Test [domain] functionality."""

import json
import pytest
from unittest.mock import Mock, patch
from moto import mock_dynamodb, mock_s3

from handlers.[domain].[handler] import lambda_handler
from models.[domain] import [RequestModel], [ResponseModel]


@pytest.fixture
def mock_environment():
    """Mock environment variables"""
    with patch.dict('os.environ', {
        'VAMS_RESOURCE_PARAM_PREFIX': '/test/resourceNames',
        '[DOMAIN]_STORAGE_TABLE_NAME': 'test-[domain]-table',  # Env var override for testing
    }):
        yield

@pytest.fixture
def mock_claims_and_roles():
    """Mock claims and roles for authorization"""
    return {
        "tokens": ["test-user@example.com"],
        "roles": ["test-role"],
        "username": "test-user@example.com"
    }

@pytest.fixture
def sample_[domain]_data():
    """Sample [domain] data for testing"""
    return {
        '[domain]Id': 'test-[domain]-id',
        '[domain]Name': 'Test [Domain]',
        'description': 'Test [domain] description',
        'tags': ['test-tag'],
        'dateCreated': '2024-01-01T00:00:00Z',
        'createdBy': 'test-user@example.com'
    }

class Test[Domain]Handler:
    """Test [domain] handler functionality."""

    @mock_dynamodb
    @mock_s3
    def test_get_[domain]_success(self, mock_environment, mock_claims_and_roles, sample_[domain]_data):
        """Test successful [domain] retrieval."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer_instance.enforce.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                with patch('handlers.[domain].[handler].get_[domain]_details') as mock_get:
                    mock_get.return_value = sample_[domain]_data

                    event = {
                        'requestContext': {
                            'http': {
                                'path': '/[domain]/test-[domain]-id',
                                'method': 'GET'
                            }
                        },
                        'pathParameters': {
                            '[domain]Id': 'test-[domain]-id'
                        },
                        'queryStringParameters': {}
                    }

                    response = lambda_handler(event, {})

                    assert response['statusCode'] == 200
                    body = json.loads(response['body'])
                    assert '[domain]Id' in body or 'message' in body

    def test_get_[domain]_not_found(self, mock_environment, mock_claims_and_roles):
        """Test [domain] not found scenario."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                with patch('handlers.[domain].[handler].get_[domain]_details') as mock_get:
                    mock_get.return_value = None

                    event = {
                        'requestContext': {
                            'http': {
                                'path': '/[domain]/nonexistent-id',
                                'method': 'GET'
                            }
                        },
                        'pathParameters': {
                            '[domain]Id': 'nonexistent-id'
                        },
                        'queryStringParameters': {}
                    }

                    response = lambda_handler(event, {})

                    assert response['statusCode'] == 404
                    body = json.loads(response['body'])
                    assert 'message' in body

    def test_create_[domain]_success(self, mock_environment, mock_claims_and_roles):
        """Test successful [domain] creation."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer_instance.enforce.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                with patch('handlers.[domain].[handler].create_[domain]') as mock_create:
                    mock_create.return_value = Mock(
                        dict=lambda: {
                            'success': True,
                            'message': '[Domain] created successfully',
                            '[domain]Id': 'test-[domain]-id',
                            'operation': 'create',
                            'timestamp': '2024-01-01T00:00:00Z'
                        }
                    )

                    event = {
                        'requestContext': {
                            'http': {
                                'path': '/[domain]',
                                'method': 'POST'
                            }
                        },
                        'body': json.dumps({
                            '[domain]Id': 'test-[domain]-id',
                            '[domain]Name': 'Test [Domain]',
                            'description': 'Test [domain] description',
                            'tags': ['test-tag']
                        })
                    }

                    response = lambda_handler(event, {})

                    assert response['statusCode'] == 200
                    body = json.loads(response['body'])
                    assert body['success'] == True

    def test_authorization_failure(self, mock_environment, mock_claims_and_roles):
        """Test authorization failure."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = False
                mock_enforcer.return_value = mock_enforcer_instance

                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/test-id',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'test-id'
                    }
                }

                response = lambda_handler(event, {})

                assert response['statusCode'] == 403

    def test_validation_error(self, mock_environment, mock_claims_and_roles, event=event):
        """Test validation error handling."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/invalid',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'invalid'  # Too short for ID validation
                    },
                    'queryStringParameters': {}
                }

                response = lambda_handler(event, {})

                assert response['statusCode'] == 400
                body = json.loads(response['body'])
                assert 'message' in body


if __name__ == '__main__':
    pytest.main([__file__])
```

## ✅ **Quality Assurance Checklist**

### **Before Implementation**

-   [ ] Requirements clearly understood
-   [ ] Gold standard patterns reviewed (`assetService.py`, `assetsV3.py`)
-   [ ] API endpoints and methods planned
-   [ ] Authorization requirements identified
-   [ ] Storage resources requirements identified
-   [ ] Frontend integration points identified
-   [ ] CLI integration points identified
-   [ ] Documentation updates planned

### **During Implementation**

-   [ ] Pydantic models created with proper validation
-   [ ] Backend handlers follow gold standard patterns
-   [ ] AWS clients configured with retry configuration
-   [ ] Environment variables loaded with error handling
-   [ ] Authorization checks implemented with Casbin
-   [ ] Error handling comprehensive with proper exceptions
-   [ ] CDK lambda builders created with proper permissions
-   [ ] Storage resources added to interface and builder
-   [ ] API routes registered in apiBuilder2-nestedStack.ts (or apiBuilder for a shared function instance)
-   [ ] Frontend service methods added with proper patterns
-   [ ] CLI API client methods added with proper exceptions

### **After Implementation**

-   [ ] All tests written and passing
-   [ ] Authorization tests included
-   [ ] Validation tests included
-   [ ] Error scenario tests included
-   [ ] Code formatted with Black
-   [ ] Code linted with Flake8
-   [ ] Type checking passes with MyPy
-   [ ] CDK code linted
-   [ ] VAMS_API.yaml updated with new endpoints and schemas
-   [ ] Docusaurus permissions docs updated with authorization mappings
-   [ ] Docusaurus developer docs updated if architecture changes
-   [ ] Frontend integration tested
-   [ ] CLI integration tested
-   [ ] End-to-end testing completed

## 🎯 **Common Implementation Patterns**

### **Event Body Validation Patterns**

All backend handlers MUST follow standardized event body validation patterns based on whether the request body is required or optional:

#### **Pattern 1: Required Event Body (POST/PUT/DELETE operations)**

```python
# ✅ CORRECT - Required body validation pattern (from createAsset.py)
def handle_post_request(event):
    """Handle POST requests with required body"""
    try:
        # Parse request body with enhanced error handling
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
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)

        # Optional: Validate required fields in the request body
        required_fields = ['databaseId', 'assetName', 'description', 'isDistributable']
        for field in required_fields:
            if field not in body:
                return validation_error(body={'message': f"Missing required field: {field}"}, event=event)

        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetRequestModel)

        # Process the request...

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)
```

#### **Pattern 2: Optional Event Body (GET operations or optional body)**

```python
# ✅ CORRECT - Optional body validation pattern
def handle_get_request(event):
    """Handle GET requests with optional body"""
    try:
        # Get body from event with default empty dict
        body = event.get('body', {})

        # If body exists, parse it safely
        if body:
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

        # Now body is always a dict (either parsed or empty)
        # Parse request model (works with both empty and populated body)
        request_model = parse(body, model=RequestModel)

        # Process the request...

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)
```

#### **Key Validation Rules:**

1. **Always check for body existence** when required using `event.get('body')`
2. **Use consistent error messages** for missing body, invalid JSON, and parsing errors
3. **Handle both string and dict body types** safely
4. **Always use try/catch blocks** around JSON parsing
5. **Log exceptions** with appropriate detail level
6. **Return proper HTTP status codes** (400 for validation errors)
7. **Use Pydantic parse()** for model validation after body parsing
8. **Validate required fields** explicitly when needed before Pydantic parsing
9. **Ensure body is always a dict** before passing to Pydantic models
10. **Follow the same error handling pattern** across all handlers

#### **Common Error Messages:**

```python
# Standard error messages to use consistently
"Request body is required"
"Invalid JSON in request body"
"Request body cannot be parsed"
"Missing required field: {field_name}"
```

### **Resource Name and Environment Variable Loading Pattern**

```python
# Standard resource name resolution with environment variable loading
from backend.common.resourceNames import get_table_name, get_bucket_name, ResourceKeys

try:
    # Resolve DynamoDB table names from SSM Parameter Store (with env var overrides)
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    auxiliary_bucket = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)

    # Optional resource names -- catch KeyError if not registered
    try:
        optional_table_name = get_table_name(ResourceKeys.OPTIONAL_TABLE)
    except KeyError:
        optional_table_name = None

    # Handler-specific env vars (direct from os.environ)
    optional_setting = os.environ.get("OPTIONAL_SETTING", "default_value")
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

# Initialize resources using resolved names
required_table = dynamodb.Table(required_table_name)
optional_table = dynamodb.Table(optional_table_name) if optional_table_name else None
```

**Resolution order:** `get_table_name(ResourceKeys.*)` first checks for legacy environment variable overrides (e.g., `REQUIRED_STORAGE_TABLE_NAME`), then consults a 60-minute in-module cache, then fetches all resource name parameters from SSM via one paginated GetParametersByPath call. This allows pytest tests and local utilities to inject names directly as environment variables while deployed handlers use SSM.

**Pipeline handlers** in `backendPipelines/` continue to use legacy environment variables and do not call `get_table_name()`.

### **AWS Client Configuration Pattern**

```python
# Standard AWS client configuration with retry
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
sns = boto3.client('sns', config=retry_config)
```

### **Authorization Check Pattern**

```python
# Standard authorization check pattern (single resource)
if resource:
    resource.update({"object__type": "[objectType]"})
    # Fail closed: an empty token list means no authenticated identity, so deny rather
    # than fall through. Never gate the enforce inside `if len(tokens) > 0` without an
    # else that denies — that silently skips authorization when tokens are empty.
    # (List-filtering handlers are the exception: they append only when enforce passes,
    # so an empty token list yields an empty result set, which is already fail-closed.)
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforce(resource, "[ACTION]"):
        return authorization_error()
```

### **Response Model Conversion Pattern**

```python
# Standard response model conversion with fallback
try:
    response_model = [Domain]ResponseModel(**resource)
    return success(body=response_model.dict())
except ValidationError as v:
    logger.exception(f"Error converting to response model: {v}")
    return success(body={"message": resource})
```

### **Pagination Handling Pattern**

```python
# Standard pagination handling
try:
    request_model = parse(query_parameters, model=[Domain]ListRequestModel)
    query_params = {
        'maxItems': request_model.maxItems,
        'pageSize': request_model.pageSize,
        'startingToken': request_model.startingToken
    }
except ValidationError as v:
    logger.exception(f"Validation error in query parameters: {v}")
    validate_pagination_info(query_parameters)
    query_params = query_parameters
```

### **DynamoDB Query Pattern (for API responses)**

```python
# Standard DynamoDB query with proper pagination using LastEvaluatedKey
# NOTE: This pattern is for main API query results. For internal data fetching to construct
# larger query sets, use the regular paginator as larger datasets are required.

# Build query parameters
query_params_dict = {
    'TableName': table_name,
    'KeyConditionExpression': 'partitionKey = :pkValue',
    'ExpressionAttributeValues': {
        ':pkValue': {'S': partition_value}
    },
    'ScanIndexForward': False,
    'Limit': int(query_params['pageSize'])
}

# Add ExclusiveStartKey if startingToken provided (decode base64)
if query_params.get('startingToken'):
    try:
        decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
        query_params_dict['ExclusiveStartKey'] = json.loads(decoded_token)
    except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
        logger.exception(f"Invalid startingToken format: {e}")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

# Single query call with pagination
response = dynamodb_client.query(**query_params_dict)

# Process items with authorization filtering
authorized_items = []
deserializer = TypeDeserializer()
for item in response.get('Items', []):
    # Deserialize the item
    deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}

    # Add object type for Casbin enforcement
    deserialized_item.update({"object__type": "[objectType]"})

    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(deserialized_item, "GET"):
            authorized_items.append(deserialized_item)

# Build response with nextToken
result = {"Items": authorized_items}

# Return LastEvaluatedKey as nextToken if present (base64 encoded)
if 'LastEvaluatedKey' in response:
    json_str = json.dumps(response['LastEvaluatedKey'])
    result["NextToken"] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
```

### **DynamoDB Scan Pattern (for API responses)**

```python
# Standard DynamoDB scan with proper pagination using LastEvaluatedKey
# NOTE: This pattern is for main API scan results. For internal data fetching to construct
# larger query sets, use the regular paginator as larger datasets are required.

# Build scan parameters
scan_params = {
    'TableName': table_name,
    'Limit': int(query_params['pageSize'])
}

# Add filter if needed
if filter_expression:
    scan_params['ScanFilter'] = filter_expression

# Add ExclusiveStartKey if startingToken provided (decode base64)
if query_params.get('startingToken'):
    try:
        decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
        scan_params['ExclusiveStartKey'] = json.loads(decoded_token)
    except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
        logger.exception(f"Invalid startingToken format: {e}")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

# Single scan call with pagination
response = dynamodb_client.scan(**scan_params)

# Process items with authorization filtering
authorized_items = []
deserializer = TypeDeserializer()
for item in response.get('Items', []):
    # Deserialize the item
    deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}

    # Add object type for Casbin enforcement
    deserialized_item.update({"object__type": "[objectType]"})

    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(deserialized_item, "GET"):
            authorized_items.append(deserialized_item)

# Build response with nextToken
result = {"Items": authorized_items}

# Return LastEvaluatedKey as nextToken if present (base64 encoded)
if 'LastEvaluatedKey' in response:
    json_str = json.dumps(response['LastEvaluatedKey'])
    result["NextToken"] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
```

### **Internal Data Fetching Pattern (for constructing larger datasets)**

```python
# Pattern for internal data fetching where complete datasets are needed
# Use this when you need to fetch ALL items to construct response data, not for API pagination
# Examples: Getting bucket details for each database, fetching related metadata, etc.

# For Query operations - fetch all items
paginator = dynamodb.meta.client.get_paginator('query')
page_iterator = paginator.paginate(
    TableName=table_name,
    KeyConditionExpression=Key('partitionKey').eq(partition_value),
    ScanIndexForward=False
).build_full_result()

all_items = []
for item in page_iterator.get('Items', []):
    all_items.append(item)

# For Scan operations - fetch all items
paginator = dynamodb_client.get_paginator('scan')
page_iterator = paginator.paginate(
    TableName=table_name,
    ScanFilter=filter_expression
).build_full_result()

all_items = []
for item in page_iterator.get('Items', []):
    deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
    all_items.append(deserialized_item)

# For S3 operations - fetch all objects under prefix
paginator = s3_client.get_paginator('list_objects_v2')
all_objects = []
for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
    if 'Contents' in page:
        for obj in page['Contents']:
            all_objects.append(obj)

# IMPORTANT: Only use this pattern when you genuinely need ALL items for internal processing.
# For API responses that return lists to users, always use the LastEvaluatedKey pattern above.
```

## 🔍 **Code Review Checklist**

### **Backend Handler Compliance**

-   [ ] Follows `assetService.py` gold standard patterns
-   [ ] Uses AWS Lambda Powertools for logging and parsing
-   [ ] Includes comprehensive error handling with proper exceptions
-   [ ] Implements Casbin authorization enforcement
-   [ ] Uses Pydantic models for request/response validation
-   [ ] Configures AWS clients with retry configuration
-   [ ] Resolves resource names via `get_table_name(ResourceKeys.*)` in module-level `try/except`
-   [ ] Separates business logic from request handling
-   [ ] Includes proper logging with structured messages

### **CDK Infrastructure Compliance**

-   [ ] Follows `assetFunctions.ts` patterns for lambda builders
-   [ ] Updates `storageBuilder-nestedStack.ts` for new resources
-   [ ] Registers routes in `apiBuilder2-nestedStack.ts` (or `apiBuilder` for a shared function instance)
-   [ ] Configures proper IAM permissions
-   [ ] Includes KMS key permissions
-   [ ] Configures VPC/subnet based on config flags
-   [ ] Uses consistent naming conventions
-   [ ] Applies CDK Nag suppressions appropriately

### **Integration Compliance**

-   [ ] Frontend service methods follow `APIService.js` patterns
-   [ ] CLI API client methods follow `api_client.py` patterns
-   [ ] Constants added to appropriate files
-   [ ] Exceptions added to exception hierarchy
-   [ ] Error handling consistent across all layers

### **Documentation Compliance**

-   [ ] `documentation/VAMS_API.yaml` updated with comprehensive schemas
-   [ ] `documentation/docusaurus-site/docs/concepts/permissions-model.md` updated with authorization mappings
-   [ ] `documentation/docusaurus-site/docs/developer/` updated with architecture information
-   [ ] `documentation/docusaurus-site/docs/api/` updated with API reference pages
-   [ ] `documentation/docusaurus-site/sidebars.ts` updated if new pages added
-   [ ] Code examples included in documentation
-   [ ] Error responses documented properly

## 🚀 **Development Commands**

### **Backend Development**

```bash
# Setup backend development environment
cd backend
python -m venv venv
# Windows PowerShell:
venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

pip install -r requirements-dev.txt

# Code quality checks
black backend/                    # Format code
flake8 backend/                   # Lint code
mypy backend/                     # Type checking
pytest                            # Run tests
pytest --cov=backend             # Run tests with coverage

# Run specific test files
pytest tests/handlers/[domain]/   # Test specific domain
pytest -v tests/handlers/[domain]/test_[handler].py  # Test specific handler
```

### **CDK Development**

```bash
# Setup CDK development environment
cd infra
npm install

# CDK commands
cdk diff                         # Show changes
cdk synth                        # Synthesize CloudFormation
cdk deploy --all                 # Deploy all stacks
cdk destroy --all                # Destroy all stacks

# Code quality checks
npm run lint                     # Lint TypeScript code
npm run test                     # Run CDK tests
```

### **Integration Testing**

```bash
# Test backend with local development
cd backend
USE_LOCAL_MOCKS=true python3 backend/localDev_api_server.py

# Test frontend integration
cd web
npm run start

# Test CLI integration
cd tools/VamsCLI
pip install -e ".[dev]"
vamscli --help
```

## 📚 **Detailed Implementation Guide**

### **Adding New API Domain**

#### **Step 1: Create Pydantic Models**

```python
# models/[domain].py
"""[Domain] API models for VAMS."""

from typing import Dict, List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="[Domain]Models")

# Add all request/response models following assetsV3.py patterns
```

#### **Step 2: Create Backend Handler**

```python
# handlers/[domain]/[handler].py
"""[Domain] service handler for VAMS API."""

import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from backend.common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.[domain] import [RequestModel], [ResponseModel]

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="[ServiceName]")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    # Resolve DynamoDB table names from SSM Parameter Store
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    required_bucket = get_bucket_name(ResourceKeys.REQUIRED_BUCKET)
    # Handler-specific env vars (direct from os.environ)
    presigned_url_timeout = os.environ.get("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

# Initialize resources
required_table = dynamodb.Table(required_table_name)

# Follow complete assetService.py patterns
```

#### **Step 3: Add Storage Resources**

```typescript
// infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts
// Add new table to interface and builder function
```

#### **Step 4: Create Lambda Builder**

```typescript
// infra/lib/lambdaBuilder/[domain]Functions.ts
// Follow assetFunctions.ts patterns
```

#### **Step 5: Register API Routes**

```typescript
// infra/lib/nestedStacks/apiLambda/apiBuilder2-nestedStack.ts
// Add route registrations using attachFunctionToApi (pass the cross-stack `registry`)
```

#### **Step 6: Add Frontend Integration**

```javascript
// web/src/services/APIService.js
// Add service methods following existing patterns. Check for other files that may already implement the API route but aren't in APIService.
```

#### **Step 7: Add CLI Integration**

```python
# tools/VamsCLI/vamscli/constants.py - Add API endpoints
# tools/VamsCLI/vamscli/utils/exceptions.py - Add exceptions
# tools/VamsCLI/vamscli/utils/api_client.py - Add API methods
# tools/VamsCLI/vamscli/commands/[domain].py - Add commands if needed
```

#### **Step 8: Update Documentation**

```yaml
# documentation/VAMS_API.yaml - Add comprehensive API documentation
```

```markdown
# documentation/docusaurus-site/docs/concepts/permissions-model.md - Add authorization mappings

# documentation/docusaurus-site/docs/api/ - Add/update API reference page

# documentation/docusaurus-site/docs/deployment/configuration-reference.md - Add new config options
```

### **Modifying Existing API**

#### **Step 1: Update Models**

-   Add new fields to existing Pydantic models
-   Update validation logic if needed
-   Maintain backward compatibility

#### **Step 2: Update Handler**

-   Add new business logic functions
-   Update request handlers for new functionality
-   Maintain existing error handling patterns

#### **Step 3: Update CDK**

-   Add new environment variables if needed
-   Update permissions if accessing new resources
-   Add new storage resources if required

#### **Step 4: Update Integration**

-   Update frontend service methods
-   Update CLI API client methods
-   Update constants and exceptions

#### **Step 5: Update Documentation**

-   Update API schemas in `documentation/VAMS_API.yaml`
-   Update permission mappings in `documentation/docusaurus-site/docs/concepts/permissions-model.md`
-   Update API reference pages in `documentation/docusaurus-site/docs/api/`
-   Update examples and usage information

## 🛠️ **Best Practices Summary**

### **Backend Development**

1. **Always** follow `assetService.py` gold standard patterns
2. **Always** use AWS Lambda Powertools for logging and parsing
3. **Always** implement comprehensive error handling
4. **Always** include Casbin authorization enforcement
5. **Always** use Pydantic models for validation
6. **Always** configure AWS clients with retry configuration
7. **Always** load environment variables with error handling
8. **Always** separate business logic from request handling
9. **Always** include proper structured logging
10. **Always** write comprehensive tests

### **CDK Development**

1. **Always** follow `assetFunctions.ts` patterns for lambda builders
2. **Always** update `storageBuilder-nestedStack.ts` for new resources
3. **Always** register routes in `apiBuilder2-nestedStack.ts` (or `apiBuilder` for a shared function instance)
4. **Always** configure proper IAM permissions
5. **Always** include KMS key permissions
6. **Always** configure VPC/subnet based on config flags
7. **Always** use consistent naming conventions
8. **Always** apply CDK Nag suppressions appropriately
9. **Always** include proper resource dependencies
10. **Always** test CDK synthesis and deployment

### **Integration Development**

1. **Always** update frontend service methods (check for where backend end-points are used)
2. **Always** update CLI API client methods
3. **Always** add constants to appropriate files
4. **Always** add exceptions to exception hierarchy
5. **Always** maintain consistent error handling
6. **Always** test integration points
7. **Always** update documentation
8. **Always** verify end-to-end functionality
9. **Always** maintain backward compatibility
10. **Always** follow existing patterns

### **Documentation Development**

1. **Always** update `VAMS_API.yaml` with comprehensive schemas
2. **Always** update Docusaurus permissions docs (`documentation/docusaurus-site/docs/concepts/permissions-model.md`) with authorization mappings
3. **Always** update Docusaurus developer docs (`documentation/docusaurus-site/docs/developer/`) with architecture changes
4. **Always** include code examples and usage information
5. **Always** document error responses properly
6. **Always** maintain consistency with existing documentation
7. **Always** verify all links and references work
8. **Always** include security requirements
9. **Always** document breaking changes clearly
10. **Always** update version information appropriately

## 🔧 **Troubleshooting Guide**

### **Common Backend Issues**

-   **Import Errors**: Ensure all imports follow the project structure
-   **Environment Variable Errors**: Check CDK environment variable configuration
-   **Authorization Failures**: Verify Casbin object-type and action mappings
-   **Validation Errors**: Check Pydantic model field definitions and validators
-   **AWS Client Errors**: Verify IAM permissions and retry configuration

### **Common CDK Issues**

-   **Permission Errors**: Check IAM role permissions and resource grants
-   **Environment Variable Issues**: Verify storage resources are passed correctly
-   **Route Registration Issues**: Check API Gateway route path and method configuration
-   **Resource Dependency Issues**: Verify resource dependencies and initialization order

### **Common Integration Issues**

-   **Frontend API Errors**: Check response format handling and error extraction
-   **CLI API Errors**: Check endpoint constants and exception handling
-   **Documentation Sync Issues**: Verify all documentation files are updated consistently

This workflow ensures that all VAMS backend API and CDK development follows established patterns and maintains consistency across the entire system ecosystem.
