# VAMS CDK Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending the VAMS CDK infrastructure. Follow these rules to ensure consistency, quality, and maintainability across all CDK implementations.

## 🏗️ **Architecture Overview**

### **CDK Project Structure Standards**

```
infra/
├── bin/
│   └── infra.ts              # CDK entry point with stack orchestration
├── config/
│   ├── config.ts             # Main configuration system with interfaces
│   ├── config.json           # Deployment-specific configuration
│   ├── saml-config.ts        # SAML authentication configuration
│   └── policy/               # IAM policy templates and configurations
├── common/
│   └── vamsAppFeatures.ts    # Feature switch constants and enums
├── lib/
│   ├── core-stack.ts         # Main orchestration stack
│   ├── cf-waf-stack.ts       # Web Application Firewall stack
│   ├── nestedStacks/         # Modular nested stack implementations
│   │   ├── auth/             # Authentication (Cognito/External OAuth)
│   │   ├── apiLambda/        # API Gateway, Lambda layers, handlers
│   │   ├── storage/          # S3, DynamoDB, KMS encryption
│   │   ├── staticWebApp/     # CloudFront/ALB web deployment
│   │   ├── searchAndIndexing/ # OpenSearch (serverless/provisioned)
│   │   ├── pipelines/        # Use-case specific processing pipelines
│   │   ├── vpc/              # VPC, subnets, endpoints
│   │   ├── locationService/  # AWS Location Services integration
│   │   └── featureEnabled/   # Dynamic feature switch management
│   ├── constructs/           # Reusable CDK constructs
│   ├── helper/               # Service helpers and utility functions
│   ├── aspects/              # CDK aspects for cross-cutting concerns
│   └── artefacts/            # Build artifacts and templates
├── test/                     # CDK unit and integration tests
└── gen/                      # Generated code and endpoints
```

## 📋 **Development Workflow Checklist**

### **Phase 1: Pre-Implementation**

-   [ ] **Analyze Requirements**: Understand the new feature/infrastructure requirements
-   [ ] **Check Architecture**: Ensure the new feature fits existing nested stack patterns
-   [ ] **Plan Configuration**: Identify new configuration options needed
-   [ ] **Review Dependencies**: Check cross-stack dependencies and resource sharing
-   [ ] **Feature Switch Planning**: Determine if feature switches are needed

### **Phase 2: Configuration Design**

#### **Step 1: Configuration Interface Design**

-   [ ] **Add Configuration Types**: Add new interfaces to `ConfigPublic` in `config.ts`
-   [ ] **Add Feature Constants**: Add feature switches to `vamsAppFeatures.ts`
-   [ ] **Add Validation Logic**: Include configuration validation in `getConfig()`
-   [ ] **Update Templates**: Update configuration templates for different environments

#### **Step 2: Service Helper Integration**

-   [ ] **Plan Resource Sharing**: Identify resources that need cross-stack access
-   [ ] **Update Service Helper**: Add new resource lookups to service helper
-   [ ] **Plan ARN Management**: Design how ARNs and endpoints will be shared
-   [ ] **SSM Parameter Strategy**: Plan SSM parameters for cross-stack references

### **Phase 3: Implementation**

#### **Step 3: Nested Stack Development**

-   [ ] **Choose Appropriate Stack**: Determine which nested stack to modify/create
-   [ ] **Follow Stack Patterns**: Use existing nested stack patterns and interfaces
-   [ ] **Implement Resource Logic**: Create AWS resources following VAMS patterns
-   [ ] **Add Cross-Stack Exports**: Export necessary resources for other stacks
-   [ ] **Handle Dependencies**: Properly manage stack dependencies

#### **Step 4: Core Stack Integration**

-   [ ] **Update Core Stack**: Integrate new nested stack into core orchestration
-   [ ] **Add Feature Logic**: Implement feature switch logic in core stack
-   [ ] **Configure Dependencies**: Set up proper stack dependency chains
-   [ ] **Add Outputs**: Create CloudFormation outputs for important resources

#### **Step 5: Security and Compliance**

-   [ ] **CDK Nag Compliance**: Ensure all resources pass CDK Nag security checks
-   [ ] **Add Suppressions**: Add justified suppressions with detailed reasons
-   [ ] **IAM Least Privilege**: Follow least privilege principles for IAM roles
-   [ ] **Encryption Standards**: Use KMS encryption where appropriate

### **Phase 4: Quality Assurance**

#### **Step 6: Testing**

-   [ ] **Write Unit Tests**: Create CDK unit tests for new constructs
-   [ ] **Test Configuration**: Test different configuration combinations
-   [ ] **Test Dependencies**: Verify stack dependency resolution
-   [ ] **Test Deployment**: Deploy to test environment and verify functionality
-   [ ] **Test Feature Switches**: Verify feature switches work correctly

#### **Step 7: Documentation**

-   [ ] **Update Configuration Guide**: Document new configuration options
-   [ ] **Update Architecture Docs**: Update architecture diagrams if needed
-   [ ] **Add Code Comments**: Include comprehensive inline documentation
-   [ ] **Update README**: Update deployment and configuration instructions

#### **Step 8: Validation**

-   [ ] **CDK Synth**: Ensure `cdk synth` completes without errors
-   [ ] **CDK Diff**: Review changes with `cdk diff` before deployment
-   [ ] **Security Review**: Complete security review of new resources
-   [ ] **Performance Impact**: Assess performance impact of changes

## 🔧 **Implementation Standards**

### **Configuration Management**

#### **Rule 1: All Features Must Be Configurable**

```typescript
// ✅ CORRECT - Add to ConfigPublic interface
export interface ConfigPublic {
    app: {
        newFeature: {
            enabled: boolean;
            optionalSetting: string;
            advancedOptions: {
                setting1: number;
                setting2: boolean;
            };
        };
    };
}

// ✅ CORRECT - Add validation in getConfig()
if (config.app.newFeature.enabled && !config.app.newFeature.optionalSetting) {
    throw new Error("Configuration Error: newFeature requires optionalSetting when enabled");
}

// ❌ INCORRECT - Don't hardcode feature enablement
const featureEnabled = true; // BAD - should be configurable
```

#### **Rule 2: Feature Switches Must Be Defined**

```typescript
// ✅ CORRECT - Add to vamsAppFeatures.ts
export enum VAMS_APP_FEATURES {
    GOVCLOUD = "GOVCLOUD",
    LOCATIONSERVICES = "LOCATIONSERVICES",
    NEW_FEATURE = "NEW_FEATURE", // Add new features here
}

// ✅ CORRECT - Use in core stack
if (props.config.app.newFeature.enabled) {
    this.enabledFeatures.push(VAMS_APP_FEATURES.NEW_FEATURE);
}
```

### **Nested Stack Implementation Standards**

#### **Rule 3: Follow Nested Stack Patterns**

```typescript
// ✅ CORRECT - Nested stack interface pattern
export interface NewFeatureNestedStackProps {
    config: Config.Config;
    storageResources: StorageResources;
    vpc?: ec2.IVpc;
    subnets?: ec2.ISubnet[];
}

// ✅ CORRECT - Nested stack implementation
export class NewFeatureNestedStack extends cdk.NestedStack {
    public readonly newFeatureResources: NewFeatureResources;

    constructor(scope: Construct, id: string, props: NewFeatureNestedStackProps) {
        super(scope, id);

        // Feature-specific resource creation
        this.newFeatureResources = this.createResources(props);
    }

    private createResources(props: NewFeatureNestedStackProps): NewFeatureResources {
        // Implementation details
    }
}
```

#### **Rule 4: Resource Sharing Through Interfaces**

```typescript
// ✅ CORRECT - Define resource interfaces
export interface NewFeatureResources {
    lambda: lambda.Function;
    table: dynamodb.Table;
    role: iam.Role;
}

// ✅ CORRECT - Export resources for cross-stack access
export class NewFeatureNestedStack extends cdk.NestedStack {
    public readonly newFeatureResources: NewFeatureResources;

    // Make resources available to other stacks
}
```

### **Service Helper Integration Standards**

#### **Rule 5: Use Service Helper for Cross-Stack Resources**

```typescript
// ✅ CORRECT - Add to service helper
export class ServiceHelper {
    public static getNewFeatureArn(): string {
        return this.getSSMParameter("/vams/newfeature/arn");
    }

    public static setNewFeatureArn(arn: string): void {
        this.setSSMParameter("/vams/newfeature/arn", arn);
    }
}

// ✅ CORRECT - Use in nested stacks
const newFeatureArn = ServiceHelper.getNewFeatureArn();
```

### **Security and Compliance Standards**

#### **Rule 6: CDK Nag Compliance Required**

```typescript
// ✅ CORRECT - Add justified suppressions
NagSuppressions.addResourceSuppressions(
    myResource,
    [
        {
            id: "AwsSolutions-IAM5",
            reason: "This role requires wildcard permissions for dynamic resource access in the VAMS asset management system. The scope is limited to VAMS-specific resources within the deployment account.",
        },
    ],
    true
);

// ❌ INCORRECT - Don't suppress without justification
NagSuppressions.addResourceSuppressions(myResource, [
    { id: "AwsSolutions-IAM5", reason: "Suppressed" }, // BAD - no justification
]);
```

#### **Rule 7: Encryption Standards**

```typescript
// ✅ CORRECT - Use KMS encryption from storage resources
const table = new dynamodb.Table(this, "MyTable", {
    encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
    encryptionKey: storageResources.encryption.kmsKey,
});

// ✅ CORRECT - S3 bucket encryption
const bucket = new s3.Bucket(this, "MyBucket", {
    encryption: s3.BucketEncryption.KMS,
    encryptionKey: storageResources.encryption.kmsKey,
});
```

### **Dependency Management Standards**

#### **Rule 8: Proper Stack Dependencies**

```typescript
// ✅ CORRECT - Explicit dependency management
export class CoreVAMSStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id, props);

        // Create storage first
        const storageStack = new StorageResourcesBuilderNestedStack(this, "Storage", config);

        // Create dependent stacks
        const apiStack = new ApiBuilderNestedStack(this, "Api", {
            storageResources: storageStack.storageResources,
            // other props
        });

        // Explicit dependency
        apiStack.addDependency(storageStack);
    }
}
```

## 🔧 **Backend Structure and Organization**

### **Backend Directory Structure (`/backend/`)**

All Lambda backend code (except pipelines) should be organized in the `/backend/` directory following the established domain-based structure:

```
backend/
├── backend/
│   ├── handlers/                 # Lambda function handlers organized by domain
│   │   ├── assets/              # Asset management handlers
│   │   ├── auth/                # Authentication handlers
│   │   ├── databases/           # Database management handlers
│   │   ├── metadata/            # Metadata operations handlers
│   │   ├── pipelines/           # Pipeline management handlers
│   │   ├── workflows/           # Workflow execution handlers
│   │   ├── search/              # Search and indexing handlers
│   │   ├── tags/                # Tag management handlers
│   │   └── [domain]/            # New domain-specific handlers
│   ├── customResources/         # CDK custom resource implementations
│   ├── common/                  # Shared utilities and helpers
│   ├── customConfigCommon/      # Organization-specific customizations
│   ├── customLogging/           # Logging utilities
│   └── models/                  # Data models and schemas
├── lambdaLayers/                # Reusable Lambda layers
├── tests/                       # Backend unit and integration tests
└── requirements.txt             # Python dependencies
```

### **Handler Organization Standards**

#### **Domain-Based Handler Structure**

```python
# ✅ CORRECT - Domain-based handler organization
backend/backend/handlers/assets/
├── __init__.py
├── createAsset.py              # POST /assets
├── assetService.py             # GET/PUT/DELETE /assets/{id}
├── assetFiles.py               # File operations
├── uploadFile.py               # File upload handling
├── downloadAsset.py            # Asset download
└── assetVersions.py            # Version management

backend/backend/handlers/auth/
├── __init__.py
├── loginProfile.py             # User profile management
├── authService.py              # Authentication operations
└── tokenValidation.py          # Token validation

# ❌ INCORRECT - Don't mix domains in single files
backend/backend/handlers/
├── allOperations.py            # BAD - mixed concerns
└── utilities.py                # BAD - unclear domain
```

#### **Handler Implementation Pattern**

```python
# ✅ CORRECT - Standard handler pattern
"""
Asset creation handler for VAMS.
Handles POST /assets endpoint.
"""

import json
import logging
from typing import Dict, Any
from backend.common.validators import validate_asset_data
from backend.common.exceptions import ValidationError, AssetError

logger = logging.getLogger(__name__)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for asset creation.

    Args:
        event: API Gateway event
        context: Lambda context

    Returns:
        API Gateway response
    """
    try:
        # Extract and validate request data
        body = json.loads(event.get('body', '{}'))
        asset_data = validate_asset_data(body)

        # Business logic implementation
        result = create_asset_logic(asset_data, event)

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(result)
        }

    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': str(e)})
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }

def create_asset_logic(asset_data: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """Business logic for asset creation."""
    # Implementation details
    pass
```

### **Custom Resources Organization**

Custom resources for CDK should be placed in `/backend/backend/customResources/`:

```python
# ✅ CORRECT - Custom resource implementation
"""
Custom resource for initializing VAMS configuration.
"""

import json
import boto3
from typing import Dict, Any

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Custom resource handler for CDK.

    Handles Create, Update, Delete operations for custom resources.
    """
    try:
        request_type = event['RequestType']

        if request_type == 'Create':
            return handle_create(event, context)
        elif request_type == 'Update':
            return handle_update(event, context)
        elif request_type == 'Delete':
            return handle_delete(event, context)

    except Exception as e:
        return send_response(event, context, 'FAILED', str(e))

def handle_create(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Handle resource creation."""
    # Implementation
    return send_response(event, context, 'SUCCESS')

def send_response(event: Dict[str, Any], context: Any, status: str, reason: str = '') -> Dict[str, Any]:
    """Send response to CloudFormation."""
    # Standard CloudFormation response implementation
    pass
```

## 🔧 **Pipeline Development Patterns**

### **Pipeline Directory Structure (`/backendPipelines/`)**

All pipeline backend code (including containers) should be organized in `/backendPipelines/` by use case:

```
backendPipelines/
├── conversion/                  # File conversion pipelines
│   └── 3dBasic/                # 3D basic conversion
│       ├── lambda/             # Lambda function code
│       ├── container/          # Container code (if needed)
│       └── README.md           # Pipeline documentation
├── genAi/                      # Generative AI pipelines
│   └── metadata3dLabeling/     # 3D metadata labeling
│       ├── lambda/
│       ├── container/
│       └── blender/            # Pipeline-specific tools
├── preview/                    # Preview generation pipelines
│   └── pcPotreeViewer/         # Point cloud preview
│       ├── lambda/
│       └── container/
├── multi/                      # Multi-service pipelines
│   ├── rapidPipeline/          # RapidPipeline integration
│   └── modelOps/               # ModelOps integration
└── [useCase]/                  # New use case pipelines
    ├── lambda/                 # Lambda handlers
    ├── container/              # Container code
    └── README.md               # Documentation
```

### **Pipeline Configuration Management**

#### **Configuration Structure for Pipelines**

```typescript
// ✅ CORRECT - Pipeline configuration in config.ts
export interface ConfigPublic {
    app: {
        pipelines: {
            useConversion3dBasic: {
                enabled: boolean;
            };
            usePreviewPcPotreeViewer: {
                enabled: boolean;
            };
            useGenAiMetadata3dLabeling: {
                enabled: boolean;
            };
            useRapidPipeline: {
                enabled: boolean;
                ecrContainerImageURI: string;
            };
            useModelOps: {
                enabled: boolean;
                ecrContainerImageURI: string;
            };
            useNewPipeline: {
                enabled: boolean;
                customSetting: string;
                advancedOptions: {
                    timeout: number;
                    memory: number;
                };
            };
        };
    };
}

// ✅ CORRECT - Pipeline validation in getConfig()
if (config.app.pipelines.useNewPipeline.enabled) {
    if (!config.app.pipelines.useNewPipeline.customSetting) {
        throw new Error("Configuration Error: useNewPipeline requires customSetting when enabled");
    }

    if (config.app.pipelines.useNewPipeline.advancedOptions.timeout < 60) {
        throw new Error("Configuration Error: Pipeline timeout must be at least 60 seconds");
    }
}
```

### **Pipeline Builder Integration**

#### **Adding New Pipeline to Pipeline Builder**

```typescript
// ✅ CORRECT - Pipeline builder integration pattern
export class PipelineBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionNames: string[] = [];

    constructor(parent: Construct, name: string, props: PipelineBuilderNestedStackProps) {
        super(parent, name);

        // Create pipeline network (security groups, subnets)
        const pipelineNetwork = new SecurityGroupGatewayPipelineConstruct(this, "PipelineNetwork", {
            config: props.config,
            vpc: props.vpc,
            vpceSecurityGroup: props.vpceSecurityGroup,
            privateSubnets: props.privateSubnets,
            isolatedSubnets: props.isolatedSubnets,
        });

        // Non-VPC Required Pipelines
        if (props.config.app.pipelines.useConversion3dBasic.enabled) {
            const conversion3dBasicPipelineNestedStack = new Conversion3dBasicNestedStack(
                this,
                "Conversion3dBasicNestedStack",
                {
                    config: props.config,
                    storageResources: props.storageResources,
                    vpc: props.vpc,
                    pipelineSubnets: pipelineNetwork.isolatedSubnets.pipeline,
                    pipelineSecurityGroups: [pipelineNetwork.securityGroups.pipeline],
                    lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                }
            );

            this.pipelineVamsLambdaFunctionNames.push(
                conversion3dBasicPipelineNestedStack.pipelineVamsLambdaFunctionName
            );
        }

        // VPC-Required Pipelines
        if (props.config.app.pipelines.useNewPipeline.enabled) {
            const newPipelineNestedStack = new NewPipelineNestedStack(
                this,
                "NewPipelineNestedStack",
                {
                    config: props.config,
                    storageResources: props.storageResources,
                    lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                    vpc: props.vpc,
                    pipelineSubnets: pipelineNetwork.isolatedSubnets.pipeline,
                    pipelineSecurityGroups: [pipelineNetwork.securityGroups.pipeline],
                }
            );

            this.pipelineVamsLambdaFunctionNames.push(
                newPipelineNestedStack.pipelineVamsLambdaFunctionName
            );
        }
    }
}
```

### **Pipeline Nested Stack Pattern**

#### **Pipeline Nested Stack Template**

```typescript
// ✅ CORRECT - Pipeline nested stack implementation
export interface NewPipelineNestedStackProps {
    config: Config.Config;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
}

export class NewPipelineNestedStack extends NestedStack {
    public readonly pipelineVamsLambdaFunctionName: string;

    constructor(scope: Construct, id: string, props: NewPipelineNestedStackProps) {
        super(scope, id);

        // Validate pipeline is enabled
        if (!props.config.app.pipelines.useNewPipeline.enabled) {
            throw new Error("NewPipeline is not enabled in configuration");
        }

        // Create pipeline Lambda function
        const pipelineLambda = this.createPipelineLambda(props);

        // Create container resources if needed
        if (props.config.app.pipelines.useNewPipeline.useContainer) {
            this.createContainerResources(props);
        }

        this.pipelineVamsLambdaFunctionName = pipelineLambda.functionName;
    }

    private createPipelineLambda(props: NewPipelineNestedStackProps): lambda.Function {
        const pipelineFunction = new lambda.Function(this, "NewPipelineFunction", {
            runtime: LAMBDA_PYTHON_RUNTIME,
            handler: "lambda_function.lambda_handler",
            code: lambda.Code.fromAsset("../backendPipelines/newPipeline/lambda"),
            layers: [props.lambdaCommonBaseLayer],
            timeout: Duration.minutes(15),
            memorySize: Config.LAMBDA_MEMORY_SIZE,

            // VPC Configuration for pipeline
            vpc: props.vpc,
            vpcSubnets: { subnets: props.pipelineSubnets },
            securityGroups: props.pipelineSecurityGroups,

            environment: {
                // Pipeline-specific environment variables
                PIPELINE_CONFIG: JSON.stringify(props.config.app.pipelines.useNewPipeline),
                S3_ASSET_AUXILIARY_BUCKET:
                    props.storageResources.s3.assetAuxiliaryBucket.bucketName,
                // Add other required environment variables
            },
        });

        // Grant necessary permissions
        props.storageResources.s3.assetAuxiliaryBucket.grantReadWrite(pipelineFunction);
        grantReadWritePermissionsToAllAssetBuckets(pipelineFunction);
        kmsKeyLambdaPermissionAddToResourcePolicy(
            pipelineFunction,
            props.storageResources.encryption.kmsKey
        );

        return pipelineFunction;
    }

    private createContainerResources(props: NewPipelineNestedStackProps): void {
        // Create ECS/Batch resources for container-based processing
        // Implementation depends on pipeline requirements
    }
}
```

### **Container Integration Patterns**

#### **Container-Based Pipeline Structure**

```
backendPipelines/newPipeline/
├── lambda/
│   ├── lambda_function.py      # Pipeline orchestration Lambda
│   └── requirements.txt        # Lambda dependencies
├── container/
│   ├── Dockerfile              # Container definition
│   ├── app.py                  # Container application
│   ├── requirements.txt        # Container dependencies
│   └── scripts/                # Processing scripts
└── README.md                   # Pipeline documentation
```

#### **Container Lambda Handler Pattern**

```python
# ✅ CORRECT - Container orchestration Lambda
"""
Pipeline Lambda that orchestrates container-based processing.
"""

import json
import boto3
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)
batch_client = boto3.client('batch')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Pipeline Lambda handler that submits jobs to AWS Batch.

    For container-based pipelines, this Lambda:
    1. Validates input parameters
    2. Submits job to AWS Batch
    3. Returns job information for tracking
    """
    try:
        # Extract pipeline parameters
        body = json.loads(event.get('body', '{}'))

        # Prepare Batch job parameters
        job_params = {
            'jobName': f"pipeline-job-{context.aws_request_id}",
            'jobQueue': 'pipeline-job-queue',
            'jobDefinition': 'pipeline-job-definition',
            'parameters': {
                'inputS3Path': body.get('inputS3AssetFilePath'),
                'outputS3Path': body.get('outputS3AssetFilesPath'),
                'pipelineConfig': json.dumps(body.get('inputParameters', {}))
            }
        }

        # Submit job to Batch
        response = batch_client.submit_job(**job_params)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'jobId': response['jobId'],
                'jobName': response['jobName'],
                'status': 'SUBMITTED'
            })
        }

    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
```

### **Pipeline Best Practices**

#### **Pipeline Development Rules**

1. **Use Case Organization**: Group pipeline code by use case in `/backendPipelines/`
2. **Configuration Driven**: All pipelines must be configurable via `config.ts`
3. **VPC Awareness**: Distinguish between VPC-required and optional pipelines
4. **Container Separation**: Keep container code separate from Lambda orchestration
5. **Error Handling**: Implement comprehensive error handling and logging
6. **Resource Cleanup**: Ensure proper cleanup of temporary resources

#### **Pipeline Configuration Rules**

1. **Enable/Disable Flags**: Every pipeline must have an `enabled` boolean flag
2. **Validation Required**: Add configuration validation in `getConfig()`
3. **Environment Specific**: Support different configurations per environment
4. **Container URIs**: External container pipelines must specify ECR URIs
5. **Resource Limits**: Define timeout, memory, and other resource limits

#### **Pipeline Security Rules**

1. **Least Privilege**: Grant only necessary permissions to pipeline functions
2. **VPC Isolation**: Use isolated subnets for pipeline processing
3. **Encryption**: Use KMS encryption for all pipeline data
4. **Network Security**: Use dedicated security groups for pipeline resources
5. **Container Security**: Scan container images for vulnerabilities

## 🔧 **Lambda Builder and Constructs Patterns**

### **Lambda Builder Pattern**

The VAMS project uses a sophisticated lambda builder pattern to organize Lambda functions by domain. Each domain has its own builder file in `infra/lib/lambdaBuilder/` that contains multiple related Lambda functions with consistent patterns for permissions, environment variables, and configuration.

#### **Lambda Builder Architecture**

```
infra/lib/lambdaBuilder/
├── assetFunctions.ts         # Asset management functions
├── authFunctions.ts          # Authentication functions
├── databaseFunctions.ts      # Database management functions
├── metadataFunctions.ts      # Metadata operations
├── pipelineFunctions.ts      # Pipeline execution functions
├── workflowFunctions.ts      # Workflow management functions
└── [domain]Functions.ts      # Domain-specific function groups
```

#### **Lambda Builder Function Pattern**

```typescript
// ✅ CORRECT - Lambda builder function pattern
export function build[FunctionName]Function(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "[functionName]";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,

        // VPC Configuration Pattern
        vpc: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
            ? vpc : undefined,
        vpcSubnets: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
            ? { subnets: subnets } : undefined,

        // Environment Variables Pattern
        environment: {
            // DynamoDB Table Names
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,

            // S3 Bucket Names
            S3_ASSET_AUXILIARY_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,

            // Authentication Tables
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,

            // Configuration Values
            PRESIGNED_URL_TIMEOUT_SECONDS: config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });

    // Permissions Pattern - DynamoDB
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Permissions Pattern - S3
    grantReadWritePermissionsToAllAssetBuckets(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);

    // Permissions Pattern - KMS
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    // Global Permissions and Environment
    globalLambdaEnvironmentsAndPermissions(fun, config);

    // CDK Nag Suppressions
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

#### **Permission Helper Functions**

```typescript
// ✅ CORRECT - Use permission helper functions from security.ts

// Grant read permissions to all asset buckets
grantReadPermissionsToAllAssetBuckets(lambdaFunction);

// Grant read/write permissions to all asset buckets
grantReadWritePermissionsToAllAssetBuckets(lambdaFunction);

// Add KMS permissions for encryption/decryption
kmsKeyLambdaPermissionAddToResourcePolicy(lambdaFunction, storageResources.encryption.kmsKey);

// Add global environment variables and permissions
globalLambdaEnvironmentsAndPermissions(lambdaFunction, config);

// Suppress CDK Nag errors for S3 permissions
suppressCdkNagErrorsByGrantReadWrite(scope);
```

#### **Lambda Function Dependencies Pattern**

```typescript
// ✅ CORRECT - Lambda function dependencies
export function buildAssetServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    sendEmailFunction: lambda.Function, // Dependency on another Lambda
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const fun = new lambda.Function(scope, "assetService", {
        // ... function configuration
        environment: {
            // Reference other Lambda functions
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
            // ... other environment variables
        },
    });

    // Grant invoke permissions to dependent functions
    sendEmailFunction.grantInvoke(fun);

    return fun;
}
```

### **Constructs Pattern**

VAMS uses CDK constructs to encapsulate reusable infrastructure patterns. Constructs provide a higher-level abstraction for complex AWS resources.

#### **Construct Structure Pattern**

```typescript
// ✅ CORRECT - Construct interface pattern
export interface [ConstructName]Props extends cdk.StackProps {
    readonly config: Config.Config;
    readonly storageResources?: storageResources;
    readonly customProperty?: string;
}

// ✅ CORRECT - Construct implementation pattern
export class [ConstructName]Construct extends Construct {
    public readonly [outputResource]: [ResourceType];

    constructor(parent: Construct, name: string, props: [ConstructName]Props) {
        super(parent, name);

        // Merge with default properties
        const mergedProps = { ...defaultProps, ...props };

        // Create resources
        this.[outputResource] = this.createResources(mergedProps);

        // Add CDK Nag suppressions if needed
        this.addNagSuppressions();
    }

    private createResources(props: [ConstructName]Props): [ResourceType] {
        // Resource creation logic
        return resource;
    }

    private addNagSuppressions(): void {
        // Add justified suppressions
        NagSuppressions.addResourceSuppressions(
            this.[outputResource],
            [
                {
                    id: "AwsSolutions-[RuleId]",
                    reason: "Detailed justification for why this suppression is needed in the VAMS context.",
                },
            ],
            true
        );
    }
}
```

#### **WAF Construct Example**

```typescript
// ✅ CORRECT - Real VAMS construct example
export class Wafv2BasicConstruct extends Construct {
    public webacl: wafv2.CfnWebACL;

    constructor(parent: Construct, name: string, props: Wafv2BasicConstructProps) {
        super(parent, name);

        // Merge with defaults
        props = { ...defaultProps, ...props };

        // Validate scope and region
        const wafScopeString = props.wafScope!.toString();

        // Create WAF WebACL
        const webacl = new wafv2.CfnWebACL(this, "webacl", {
            description: "Basic WAF for VAMS",
            defaultAction: { allow: {} },
            rules: props.rules,
            scope: wafScopeString,
            visibilityConfig: {
                cloudWatchMetricsEnabled: true,
                metricName: "WAFACLGlobal",
                sampledRequestsEnabled: true,
            },
        });

        this.webacl = webacl;
    }
}
```

### **Security Helper Integration**

#### **KMS Key Permissions Pattern**

```typescript
// ✅ CORRECT - KMS key permissions for Lambda functions
export function kmsKeyLambdaPermissionAddToResourcePolicy(
    lambdaFunction: lambda.IFunction,
    kmsKey?: kms.IKey
) {
    if (kmsKey) {
        lambdaFunction.addToRolePolicy(kmsKeyPolicyStatementGenerator(kmsKey));
    }
}

// ✅ CORRECT - KMS policy statement generation
export function kmsKeyPolicyStatementGenerator(kmsKey?: kms.IKey): iam.PolicyStatement {
    return new iam.PolicyStatement({
        actions: [
            "kms:Decrypt",
            "kms:DescribeKey",
            "kms:Encrypt",
            "kms:GenerateDataKey*",
            "kms:ReEncrypt*",
            "kms:ListKeys",
            "kms:CreateGrant",
            "kms:ListAliases",
        ],
        effect: iam.Effect.ALLOW,
        resources: [kmsKey.keyArn],
    });
}
```

#### **S3 Bucket Security Pattern**

```typescript
// ✅ CORRECT - S3 bucket security policies
export function requireTLSAndAdditionalPolicyAddToResourcePolicy(
    bucket: s3.IBucket,
    config: Config.Config
) {
    // Require TLS for all S3 operations
    bucket.addToResourcePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.DENY,
            principals: [new iam.AnyPrincipal()],
            actions: ["s3:*"],
            resources: [`${bucket.bucketArn}/*`, bucket.bucketArn],
            conditions: {
                Bool: { "aws:SecureTransport": "false" },
            },
        })
    );

    // Add additional custom policies from configuration
    if (config.s3AdditionalBucketPolicyJSON) {
        const policyStatementJSON = config.s3AdditionalBucketPolicyJSON;
        policyStatementJSON.Resource = [`${bucket.bucketArn}/*`, bucket.bucketArn];
        bucket.addToResourcePolicy(iam.PolicyStatement.fromJson(policyStatementJSON));
    }
}
```

#### **Content Security Policy Generation**

```typescript
// ✅ CORRECT - Dynamic CSP generation based on configuration
export function generateContentSecurityPolicy(
    storageResources: storageResources,
    authenticationDomain: string,
    apiUrl: string,
    config: Config.Config
): string {
    const connectSrc = ["'self'", "blob:", authenticationDomain, `https://${apiUrl}`];
    const scriptSrc = ["'self'", "blob:", authenticationDomain];

    // Add Cognito endpoints if enabled
    if (config.app.authProvider.useCognito.enabled) {
        connectSrc.push(`https://${Service("COGNITO_IDP").Endpoint}/`);
        scriptSrc.push(`https://${Service("COGNITO_IDP").Endpoint}/`);
    }

    // Add unsafe-eval if explicitly enabled
    if (config.app.webUi.allowUnsafeEvalFeatures) {
        scriptSrc.push(`'unsafe-eval'`);
    }

    // Add Location Services if enabled
    if (config.app.useLocationService.enabled) {
        connectSrc.push(`https://maps.${Service("GEO").Endpoint}/`);
    }

    return `default-src 'none'; connect-src ${connectSrc.join(" ")}; script-src ${scriptSrc.join(
        " "
    )}; ...`;
}
```

### **Lambda Builder Integration in Nested Stacks**

#### **Using Lambda Builders in Nested Stacks**

```typescript
// ✅ CORRECT - Integration pattern in nested stacks
export class ApiBuilderNestedStack extends cdk.NestedStack {
    constructor(scope: Construct, id: string, props: ApiBuilderNestedStackProps) {
        super(scope, id);

        // Build domain-specific Lambda functions using builders
        const createAssetFunction = buildCreateAssetFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.subnets
        );

        const assetServiceFunction = buildAssetServiceFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            sendEmailFunction, // Pass dependencies
            props.config,
            props.vpc,
            props.subnets
        );

        // Create API Gateway integrations
        const createAssetIntegration = new apigatewayv2.HttpLambdaIntegration(
            "CreateAssetIntegration",
            createAssetFunction
        );

        // Add routes to API Gateway
        props.apiGatewayV2.addRoutes({
            path: "/assets",
            methods: [apigatewayv2.HttpMethod.POST],
            integration: createAssetIntegration,
        });
    }
}
```

### **Best Practices for Lambda Builders and Constructs**

#### **Lambda Builder Rules**

1. **Domain Organization**: Group related Lambda functions in domain-specific builder files
2. **Consistent Patterns**: Use consistent patterns for environment variables, permissions, and VPC configuration
3. **Permission Helpers**: Use security helper functions for common permission patterns
4. **Dependency Injection**: Pass dependencies as parameters rather than creating them inside builders
5. **Configuration Driven**: Use configuration to control VPC, timeout, and memory settings

#### **Construct Rules**

1. **Single Responsibility**: Each construct should encapsulate a single logical unit of infrastructure
2. **Configurable**: Make constructs configurable through props interfaces
3. **Reusable**: Design constructs to be reusable across different contexts
4. **Default Props**: Provide sensible defaults while allowing customization
5. **Output Resources**: Expose created resources through public readonly properties

#### **Security Rules**

1. **Least Privilege**: Grant only the minimum permissions required
2. **KMS Integration**: Always use KMS encryption for sensitive resources
3. **TLS Enforcement**: Require TLS for all S3 and API communications
4. **CDK Nag Compliance**: Add justified suppressions for security rules
5. **Configuration Driven**: Use configuration to control security settings

## 🔐 **Custom Authorizer Pattern**

### **VAMS Custom Authorizer Standard**

VAMS uses a unified custom Lambda authorizer pattern for all API Gateway endpoints. This pattern replaces built-in CDK authorizers and provides enhanced security features.

#### **Custom Authorizer Architecture**

```
infra/lib/lambdaBuilder/authFunctions.ts
├── buildApiGatewayAuthorizerHttpFunction()     # HTTP API authorizer
└── buildApiGatewayAuthorizerWebsocketFunction() # WebSocket API authorizer

backend/backend/handlers/auth/
├── apiGatewayAuthorizerHttp.py      # HTTP authorizer implementation
└── apiGatewayAuthorizerWebsocket.py # WebSocket authorizer implementation

infra/config/config.ts
└── CUSTOM_AUTHORIZER_IGNORED_PATHS  # Paths that bypass authorization
```

#### **Custom Authorizer Features**

1. **Unified Authentication**: Supports both Cognito and External OAuth IDP
2. **IP Range Restrictions**: Optional IP-based access control
3. **Path-Based Bypass**: Configurable paths that skip authorization
4. **Token Caching**: Public key caching for performance optimization
5. **Comprehensive Logging**: AWS Lambda Powertools integration

#### **Configuration Pattern**

```typescript
// ✅ CORRECT - Custom authorizer configuration
export interface ConfigPublic {
    app: {
        authProvider: {
            authorizerOptions: {
                allowedIpRanges: string[][]; // [["min_ip", "max_ip"], ...]
            };
            useCognito: {
                enabled: boolean;
                // ... other Cognito settings
            };
            useExternalOAuthIdp: {
                enabled: boolean;
                // ... other External IDP settings
            };
        };
    };
}

// ✅ CORRECT - IP range validation in getConfig()
if (config.app.authProvider.authorizerOptions.allowedIpRanges) {
    for (let i = 0; i < config.app.authProvider.authorizerOptions.allowedIpRanges.length; i++) {
        const range = config.app.authProvider.authorizerOptions.allowedIpRanges[i];
        if (!Array.isArray(range) || range.length !== 2) {
            throw new Error(
                `Configuration Error: IP range at index ${i} must be an array of exactly 2 IP addresses [min, max]`
            );
        }
    }
}
```

#### **Lambda Builder Pattern for Authorizers**

```typescript
// ✅ CORRECT - Custom authorizer builder pattern
export function buildApiGatewayAuthorizerHttpFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "apiGatewayAuthorizerHttp";

    // Determine auth mode based on configuration
    const authMode = config.app.authProvider.useCognito.enabled
        ? "cognito"
        : config.app.authProvider.useExternalOAuthIdp.enabled
        ? "external"
        : "cognito";

    // Build environment variables
    const environment: { [key: string]: string } = {
        AUTH_MODE: authMode,
        ALLOWED_IP_RANGES: JSON.stringify(
            config.app.authProvider.authorizerOptions.allowedIpRanges || []
        ),
        IGNORED_PATHS: JSON.stringify(CUSTOM_AUTHORIZER_IGNORED_PATHS),
    };

    // Add auth-specific environment variables
    if (config.app.authProvider.useCognito.enabled) {
        environment.USER_POOL_ID = "${cognito_user_pool_id}"; // Replaced at runtime
        environment.APP_CLIENT_ID = "${cognito_app_client_id}"; // Replaced at runtime
    }

    if (config.app.authProvider.useExternalOAuthIdp.enabled) {
        environment.JWT_ISSUER_URL =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl;
        environment.JWT_AUDIENCE =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience;
    }

    const authorizerFunc = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(1),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: environment,
    });

    // Grant API Gateway invoke permissions
    authorizerFunc.grantInvoke(Service("APIGATEWAY").Principal);
    globalLambdaEnvironmentsAndPermissions(authorizerFunc, config);

    return authorizerFunc;
}
```

#### **API Gateway Integration Pattern**

```typescript
// ✅ CORRECT - Custom authorizer integration
export class ApiGatewayV2AmplifyNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: ApiGatewayV2AmplifyNestedStackProps) {
        super(parent, name);

        // Create custom authorizer Lambda function
        const customAuthorizerFunction = buildApiGatewayAuthorizerHttpFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.subnets
        );

        // Update environment variables with actual Cognito values if using Cognito
        if (props.config.app.authProvider.useCognito.enabled) {
            customAuthorizerFunction.addEnvironment(
                "USER_POOL_ID",
                props.authResources.cognito.userPoolId
            );
            customAuthorizerFunction.addEnvironment(
                "APP_CLIENT_ID",
                props.authResources.cognito.webClientId
            );
        }

        // Setup custom Lambda authorizer
        const apiGatewayAuthorizer = new apigwAuthorizers.HttpLambdaAuthorizer(
            "CustomHttpAuthorizer",
            customAuthorizerFunction,
            {
                authorizerName: "VamsCustomAuthorizer",
                resultsCacheTtl: cdk.Duration.seconds(300), // 5 minutes cache
                identitySource: ["$request.header.Authorization"],
                responseTypes: [apigwAuthorizers.HttpLambdaResponseType.IAM],
            }
        );

        // Use custom authorizer as default for API Gateway
        const api = new apigw.HttpApi(this, "Api", {
            defaultAuthorizer: apiGatewayAuthorizer,
            // ... other API configuration
        });
    }
}
```

#### **Path-Based Authorization Bypass**

```typescript
// ✅ CORRECT - Define ignored paths as constants
export const CUSTOM_AUTHORIZER_IGNORED_PATHS = ["/api/amplify-config", "/api/version"];

// ✅ CORRECT - Remove no-op authorizers from constructs
export class AmplifyConfigLambdaConstruct extends Construct {
    constructor(parent: Construct, name: string, props: AmplifyConfigLambdaConstructProps) {
        // ... lambda function creation

        // No authorizer needed - path is ignored by custom authorizer
        props.api.addRoutes({
            path: "/api/amplify-config",
            methods: [apigatewayv2.HttpMethod.GET],
            integration: lambdaFnIntegration,
            // No authorizer property - uses default custom authorizer with path bypass
        });
    }
}
```

### **Custom Authorizer Development Rules**

#### **Rule 9: Use Custom Authorizer Pattern**

```typescript
// ✅ CORRECT - Use custom Lambda authorizer
const customAuthorizer = new apigwAuthorizers.HttpLambdaAuthorizer(
    "CustomAuthorizer",
    authorizerFunction,
    {
        authorizerName: "VamsCustomAuthorizer",
        resultsCacheTtl: cdk.Duration.seconds(300),
        identitySource: ["$request.header.Authorization"],
        responseTypes: [apigwAuthorizers.HttpLambdaResponseType.IAM],
    }
);

// ❌ INCORRECT - Don't use built-in authorizers
const builtInAuthorizer = new apigwAuthorizers.HttpUserPoolAuthorizer(); // VIOLATION
```

#### **Rule 10: Configure IP Restrictions Properly**

```typescript
// ✅ CORRECT - IP range configuration validation
if (config.app.authProvider.authorizerOptions.allowedIpRanges) {
    for (const range of config.app.authProvider.authorizerOptions.allowedIpRanges) {
        if (!Array.isArray(range) || range.length !== 2) {
            throw new Error(
                "Configuration Error: Each IP range must be an array of exactly 2 IP addresses [min, max]"
            );
        }
    }
}

// ❌ INCORRECT - Don't skip IP range validation
// No validation for IP ranges - VIOLATION
```

#### **Rule 11: Handle Path Bypass Correctly**

```typescript
// ✅ CORRECT - Use constants for ignored paths
import { CUSTOM_AUTHORIZER_IGNORED_PATHS } from "../../config/config";

// Pass to authorizer environment
environment.IGNORED_PATHS = JSON.stringify(CUSTOM_AUTHORIZER_IGNORED_PATHS);

// ❌ INCORRECT - Don't hardcode ignored paths
const ignoredPaths = ["/api/version"]; // VIOLATION - should use constant
```

## 📝 **Development Templates**

### **New Lambda Builder Template**

```typescript
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    grantReadWritePermissionsToAllAssetBuckets,
    suppressCdkNagErrorsByGrantReadWrite,
} from "../helper/security";

export function build[FunctionName]Function(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "[functionName]";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,

        // VPC Configuration - Use global VPC settings
        vpc: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
            ? vpc : undefined,
        vpcSubnets: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
            ? { subnets: subnets } : undefined,

        environment: {
            // DynamoDB Tables
            [DOMAIN]_STORAGE_TABLE_NAME: storageResources.dynamo.[domain]StorageTable.tableName,

            // Authentication Tables
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,

            // S3 Buckets
            S3_ASSET_AUXILIARY_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,

            // Configuration Values
            CUSTOM_CONFIG_VALUE: config.app.[feature].[setting].toString(),
        },
    });

    // DynamoDB Permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // S3 Permissions
    grantReadWritePermissionsToAllAssetBuckets(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);

    // KMS Permissions
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    // Global Environment and Permissions
    globalLambdaEnvironmentsAndPermissions(fun, config);

    // CDK Nag Suppressions
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function build[FunctionName]WithDependenciesFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    dependentFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "[functionNameWithDependencies]";
    const fun = new lambda.Function(scope, name, {
        // ... standard configuration
        environment: {
            // ... standard environment variables
            DEPENDENT_FUNCTION_NAME: dependentFunction.functionName,
        },
    });

    // ... standard permissions

    // Grant invoke permissions to dependent functions
    dependentFunction.grantInvoke(fun);

    return fun;
}
```

### **New Construct Template**

```typescript
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../config/config";

export interface [ConstructName]Props extends cdk.StackProps {
    readonly config: Config.Config;
    readonly customProperty?: string;
    readonly requiredProperty: string;
}

/**
 * Default properties for the construct
 */
const defaultProps: Partial<[ConstructName]Props> = {
    customProperty: "defaultValue",
};

/**
 * [Construct description and purpose]
 */
export class [ConstructName]Construct extends Construct {
    public readonly [outputResource]: [ResourceType];

    constructor(parent: Construct, name: string, props: [ConstructName]Props) {
        super(parent, name);

        // Merge with default properties
        const mergedProps = { ...defaultProps, ...props };

        // Validate required configuration
        this.validateConfiguration(mergedProps);

        // Create resources
        this.[outputResource] = this.createResources(mergedProps);

        // Add CDK Nag suppressions
        this.addNagSuppressions();
    }

    private validateConfiguration(props: [ConstructName]Props): void {
        if (!props.requiredProperty) {
            throw new Error("[ConstructName] requires requiredProperty to be specified");
        }

        // Add additional validation as needed
        if (props.config.app.[feature].enabled && !props.customProperty) {
            throw new Error("[ConstructName] requires customProperty when [feature] is enabled");
        }
    }

    private createResources(props: [ConstructName]Props): [ResourceType] {
        // Create the main resource
        const resource = new [ResourceType](this, "[ResourceName]", {
            // Resource configuration based on props
            property1: props.requiredProperty,
            property2: props.customProperty,

            // Configuration-driven properties
            enableFeature: props.config.app.[feature].enabled,
        });

        return resource;
    }

    private addNagSuppressions(): void {
        NagSuppressions.addResourceSuppressions(
            this.[outputResource],
            [
                {
                    id: "AwsSolutions-[RuleId]",
                    reason: "Detailed justification for why this suppression is needed in the VAMS context. Explain the security consideration and why this pattern is acceptable for VAMS use case.",
                },
            ],
            true
        );
    }
}
```

### **New Nested Stack Template**

```typescript
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../config/config";

export interface [FeatureName]Resources {
    lambda: lambda.Function;
    role: iam.Role;
    // Add other resources as needed
}

export interface [FeatureName]NestedStackProps {
    config: Config.Config;
    storageResources?: any; // Import proper type
    vpc?: ec2.IVpc;
    subnets?: ec2.ISubnet[];
}

export class [FeatureName]NestedStack extends cdk.NestedStack {
    public readonly [featureName]Resources: [FeatureName]Resources;

    constructor(scope: Construct, id: string, props: [FeatureName]NestedStackProps) {
        super(scope, id);

        // Validate configuration
        if (!props.config.app.[featureName].enabled) {
            throw new Error("Feature is not enabled in configuration");
        }

        // Create resources
        this.[featureName]Resources = this.createResources(props);

        // Add CDK Nag suppressions if needed
        this.addNagSuppressions();
    }

    private createResources(props: [FeatureName]NestedStackProps): [FeatureName]Resources {
        // Create IAM role
        const role = new iam.Role(this, "[FeatureName]Role", {
            assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"),
            ],
        });

        // Create Lambda function
        const lambdaFunction = new lambda.Function(this, "[FeatureName]Function", {
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromAsset("../backend/[featureName]"),
            role: role,
            memorySize: Config.LAMBDA_MEMORY_SIZE,
            timeout: cdk.Duration.minutes(15),
            environment: {
                // Add environment variables
            },
        });

        // Add VPC configuration if needed
        if (props.vpc && props.subnets) {
            // Configure VPC settings
        }

        return {
            lambda: lambdaFunction,
            role: role,
        };
    }

    private addNagSuppressions(): void {
        // Add justified CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Using AWS managed policy for Lambda basic execution role as recommended by AWS best practices.",
                },
            ],
            true
        );
    }
}
```

### **Configuration Addition Template**

```typescript
// Add to ConfigPublic interface in config.ts
export interface ConfigPublic {
    app: {
        // ... existing configuration
        [featureName]: {
            enabled: boolean;
            [specificSetting]: string;
            [advancedOptions]: {
                [option1]: number;
                [option2]: boolean;
            };
        };
    };
}

// Add validation in getConfig() function
if (config.app.[featureName].enabled) {
    if (!config.app.[featureName].[specificSetting] ||
        config.app.[featureName].[specificSetting] === "UNDEFINED") {
        throw new Error(
            "Configuration Error: [featureName] requires [specificSetting] when enabled"
        );
    }
}

// Add feature switch constant
export enum VAMS_APP_FEATURES {
    // ... existing features
    [FEATURE_NAME] = "[FEATURE_NAME]",
}
```

### **Core Stack Integration Template**

```typescript
// Add to CoreVAMSStack constructor
export class CoreVAMSStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id, props);

        // ... existing stack creation

        // Add feature-specific nested stack
        if (props.config.app.[featureName].enabled) {
            const [featureName]NestedStack = new [FeatureName]NestedStack(
                this,
                "[FeatureName]",
                {
                    config: props.config,
                    storageResources: storageResourcesNestedStack.storageResources,
                    vpc: this.vpc,
                    subnets: this.subnetsIsolated,
                }
            );

            [featureName]NestedStack.addDependency(storageResourcesNestedStack);

            // Add feature switch
            this.enabledFeatures.push(VAMS_APP_FEATURES.[FEATURE_NAME]);

            // Add outputs if needed
            const [featureName]Output = new cdk.CfnOutput(this, "[FeatureName]Output", {
                value: [featureName]NestedStack.[featureName]Resources.lambda.functionArn,
                description: "[Feature description] Lambda function ARN",
            });
        }
    }
}
```

## 🚨 **Mandatory Rules**

### **Rule 1: Configuration MUST Be Validated**

```typescript
// ✅ ALWAYS DO THIS - Add validation in getConfig()
if (config.app.newFeature.enabled && !config.app.newFeature.requiredSetting) {
    throw new Error("Configuration Error: newFeature requires requiredSetting when enabled");
}

// ❌ NEVER DO THIS - Skip configuration validation
// No validation - VIOLATION
```

### **Rule 2: Feature Switches MUST Be Used**

```typescript
// ✅ CORRECT - Use feature switches for new features
if (props.config.app.newFeature.enabled) {
    this.enabledFeatures.push(VAMS_APP_FEATURES.NEW_FEATURE);
}

// ❌ INCORRECT - Don't hardcode feature enablement
const newFeatureStack = new NewFeatureStack(); // VIOLATION - should check config
```

### **Rule 3: CDK Nag Suppressions MUST Be Justified**

```typescript
// ✅ CORRECT - Detailed justification
NagSuppressions.addResourceSuppressions(resource, [
    {
        id: "AwsSolutions-IAM5",
        reason: "This role requires wildcard permissions for dynamic S3 object access within the VAMS asset management system. The permissions are scoped to the specific asset buckets created by this deployment and follow the principle of least privilege for the VAMS use case.",
    },
]);

// ❌ INCORRECT - Generic or missing justification
NagSuppressions.addResourceSuppressions(resource, [
    { id: "AwsSolutions-IAM5", reason: "Required for functionality" }, // VIOLATION
]);
```

### **Rule 4: Stack Dependencies MUST Be Explicit**

```typescript
// ✅ CORRECT - Explicit dependency management
const dependentStack = new DependentStack(this, "Dependent", {
    dependency: baseStack.exportedResource,
});
dependentStack.addDependency(baseStack);

// ❌ INCORRECT - Implicit dependencies
const dependentStack = new DependentStack(this, "Dependent", {
    dependency: baseStack.exportedResource, // VIOLATION - no explicit dependency
});
```

### **Rule 5: Resources MUST Use Proper Encryption**

```typescript
// ✅ CORRECT - Use KMS encryption from storage resources
const table = new dynamodb.Table(this, "Table", {
    encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
    encryptionKey: storageResources.encryption.kmsKey,
});

// ❌ INCORRECT - No encryption or default encryption
const table = new dynamodb.Table(this, "Table", {
    // VIOLATION - no encryption specified
});
```

### **Rule 6: Cross-Stack Resources MUST Use Service Helper**

```typescript
// ✅ CORRECT - Use service helper for cross-stack access
const resourceArn = ServiceHelper.getResourceArn();

// ❌ INCORRECT - Direct SSM parameter access
const resourceArn = ssm.StringParameter.valueFromLookup(this, "/path"); // VIOLATION
```

## 📚 **Detailed Implementation Guide**

### **Adding New Configuration Options**

#### **Step 1: Define Configuration Interface**

```typescript
// config.ts - Add to ConfigPublic interface
export interface ConfigPublic {
    app: {
        newFeature: {
            enabled: boolean;
            mode: "basic" | "advanced";
            settings: {
                timeout: number;
                retries: number;
            };
        };
    };
}
```

#### **Step 2: Add Configuration Validation**

```typescript
// config.ts - Add to getConfig() function
if (config.app.newFeature.enabled) {
    if (
        config.app.newFeature.settings.timeout < 1 ||
        config.app.newFeature.settings.timeout > 900
    ) {
        throw new Error(
            "Configuration Error: newFeature timeout must be between 1 and 900 seconds"
        );
    }

    if (config.app.newFeature.mode === "advanced" && !config.app.newFeature.settings.retries) {
        throw new Error("Configuration Error: advanced mode requires retry configuration");
    }
}
```

#### **Step 3: Add Feature Switch**

```typescript
// vamsAppFeatures.ts
export enum VAMS_APP_FEATURES {
    NEW_FEATURE = "NEW_FEATURE",
}

// core-stack.ts - Add to constructor
if (props.config.app.newFeature.enabled) {
    this.enabledFeatures.push(VAMS_APP_FEATURES.NEW_FEATURE);
}
```

### **Creating New Nested Stacks**

#### **Step 1: Create Nested Stack File**

```typescript
// lib/nestedStacks/newFeature/newFeature-nestedStack.ts
export class NewFeatureNestedStack extends cdk.NestedStack {
    public readonly newFeatureResources: NewFeatureResources;

    constructor(scope: Construct, id: string, props: NewFeatureNestedStackProps) {
        super(scope, id);

        this.newFeatureResources = this.createResources(props);
    }
}
```

#### **Step 2: Integrate with Core Stack**

```typescript
// core-stack.ts - Add to constructor
if (props.config.app.newFeature.enabled) {
    const newFeatureStack = new NewFeatureNestedStack(this, "NewFeature", {
        config: props.config,
        storageResources: storageResourcesNestedStack.storageResources,
    });

    newFeatureStack.addDependency(storageResourcesNestedStack);
}
```

### **Managing Resource Dependencies**

#### **Step 1: Define Resource Interfaces**

```typescript
export interface NewFeatureResources {
    lambda: lambda.Function;
    table: dynamodb.Table;
    bucket: s3.Bucket;
}
```

#### **Step 2: Export Resources**

```typescript
export class NewFeatureNestedStack extends cdk.NestedStack {
    public readonly newFeatureResources: NewFeatureResources;

    // Resources are automatically available to parent stack
}
```

#### **Step 3: Use in Dependent Stacks**

```typescript
const dependentStack = new DependentStack(this, "Dependent", {
    newFeatureResources: newFeatureStack.newFeatureResources,
});
dependentStack.addDependency(newFeatureStack);
```

## ✅ **Quality Assurance Checklist**

### **Before Implementation**

-   [ ] Configuration requirements clearly defined
-   [ ] Feature switch strategy planned
-   [ ] Stack dependencies mapped
-   [ ] Security requirements identified
-   [ ] Performance impact assessed

### **During Implementation**

-   [ ] Configuration interfaces updated
-   [ ] Feature switches implemented
-   [ ] Nested stack patterns followed
-   [ ] Resource sharing properly implemented
-   [ ] CDK Nag compliance maintained
-   [ ] Dependencies explicitly managed

### **After Implementation**

-   [ ] Unit tests written and passing
-   [ ] CDK synth completes successfully
-   [ ] CDK diff reviewed
-   [ ] Security review completed
-   [ ] Documentation updated
-   [ ] Configuration guide updated

## 🔍 **Code Review Checklist**

### **Architecture Compliance**

-   [ ] Follows nested stack patterns
-   [ ] Uses proper configuration management
-   [ ] Implements feature switches correctly
-   [ ] Manages dependencies explicitly

### **Security**

-   [ ] CDK Nag suppressions justified
-   [ ] Encryption properly implemented
-   [ ] IAM follows least privilege
-   [ ] No hardcoded secrets or credentials

### **Code Quality**

-   [ ] TypeScript types properly defined
-   [ ] Error handling comprehensive
-   [ ] Code comments and documentation
-   [ ] Consistent naming conventions

### **Testing**

-   [ ] Unit tests cover new functionality
-   [ ] Integration tests validate stack deployment
-   [ ] Configuration combinations tested
-   [ ] Feature switches tested

## 🚀 **Deployment Checklist**

### **Pre-Deployment**

-   [ ] Configuration validated
-   [ ] CDK synth successful
-   [ ] CDK diff reviewed
-   [ ] Security review completed
-   [ ] Backup strategy confirmed

### **Deployment Process**

-   [ ] Deploy to test environment first
-   [ ] Validate functionality
-   [ ] Monitor CloudWatch logs
-   [ ] Verify feature switches work
-   [ ] Test rollback procedures

### **Post-Deployment**

-   [ ] Verify all resources created
-   [ ] Test end-to-end functionality
-   [ ] Monitor performance metrics
-   [ ] Update documentation
-   [ ] Notify stakeholders

## 📖 **Best Practices Summary**

1. **Always** make features configurable through the config system
2. **Always** use feature switches for new functionality
3. **Always** follow nested stack patterns for modularity
4. **Always** validate configuration in getConfig()
5. **Always** use explicit stack dependencies
6. **Always** justify CDK Nag suppressions with detailed reasons
7. **Always** use KMS encryption from storage resources
8. **Always** use service helper for cross-stack resource access
9. **Always** write comprehensive tests
10. **Always** update documentation

## 🛠️ **Development Commands**

```bash
# Setup development environment
cd infra
npm install

# Configuration validation
npm run build

# CDK commands
cdk synth --all                    # Synthesize all stacks
cdk diff --all                     # Show differences
cdk deploy --all --require-approval never  # Deploy all stacks

# Testing
npm test                           # Run unit tests
npm run test:watch                 # Watch mode for tests

# Code quality
npm run lint                       # Lint TypeScript code
npm run format                     # Format code

# Generate endpoints (if needed)
npm run gen                        # Generate API endpoints
```

## 🔧 **Troubleshooting Common Issues**

### **Configuration Errors**

```bash
# Error: Configuration validation failed
# Solution: Check config.json against ConfigPublic interface
# Verify all required fields are present and valid
```

### **Stack Dependency Issues**

```bash
# Error: Resource not found in cross-stack reference
# Solution: Ensure explicit dependencies are set
# Use addDependency() method
```

### **CDK Nag Failures**

```bash
# Error: CDK Nag security check failed
# Solution: Add justified suppressions or fix the security issue
# Review AWS Well-Architected Framework guidelines
```

### **Feature Switch Issues**

```bash
# Error: Feature not working despite being enabled
# Solution: Check feature switch logic in core stack
# Verify feature constant is added to enabledFeatures array
```

This workflow ensures that all VAMS CDK development follows established patterns and maintains the high quality standards of the codebase while supporting the complex multi-stack architecture and rich configuration system.

## 📋 **Recommended MCP Servers for CDK Development**

When following this CDK development workflow, leverage these MCP servers to enhance your development process:

### **Core Development Support**

1. **awslabs.core-mcp-server** - Use for initial prompt understanding and translating requirements into AWS expert guidance
2. **awslabs.cdk-mcp-server** - Essential for CDK best practices, construct patterns, CDK Nag rule explanations, and AWS Solutions Constructs discovery
3. **awslabs.aws-documentation-mcp-server** - Search and access AWS service documentation for implementation details

### **Infrastructure as Code**

4. **awslabs.terraform-mcp-server** - When comparing CDK patterns with Terraform or migrating infrastructure
5. **awslabs.cfn-mcp-server** - For direct CloudFormation resource management and template generation

### **Security and Compliance**

6. **ai3-security-expert** - Analyze CDK projects for security issues and AWS Well-Architected compliance
7. **awslabs.aws-pricing-mcp-server** - Analyze CDK projects for cost implications and generate cost reports

### **Documentation and Visualization**

8. **awslabs.code-doc-gen-mcp-server** - Generate comprehensive documentation from CDK code analysis
9. **awslabs.aws-diagram-mcp-server** - Create architecture diagrams to visualize CDK infrastructure designs

### **Development Tools**

10. **awslabs.git-repo-research-mcp-server** - Semantic search through CDK codebases and research existing patterns
11. **context7** - Access up-to-date CDK and AWS service documentation and examples

### **Specialized Services**

12. **awslabs.frontend-mcp-server** - When CDK modifications involve React web applications or frontend components
13. **awslabs.aws-location-mcp-server** - For CDK modifications involving AWS Location Services
14. **awslabs.amazon-sns-sqs-mcp-server** - When implementing messaging patterns in CDK

### **Usage Examples in CDK Development**

```bash
# Start CDK development with expert guidance
Use awslabs.core-mcp-server for prompt understanding

# Research CDK patterns and best practices
Use awslabs.cdk-mcp-server for construct patterns and CDK Nag guidance

# Analyze security implications
Use ai3-security-expert to review CDK code for security compliance

# Generate architecture diagrams
Use awslabs.aws-diagram-mcp-server to visualize infrastructure designs

# Research existing implementations
Use awslabs.git-repo-research-mcp-server to find similar patterns in codebases

# Document the implementation
Use awslabs.code-doc-gen-mcp-server to generate comprehensive documentation
```

This workflow document provides the foundation for consistent, secure, and maintainable CDK development within the VAMS ecosystem, enhanced by the appropriate MCP server tools.
