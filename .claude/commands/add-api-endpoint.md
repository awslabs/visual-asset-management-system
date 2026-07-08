# Add VAMS API Endpoint

Scaffold a new VAMS backend API endpoint with all required files following the established gold standard patterns. This skill creates a complete, working endpoint with master route definition, handler, models, CDK infrastructure, API routing, tests, and documentation updates.

## Instructions

You are scaffolding a new VAMS API endpoint. Follow root `CLAUDE.md` "Pattern 1: Adding a New API Endpoint" — the authoritative checklist. The required layers are:

1. **Master route** (`backend/backend/common/apiRoutes.py`) - `ApiRoute` constant + category group
2. **Backend handler** (Python Lambda) - Business logic with two-tier Casbin auth
3. **Pydantic models** - Request/response validation (Pydantic v1)
4. **CDK Lambda builder** (TypeScript) - Infrastructure definition
5. **API route binding** - Route registry registration (prefer `apiBuilder2-nestedStack.ts`)
6. **Frontend service** (`web/src/services/APIService.ts`) - API call method (if exposed to web UI)
7. **CLI command** (`tools/VamsCLI/vamscli/`) - Endpoint constant + command (if applicable)
8. **Documentation** - `documentation/VAMS_API.yaml` AND `documentation/docusaurus-site/docs/api/{domain}.md` (both must be updated together)
9. **Tests** - Unit tests for the handler

### Step 1: Gather Requirements

Ask the user for:

-   **Domain name**: The functional domain (e.g., `assets`, `pipelines`, `workflows`, `comments`, `tags`). This determines the folder structure.
-   **Handler name**: The specific handler file name in camelCase (e.g., `assetService`, `commentService`, `tagService`)
-   **Endpoint path(s)**: The API Gateway route path(s) (e.g., `/database/{databaseId}/myResource`, `/myResource/{resourceId}`)
-   **HTTP methods**: Which methods to support (GET, POST, PUT, DELETE)
-   **Description**: What the endpoint does
-   **DynamoDB tables needed**: Which existing storage tables it needs access to, or if new tables are needed
-   **Authorization**: What object type for Casbin enforcement (e.g., `asset`, `database`, `pipeline`, or a new type)
-   **Exposure**: Should the endpoint be exposed in the web frontend and/or the VamsCLI?

### Step 2: Define the Master Route

Add the route to `backend/backend/common/apiRoutes.py` — the single source of truth for the backend API surface:

1. Define an `ApiRoute` constant with the path template, allowed methods, and category.
2. Add the constant to the appropriate category group array (e.g., `ASSET_ROUTES`, `AUTH_ROUTES`) so it is included in `ALL_API_ROUTES` and served by the `GET /auth/routes/api` listing.

```python
API_MY_RESOURCE = ApiRoute("/myResource/{resourceId}", (GET, PUT), "myDomain")

MY_DOMAIN_ROUTES: Tuple[ApiRoute, ...] = (API_MY_RESOURCE, ...)
```

A route missing from the group arrays is invisible to constraint authoring and the CLI. Route templates MUST match the routes attached in the CDK api builder stacks.

### Step 3: Create Backend Handler

Create `backend/backend/handlers/{domain}/{handlerName}.py` following the gold standard `backend/backend/handlers/assets/assetService.py` (see `backend/CLAUDE.md` for the full pattern and copy-paste template):

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.apiRoutes import API_MY_RESOURCE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.{domain} import (
    # Import request/response models here
)

# Configure AWS clients with retry configuration
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="{HandlerName}")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    from common.resourceNames import ResourceKeys, get_table_name
    my_table_name = get_table_name(ResourceKeys.MY_STORAGE_TABLE)
    # Handler-specific env vars (direct from os.environ) go here too
except Exception as e:
    logger.exception("Failed loading resource names and environment variables")
    raise e

# Initialize DynamoDB tables
my_table = dynamodb.Table(my_table_name)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for {description}"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)  # normalizes the REST event internally

    try:
        # Safe to read only AFTER request_to_claims (or normalize_event) has run
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        # Check API authorization (Tier 1)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Dispatch via the master route constants -- never hard-coded path fragments
        if API_MY_RESOURCE.matches(path):
            if method == 'GET':
                return handle_get_request(event)
            elif method == 'PUT':
                return handle_put_request(event)

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

**Key patterns to follow:**

-   Module-level setup: imports, retry config, boto3 clients, logger, resource name resolution, table init
-   **Resource names resolve via SSM**: `get_table_name(ResourceKeys.*)` / `get_bucket_name(ResourceKeys.*)` from `common.resourceNames` at module level in a try/except. Never `os.environ["TABLE_NAME"]` for resource names in non-pipeline handlers.
-   `lambda_handler` calls `request_to_claims(event)` FIRST — it normalizes the REST API (v1) event (injects `requestContext.http`, coerces null `pathParameters`/`queryStringParameters` to `{}`). If the handler must read the event before claims, call `normalize_event(event)` from `common.auth.apiEvent` as the first statement.
-   Dispatch on `ApiRoute.matches(path)` from `common/apiRoutes.py`, never `path.endswith(...)`.
-   Two-tier auth: `enforceAPI` (Tier 1) in `lambda_handler`, then `casbin_enforcer.enforce(event, item)` (Tier 2) in method handlers with `item['object__type']` set first.
-   Error handling: catch `ValidationError`, `VAMSGeneralErrorResponse`, generic `Exception`
-   Response helpers: `success()`, `validation_error()`, `general_error()`, `internal_error()`, `authorization_error()`
-   Never echo request input or internal details in client error messages — log specifics, return generic messages.

### Step 4: Create Pydantic Models

Create `backend/backend/models/{domain}.py` following `backend/backend/models/assetsV3.py` patterns:

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, List, Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from customLogging.logger import safeLogger
from common.validators import validate, id_pattern, object_name_pattern

logger = safeLogger(service_name="{Domain}Models")

class CreateResourceRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a resource"""
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: Optional[str] = Field(None, max_length=256)

    @root_validator
    def validate_fields(cls, values):
        # Use the validate() dispatcher for complex validation
        return values

class ResourceResponseModel(BaseModel, extra='ignore'):
    """Response model for resource data"""
    resourceId: str
    name: str
    description: Optional[str] = None
```

**Key patterns:**

-   **Pydantic v1 only** (1.10.13): `@root_validator`, `@validator`, never `model_validator`/`ConfigDict`
-   Import `BaseModel` from `aws_lambda_powertools.utilities.parser`, not from pydantic directly
-   Always use `extra='ignore'` on BaseModel
-   Use `Field()` with min_length, max_length, pattern validators
-   Import validators from `common.validators`: `id_pattern`, `object_name_pattern`, `filename_pattern`, `relative_file_path_pattern`
-   Separate Request and Response models

### Step 5: Create Lambda Builder Function

Create or update `infra/lib/lambdaBuilder/{domain}Functions.ts` following `assetFunctions.ts`:

```typescript
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    setupSecurityAndLoggingEnvironmentAndPermissions,
} from "../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";

export function buildMyFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "handlerName"; // Must match Python handler module name
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.{domain}.${name}.lambda_handler`,
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
            // Handler-specific env vars only -- resource names are resolved from SSM
            // (globalLambdaEnvironmentsAndPermissions injects VAMS_RESOURCE_PARAM_PREFIX)
        },
    });

    // Grant DynamoDB permissions
    // storageResources.dynamo.myStorageTable.grantReadWriteData(fun);

    // Required security helper calls
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope); // only if the function uses grantRead/grantReadWrite

    return fun;
}
```

**Key patterns:**

-   Standard function signature: `(scope, lambdaCommonBaseLayer, storageResources, config, vpc, subnets)`
-   Code path: `path.join(__dirname, '../../../backend/backend')`
-   Handler convention: `handlers.{domain}.${name}.lambda_handler`
-   VPC conditional based on `config.app.useGlobalVpc`
-   Do NOT inject table-name environment variables — non-pipeline handlers resolve resource names from SSM Parameter Store (`globalLambdaEnvironmentsAndPermissions` supplies the prefix + SSM grants)
-   **Required security helper calls** at the end of every function:
    1. `kmsKeyLambdaPermissionAddToResourcePolicy`
    2. `setupSecurityAndLoggingEnvironmentAndPermissions`
    3. `globalLambdaEnvironmentsAndPermissions`
    4. `suppressCdkNagLambda` (required on every authored Lambda)
    5. `suppressCdkNagErrorsByGrantReadWrite` (only if the function uses S3/table `grantRead`/`grantReadWrite`)

### Step 6: Add API Route Binding

**Prefer `infra/lib/nestedStacks/apiLambda/apiBuilder2-nestedStack.ts`** for new endpoints — the primary `apiBuilder-nestedStack.ts` is near the CloudFormation per-stack resource limit. Only place a function in `apiBuilder` if it must share a directly-referenced function instance defined there.

1. Add import for the new builder function at the top
2. Build the Lambda function in the constructor
3. Call `attachFunctionToApi()` for each route+method combination, passing the route `registry`

```typescript
// Import at top
import { buildMyFunction } from "../../lambdaBuilder/{domain}Functions";

// In constructor, build function
const myFunction = buildMyFunction(
    this,
    lambdaCommonBaseLayer,
    storageResources,
    config,
    vpc,
    subnets
);

// Register routes in the cross-stack route registry
attachFunctionToApi(this, myFunction, {
    routePath: "/myResource",
    method: apigateway.HttpMethod.GET,
    registry: registry,
});
attachFunctionToApi(this, myFunction, {
    routePath: "/myResource/{resourceId}",
    method: apigateway.HttpMethod.GET,
    registry: registry,
    // allowAnonymous: true, // only for routes served by the IP-only anonymous authorizer
});
```

The REST API builder renders all registered descriptors into a single OpenAPI spec and materializes them on the `SpecRestApi`. Route paths registered here MUST match the `ApiRoute` templates from Step 2.

### Step 7: Add Frontend Service Method (if web-exposed)

Add an API call method to `web/src/services/APIService.ts` following the existing apiClient call patterns in that file.

### Step 8: Add CLI Command (if applicable)

1. Define the endpoint path constant in `tools/VamsCLI/vamscli/constants.py` (Rule 7: never hardcode endpoint paths in command files or API client methods)
2. Add the command in `tools/VamsCLI/vamscli/commands/{group}.py` following `roleUserConstraints.py` patterns (Click decorators, profile support, `--json-output`, error handling)

### Step 9: Update Documentation (both sources)

API documentation lives in **two places that must be updated together**:

1. **`documentation/VAMS_API.yaml`** — add/update the path and its component schemas
2. **`documentation/docusaurus-site/docs/api/{domain}.md`** — add/update the human-readable endpoint reference (e.g. `api/auth.md` for `/auth/*`)

If a CLI command was added, also update the relevant `documentation/docusaurus-site/docs/cli/commands/{group}.md` page.

### Step 10: Create Test File

Create `backend/tests/handlers/{domain}/test_{handlerName}.py`:

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.mark.unit
class TestHandlerName:
    """Tests for {handlerName} Lambda handler"""

    def _make_event(self, method="GET", path="/myResource", path_params=None, body=None, query_params=None):
        """Helper to create API Gateway event"""
        event = {
            "requestContext": {
                "http": {
                    "method": method,
                    "path": path,
                },
            },
            "pathParameters": path_params or {},
            "queryStringParameters": query_params or {},
            "headers": {"authorization": "Bearer test-token"},
        }
        if body:
            event["body"] = json.dumps(body) if isinstance(body, dict) else body
        return event

    def test_get_returns_success(self):
        """Test basic GET returns 200"""
        # Implement test
        pass

    def test_unauthorized_returns_403(self):
        """Test unauthorized request returns 403"""
        # Implement test
        pass

    def test_rest_shaped_event_with_null_params(self):
        """Test REST API v1 event shape (top-level path/httpMethod, null pathParameters)"""
        # Cover the REST-shaped event including explicit null pathParameters/queryStringParameters
        pass
```

Also create `backend/tests/handlers/{domain}/__init__.py` if it does not exist. Include at least one test with the REST API (v1) event shape (top-level `path`/`httpMethod`, explicit `null` `pathParameters`) — v2-shaped-only tests miss event-normalization regressions.

### Step 11: Validate Cross-References

After creating all files, verify:

-   [ ] `ApiRoute` constant defined in `apiRoutes.py` AND added to a category group array
-   [ ] Route templates in `apiRoutes.py` match the paths registered via `attachFunctionToApi`
-   [ ] Handler dispatches via `ApiRoute.matches()`, not hard-coded path fragments
-   [ ] Handler imports match model file names and class names
-   [ ] CDK handler path matches Python module path: `handlers.{domain}.{handlerName}.lambda_handler`
-   [ ] Resource names resolved via `get_table_name(ResourceKeys.*)` — no table-name env vars in CDK or handler
-   [ ] DynamoDB table grants in CDK match tables used in handler
-   [ ] All required security helper calls present, including `suppressCdkNagLambda(fun)`
-   [ ] Import statement in the api builder stack matches the export in the lambdaBuilder file
-   [ ] `VAMS_API.yaml` AND `docs/api/{domain}.md` both updated
-   [ ] CLI endpoint constant in `constants.py` (if CLI-exposed)

## Workflow

1. Gather requirements from the user (or parse from $ARGUMENTS)
2. Check if a similar domain/handler already exists to avoid conflicts
3. Create all files in order: master route -> models -> handler -> CDK builder -> API route -> frontend/CLI -> docs -> tests
4. Run a quick validation that all imports and references are consistent
5. Summarize what was created and what manual steps remain (e.g., adding new DynamoDB tables to the storage stack — see root `CLAUDE.md` "Adding a New DynamoDB Table" for the three-way constants update)

## User Request

$ARGUMENTS
