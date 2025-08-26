# VAMS Backend + CDK Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending VAMS backend APIs and CDK infrastructure. Follow these rules to ensure consistency, quality, and maintainability across all backend and infrastructure implementations.

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
-   [ ] **Implement Error Handling**: Use comprehensive try/catch with proper exceptions
-   [ ] **Add Authorization**: Include Casbin enforcement with object-type checking
-   [ ] **Add Logging**: Use `safeLogger` for structured logging
-   [ ] **Add Environment Variables**: Load required environment variables with error handling
-   [ ] **Add AWS Clients**: Configure AWS clients with retry configuration
-   [ ] **Implement Business Logic**: Separate business logic from request handling
-   [ ] **Add Response Enhancement**: Include version info and bucket details where applicable

#### **Step 3: CDK Infrastructure**

-   [ ] **Update Storage Resources**: Add new DynamoDB tables/S3 buckets in `storageBuilder-nestedStack.ts`
-   [ ] **Create Lambda Builder**: Add lambda function builder in `lambdaBuilder/[domain]Functions.ts`
-   [ ] **Configure Environment Variables**: Pass storage resources to lambda environment
-   [ ] **Configure Permissions**: Grant appropriate DynamoDB/S3/SNS permissions
-   [ ] **Configure VPC**: Add VPC/subnet configuration based on config flags
-   [ ] **Add KMS Permissions**: Include KMS key permissions for encryption
-   [ ] **Add API Routes**: Register routes in `apiBuilder-nestedStack.ts`
-   [ ] **Follow Naming Conventions**: Use consistent naming patterns

#### **Step 4: API Gateway Integration**

-   [ ] **Add Route Definitions**: Use `attachFunctionToApi` for route registration
-   [ ] **Configure HTTP Methods**: Set appropriate HTTP methods for each endpoint
-   [ ] **Add Security**: Ensure Cognito authorizer is applied
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

-   [ ] **Update VAMS_API.yaml**: Add new endpoints, schemas, and responses
-   [ ] **Update DeveloperGuide.md**: Add architecture and usage information
-   [ ] **Update PermissionsGuide.md**: Add authorization mappings for new endpoints
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

# Load environment variables with error handling
try:
    required_table = os.environ["REQUIRED_TABLE_NAME"]
    required_bucket = os.environ["REQUIRED_BUCKET_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
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
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
```

### **Rule 2: Pydantic Models MUST Follow assetsV3.py Patterns**

```python
# ✅ CORRECT - Follow assetsV3.py patterns
from typing import Dict, List, Optional, Literal
from pydantic import Field, Extra
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern, object_name_pattern

class [Domain]RequestModel(BaseModel, extra=Extra.ignore):
    """Request model for [operation] [domain]"""
    requiredField: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=id_pattern)
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

class [Domain]ResponseModel(BaseModel, extra=Extra.ignore):
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
            REQUIRED_TABLE_NAME: storageResources.dynamo.requiredTable.tableName,
            REQUIRED_BUCKET_NAME: storageResources.s3.requiredBucket.bucketName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    // Grant permissions
    storageResources.dynamo.requiredTable.grantReadWriteData(fun);
    storageResources.s3.requiredBucket.grantReadWrite(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
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
            return validation_error(body={'message': message})

        # Get the resource
        resource = get_resource_details(path_parameters['databaseId'], path_parameters['assetId'])

        # Check authorization
        if resource:
            resource.update({"object__type": "[objectType]"})
            if len(claims_and_roles["tokens"]) > 0:
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
            return general_error(body={"message": "Resource not found"}, status_code=404)

    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()
```

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

### **Rule 6: API Routes MUST Be Registered in apiBuilder-nestedStack.ts**

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
attachFunctionToApi(scope, [domain]Service, {
    routePath: "/[domain]",
    method: apigwv2.HttpMethod.GET,
    api: api,
});

attachFunctionToApi(scope, [domain]Service, {
    routePath: "/[domain]/{[domain]Id}",
    method: apigwv2.HttpMethod.GET,
    api: api,
});

attachFunctionToApi(scope, [domain]Service, {
    routePath: "/[domain]",
    method: apigwv2.HttpMethod.POST,
    api: api,
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
-   **Authorization changes** → Update `PermissionsGuide.md` with new permission mappings
-   **Architecture changes** → Update `DeveloperGuide.md` with component information
-   **Major features** → Update main `README.md`

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

#### **PermissionsGuide.md Update Pattern:**

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
        'REQUIRED_TABLE_NAME': 'test-table',
        'REQUIRED_BUCKET_NAME': 'test-bucket',
        'AUTH_TABLE_NAME': 'test-auth-table',
        'USER_ROLES_TABLE_NAME': 'test-user-roles-table',
        'ROLES_TABLE_NAME': 'test-roles-table',
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

    def test_[operation]_validation_error(self, mock_environment, mock_claims_and_roles):
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

# Load environment variables with error handling
try:
    required_table_name = os.environ["REQUIRED_TABLE_NAME"]
    required_bucket_name = os.environ["REQUIRED_BUCKET_NAME"]
    auth_table_name = os.environ["AUTH_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
    roles_table_name = os.environ["ROLES_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize resources
required_table = dynamodb.Table(required_table_name)
auth_table = dynamodb.Table(auth_table_name)
user_roles_table = dynamodb.Table(user_roles_table_name)
roles_table = dynamodb.Table(roles_table_name)

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
        raise VAMSGeneralErrorResponse(f"Error retrieving [domain]: {str(e)}")

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
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce([domain]_data, "POST"):
                raise authorization_error()

        # Create the [domain]
        logger.info(f"Creating [domain] {[domain]_data['[domain]Id']}")

        # Add metadata
        now = datetime.utcnow().isoformat()
        username = claims_and_roles.get("username", "system")
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
        raise VAMSGeneralErrorResponse(f"Error creating [domain]: {str(e)}")

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
                    return validation_error(body={'message': "Invalid JSON in request body"})
            elif isinstance(body, dict):
                body = body
            else:
                logger.error("Request body is not a string or dict")
                return validation_error(body={'message': "Request body cannot be parsed"})

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
                return validation_error(body={'message': message})

            # Parse query parameters if needed
            try:
                request_model = parse(query_parameters, model=[Domain]RequestModel)
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                return validation_error(body={'message': str(v)})

            # Get the [domain]
            [domain] = get_[domain]_details(path_parameters['[domain]Id'])

            # Check if [domain] exists and user has permission
            if [domain]:
                [domain].update({"object__type": "[domain]"})
                if len(claims_and_roles["tokens"]) > 0:
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
                return general_error(body={"message": "[Domain] not found"}, status_code=404)

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
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()

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
            return validation_error(body={'message': "Request body is required"})

        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})

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
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error()

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
            return validation_error(body={'message': "Method not allowed"})

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
```

### **New Pydantic Models Template**

```python
"""[Domain] API models for VAMS."""

from typing import Dict, List, Optional, Literal
from pydantic import Field, Extra
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="[Domain]Models")

######################## [Domain] API Models ##########################

class [Domain]RequestModel(BaseModel, extra=Extra.ignore):
    """Request model for getting a [domain]"""
    includeDeleted: Optional[bool] = False

class [Domain]ListRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for listing [domain]s"""
    maxItems: Optional[int] = Field(default=1000, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=1000, ge=1, le=1000)
    startingToken: Optional[str] = None
    includeDeleted: Optional[bool] = False

class [Domain]CreateRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for creating a [domain]"""
    [domain]Id: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    [domain]Name: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
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

class [Domain]UpdateRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for updating a [domain]"""
    [domain]Name: Optional[str] = Field(None, min_length=1, max_length=256, pattern=object_name_pattern)
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

class [Domain]DeleteRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for deleting a [domain]"""
    confirmDelete: bool = Field(default=False)
    reason: Optional[str] = Field(None, max_length=256)

    @validator('confirmDelete')
    def validate_confirmation(cls, v):
        """Ensure confirmation is provided for deletion"""
        if not v:
            raise ValueError("confirmDelete must be true for deletion")
        return v

class [Domain]ResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for [domain] data"""
    [domain]Id: str
    [domain]Name: str
    description: str
    tags: Optional[List[str]] = []
    status: Optional[str] = "active"
    dateCreated: Optional[str] = None
    createdBy: Optional[str] = None

class [Domain]OperationResponseModel(BaseModel, extra=Extra.ignore):
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
            [DOMAIN]_STORAGE_TABLE_NAME: storageResources.dynamo.[domain]StorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    // Grant permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
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
            [DOMAIN]_STORAGE_TABLE_NAME: storageResources.dynamo.[domain]StorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    // Grant permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
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
        '[DOMAIN]_STORAGE_TABLE_NAME': 'test-[domain]-table',
        'AUTH_TABLE_NAME': 'test-auth-table',
        'USER_ROLES_TABLE_NAME': 'test-user-roles-table',
        'ROLES_TABLE_NAME': 'test-roles-table',
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

    def test_validation_error(self, mock_environment, mock_claims_and_roles):
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
-   [ ] API routes registered in apiBuilder-nestedStack.ts
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
-   [ ] PermissionsGuide.md updated with authorization mappings
-   [ ] DeveloperGuide.md updated if architecture changes
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
            return validation_error(body={'message': "Request body is required"})

        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})

        # Optional: Validate required fields in the request body
        required_fields = ['databaseId', 'assetName', 'description', 'isDistributable']
        for field in required_fields:
            if field not in body:
                return validation_error(body={'message': f"Missing required field: {field}"})

        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetRequestModel)

        # Process the request...

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error()
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
                    return validation_error(body={'message': "Invalid JSON in request body"})
            elif isinstance(body, dict):
                body = body
            else:
                logger.error("Request body is not a string or dict")
                return validation_error(body={'message': "Request body cannot be parsed"})

        # Now body is always a dict (either parsed or empty)
        # Parse request model (works with both empty and populated body)
        request_model = parse(body, model=RequestModel)

        # Process the request...

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()
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

### **Environment Variable Loading Pattern**

```python
# Standard environment variable loading with error handling
try:
    required_table_name = os.environ["REQUIRED_TABLE_NAME"]
    optional_setting = os.environ.get("OPTIONAL_SETTING", "default_value")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e
```

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
# Standard authorization check pattern
if resource:
    resource.update({"object__type": "[objectType]"})
    if len(claims_and_roles["tokens"]) > 0:
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

### **DynamoDB Query Pattern**

```python
# Standard DynamoDB query with pagination
paginator = dynamodb.meta.client.get_paginator('query')
page_iterator = paginator.paginate(
    TableName=table_name,
    KeyConditionExpression=Key('partitionKey').eq(partition_value),
    ScanIndexForward=False,
    PaginationConfig={
        'MaxItems': int(query_params['maxItems']),
        'PageSize': int(query_params['pageSize']),
        'StartingToken': query_params.get('startingToken')
    }
).build_full_result()

# Process items with authorization filtering
authorized_items = []
for item in page_iterator.get('Items', []):
    item.update({"object__type": "[objectType]"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(item, "GET"):
            authorized_items.append(item)

result = {"Items": authorized_items}
if 'NextToken' in page_iterator:
    result["NextToken"] = page_iterator['NextToken']
```

## 🔍 **Code Review Checklist**

### **Backend Handler Compliance**

-   [ ] Follows `assetService.py` gold standard patterns
-   [ ] Uses AWS Lambda Powertools for logging and parsing
-   [ ] Includes comprehensive error handling with proper exceptions
-   [ ] Implements Casbin authorization enforcement
-   [ ] Uses Pydantic models for request/response validation
-   [ ] Configures AWS clients with retry configuration
-   [ ] Loads environment variables with error handling
-   [ ] Separates business logic from request handling
-   [ ] Includes proper logging with structured messages

### **CDK Infrastructure Compliance**

-   [ ] Follows `assetFunctions.ts` patterns for lambda builders
-   [ ] Updates `storageBuilder-nestedStack.ts` for new resources
-   [ ] Registers routes in `apiBuilder-nestedStack.ts`
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

-   [ ] `VAMS_API.yaml` updated with comprehensive schemas
-   [ ] `PermissionsGuide.md` updated with authorization mappings
-   [ ] `DeveloperGuide.md` updated with architecture information
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
from pydantic import Field, Extra
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

# Follow complete assetService.py template above
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
// infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts
// Add route registrations using attachFunctionToApi
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
# VAMS_API.yaml - Add comprehensive API documentation
```

```markdown
# PermissionsGuide.md - Add authorization mappings
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

-   Update API schemas in VAMS_API.yaml
-   Update permission mappings in PermissionsGuide.md
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
3. **Always** register routes in `apiBuilder-nestedStack.ts`
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
2. **Always** update `PermissionsGuide.md` with authorization mappings
3. **Always** update `DeveloperGuide.md` with architecture changes
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
