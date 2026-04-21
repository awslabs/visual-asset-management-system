# Add VAMS API Endpoint

Scaffold a new VAMS backend API endpoint with all required files following the established gold standard patterns. This skill creates a complete, working endpoint with handler, models, CDK infrastructure, API routing, and tests.

## Instructions

You are scaffolding a new VAMS API endpoint. The VAMS system follows a strict layered architecture:

1. **Backend handler** (Python Lambda) - Business logic
2. **Pydantic models** - Request/response validation
3. **CDK Lambda builder** (TypeScript) - Infrastructure definition
4. **API route binding** - API Gateway integration
5. **Tests** - Unit tests for the handler

### Step 1: Gather Requirements

Ask the user for:

-   **Domain name**: The functional domain (e.g., `assets`, `pipelines`, `workflows`, `comments`, `tags`). This determines the folder structure.
-   **Handler name**: The specific handler file name in camelCase (e.g., `assetService`, `commentService`, `tagService`)
-   **Endpoint path(s)**: The API Gateway route path(s) (e.g., `/database/{databaseId}/myResource`, `/myResource/{resourceId}`)
-   **HTTP methods**: Which methods to support (GET, POST, PUT, DELETE)
-   **Description**: What the endpoint does
-   **DynamoDB tables needed**: Which existing storage tables it needs access to, or if new tables are needed
-   **Authorization**: What object type for Casbin enforcement (e.g., `asset`, `database`, `pipeline`, or a new type)

### Step 2: Create Backend Handler

Create `backend/backend/handlers/{domain}/{handlerName}.py` following the assetService.py pattern:

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
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
from models.{domain} import (
    # Import request/response models here
)

# Configure AWS clients with retry configuration
region = os.environ.get('AWS_REGION', 'us-east-1')
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="{HandlerName}")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables
try:
    # table_name = os.environ["TABLE_NAME"]
    pass
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
# table = dynamodb.Table(table_name)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for {description}"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        # Parse request
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


def handle_get_request(event):
    """Handle GET requests"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}

    try:
        # Validate path parameters
        # (valid, message) = validate({...})

        # Fetch data from DynamoDB

        # Check object-level authorization (Tier 2)
        # item.update({"object__type": "{objectType}"})
        # if len(claims_and_roles["tokens"]) > 0:
        #     casbin_enforcer = CasbinEnforcer(claims_and_roles)
        #     if not casbin_enforcer.enforce(item, "GET"):
        #         return authorization_error()

        # Return response
        return success(body={"message": "OK"})

    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)
```

**Key patterns to follow:**

-   Module-level setup: imports, retry config, boto3 clients, logger, env vars, table init
-   `lambda_handler` with `request_to_claims`, `enforceAPI` (Tier 1), method routing
-   Method handlers with path parameter validation using `validate()`, Pydantic parsing, business logic, object-level auth (Tier 2) using `CasbinEnforcer.enforce()`
-   Error handling: catch `ValidationError`, `VAMSGeneralErrorResponse`, generic `Exception`
-   Response helpers: `success()`, `validation_error()`, `general_error()`, `internal_error()`, `authorization_error()`

### Step 3: Create Pydantic Models

Create `backend/backend/models/{domain}.py` following assetsV3.py patterns:

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from customLogging.logger import safeLogger
from common.validators import validate, id_pattern, object_name_pattern
from typing import Dict, List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError

logger = safeLogger(service_name="{Domain}Models")

class CreateResourceRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a resource"""
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: Optional[str] = Field(None, max_length=256)

    @root_validator
    def validate_fields(cls, values):
        # Custom validation logic
        return values

class ResourceResponseModel(BaseModel, extra='ignore'):
    """Response model for resource data"""
    resourceId: str
    name: str
    description: Optional[str] = None
```

**Key patterns:**

-   Always use `extra='ignore'` on BaseModel
-   Use `Field()` with min_length, max_length, pattern validators
-   Use `@root_validator` for cross-field validation
-   Import validators from `common.validators`: `id_pattern`, `object_name_pattern`, `filename_pattern`, `relative_file_path_pattern`
-   Separate Request and Response models

### Step 4: Create Lambda Builder Function

Create or update `infra/lib/lambdaBuilder/{domain}Functions.ts` following assetFunctions.ts:

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
            // Add required table names from storageResources.dynamo
        },
    });

    // Grant DynamoDB permissions
    // storageResources.dynamo.tableStorageTable.grantReadWriteData(fun);

    // 4 required security helper calls
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

**Key patterns:**

-   Standard function signature: `(scope, lambdaCommonBaseLayer, storageResources, config, vpc, subnets)`
-   Code path: `path.join(__dirname, '../../../backend/backend')`
-   Handler convention: `handlers.{domain}.${name}.lambda_handler`
-   VPC conditional based on `config.app.useGlobalVpc`
-   **4 required security helper calls** at the end of every function:
    1. `kmsKeyLambdaPermissionAddToResourcePolicy`
    2. `setupSecurityAndLoggingEnvironmentAndPermissions`
    3. `globalLambdaEnvironmentsAndPermissions`
    4. `suppressCdkNagErrorsByGrantReadWrite`

### Step 5: Add API Route Binding

Update `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts`:

1. Add import for the new builder function at the top
2. Build the Lambda function in the constructor
3. Call `attachFunctionToApi()` for each route+method combination

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

// Attach to API routes
attachFunctionToApi(this, myFunction, {
    routePath: "/myResource",
    method: apigateway.HttpMethod.GET,
    api: api,
});
attachFunctionToApi(this, myFunction, {
    routePath: "/myResource/{resourceId}",
    method: apigateway.HttpMethod.GET,
    api: api,
});
```

### Step 6: Create Test File

Create `backend/tests/handlers/{domain}/test_{handlerName}.py`:

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
import json
import os
from unittest.mock import patch, MagicMock

# Set environment variables before importing handler
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
# os.environ["TABLE_NAME"] = "test-table"

class TestHandlerName:
    """Tests for {handlerName} Lambda handler"""

    def _make_event(self, method="GET", path="/myResource", path_params=None, body=None, query_params=None):
        """Helper to create API Gateway v2 event"""
        event = {
            "requestContext": {
                "http": {
                    "method": method,
                    "path": path,
                },
                "authorizer": {
                    "jwt": {
                        "claims": {
                            "sub": "test-user-id",
                            "cognito:groups": "test-group",
                        }
                    }
                }
            },
            "pathParameters": path_params or {},
            "queryStringParameters": query_params or {},
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
```

Also create `backend/tests/handlers/{domain}/__init__.py` if it does not exist.

### Step 7: Validate Cross-References

After creating all files, verify:

-   [ ] Handler imports match model file names and class names
-   [ ] CDK handler path matches Python module path: `handlers.{domain}.{handlerName}.lambda_handler`
-   [ ] Environment variable names in CDK match `os.environ` keys in handler
-   [ ] API route paths in `attachFunctionToApi` match what the handler expects
-   [ ] DynamoDB table grants in CDK match tables used in handler
-   [ ] Import statement in apiBuilder-nestedStack.ts matches the export in the lambdaBuilder file

## Workflow

1. Gather requirements from the user (or parse from $ARGUMENTS)
2. Check if a similar domain/handler already exists to avoid conflicts
3. Create all files in order: models -> handler -> CDK builder -> API route -> tests
4. Run a quick validation that all imports and references are consistent
5. Summarize what was created and what manual steps remain (e.g., adding new DynamoDB tables to storage stack)

## User Request

$ARGUMENTS
