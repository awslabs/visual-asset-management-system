# CLAUDE.md -- VAMS CDK Infrastructure

This is the Claude Code steering document for the `infra/` directory. It is auto-loaded when Claude Code operates within the VAMS CDK infrastructure-as-code.

---

## Project Identity

-   **Name**: VAMS (Visual Asset Management System) -- CDK Infrastructure
-   **Version**: (tracked in `config/config.ts` as `VAMS_VERSION`)
-   **Runtime**: AWS CDK v2 (TypeScript), targeting `aws-cdk-lib`
-   **Node**: NODEJS_20_X for Lambda and CDK
-   **Python**: PYTHON_3_12 for all Lambda functions
-   **Lambda Memory**: 5308 MB (all functions)
-   **Lambda Timeout**: 15 minutes (all functions)
-   **License**: Apache-2.0

---

## Directory Structure

> **Maintenance note:** Update this tree when adding new nested stacks, lambda builders, constructs, or pipeline types. See root `CLAUDE.md` Rule 11.

```
infra/
  bin/
    infra.ts                    # CDK app entry point
  common/
    vamsAppFeatures.ts          # VAMS_APP_FEATURES enum (feature flags)
  config/
    config.ts                   # Config interfaces, getConfig(), constants
    config.json                 # Active deployment configuration
    config.template.commercial.json  # Commercial template
    config.template.govcloud.json    # GovCloud template
    saml-config.ts              # SAML provider settings
    csp/                        # CSP additional config (cspAdditionalConfig.json)
    docker/                     # Docker build configurations
    policy/                     # S3 additional bucket policy JSON
  gen/
    genEndpoints.ts             # Endpoint generation utility
  lib/
    core-stack.ts               # CoreVAMSStack -- root stack orchestrator
    cf-waf-stack.ts             # WAF stack (us-east-1 for CloudFront, regional for ALB)
    aspects/
      iam-role-transform.aspect.ts  # IAM role naming + permission boundaries
      log-retention.aspect.ts       # Forces 1-year log retention on all LogGroups
    constructs/
      wafv2-basic-construct.ts  # WAFv2 web ACL construct
    helper/
      const.ts                  # SERVICE_LOOKUP: partition-aware endpoints (aws, aws-us-gov, aws-cn, aws-iso)
      lambda.ts                 # Layer bundling commands (poetry-based)
      s3AssetBuckets.ts         # Global asset bucket registry (shared across stacks)
      security.ts               # KMS, CDK Nag, CSP, TLS enforcement, audit logging setup
      service-helper.ts         # ServiceFormatter class: ARN(), Endpoint, Principal
    lambdaBuilder/              # ~17 builder files, ~40+ function builders
      assetFunctions.ts
      assetsLinkFunctions.ts
      authFunctions.ts          # Includes buildApiKeyServiceFunction
      commentFunctions.ts
      configFunctions.ts
      databaseFunctions.ts
      metadataFunctions.ts
      metadataSchemaFunctions.ts
      pipelineFunctions.ts
      roleFunctions.ts
      searchIndexBucketSyncFunctions.ts
      sendEmailFunctions.ts
      subscriptionFunctions.ts
      tagFunctions.ts
      tagTypeFunctions.ts
      userRoleFunctions.ts
      workflowFunctions.ts
    nestedStacks/
      vpc/
        vpcBuilder-nestedStack.ts          # VPC, subnets, VPC endpoints
      storage/
        storageBuilder-nestedStack.ts      # ~1800 lines: DynamoDB tables, S3, SNS, SQS, KMS, CloudWatch
        customResources/
          populateS3AssetBucketsTable.ts   # Custom resource for S3 bucket table population
      auth/
        authBuilder-nestedStack.ts         # Cognito user pool, identity pool, SAML, external OAuth
        constructs/
          cognito-web-native-construct.ts
          dynamodb-authdefaults-admin-construct.ts
          dynamodb-authdefaults-ro-construct.ts
      apiLambda/
        apigatewayv2-amplify-nestedStack.ts  # API Gateway V2 HttpApi + Lambda authorizer
        apiBuilder-nestedStack.ts            # ~1375 lines: all API routes + Lambda wiring
        lambdaLayersBuilder-nestedStack.ts   # Lambda layer construction
        constructs/
          apigatewayv2-lambda-construct.ts       # Route attachment helper
          amplify-config-lambda-construct.ts     # /api/amplify-config endpoint
          vams-version-lambda-construct.ts       # /api/version endpoint
          dynamodb-metadataschema-defaults-construct.ts
      staticWebApp/
        staticWebBuilder-nestedStack.ts    # S3 + CloudFront or ALB web hosting
        constructs/
          cloudfront-s3-website-construct.ts
          alb-s3-website-albDeploy-construct.ts
          gateway-albDeploy-construct.ts
          custom-cognito-config-construct.ts
      searchAndIndexing/
        searchBuilder-nestedStack.ts       # OpenSearch serverless or provisioned
        constructs/
          opensearch-serverless.ts
          opensearch-provisioned.ts
          schemaDeploy/
            deployschema.ts
      pipelines/
        pipelineBuilder-nestedStack.ts     # Pipeline orchestrator
        constructs/
          batch-fargate-pipeline.ts
          batch-gpu-pipeline.ts
          securitygroup-gateway-pipeline-construct.ts
        conversion/
          3dBasic/                          # 3D file conversion pipeline
          meshCadMetadataExtraction/        # CAD/mesh metadata extraction
        preview/
          pcPotreeViewer/                   # Point cloud Potree viewer pipeline
          3dThumbnail/                      # 3D preview thumbnail pipeline (GIF/JPG/PNG)
        3dRecon/
          splatToolbox/                     # Gaussian splatting pipeline
        genAi/
          metadata3dLabeling/              # AI-powered metadata labeling
        multi/
          modelOps/                        # Model optimization pipeline
          rapidPipeline/                   # RapidPipeline ECS
          rapidPipelineEKS/               # RapidPipeline EKS
        simulation/
          isaacLabTraining/               # NVIDIA Isaac Lab training
      featureEnabled/
        custom-featureEnabled-config-nestedStack.ts  # Feature flag DynamoDB persistence
      locationService/
        location-service-nestedStack.ts    # Amazon Location Service (commercial only)
      addon/
        addonBuilder-nestedStack.ts        # Addon orchestrator
        garnetFramework/                   # Garnet NGSI-LD digital twin framework
  test/
    infra.test.ts              # Single snapshot test (outdated, uses legacy @aws-cdk/assert)
  deploymentDataMigration/     # Data migration utilities
    v2.4_to_v2.5/upgrade/    # Backfills databaseId and databaseId:assetId on asset version records
```

---

## Architecture Overview

### Nested Stack Dependency Chain

```
CoreVAMSStack (root)
  |
  +-- VPCBuilder (conditional: useGlobalVpc.enabled)
  +-- LambdaLayers
  +-- StorageResourcesBuilder (foundation: DynamoDB, S3, SNS, SQS, KMS, CloudWatch)
  |     |
  |     +-- AuthBuilder (depends on Storage)
  |     |     |
  |     |     +-- ApiGatewayV2Amplify (API Gateway + authorizer)
  |     |     |     |
  |     |     |     +-- ApiBuilder (all API route Lambda wiring)
  |     |     |     +-- StaticWeb (CloudFront or ALB hosting)
  |     |     |     +-- SearchBuilder (OpenSearch)
  |     |     |     +-- PipelineBuilder (all use-case pipelines)
  |     |     |     +-- AddonBuilder (Garnet)
  |     |
  +-- LocationService (conditional: useLocationService.enabled)
  +-- CustomFeatureEnabledConfig (writes enabled features to DynamoDB)
```

### Cross-Stack Shared Interfaces

**`storageResources`** (defined in `storageBuilder-nestedStack.ts`):

```typescript
interface storageResources {
    encryption: { kmsKey?: kms.IKey };
    s3: {
        assetAuxiliaryBucket: s3.Bucket;
        artefactsBucket: s3.Bucket;
        accessLogsBucket: s3.Bucket;
    };
    sqs: { workflowAutoExecuteQueue: sqs.Queue };
    sns: {
        eventEmailSubscriptionTopic: sns.Topic;
        fileIndexerSnsTopic: sns.Topic;
        assetIndexerSnsTopic: sns.Topic;
        databaseIndexerSnsTopic: sns.Topic;
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
        // 20+ DynamoDB tables -- see storageBuilder-nestedStack.ts lines 72-98
        appFeatureEnabledStorageTable;
        assetLinksStorageTableV2;
        assetLinksMetadataStorageTable;
        assetStorageTable;
        assetUploadsStorageTable;
        assetVersionsStorageTable;
        assetFileVersionsStorageTable;
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
        apiKeyStorageTable: dynamodb.Table; // GSIs: apiKeyHashIndex (PK: apiKeyHash), userIdIndex (PK: userId)
        workflowStorageTable: dynamodb.Table;
        // assetVersionsStorageTable has GSI: databaseIdAssetIdIndex (PK: databaseId:assetId, SK: assetVersionId)
    };
}
```

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

---

## Configuration System

### 3-Tier Fallback Chain

Configuration values resolve in order:

1. CDK context (`-c key=value`)
2. `config/config.json` file
3. Environment variables
4. Hardcoded defaults

The entry point `bin/infra.ts` calls `Config.getConfig(app)` then `Service.SetConfig(config)`.

### Key Constants (config/config.ts)

| Constant                          | Value                                     |
| --------------------------------- | ----------------------------------------- |
| `VAMS_VERSION`                    | `"2.X.0"`                                 |
| `LAMBDA_PYTHON_RUNTIME`           | `Runtime.PYTHON_3_12`                     |
| `LAMBDA_NODE_RUNTIME`             | `Runtime.NODEJS_20_X`                     |
| `LAMBDA_MEMORY_SIZE`              | `5308`                                    |
| `OPENSEARCH_VERSION`              | `OPENSEARCH_2_7`                          |
| `CUSTOM_AUTHORIZER_IGNORED_PATHS` | `["/api/amplify-config", "/api/version"]` |

### ConfigPublic Interface

The `ConfigPublic` interface (~200 lines in `config/config.ts`) defines all deployment parameters. Key sections:

-   `env`: account, region, partition, coreStackName
-   `app.assetBuckets`: createNewBucket, defaultNewBucketSyncDatabaseId, externalAssetBuckets
-   `app.useGlobalVpc`: enabled, useForAllLambdas, addVpcEndpoints, optionalExternalVpcId, vpcCidrRange
-   `app.openSearch`: useServerless, useProvisioned, reindexOnCdkDeploy
-   `app.useAlb`: enabled, usePublicSubnet, domainHost, certificateArn
-   `app.useCloudFront`: enabled, customDomain (domainHost, certificateArn, optionalHostedZoneId)
-   `app.pipelines`: useConversion3dBasic, useConversionCadMeshMetadataExtraction, usePreviewPcPotreeViewer, useSplatToolbox, useGenAiMetadata3dLabeling, useRapidPipeline (useEcs, useEks), useModelOps, useIsaacLabTraining
-   `app.addons`: useGarnetFramework
-   `app.authProvider`: useCognito (enabled, useSaml, useUserPasswordAuthFlow), useExternalOAuthIdp, authorizerOptions.allowedIpRanges
-   `app.api`: globalRateLimit (default 50), globalBurstLimit (default 100)
-   `app.govCloud`: enabled, il6Compliant
-   `app.webUi`: optionalBannerHtmlMessage, allowUnsafeEvalFeatures

### Config extends ConfigPublic (Internal)

Adds: `enableCdkNag`, `dockerDefaultPlatform`, `s3AdditionalBucketPolicyJSON`, `openSearchAssetIndexName`, `openSearchFileIndexName`, SSM parameter paths.

### Feature Flags (common/vamsAppFeatures.ts)

```typescript
enum VAMS_APP_FEATURES {
    GOVCLOUD,
    ALLOWUNSAFEEVAL,
    LOCATIONSERVICES,
    ALBDEPLOY,
    CLOUDFRONTDEPLOY,
    NOOPENSEARCH,
    AUTHPROVIDER_COGNITO,
    AUTHPROVIDER_COGNITO_SAML,
    AUTHPROVIDER_EXTERNALOAUTHIDP,
}
```

Features are tracked in the `enabledFeatures` array on `CoreVAMSStack` and persisted to DynamoDB by `CustomFeatureEnabledConfigNestedStack`.

---

## Lambda Builder Pattern

All 17 lambda builder files in `lib/lambdaBuilder/` follow a strict, consistent pattern. Every function builder:

### Standard Function Signature

```typescript
export function buildSomeFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
```

### Standard Lambda Configuration

```typescript
const name = "functionName";
const fun = new lambda.Function(scope, name, {
    code: lambda.Code.fromAsset(path.join(__dirname, "../../../backend/backend")),
    handler: `handlers.{category}.${name}.lambda_handler`,
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
        // Table name environment variables
    },
});
```

### 4 Required Security Calls (Every Lambda Builder)

Every lambda builder function MUST include these four calls after creating the function:

```typescript
// 1. KMS permissions (if encryption enabled)
kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

// 2. Auth table access + audit log setup
setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);

// 3. Global environment variables (Cognito auth flag)
globalLambdaEnvironmentsAndPermissions(fun, config);

// 4. CDK Nag suppression for S3 grant patterns
suppressCdkNagErrorsByGrantReadWrite(scope);
```

### What the Security Helpers Do

-   **`kmsKeyLambdaPermissionAddToResourcePolicy`**: Grants KMS Decrypt/Encrypt/GenerateDataKey/ReEncrypt/ListKeys/CreateGrant/ListAliases on the VAMS KMS key
-   **`setupSecurityAndLoggingEnvironmentAndPermissions`**: Adds env vars for AUTH_TABLE_NAME, CONSTRAINTS_TABLE_NAME, USER_ROLES_TABLE_NAME, ROLES_TABLE_NAME + 9 audit log group env vars. Grants read on auth/constraints/userRoles/roles tables. Grants CloudWatch PutLogEvents on all audit log groups.
-   **`globalLambdaEnvironmentsAndPermissions`**: Sets COGNITO_AUTH_ENABLED based on Cognito + VPC configuration
-   **`suppressCdkNagErrorsByGrantReadWrite`**: Suppresses AwsSolutions-IAM5 for S3 and resource wildcards

---

## API Gateway Pattern

### HttpApi Setup (apigatewayv2-amplify-nestedStack.ts)

-   API Gateway V2 HttpApi with custom Lambda authorizer
-   Authorizer: `HttpLambdaResponseType.SIMPLE`, cache TTL 30 seconds, identity source `$request.header.Authorization`
-   CORS: all origins (`*`), standard + auth headers, all HTTP methods, credentials=false
-   Rate limiting: `config.app.api.globalRateLimit` (default 50) / `config.app.api.globalBurstLimit` (default 100)
-   Access logging to CloudWatch with structured JSON format

### Route Attachment (attachFunctionToApi helper)

Routes are wired in `apiBuilder-nestedStack.ts` using:

```typescript
attachFunctionToApi(this, lambdaFunction, {
    routePath: "/database/{databaseId}",
    method: apigateway.HttpMethod.GET,
    api: api,
});
```

This creates an `ApiGatewayV2LambdaConstruct` which:

1. Grants invoke permission to the API Gateway service principal
2. Creates an HttpLambdaIntegration
3. Adds the route to the API

### RESTful Route Convention

Routes use path parameters: `/database/{databaseId}/assets/{assetId}`

Additional asset version routes:

-   `PUT /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}` -- update version (alias, comment)
-   `POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive` -- archive version
-   `POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive` -- unarchive version

Unauthenticated paths (no authorizer): `/api/amplify-config`, `/api/version`

---

## Service Helper (Partition-Aware ARN/Endpoint Generation)

### Critical Initialization

```typescript
// In bin/infra.ts -- MUST be called at startup
const config = Config.getConfig(app);
Service.SetConfig(config); // Required before any Service() call
```

### ServiceFormatter Class (lib/helper/service-helper.ts)

```typescript
Service(name: SERVICE, useFipsOverride?: boolean): ServiceFormatter
// Returns object with:
//   .ARN(resource, resourceName?)  -- partition-aware ARN
//   .Endpoint                      -- hostname (FIPS-aware)
//   .Principal                     -- iam.ServicePrincipal
//   .PrincipalString               -- string principal

IAMArn(name: string): { role, policy, statemachine, statemachineExecution,
    stateMachineEvents, lambda, subnet, vpc, securitygroup, ssm, loggroup,
    geomap, geoapi }

Partition(): string  // Returns current partition
```

### Partition Lookup (lib/helper/const.ts)

Massive lookup table supporting 4 AWS partitions:

-   `aws` (commercial)
-   `aws-us-gov` (GovCloud)
-   `aws-cn` (China)
-   `aws-iso` (isolated)

Each entry contains: `arn`, `hostname`, `fipsHostname`, `principal`

---

## Security Patterns

### CDK Nag (Always Enabled)

```typescript
// bin/infra.ts
config.enableCdkNag = true;
if (config.enableCdkNag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
}
```

CDK Nag suppressions are applied at multiple levels:

-   **Stack-level**: AwsSolutions-COG3 for GovCloud, AwsSolutions-IAM4/IAM5 for Lambda execution roles
-   **Resource-level**: `suppressCdkNagErrorsByGrantReadWrite()` in every lambda builder
-   **Path-level**: Specific suppressions for workflow IAM roles

### KMS Encryption

-   Optional CMK via `config.app.useKmsCmkEncryption`
-   `kmsKeyLambdaPermissionAddToResourcePolicy()` grants Lambda access to KMS key
-   `kmsKeyPolicyStatementPrincipalGenerator()` creates key policy with service principals (S3, DynamoDB, SQS, SNS, ECS, EKS, Lambda, etc.)

### S3 TLS Enforcement

Every S3 bucket gets:

```typescript
requireTLSAndAdditionalPolicyAddToResourcePolicy(bucket, config);
// Adds Deny policy for s3:* when aws:SecureTransport=false
// Plus optional additional policy from config/policy/s3AdditionalBucketPolicyConfig.json
```

### Content Security Policy (CSP)

`generateContentSecurityPolicy()` in `security.ts` builds CSP headers for the web app:

-   Base sources: self, blob, data, API URL, S3 endpoint
-   Conditional: Cognito IDP/Identity endpoints, Location Service, unsafe-eval
-   Extensible via `config/csp/cspAdditionalConfig.json`

### IAM Aspects

-   **IamRoleTransform**: Applies role name prefixes and permission boundaries (from `cdk.json` "aws" environment settings)
-   **LogRetentionAspect**: Forces `RetentionDays.ONE_YEAR` on all CfnLogGroup resources in the stack

---

## GovCloud Considerations

### Required Configuration

When `config.app.govCloud.enabled = true`:

1. `useGlobalVpc.enabled` MUST be `true`
2. `useCloudFront.enabled` MUST be `false` (no CloudFront in GovCloud)
3. `useLocationService.enabled` MUST be `false`

### IL6 Compliance (Additional)

When `config.app.govCloud.il6Compliant = true`:

1. Cognito MUST be disabled (`useCognito.enabled = false`)
2. WAF MUST be disabled (`useWaf = false`)
3. KMS CMK encryption MUST be enabled (`useKmsCmkEncryption.enabled = true`)

### GovCloud-Specific Behavior

-   FIPS endpoints via `config.app.useFips` (used by ServiceFormatter)
-   `AwsSolutions-COG3` suppressed (AdvancedSecurityMode not available)
-   EventSourceMapping tags removed via `addPropertyDeletionOverride` (some resources don't support tags in GovCloud)
-   VPC endpoints conditional on feature flags
-   ALB deployment instead of CloudFront for static web hosting

---

## Development Rules

### 1. Configuration Changes

1. Add new properties to `ConfigPublic` interface in `config/config.ts`
2. Add backward-compatibility defaults in `getConfig()` (check for `undefined`)
3. Add validation logic in `getConfig()` if constraints exist
4. Update BOTH template files: `config.template.commercial.json` and `config.template.govcloud.json`
5. Update `config.json` for the active deployment

### 2. Adding a New Lambda Function

1. Create the builder function in the appropriate file under `lib/lambdaBuilder/`
2. Follow the standard pattern exactly:
    - `lambda.Code.fromAsset(path.join(__dirname, '../../../backend/backend'))`
    - `handler: handlers.{category}.${name}.lambda_handler`
    - `runtime: LAMBDA_PYTHON_RUNTIME`
    - `timeout: Duration.minutes(15)`
    - `memorySize: Config.LAMBDA_MEMORY_SIZE`
    - VPC conditional on `config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas`
3. Grant DynamoDB table permissions (grantReadData or grantReadWriteData)
4. Apply ALL 4 security calls:
    - `kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey)`
    - `setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources)`
    - `globalLambdaEnvironmentsAndPermissions(fun, config)`
    - `suppressCdkNagErrorsByGrantReadWrite(scope)`
5. Wire the function to API Gateway in `apiBuilder-nestedStack.ts` using `attachFunctionToApi()`

### 3. Adding a New Nested Stack

1. Create file at `lib/nestedStacks/{name}/{name}Builder-nestedStack.ts`
2. Extend `NestedStack`
3. Accept `config`, `storageResources`, and other shared resources as constructor params
4. Instantiate in `core-stack.ts` with `addDependency(storageResourcesNestedStack)`
5. Export any resources needed by other stacks via public properties

### 4. Adding a New DynamoDB Table

1. Add to `storageResources` interface in `storageBuilder-nestedStack.ts`
2. Create the table in `storageResourcesBuilder()` function
3. Apply KMS encryption if `config.app.useKmsCmkEncryption.enabled`
4. Add `RemovalPolicy.DESTROY` (current pattern -- all tables use DESTROY)
5. Update lambda builders to reference the new table name env var and grant permissions

### 5. Service Helper Usage

Always use the `Service()` helper for partition-aware resources:

```typescript
// Correct -- partition-aware
Service("S3").Endpoint;
Service("DYNAMODB").ARN("table/myTable");
Service("LAMBDA").Principal;

// Wrong -- hardcoded partition
("arn:aws:s3:::my-bucket");
("dynamodb.us-east-1.amazonaws.com");
```

---

## Anti-Patterns to Avoid

1. **Hardcoding ARN partitions**: Never use `arn:aws:`. Always use `Service()` or `Service.Partition()` for partition-aware ARNs. The system supports aws, aws-us-gov, aws-cn, and aws-iso.

2. **Skipping security calls**: Every lambda builder MUST include all 4 security helper calls. Missing `setupSecurityAndLoggingEnvironmentAndPermissions` breaks auth checking in Lambda handlers.

3. **Forgetting VPC conditional**: All Lambda functions must conditionally attach to VPC:

    ```typescript
    vpc: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
        ? vpc
        : undefined;
    ```

4. **Hardcoding Lambda runtime/memory**: Always use `LAMBDA_PYTHON_RUNTIME` and `Config.LAMBDA_MEMORY_SIZE` constants.

5. **Missing backward compatibility**: When adding new config properties, always add `undefined` checks in `getConfig()` to handle old config files.

6. **Not calling Service.SetConfig()**: The service helper module-level `config` must be initialized via `SetConfig(config)` in `bin/infra.ts` before any `Service()` calls.

7. **Creating resources without CDK Nag suppression**: CDK Nag is always enabled. New IAM policies, S3 buckets, or Lambda functions will fail synthesis without appropriate suppressions.

8. **Ignoring GovCloud constraints**: Features conditional on GovCloud (CloudFront, Location Service, Cognito AdvancedSecurityMode) must be checked before use.

9. **Forgetting stack dependencies**: All nested stacks that use `storageResources` must call `nestedStack.addDependency(storageResourcesNestedStack)`.

10. **Using `grantReadWrite` without Nag suppression**: S3 bucket `grantRead`/`grantReadWrite` generates IAM wildcard actions that CDK Nag flags. Always pair with `suppressCdkNagErrorsByGrantReadWrite(scope)`.

---

## Templates

### New Lambda Builder Function

```typescript
export function buildMyNewFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "myNewFunction";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.myCategory.${name}.lambda_handler`,
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
            MY_TABLE_NAME: storageResources.dynamo.myTable.tableName,
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.myTable.grantReadWriteData(fun);

    // Required security calls (all 4)
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

### New API Route Wiring (in apiBuilder-nestedStack.ts)

```typescript
// Build the function
const myFunction = buildMyNewFunction(
    this,
    lambdaCommonBaseLayer,
    storageResources,
    config,
    vpc,
    subnets
);

// Wire to API Gateway
attachFunctionToApi(this, myFunction, {
    routePath: "/my-resource/{resourceId}",
    method: apigateway.HttpMethod.GET,
    api: api,
});
attachFunctionToApi(this, myFunction, {
    routePath: "/my-resource",
    method: apigateway.HttpMethod.POST,
    api: api,
});
```

### New Nested Stack

```typescript
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as Config from "../../../config/config";
import { storageResources } from "../storage/storageBuilder-nestedStack";

export interface MyBuilderNestedStackProps {
    config: Config.Config;
    storageResources: storageResources;
    // Add other required resources
}

export class MyBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: MyBuilderNestedStackProps) {
        super(parent, name);
        // Build resources here
    }
}
```

### New Config Property (with backward compatibility)

```typescript
// 1. Add to ConfigPublic interface
app: {
    myNewFeature: {
        enabled: boolean;
        someOption: string;
    }
}

// 2. Add backward-compatibility check in getConfig()
if (config.app.myNewFeature == undefined) {
    config.app.myNewFeature = {
        enabled: false,
        someOption: "",
    };
}

// 3. Add validation if needed
if (config.app.myNewFeature.enabled && !config.app.myNewFeature.someOption) {
    throw new Error("Configuration Error: myNewFeature requires someOption when enabled");
}
```

---

## Pipeline Nested Stack Pattern

Each pipeline follows a consistent structure:

```
lib/nestedStacks/pipelines/{category}/{pipelineName}/
    {pipelineName}Builder-nestedStack.ts    # Stack definition
    constructs/
        {pipelineName}-construct.ts         # Infrastructure construct
    lambdaBuilder/
        {pipelineName}Functions.ts          # Lambda builder functions
```

**CRITICAL — Pipeline Lambda Directory Structure:** Every pipeline's `lambda/` directory in `backendPipelines/` MUST include:

```
lambda/
  __init__.py                    # Package marker (copy from existing pipeline)
  customLogging/
    __init__.py                  # Package marker
    logger.py                    # safeLogger + mask_sensitive_data (copy from existing pipeline)
  vamsExecute*.py                # Pipeline handler(s)
  constructPipeline.py           # Batch job definition builder
  openPipeline.py                # Step Functions starter
  pipelineEnd.py                 # Cleanup + task token callback
```

Without `__init__.py` and `customLogging/logger.py`, Lambda will fail at import time with `No module named 'customLogging'`. Copy these files from any existing pipeline (e.g., `backendPipelines/3dRecon/splatToolbox/lambda/`).

Pipelines are conditionally created in `pipelineBuilder-nestedStack.ts` based on config flags.

**CRITICAL — VPC Builder Updates:** New pipelines that use AWS Batch, ECS, or Fargate MUST be added to **all three** condition blocks in `lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts`. Missing any one of these causes deployment failures. Search for `useSplatToolbox` in the file to find all locations:

1. **Subnet creation condition** (~line 341): The `if` block that pushes `subnetPublicConfig` and `subnetPrivateConfig`. Without this, the VPC has only isolated subnets and Batch compute environments fail with `"Resource subnets are required"`.
2. **VPC endpoint condition** (~line 540): The `if` block that creates Batch, ECR API, ECR Docker, and optionally EFS interface VPC endpoints. Without this, Batch jobs cannot pull container images or access AWS services.
3. **ECS endpoint condition** (~line 619): The `needsEcsPrivate` variable. Without this, the ECS agent on Batch instances cannot register with the ECS service.

### Pipeline S3 Output Path Conventions

The workflow ASL (built by `createWorkflow.py`) generates S3 paths for each pipeline step. The `vamsExecute` lambda and `constructPipeline` lambda must handle these correctly:

| Path                                   | Bucket    | Use For                                                                     |
| -------------------------------------- | --------- | --------------------------------------------------------------------------- |
| `outputS3AssetFilesPath`               | Asset     | File-level outputs: new files, file previews (`.previewFile.X`). Versioned. |
| `outputS3AssetPreviewPath`             | Asset     | Asset-level previews only (whole-asset representative image). Versioned.    |
| `outputS3AssetMetadataPath`            | Asset     | Metadata output. Versioned.                                                 |
| `inputOutputS3AssetAuxiliaryFilesPath` | Auxiliary | Temporary working files or special non-versioned viewer data only.          |

**Key distinction:** `outputS3AssetFilesPath` is for file-level outputs, including `.previewFile.gif/.jpg/.png` thumbnails tied to specific files. `outputS3AssetPreviewPath` is only for asset-level preview images representing the asset as a whole. Most pipelines producing file previews should write to `outputS3AssetFilesPath`.

**Rules:**

1. The `vamsExecute` lambda **must pass through** all output paths from the workflow payload to the `constructPipeline` lambda. Never hardcode empty strings — the workflow's process-output step depends on finding files at these locations.
2. The `constructPipeline` lambda should use the appropriate output path for the container's `outputFiles` stage definition: `outputS3AssetFilesPath` for file-level outputs (including `.previewFile.X` thumbnails), `outputS3AssetPreviewPath` for asset-level previews only. Fall back to `inputOutputS3AssetAuxiliaryFilesPath` only for direct/local invocations.
3. The **auxiliary path** (`inputOutputS3AssetAuxiliaryFilesPath`) is for temporary files during container processing or special non-versioned viewer data (e.g., Potree octree files that the frontend reads directly). It should **not** be used for standard pipeline outputs that flow through the workflow's process-output step.
4. Container IAM roles must have write access to the target buckets. The `inputBucketPolicy` in pipeline constructs typically grants read/write to all asset buckets; the `outputBucketPolicy` covers the auxiliary bucket.
5. **Containers must preserve the input file's relative path** when writing asset-adjacent outputs (e.g., `.previewFile.X` thumbnails). Asset files are stored at `{assetId}/{relative_dirs}/{filename}` — the relative subdirectory structure between the asset ID and filename must be maintained in the output S3 key. The process-output step expects outputs at the same relative location as the input. The `assetId` is a workflow state variable that must be **threaded through the entire chain** (vamsExecute → constructPipeline → pipeline definition → container) — never derive it from path segments. In the container, use the explicit `assetId` to find the split point in the input object key: `"/".join(input_parts[input_parts.index(assetId) + 1:-1])`.

---

## Build and Deploy

### CDK Commands

```bash
cd infra
npm install
npx cdk synth        # Synthesize CloudFormation
npx cdk deploy       # Deploy to AWS
npx cdk diff         # Show pending changes
npx cdk destroy      # Tear down stack
```

### Context Variables

```bash
npx cdk synth
```

Note: The test file uses the legacy `@aws-cdk/assert` library and has an outdated mock config. Test updates may be needed when adding new features.

---

## Key Files Quick Reference

| Purpose                          | File                                                              |
| -------------------------------- | ----------------------------------------------------------------- |
| CDK entry point                  | `bin/infra.ts`                                                    |
| Config & constants               | `config/config.ts`                                                |
| Root stack                       | `lib/core-stack.ts`                                               |
| Storage (DynamoDB, S3, SNS, SQS) | `lib/nestedStacks/storage/storageBuilder-nestedStack.ts`          |
| API routes                       | `lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts`            |
| API Gateway setup                | `lib/nestedStacks/apiLambda/apigatewayv2-amplify-nestedStack.ts`  |
| Auth (Cognito/SAML/OAuth)        | `lib/nestedStacks/auth/authBuilder-nestedStack.ts`                |
| Security helpers                 | `lib/helper/security.ts`                                          |
| Service helper (ARN/endpoint)    | `lib/helper/service-helper.ts`                                    |
| Partition lookup                 | `lib/helper/const.ts`                                             |
| S3 bucket registry               | `lib/helper/s3AssetBuckets.ts`                                    |
| Feature flags enum               | `common/vamsAppFeatures.ts`                                       |
| WAF stack                        | `lib/cf-waf-stack.ts`                                             |
| IAM role aspect                  | `lib/aspects/iam-role-transform.aspect.ts`                        |
| Log retention aspect             | `lib/aspects/log-retention.aspect.ts`                             |
| Pipeline orchestrator            | `lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts`       |
| Static web hosting               | `lib/nestedStacks/staticWebApp/staticWebBuilder-nestedStack.ts`   |
| OpenSearch                       | `lib/nestedStacks/searchAndIndexing/searchBuilder-nestedStack.ts` |
