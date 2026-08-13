# VAMS CDK Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending the VAMS CDK infrastructure. Follow these rules to ensure consistency, quality, and maintainability across all CDK implementations.

> **Steering Document Sync (bidirectional):** This document mirrors the Claude Code steering in `infra/CLAUDE.md` (and cross-cutting rules in the root `CLAUDE.md`). Whenever you change a rule, pattern, or convention here, make the equivalent change in `infra/CLAUDE.md` in the same change — and whenever those `CLAUDE.md` files change, reflect it back here. Keep the two sets of documents saying the same thing.

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
│   │   ├── const.ts          # Partition-aware service endpoints
│   │   ├── s3AssetBuckets.ts # Global asset bucket registry
│   │   └── security.ts       # KMS, CDK Nag, CSP, TLS enforcement, presigned URL bucket policy restrictions
│   ├── aspects/              # CDK aspects for cross-cutting concerns
│   └── artefacts/            # Build artifacts and templates
├── test/                     # CDK unit and integration tests
└── gen/                      # Generated code and endpoints
```

### **Nested Stack Dependency Chain**

Each arrow is an explicit `addDependency()` call in `core-stack.ts`, so the chain reads bottom-up: a stack is created after everything it points to.

```
CoreVAMSStack (root)
  |
  +-- VPCBuilder (conditional: useGlobalVpc.enabled)
  +-- LambdaLayers
  +-- StorageResourcesBuilder (foundation: DynamoDB, S3, SNS, SQS, EventBridge, KMS, CloudWatch)
  |     |
  |     +-- ResourceNamesBuilder (publishes 62 SSM resource-name parameters)
  |     +-- AuthBuilder                                     -> storage, resourceNames
  |     +-- ApiBuilder (primary API route Lambda wiring)     -> storage, resourceNames
  |     +-- ApiBuilder2 (secondary API stack: Tags, Tag Types, Auth Constraints, asset history,
  |     |    and the pipeline / pipeline template / workflow / workflow trigger / execution routes)
  |     |                                                    -> storage, resourceNames, ApiBuilder
  |     +-- SearchBuilder (OpenSearch)                       -> storage, resourceNames
  |     +-- PipelineBuilder (all use-case pipelines)         -> storage, ApiBuilder2
  |     |    (its vamsSchema registration custom resources invoke an ApiBuilder2 Lambda)
  |     +-- AddonBuilder (Garnet, Physna Sync)               -> storage, resourceNames
  |     +-- RestApi (ApiNestedStack: API Gateway + authorizer)
  |     |                                                    -> storage, AuthBuilder, ApiBuilder,
  |     |                                                       ApiBuilder2, SearchBuilder, AddonBuilder
  |     +-- StaticWeb (CloudFront or ALB hosting)            -> storage
  |
  +-- LocationService (conditional: useLocationService.enabled)
  +-- CustomFeatureEnabledConfig (writes enabled features to DynamoDB)
```

`RestApi` materializes the routes every API stack registered into `RouteRegistry`, which is why it depends on all of them rather than the reverse.

### **Cross-Stack Shared Interfaces**

**`storageResources`** (defined in `storageBuilder-nestedStack.ts`):

```typescript
interface storageResources {
    encryption: { kmsKey?: kms.IKey };
    s3: {
        assetAuxiliaryBucket: s3.Bucket;
        artefactsBucket: s3.Bucket;
        accessLogsBucket: s3.Bucket;
    };
    // No sqs member: the two Amazon SQS queues the builder creates buffer S3 object-created /
    // object-deleted notifications for the indexers and are wired locally, and each workflow
    // trigger Lambda owns its own queue + DLQ in lib/lambdaBuilder/workflowFunctions.ts.
    sns: {
        eventEmailSubscriptionTopic: sns.Topic;
        fileIndexerSnsTopic: sns.Topic;
        assetIndexerSnsTopic: sns.Topic;
        databaseIndexerSnsTopic: sns.Topic;
    };
    eventBridge: {
        orchestrationBus: events.EventBus; // Top-level VAMS orchestration event bus
        orchestrationBusAuditLogGroup: logs.LogGroup; // Starter audit rule target
        eventSourcePrefix: string; // Deployment-unique source prefix, e.g. "vams.prod-us-east-1"
    };
    cloudWatchAuditLogGroups: {
        authentication;
        authorization;
        fileUpload;
        fileDownload;
        fileDownloadStreamed;
        authOther;
        authChanges;
        actions;
        errors: logs.LogGroup;
    };
    dynamo: {
        // 46 DynamoDB tables -- see the interface at the top of storageBuilder-nestedStack.ts
        appFeatureEnabledStorageTable;
        assetLinksStorageTableV2;
        assetLinksMetadataStorageTable;
        assetStorageTable;
        assetUploadsStorageTable;
        assetVersionsStorageTable;
        assetFileVersionsStorageTable;
        assetFileVersionHistoryStorageTable; // GSIs: DatabaseIdAssetIdIndex (PK databaseId:assetId, SK versionId), WorkflowExecutionIdIndex (PK changeWorkflowExecutionId, SK databaseId:assetId:filePath; sparse — workflow-produced versions only)
        assetHistoryStorageTable;
        syncTrackingOutboundStorageTable;
        assetFileMetadataVersionsStorageTable;
        authEntitiesStorageTable;
        commentStorageTable;
        constraintsStorageTable;
        databaseStorageTable;
        metadataSchemaStorageTableV2;
        databaseMetadataStorageTable;
        assetFileMetadataStorageTable;
        fileAttributeStorageTable;
        pipelineStorageTable;
        rolesStorageTable;
        s3AssetBucketsStorageTable;
        subscriptionsStorageTable;
        tagStorageTable;
        tagTypeStorageTable;
        userRolesStorageTable;
        userStorageTable;
        workflowExecutionsStorageTable;
        workflowExecutionsStorageTableV2; // V2: PK workflowExecutionId, SK workflowDatabaseId:workflowId; GSI WorkflowExecutionsByWorkflowGSI
        pipelineExecutionsStorageTable; // PK pipelineExecutionId, SK workflowExecutionId; GSIs PipelineExecByWorkflowExecGSI / PipelineExecChainGSI / PipelineExecEndStateGSI
        pipelineExecutionInputFilesStorageTable; // PK pipelineExecutionId; GSI InputFilesByAssetGSI
        pipelineExecutionInputMetadataStorageTable;
        pipelineExecutionInputConfigurationStorageTable;
        pipelineExecutionOutputFilesStorageTable;
        pipelineExecutionOutputMetadataStorageTable;
        pipelineExecutionOutputResultsStorageTable;
        pipelineExecutionLogsStorageTable;
        workflowExecutionInputsStorageTable; // PK workflowExecutionId; GSI WorkflowExecInputsByAssetGSI (asset-scoped execution listing)
        workflowExecutionConfigurationStorageTable;
        apiKeyStorageTable: dynamodb.Table; // GSIs: apiKeyHashIndex (PK: apiKeyHash), userIdIndex (PK: userId)
        workflowStorageTable: dynamodb.Table;
        // assetVersionsStorageTable has GSI: databaseIdAssetIdIndex (PK: databaseId:assetId, SK: assetVersionId)

        // Pipeline + workflow V2 data model tables
        pipelineStorageTableV2: dynamodb.Table; // PK databaseId, SK pipelineId; GSIs PipelinesByDatabaseGSI / PipelinesByCategoryGSI / PipelinesByDateGSI
        pipelineTemplatesStorageTable: dynamodb.Table; // PK pipelineDatabaseId:pipelineId, SK templateId
        pipelineTemplateTagSchemaStorageTable: dynamodb.Table; // PK tagSchemaId, SK pipelineDatabaseId:pipelineId:templateId; GSI TagSchemaByTemplateGSI
        workflowStorageTableV2: dynamodb.Table; // PK databaseId, SK workflowId; GSIs WorkflowsByDatabaseGSI / WorkflowsByCategoryGSI / WorkflowsByDateGSI
        workflowTriggersStorageTable: dynamodb.Table; // PK workflowDatabaseId:workflowId, SK triggerType; GSI TriggersByBaseTypeGSI (PK triggerBaseType — the BARE type)
    };
}
```

The `*ByDateGSI` indexes on the pipeline, workflow, and workflow-execution V2 tables are partitioned on a constant `allListPartition` attribute, which is what makes the global (all-databases) list endpoints a query rather than a table scan. Every write path — including the data-migration transforms — must set that attribute, or the row is invisible to those lists.

**`authResources`** (defined in `authBuilder-nestedStack.ts`):

```typescript
interface authResources {
    roles: { unAuthenticatedRole: iam.Role };
    cognito: {
        userPool: cognito.UserPool;
        webClientUserPool: cognito.UserPoolClient;
        userPoolId: string;
        identityPoolId: string;
        webClientId: string;
    };
}
```

### **Workflow Execution Storage (V2 data model)**

Workflow executions use a workflow-keyed data model spread across 11 DynamoDB tables. The main execution row is keyed by a VAMS GUID (`executionId`), and asset/database linkage lives in the input tables rather than on the main row:

-   `workflowExecutionsStorageTableV2` — PK `workflowExecutionId`, SK `workflowDatabaseId:workflowId`; GSI `WorkflowExecutionsByWorkflowGSI` (PK `workflowDatabaseId:workflowId`, SK `executionStartDate`).
-   `pipelineExecutionsStorageTable` — PK `pipelineExecutionId`, SK `workflowExecutionId`; GSIs `PipelineExecByWorkflowExecGSI`, `PipelineExecChainGSI`, `PipelineExecEndStateGSI`.
-   `pipelineExecutionInputFilesStorageTable` — PK `pipelineExecutionId`, SK `databaseId:assetId:inputAssetFileKey`; GSI `InputFilesByAssetGSI`.
-   `pipelineExecutionInputMetadataStorageTable` — PK `pipelineExecutionId`, SK `databaseId:assetId:filePath`.
-   `pipelineExecutionInputConfigurationStorageTable` — PK `pipelineExecutionId`, SK `recordType`.
-   `pipelineExecutionOutputFilesStorageTable` — PK `pipelineExecutionId`, SK `fileType:relativeFilePath`.
-   `pipelineExecutionOutputMetadataStorageTable` — PK `pipelineExecutionId`, SK `targetFilePath:metadataKey`.
-   `pipelineExecutionOutputResultsStorageTable` — PK `pipelineExecutionId`, SK `relativeFilePath`.
-   `pipelineExecutionLogsStorageTable` — PK `pipelineExecutionId`, SK `logType`.
-   `workflowExecutionInputsStorageTable` — PK `workflowExecutionId`, SK `databaseId:assetId:inputAssetFileKey`; GSI `WorkflowExecInputsByAssetGSI` (PK `databaseId:assetId`, SK `executionStartDate`) backs the asset-scoped execution listing.
-   `workflowExecutionConfigurationStorageTable` — PK `workflowExecutionId`, SK `recordType`; GSI `WorkflowExecConfigByOutputAssetGSI` (PK `outputDatabaseId:outputAssetId`, SK `executionStartDate`; sparse — the partition attribute is written only for an asset-output run) backs the OUTPUT half of the asset-scoped execution listing. An asset's history is the union of this and `WorkflowExecInputsByAssetGSI`; every write path (including data migration) must set the attribute or those executions are absent from the index entirely.

The legacy `WorkflowExecutionsStorageTable` is retained intact as the migration read source.

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
-   [ ] **Update Templates**: Update **all** configuration templates — `config.template.commercial.json`, `config.template.govcloud.json`, **and** `config.template.eusovereign.json` — plus the active `config.json`. A missed template silently falls back to `getConfig()` defaults and drops any operator-set value.

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

-   [ ] **Update Configuration Reference**: Document new config options in `documentation/docusaurus-site/docs/deployment/configuration-reference.md`
-   [ ] **Update Architecture Docs**: Update `documentation/docusaurus-site/docs/architecture/` pages if architecture changes
-   [ ] **Update Features Page**: Add new features to `documentation/docusaurus-site/docs/overview/features.md`
-   [ ] **Update API Docs**: If new API endpoints, update `documentation/docusaurus-site/docs/api/` and `documentation/VAMS_API.yaml`
-   [ ] **Update Pipeline Docs**: If new pipeline, create page in `documentation/docusaurus-site/docs/pipelines/` and update `sidebars.ts`
-   [ ] **Add Code Comments**: Include comprehensive inline documentation
-   [ ] **Update README**: Update main `README.md` if needed

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
        // assetBuckets: createNewBucket, defaultNewBucketSyncDatabaseId,
        // externalAssetBuckets (bucketArn, baseAssetsPrefix, defaultSyncDatabaseId,
        // optional bucketAccountId/bucketRegion/bucketKmsKeyArn),
        // presignedUrlNetworkRestrictions (allowedIpRanges/allowedVpceIds; mutually exclusive;
        // non-empty list adds a presigned-only bucket policy Deny to the created asset + auxiliary buckets)
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

### **Reporting Failure on a Task-Token Pipeline**

A pipeline registered with `waitForCallback: "Enabled"` receives an AWS Step Functions task token, and the
workflow's task stays `RUNNING` until something reports against it. The pipeline owns both outcomes:
`SendTaskSuccess` on completion and **`SendTaskFailure` on every failure route**. A route that returns or
raises without reporting leaves the task pending for its full `taskTimeout` — hours on the GPU pipelines.

Both halves are required, and each is inert without the other:

-   **The handler** calls `SendTaskFailure` from every failing path — each `except` block _and_ every early
    `return` that emits a 4xx. A pre-invoke rejection is the common case: the container never starts, so
    nothing else can report.
-   **The CDK lambda builder** grants `states:SendTaskFailure` on the `vamsExecute` function itself. Verify
    the function, not the file — a builder often grants it on `openPipeline`/`pipelineEnd` while the
    `vamsExecute` builder lacks it, which reads as present to a file-level grep:

    ```bash
    awk '/export function build.*VamsExecute/,/^}/' <builder>.ts | grep -c SendTaskFailure
    ```

    Without the grant the call raises `AccessDeniedException`, the handler logs it, and the task hangs
    exactly as before.

Report the token before propagating so the original error still reaches Amazon CloudWatch, and make the call
conditional on a token being present — a direct invoke carries none.

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
│   ├── metadata3dLabeling/     # 3D metadata labeling
│   │   ├── lambda/
│   │   ├── container/
│   │   └── blender/            # Pipeline-specific tools
│   └── nvidia/cosmos/          # NVIDIA Cosmos pipelines
│       ├── 3/                  # Cosmos 3 (omni generation)
│       │   ├── lambda/
│       │   └── container/
│       └── predict/            # Cosmos Predict (Text2World, Video2World)
│           ├── lambda/
│           └── container/
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
    ├── vamsSchema/             # Registration bundle (see below)
    └── README.md               # Documentation
```

### **vamsSchema Registration (required for a pipeline to be usable)**

A pipeline's CDK stack only creates AWS resources. What makes it appear in VAMS -- as a pipeline, its
templates, and a runnable workflow -- is a `vamsSchema/` bundle uploaded to the artefacts bucket and
imported at deploy time through `SYSTEM_USER` cross-calls
(`backend/backend/common/workflows/vamsSchemaImport.py`):

```
backendPipelines/{useCase}/{name}/vamsSchema/
    pipeline.json                  # required
    workflow.json                  # optional -- one built-in workflow per pipeline
    templates/{templateId}.json    # optional -- one file per template
```

The registration custom resource re-fires only when the bundle changes: `schemaHash` covers
`pipeline.json`, `workflow.json`, and the **top-level** `templates/*.json` (a subdirectory is skipped,
not read). Two rules follow:

-   **Never hash an unresolved CDK token.** Override values like `fn.functionName` stringify to
    `${Token[TOKEN.n]}`, where `n` shifts when any unrelated construct is added. Hashing that text
    re-fires every registration on an unrelated deploy, and each one overwrites operator edits to the
    built-in (rename, retuned `systemConfig`, deliberate archive) from the schema files. Substitute a
    placeholder for token values -- the resolved value still reaches CloudFormation via the
    `resourceOverrides` / `idOverrides` properties, which detect a real retarget themselves. Test:
    `infra/test/vamsSchemaRegistrationHash.test.ts`.
-   **Bundles share the artefacts bucket with `infra/lib/artefacts/`.** The root `DeployArtefacts`
    deployment prunes (`s3 sync --delete`) over the bucket root, so it must keep excluding
    `vamsSchema/*` -- otherwise refreshing an unrelated artefact deletes every bundle while the
    registration resources still expect to read them. Test: `infra/test/artefactsBucketPrune.test.ts`.

Register it from the pipeline's nested stack with the `VamsSchemaRegistration` construct, passing the
deploy-time resolved resource values:

```typescript
new VamsSchemaRegistration(this, "MyPipelineSchema", {
    schemaPath: path.join(__dirname, "../../../../../backendPipelines/{useCase}/{name}/vamsSchema"),
    resourceOverrides: { lambdaName: myPipelineFunction.functionName },
    importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
});
```

`pipeline.json` carries **no ARNs or account ids** -- the execution target is injected per
`executionConfig.executionType` (`lambda.resourceId`, `sqs.queueUrl`, `eventBridge.busArn`,
`deadlineCloud.farmId`), so the same file works in every account and partition. Registration is
idempotent: a redeploy overwrites the definition and clears the archived flag.

Several `systemConfig` conditions each produce a silently unusable pipeline or workflow when wrong:

1. **`inputFileFilters.allow` must match the file types the container handles.** These globs are what
   the execute API and the file-upload trigger match against; a missing extension makes the pipeline
   unselectable for that type with no error.
   **An omitted, empty, or `*` allow list means "any file"** and defers the decision to the rest of the
   chain (workflow -> pipeline -> the chosen template's `overrides`); an omitted exclude list excludes
   nothing. A filter only ever NARROWS eligibility. A match-everything pattern in an `exclude` list
   (`*`, `**`, `*.*`, `/*`, `/**`) is REJECTED on save at every level including triggers, since exclude
   is applied last and would remove every file — leave the list empty to exclude nothing.
2. **`requireTemplate: true` needs a default template.** Execute auto-selects the default; with none,
   every caller must name a `templateId`. A bundle with exactly one template has it promoted
   automatically -- with two or more, mark one `"isDefault": true`.
3. **`inputFileArity: "none"`** means no input files, so `assetId` / `databaseId` resolve from the
   execution's output target (`outputAssetId` / `outputDatabaseId`).
4. **`assetScope` accepts two vocabularies** -- the shorthand `{"wholeAsset": true|false}` and the
   canonical four `*Allowed` keys. A malformed value can fail the import while the deploy still exits
   0, so confirm the row landed after deploying:
5. **A partial `systemConfig` is safe — registration fills every omitted field with its default.**
   The stored record replaces `systemConfig` wholesale rather than merging, so the importer completes a
   bundle's block first: the declaration wins, omissions become the documented defaults (nested maps
   filled key-by-key). Declare only what differs. This also keeps a newly-added `systemConfig` field
   from changing the meaning of bundles written before it existed.
6. **`allowWorkflowTriggerChaining` (default `false`)** lets ANOTHER workflow's output fire this
   workflow's triggers -- how a preview or metadata built-in runs on a conversion pipeline's result. A
   workflow never fires on its own output whatever the value, so it cannot loop on its own files; a
   chained file must still match the trigger's `inputFileFilters`.
7. **A workflow's `defaultOutputFileBaseExecutionPathExtension` supplies the output path prefix when
   an execution names none.** It is stored UNRESOLVED, so its `{{tag}}` placeholders resolve per run --
   one stored `/{{jobName}}/` gives every execution its own output folder. The prefix is inserted
   immediately before each output file's own name, so a container's own output folders are preserved.
   **A container must therefore not create its own per-job folder** -- the workflow prefix is what
   separates runs, and a container-side folder shows up as a stray level inside every asset. The
   Gaussian Splat and Isaac Lab bundles use it for exactly this.
8. **Let the TEMPLATE decide whether a step needs an input file.** When one pipeline supports several
   modes that differ in what they consume, set the pipeline's `inputFileArity` to the LOWEST value any
   of its templates needs (usually `none`) and let each template raise it via its `overrides`
   (`inputFileArity`, `assetScope`, `metadataInputs`, `inputFileFilters` — validated on save; unknown
   keys and bad arity values are rejected). A text-to-video template then needs no input file while an
   image-to-video template on the same pipeline overrides arity to `one`. This keeps one pipeline per
   MODEL rather than one per mode, and the execute form asks for a file only when the chosen template
   consumes one. The Cosmos 3 bundles are configured this way.
   **A template's `tagSchema` is how a run gets OPERATOR-SUPPLIED options** — a prompt, a seed, an
   output format, a quality preset — so each becomes a form field rather than a hardcoded value. Each
   entry declares `tagKey` (letters/digits/underscore only, so `{{tagKey}}` substitutes), `type`
   (`string` | `integer` | `number` | `boolean` | `string-list` | `enum`), `required`, `default`,
   `enumValues` (required for `enum`), and `label` / `description` for the form. Reference every
   declared tag as `{{TAG}}` in the `configBody` — a tag the body never references is silently unused,
   so the operator fills in a field that reaches no pipeline.
   **In a `json` body, quoting is type-driven and checked on save:** an `integer`/`number`/`boolean`/
   `string-list` placeholder is a bare JSON value (`"seed": {{SEED}}`) while a `string`/`enum` one sits
   inside the string it fills (`"prompt": "{{PROMPT}}"`). The reverse is rejected with a 400, since
   quoting a typed tag would hand the container `"42"` where it expects `42`. Non-`json` formats
   (`yaml`, `xml`, `openjd`, `raw`) are stored verbatim and not shape-checked. A `tagKey` may not
   collide with a reserved system tag name or begin with the reserved `metadata_` prefix.
   **A workflow's `inputFileArity` is authored, not derived** — templates are chosen per execution, so
   set it to the MAXIMUM any pipeline/template combination in that workflow can require; a lower gate
   rejects a selection a template would have accepted.
9. **A workflow ref's `jobName` is an output-path segment, not a display label.** It becomes the
   `{jobName}` folder in `pipelines/{pipelineName}/{jobName}/output/{executionId}/files/`, is persisted
   on the workflow record as the derived `jobNames[]`, and is what `executeWorkflow` reads to
   reconstruct those prefixes at launch. Omit it in a bundle unless the pipeline id would not identify
   the step — blank already falls back to the pipeline id, keeping each step's output distinct. It
   takes the id charset only (3-63 chars), so **`{{tag}}` placeholders are rejected**: use the
   workflow's `defaultOutputFileBaseExecutionPathExtension` (rule 7) to vary the path per run. Do not
   confuse the FIELD with the `{{jobName}}` TAG, which resolves to the run's generated job name.
10. **A workflow may not list the same pipeline twice.** Per-step execute params, resolved template
    configs, and filtered inputs are all keyed by `pipelineDatabaseId:pipelineId`, so a repeated
    pipeline silently overwrites the earlier step's resolved config and both steps run identically —
    with no error. When one model needs two modes in one workflow (train then evaluate, say), ship two
    pipelines sharing a container image / ECR repo / compute environment rather than one pipeline
    listed twice with different templates.
11. **A sub-state-machine execution name must be unique at millisecond concurrency.** `openPipeline.py`
    derives the name a pipeline's own state machine runs under (`PipelineJob_<stamp>_<random>`), and
    Step Functions rejects a repeat with `ExecutionAlreadyExists`. A workflow may carry several triggers
    of one type, so one upload fans out to N simultaneous runs of the SAME pipeline — a timestamp alone
    (even to the millisecond) is not enough, so keep the random suffix. The name also namespaces
    per-execution S3 objects in some pipelines (`rp_config_{jobName}.json`), where a collision has
    concurrent runs overwrite each other's config instead of merely failing to start. Keep it within the
    80-character Step Functions limit and free of `:` and `/`.

```bash
vamscli pipeline get -d GLOBAL -p {pipelineId} --json-output
vamscli pipeline template list -d GLOBAL -p {pipelineId}
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
5. **Required Lambda Files (CRITICAL)**: Every pipeline `lambda/` directory MUST include `__init__.py`, `customLogging/__init__.py`, and `customLogging/logger.py`. Copy from any existing pipeline (e.g., `backendPipelines/3dRecon/splatToolbox/lambda/`). Without these, Lambda fails with `No module named 'customLogging'`.
6. **Error Handling**: Implement comprehensive error handling and logging
7. **Resource Cleanup**: Ensure proper cleanup of temporary resources
8. **VPC Builder Updates (CRITICAL)**: Pipelines using AWS Batch, ECS, or Fargate MUST be added to **all three** condition blocks in `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts` (search for `useSplatToolbox` to find them):
    - **Subnet creation** (~line 341): adds public + private subnets to the VPC
    - **VPC endpoints** (~line 540): creates Batch, ECR, ECR Docker endpoints
    - **ECS endpoint** (~line 619): includes private subnets in the ECS endpoint

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

### **OpenSearch Serverless Connectivity**

A **private** OpenSearch Serverless collection (`app.openSearch.useServerless.allowPublic = false`) is reached only through a VPC endpoint, and the endpoint **type is selected by the collection generation** because the two generations expose different endpoint hostnames:

-   **NEXTGEN** (`nextGen = true`): hostname `{collection-id}.aoss.{region}.on.aws`, reached through a **standard EC2 interface endpoint** (`ec2.InterfaceVpcEndpoint`, service `com.amazonaws.{region}.aoss-data`, `privateDnsEnabled: true`), built via `new ec2.InterfaceVpcEndpointAwsService("aoss-data", "com.amazonaws", 443)`.
-   **CLASSIC** (`nextGen = false`): hostname `{collection-id}.{region}.aoss.amazonaws.com`, reached through the OpenSearch Serverless-managed endpoint (`opensearchserverless.CfnVpcEndpoint`), which provisions its own Route 53 private hosted zone.

The chosen endpoint's id populates the network policy `SourceVPCEs`. Only the OpenSearch-facing Lambdas (search, fileIndexer, assetIndexer, crOsReindexer, and the schema-deploy custom resource) run in the VPC — `useForAllLambdas` is not required for a private collection. The schema-deploy custom resource Lambda uses a long timeout (14 min) and a readiness poll because a freshly created collection/endpoint, plus a NEXTGEN scale-to-zero cold start (10–30s), can take minutes to become reachable. Backend Lambdas sign with SigV4 service name `aoss` when `OPENSEARCH_TYPE=serverless`.

**`addVpcEndpoints` gating (NEXTGEN only).** The NEXTGEN endpoint is a standard EC2 interface endpoint, so it follows `useGlobalVpc.addVpcEndpoints` like every other interface endpoint. The construct computes `createEndpointResources = useVPCEndpoint && (!nextGen || addVpcEndpoints)`:

-   When true, VAMS creates the endpoint, its security group, and the VPC network access policy, and runs the schema-deploy function in the VPC.
-   When false (private NEXTGEN + `addVpcEndpoints = false`, the **deferred** case), VAMS skips the endpoint **and** the network policy. The schema-deploy function runs **outside** the VPC, writes the SSM parameters, and skips index creation (the `DeploySSMIndexSchema` custom resource passes `deferIndexCreation: "true"`). The operator creates the `aoss-data` endpoint and a matching network policy manually. To then create the index mappings, set `app.openSearch.useServerless.deployDeferredIndexSchema = true` for one deployment (also overridable via CDK context); the construct computes `deferIndexCreation = deferVpcSetup && !deployDeferredIndexSchema` and runs schema-deploy in the VPC against the operator endpoint. Then reindex. The flag is ignored when `addVpcEndpoints = true`. CLASSIC's managed endpoint is not an EC2 interface endpoint, so it is not governed by `addVpcEndpoints` and is always created for a private collection. See `documentation/docusaurus-site/docs/developer/opensearch.md`.

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
            // Handler-specific env vars only (resource names resolved from SSM)
            PRESIGNED_URL_TIMEOUT_SECONDS: config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });

    // Permissions Pattern - DynamoDB
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    // SSM resource name parameters grant via globalLambdaEnvironmentsAndPermissions

    // Permissions Pattern - S3
    grantReadWritePermissionsToAllAssetBuckets(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);

    // Permissions Pattern - KMS
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    // Global Permissions and Environment
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant

    // CDK Nag Suppressions
    suppressCdkNagLambda(fun);
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

// Grant access to external asset bucket customer managed KMS keys (no-op when
// no external bucket declares a bucketKmsKeyArn). The grant*AssetBuckets helpers
// above already call this; call it directly for locally-built container/Batch/
// ECS/EKS/Step Functions roles that read or write asset buckets.
grantExternalAssetBucketKmsKeys(roleOrFunction);

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

The Web ACL rules come from `config/policy/wafPolicyConfig.json` (loaded into `config.wafPolicyJSON` in `getConfig()` and passed through `cf-waf-stack.ts` as `props.wafPolicy`). Rule precedence is `props.rules` (explicit) > `buildRulesFromPolicy(props.wafPolicy)` (config-driven) > `legacyDefaultRules` (count-only Common Rule Set when no policy is supplied).

```typescript
// ✅ CORRECT - Real VAMS construct example
export interface WafPolicyConfig {
    managedRuleGroups?: Array<{
        name: string;
        vendorName: string;
        managedRuleGroupName: string;
        priority: number;
        block?: boolean; // true => group's own block actions apply; false => count-only
        // Per-rule overrides within the group: set one rule to count/allow/block without
        // disabling the group (e.g. SizeRestrictions_BODY -> count so large upload bodies pass,
        // SizeRestrictions_QUERYSTRING -> count so long presigned-URL query strings pass).
        ruleActionOverrides?: Array<{ name: string; action: "count" | "block" | "allow" }>;
    }>;
    rateBasedRules?: Array<{
        name: string;
        priority: number;
        limit: number; // per 5-min window per aggregate key
        aggregateKeyType?: string; // "IP" (default) or "FORWARDED_IP"
        forwardedIPConfig?: { headerName?: string; fallbackBehavior?: string }; // for FORWARDED_IP
        blockResponseCode?: number; // default 429 (throttle), with a JSON custom-response body
    }>;
}

function buildRulesFromPolicy(policy: WafPolicyConfig): Array<wafv2.CfnWebACL.RuleProperty> {
    const rules: Array<wafv2.CfnWebACL.RuleProperty> = [];
    for (const group of policy.managedRuleGroups || []) {
        const ruleActionOverrides = (group.ruleActionOverrides || []).map((o) => ({
            name: o.name,
            actionToUse:
                o.action === "count"
                    ? { count: {} }
                    : o.action === "allow"
                    ? { allow: {} }
                    : { block: {} },
        }));
        rules.push({
            name: group.name,
            priority: group.priority,
            overrideAction: group.block === false ? { count: {} } : { none: {} },
            statement: {
                managedRuleGroupStatement: {
                    vendorName: group.vendorName,
                    name: group.managedRuleGroupName,
                    ...(ruleActionOverrides.length ? { ruleActionOverrides } : {}),
                },
            },
            visibilityConfig: {
                sampledRequestsEnabled: true,
                cloudWatchMetricsEnabled: true,
                metricName: group.name,
            },
        });
    }
    // ... rateBasedRules omitted for brevity
    return rules;
}

export class Wafv2BasicConstruct extends Construct {
    public webacl: wafv2.CfnWebACL;

    constructor(parent: Construct, name: string, props: Wafv2BasicConstructProps) {
        super(parent, name);
        props = { ...defaultProps, ...props };
        const wafScopeString = props.wafScope!.toString();

        // Precedence: explicit props.rules > policy config > legacy default.
        const resolvedRules =
            props.rules ||
            (props.wafPolicy ? buildRulesFromPolicy(props.wafPolicy) : legacyDefaultRules);

        const webacl = new wafv2.CfnWebACL(this, "webacl", {
            description: "Basic WAF",
            defaultAction: { allow: {} },
            rules: resolvedRules,
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

The shipped `wafPolicyConfig.json` overrides two Common Rule Set rules to `count`. `SizeRestrictions_BODY` is the only Common Rule Set rule that blocks purely on body size (>8 KB), so counting it lets multi-part upload bodies up to the API Gateway REST 10 MB payload cap pass while every other managed rule keeps blocking. `SizeRestrictions_QUERYSTRING` is likewise overridden to `count`: it blocks query strings over 2048 bytes, and the SuperSplat viewer loads a file by passing a presigned Amazon S3 URL in a `?load=` parameter. A presigned URL carrying a session security token already approaches that limit, and the viewer requires the value double-encoded to survive its own two decode passes, which roughly doubles it again — so the iframe request for the static viewer page was blocked with a 403 before it ever reached S3.

The shipped `VAMS-RateLimit` rate-based rule uses `aggregateKeyType: FORWARDED_IP` (with `forwardedIPConfig` on `X-Forwarded-For`, `NO_MATCH` fallback) so it counts the real client IP behind CloudFront, an ALB, or a shared NAT/VPN egress — the same policy applies to both the CloudFront-scoped and regional web ACLs. The limit is set well above a single active user's request rate (VAMS polls execution status, does multi-part uploads, and streams large viewer files). Rate blocks return `429` (`blockResponseCode`, default 429) with a shared `CustomResponseBody` (`VamsRateLimitBody`) registered on the ACL — distinct from the `403` used for auth denials, so the web `apiClient` and the VAMS CLI treat it as a retryable throttle (honor `Retry-After`) rather than an auth failure. Unit test: `infra/test/wafRateLimit.test.ts`.

### **Security Helper Integration**

#### **What the Security Helpers Do**

-   **`kmsKeyLambdaPermissionAddToResourcePolicy`**: Grants KMS Decrypt/Encrypt/GenerateDataKey/ReEncrypt/ListKeys/CreateGrant/ListAliases on the VAMS KMS key
-   **`setupSecurityAndLoggingEnvironmentAndPermissions`**: Grants read on auth/constraints/userRoles/roles tables. Grants CloudWatch PutLogEvents on all 9 audit log groups. **No longer injects table or log group environment variables** (non-pipeline handlers resolve these from SSM).
-   **`globalLambdaEnvironmentsAndPermissions`**: Adds `VAMS_RESOURCE_PARAM_PREFIX` env var (SSM parameter prefix for resource name resolution) and grants ssm:GetParameter, ssm:GetParameters, ssm:GetParametersByPath on the deployment's resource-name parameter prefix.
-   **`isCognitoMfaCheckEnabled`** (used by the authorizer builder only): computes whether the API Gateway authorizer can reach Cognito for the MFA-preference check (Cognito enabled and the authorizer running **outside** the VPC — `FALSE` when Lambdas run in the VPC via `useForAllLambdas`, regardless of partition, because VAMS creates no Cognito VPC interface endpoints). Sets `COGNITO_AUTH_ENABLED` on the **authorizer Lambda only**; the authorizer resolves MFA status via `AdminGetUser` and passes `vams:mfaEnabled` to handlers through the authorizer context, so handler Lambdas need no Cognito access.
-   **`suppressCdkNagLambda`**: Applies the standard per-Lambda IAM4/IAM5 suppressions (AWSLambdaBasicExecutionRole, AWSLambdaVPCAccessExecutionRole, wildcard KMS actions), scoped to the function instead of the whole stack
-   **`suppressCdkNagErrorsByGrantReadWrite`**: Suppresses AwsSolutions-IAM5 for S3 and resource wildcards
-   **`suppressCdkNagLambdaFrameworkResources`**: Called once on the core stack. Applies the same IAM4/IAM5 suppressions only to CDK-generated framework roles (custom-resource providers, bucket deployments, `AwsCustomResource`) and VAMS custom-resource roles that the per-function helper cannot reach

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

        // Register routes into the cross-stack route registry. RestApiBuilder
        // renders the full registry into one OpenAPI spec on a single SpecRestApi.
        attachFunctionToApi(this, createAssetFunction, {
            routePath: "/assets",
            method: apigateway.HttpMethod.POST,
            registry: props.registry,
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
└── buildApiGatewayAuthorizerRestFunction()      # REST API REQUEST authorizer

backend/backend/handlers/auth/
└── apiGatewayAuthorizerRest.py      # REST REQUEST authorizer (returns IAM policy)

backend/backend/common/auth/
├── authorizerCore.py                # Shared auth logic (Cognito/external JWT, API key, IP)
├── clientIp.py                      # Trusted client-IP resolution + IP-range check
└── apiEvent.py                      # REST→canonical event normalization shim

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
export function buildApiGatewayAuthorizerRestFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "apiGatewayAuthorizerRest";

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
        const customAuthorizerFunction = buildApiGatewayAuthorizerRestFunction(
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
                resultsCacheTtl: cdk.Duration.seconds(30),
                identitySource: ["method.request.header.Authorization"],
            }
        );

        // The REST authorizer is declared as the OpenAPI security scheme applied
        // to all non-anonymous routes; RestApiBuilder builds the SpecRestApi from
        // the route registry and attaches this authorizer via the spec.
    }
}
```

#### **Path-Based Authorization Bypass**

```typescript
// ✅ CORRECT - Define ignored paths as constants
export const CUSTOM_AUTHORIZER_IGNORED_PATHS = ["/api/amplify-config", "/api/version"];

// ✅ CORRECT - Anonymous endpoints register with allowAnonymous: true so the
// OpenAPI spec omits the authorizer security scheme for that route. The authorizer
// also bypasses CUSTOM_AUTHORIZER_IGNORED_PATHS at runtime as defense-in-depth.
export class AmplifyConfigLambdaConstruct extends Construct {
    public readonly lambdaFn: lambda.Function;
    constructor(parent: Construct, name: string, props: AmplifyConfigLambdaConstructProps) {
        // ... lambda function creation; RestApiBuilder registers the route:
        // registry.register({ path: "/api/amplify-config", method: HttpMethod.GET,
        //                     lambdaFn: this.lambdaFn, allowAnonymous: true });
    }
}
```

#### **REST API CORS and Resource Policy**

CORS on the REST API is set in **three** places because REST responses come from three layers (the migration from HTTP API v2 removed the automatic ACAO injection HTTP APIs performed):

1. **OPTIONS preflight** — `buildOpenApiSpec.ts` emits a per-path OPTIONS **MOCK** method with **no `security`** (a preflight must be unauthenticated) that returns `Access-Control-Allow-Origin` (and allow-headers/methods). If OPTIONS carried an authorizer, the preflight itself would get 401/403 with no CORS headers.
2. **Gateway-level responses** — `rest-api-gateway-construct.ts` adds `GatewayResponse` resources for `DEFAULT_4XX` and `DEFAULT_5XX` that inject ACAO. Authorizer denials (401/403), missing-auth-token, and errors are produced by API Gateway itself and never reach a Lambda, so `commonHeaders()` cannot cover them; without this a token-expiry 401 is CORS-blocked in the browser and looks like a CORS bug.
3. **Lambda proxy response** — the handler adds ACAO to its own response body via `commonHeaders()`; API Gateway returns proxy responses verbatim.

**Resource policy** is always written explicitly to match `endpointType` (`buildOpenApiSpec.ts`): an `aws:SourceVpce`-restricted policy for `PRIVATE`, a public allow-all policy for `REGIONAL`. Amazon API Gateway does **not** remove a previously-set resource policy when an update omits one, so emitting it for both endpoint types ensures a `PRIVATE`↔`REGIONAL` switch overwrites the prior policy. A stale `PRIVATE` policy left on a now-`REGIONAL` API denies every request (including the CORS preflight) with `403 AccessDeniedException` at the resource-policy layer — a browser misreports this as a failed CORS preflight rather than an authorization error.

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
            // Handler-specific env vars only (resource names resolved from SSM)
            CUSTOM_CONFIG_VALUE: config.app.[feature].[setting].toString(),
        },
    });

    // DynamoDB Permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    // SSM resource name parameters grant via globalLambdaEnvironmentsAndPermissions

    // S3 Permissions
    grantReadWritePermissionsToAllAssetBuckets(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);

    // KMS Permissions
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    // Global Environment and Permissions
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant

    // CDK Nag Suppressions
    suppressCdkNagLambda(fun);
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

### **Rule 7: Documentation and Steering Files MUST Be Updated**

When making CDK infrastructure changes, update the corresponding documentation and steering files:

#### **Docusaurus Documentation Updates:**

-   **New config option** → Update `documentation/docusaurus-site/docs/deployment/configuration-reference.md`
-   **New config option** → Also mirror it into the interactive **ConfigBuilder** component (`documentation/docusaurus-site/src/components/ConfigBuilder/`) so the config generator stays in sync — see the component `README.md` for which files to touch (`schema.ts`, `defaults.ts`, `validation.ts`), then confirm the `infra/test/configBuilderSync.test.ts` drift check passes. The drift check only verifies `schema.ts` fields and `defaults.ts` presets — it does **not** cover `validation.ts`, so new/changed `getConfig()` validation logic must be hand-ported into `validation.ts` and kept in sync by review, not by the test. A missing rule leaves the ConfigBuilder approving a config that then fails `cdk synth`, which is worse than no validation because the operator was told it was valid. Two exclusions: rules reading a value the browser cannot see are out of scope — notably the `app.iamRoleConfig` checks, which validate the contents of `infra/config/policy/iamRoleConfig.json`. When checking the port, compare the config FIELD PATHS each rule references; the two files word the same rule differently, so matching on message text under-reports drift.
-   **New pipeline** → Create page in `pipelines/`, update `pipelines/overview.md`, `overview/features.md`, `sidebars.ts`
-   **New DynamoDB table** → Update `architecture/aws-resources.md`, `architecture/data-model.md`; add the resource-name constant to `infra/common/resourceParamKeys.ts`, `backend/backend/common/resourceNames.py`, AND `infra/deploymentDataMigration/tools/ssm_resource_lookup.py` (data-migration scripts resolve names from the published SSM parameters), then register the descriptor in `resourceNameRegistry` in `storageBuilder-nestedStack.ts`. Same three-way constants update for new audit CloudWatch log groups. Deprecated tables kept for migration move to `RESOURCE_PARAM_KEYS.dynamoTablesLegacy` (published under `dynamoTables/legacy/`).
-   **New or changed S3 bucket** → Update the Amazon S3 Buckets table in `architecture/aws-resources.md` (including its removal policy and whether it has a custom/fixed name) and the bucket list in `deployment/uninstall.md`
-   **New or changed CloudWatch log group** → Update the Amazon CloudWatch section in `architecture/aws-resources.md` and the log group cleanup in `deployment/uninstall.md`
-   **New nested stack** → Update `architecture/details.md`
-   **New feature switch** → Update `overview/features.md`
-   **New external configuration/policy file** (e.g. `config/policy/iamRoleConfig.json`) → Add it to the "Additional configuration files" table in `deployment/configuration-reference.md`, document the `config.json` flag that enables it, and explain the file structure.

:::note[Document two independent properties: removal policy and custom name]
When adding or changing a storage resource (Amazon S3 bucket, Amazon DynamoDB table) or Amazon CloudWatch log group, document **both** of these properties in `architecture/aws-resources.md`, and reflect them in `deployment/uninstall.md`:

1. **Removal on teardown** — `RemovalPolicy.RETAIN` (survives `cdk destroy`, needs manual deletion) vs. `RemovalPolicy.DESTROY` (removed automatically; pair S3 buckets with `autoDeleteObjects: true`).
2. **Custom name (redeploy-collision flag)** — Whether the resource sets an explicit name (`bucketName`, `tableName`, `logGroupName`, including deterministic `generateUniqueNameHash` names). Only explicitly named resources can cause a **name collision on redeploy** with the same configuration name and account.

These axes are independent. A resource that is **retained but auto-named** (for example, the VAMS asset, auxiliary, artefacts, and access logs buckets, and all DynamoDB tables) does **not** need to be deleted before redeploying with the same config — leave it unless you intend to remove the data. A resource with a **custom/fixed name** (for example, the ALB web app bucket and its access logs bucket, named for the domain host; and all `/aws/vendedlogs/...` log groups) **must** be flagged so operators delete any orphaned copy before redeploying.
:::

#### **Cross-Steering File Updates:**

When changes affect development standards, architecture patterns, or quality requirements:

1. Update **all** affected CLAUDE.md files (root, web/, backend/, infra/, tools/VamsCLI/, documentation/)
2. Update the `.kiro/steering/` version of affected workflow files
3. Keep WEB_DEVELOPMENT_WORKFLOW.md, WEB_FRONTEND.md, BACKEND_CDK_DEVELOPMENT_WORKFLOW.md, CDK_DEVELOPMENT_WORKFLOW.md, CLI_DEVELOPMENT_WORKFLOW.md, and DOCUMENTATION_WORKFLOW.md aligned when cross-component patterns change
4. Update any Claude Code skills in `.claude/commands/` that scaffold or reference the changed rule, pattern, checklist, or file path (see root `CLAUDE.md` Rule 12 for the skill-to-steering mapping) — a stale skill actively scaffolds outdated code

---

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

#### **Step 4: Update Documentation and the ConfigBuilder**

The docs-site config generator is a hand-maintained mirror of `config.ts` — it does **not** auto-update. After adding the option:

1. Document it in `documentation/docusaurus-site/docs/deployment/configuration-reference.md`.
2. Update the **ConfigBuilder** component (`documentation/docusaurus-site/src/components/ConfigBuilder/`):
    - `schema.ts` — add a `FIELDS` entry (path + label + input kind + section).
    - `defaults.ts` — add the default (kept deep-equal to `config.template.commercial.json` / `config.template.govcloud.json`).
    - `validation.ts` — add a `Rule` mirroring any new `throw new Error(...)` / `console.warn(...)` you added in `getConfig()`.
3. Run `cd infra && npm test` — the `configBuilderSync.test.ts` drift check deep-equals `defaults.ts` against the templates and asserts every `ConfigPublic` leaf has a form field. **Note:** the test covers only `schema.ts` (fields) and `defaults.ts` (presets); it does **not** validate `validation.ts` against `getConfig()`. Keeping the `validation.ts` rules in step with `getConfig()`'s `throw`/`warn` logic is a manual, review-enforced task — a stale or missing rule will not be caught by any test.

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

## 🌍 **Partition Portability (Commercial / GovCloud / EU Sovereign)**

VAMS deploys to `aws`, `aws-us-gov`, `aws-eusc` (EU Sovereign Cloud, region `eusc-de-east-1`), and potentially `aws-cn` / `aws-iso*`. Partition defects are invisible in commercial synth and unit tests, and usually surface as a `CREATE_FAILED` **mid-deploy**, rolling back the whole core stack (~30 min).

### **`govCloud.enabled` is the restricted-partition flag, not a GovCloud flag**

**Both `config.template.govcloud.json` and `config.template.eusovereign.json` set `app.govCloud.enabled: true`**; only commercial sets `false`. Read it as "this partition has reduced service/feature capability" — gating a capability downgrade on it covers EU Sovereign automatically.

-   Use `config.app.govCloud.enabled` for a downgrade shared by every restricted partition (the EventSourceMapping tag strips, the Cognito feature downgrades, the API Gateway TLS-policy skip).
-   Use `config.env.partition` / `Partition()` when the decision must hold regardless of operator flag hygiene, or is genuinely partition-specific (the commercial-only EventBridge bus CMK, the SAML and Deadline Cloud `=== "aws"` gates, the `aws-eusc` OpenSearch version pick).
-   **Never write `Partition() === "aws-us-gov"`** — it misses EU Sovereign. When a deny-list is needed, name every restricted partition explicitly (the VPC builder's Cognito-PrivateLink check is the model: it excludes `aws-us-gov`, `aws-eusc`, `aws-iso*` while still allowing `aws-cn`, where the service exists).

> **Known gap:** nothing validates that `app.govCloud.enabled` agrees with `config.env.partition`. Deploying to a restricted partition with the flag left `false` passes synth, then fails at the first EventSourceMapping with "Tags not supported in request."

### **Checklist for new infrastructure**

1. **Event source mappings — never call `fun.addEventSource()` unconditionally.** CDK stamps the stack tags onto the underlying `AWS::Lambda::EventSourceMapping`, which GovCloud/EU Sovereign Lambda rejects. This covers SQS (`eventSourceArn`) and DynamoDB streams (`tableStreamArn`) alike; it is the only CFN resource type in VAMS needing a property stripped for partition reasons.

    ```typescript
    queue.grantConsumeMessages(fun); // addEventSource() did this implicitly; do it explicitly now
    if (config.app.govCloud.enabled) {
        const esm = new lambda.EventSourceMapping(scope, "MyQueueSqsEventSource", {
            eventSourceArn: queue.queueArn,
            target: fun,
            batchSize: 10,
            maxBatchingWindow: Duration.seconds(3),
        });
        (esm.node.defaultChild as lambda.CfnEventSourceMapping).addPropertyDeletionOverride("Tags");
    } else {
        fun.addEventSource(
            new eventsources.SqsEventSource(queue, {
                batchSize: 10,
                maxBatchingWindow: Duration.seconds(3),
            })
        );
    }
    ```

    No CDK aspect can do this for you — the L1 is created lazily inside `addEventSource()`, after aspects finish visiting the tree. Regression coverage: `infra/test/eventSourceMappingGovCloudTags.test.ts`.

2. **Never hardcode a partition, DNS suffix, or region.** Use `Service("X").ARN(...)` / `.Endpoint` / `.Principal`, `IAMArn(name)`, `Partition()`. Suffixes differ (`.amazonaws.com`, `.amazonaws.com.cn`, **`.amazonaws.eu`** for `aws-eusc`, `.c2s.ic.gov`, …), while service **principals** stay `.amazonaws.com` in `aws-us-gov` and `aws-eusc` — so a literal `ServicePrincipal("lambda.amazonaws.com")` happens to work there but is wrong in `aws-cn`/ISO.

3. **A new `Service()` call means checking `SERVICE_LOOKUP` coverage.** `ServiceFormatter` **throws** at synth (`Service ${name} not found in partition ${partition}`) when the partition entry is missing. `AOSS`, `GEO`, `CLOUDFRONT`, and `COGNITO_HOSTED_UI` are not present for every partition. Resolve it one of two ways — a product decision, not a mechanical one: **add the partition entry** in `infra/lib/helper/const.ts` if the service exists there, or **forbid the feature in `getConfig()`** if it does not. Prefer the validation when the service is genuinely unavailable, because a `Service ${name} not found` throw names the service rather than the configuration field that caused it and sends the operator to the wrong file. Worked example: OpenSearch Serverless is not offered in the EU Sovereign Cloud, so `getConfig()` rejects `openSearch.useServerless.enabled` for `aws-eusc` and points at `useProvisioned`.

4. **Gate unavailable services in `getConfig()`** so a bad combination fails at synth with a clear message rather than mid-deploy; mirror the rule into `ConfigBuilder/validation.ts` by hand.

5. **Service versions and model ids can differ.** `OPENSEARCH_VERSION_EUSOVEREIGN` (2.19 vs 3.5) is selected on `Partition() === "aws-eusc"`; the Bedrock model id is downgraded in both restricted templates.

6. **Update all three config templates together** — `commercial`, `govcloud`, `eusovereign`. `useFips` is the one capability flag where the restricted templates disagree (`true` GovCloud, `false` EU Sovereign).

7. **No internet egress at build time.** A `curl`/download in a Docker bundling command pinned to a commercial S3 host fails on a restricted-partition build host.

8. **IAM resource matching is case-sensitive.** `/aws/vendedlogs/*` grants are explicit allow-lists, and pipeline constructs are split across `VAMSStateMachine-*` and `VAMSstateMachine-*` (both granted). A new pipeline inventing a third casing silently loses log-read access.

### **Verifying a partition change**

Assert the restricted output **and** the commercial output — otherwise a correct tag strip is indistinguishable from a resource that was never emitted. Inspect the emitted nested template, not the construct tree, and prefer a Jest test over a one-off synth (see `infra/test/eventSourceMappingGovCloudTags.test.ts`, which tags its test stack the way `core-stack.ts` does so the assertion is load-bearing rather than vacuous).

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
11. **Always** match the surrounding comment density and style — describe **what** code is, not why it was added; never reference "upgrades", "new in vX", or the prompting change request in source comments (changelog narration belongs in `CHANGELOG.md` and the docs revision history, not in code)

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

### **Platform-specific native bindings in the lockfile**

`esbuild` is a direct dependency of `infra/` because `NodejsFunction` bundling runs it at synth (e.g. the OpenSearch schema-deploy Lambda). npm records only the compiled binary matching the platform that generated the lockfile ([npm/cli#4828](https://github.com/npm/cli/issues/4828)), and a later `npm install` on a different platform does **not** add the missing one — a lockfile written on Windows leaves a Linux CI runner without `@esbuild/linux-x64`.

`infra/package.json` therefore declares the other platforms explicitly under `optionalDependencies` (`@esbuild/linux-x64`, `@esbuild/darwin-arm64`, `@esbuild/darwin-x64`). Each carries its own `os`/`cpu` constraints, so only the matching binary is installed.

**The versions are coupled to `esbuild` and no test catches drift.** When bumping it, re-pin these to the version `npm ls esbuild` reports, then confirm every platform is still recorded:

```bash
node -e "const l=require('./package-lock.json');Object.entries(l.packages).filter(([,v])=>v.os).forEach(([k,v])=>console.log(k,v.os))"
```

Expect `darwin`, `linux`, **and** `win32`. If one is missing, add it with `npm install --package-lock-only --save-optional <pkg>@<version>`; `npm install --force` and the `--os`/`--cpu` flags do **not** repair an already-pruned lockfile. `web/` needs the same treatment for rolldown and esbuild (see `.kiro/steering/WEB_DEVELOPMENT_WORKFLOW.md`).

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
