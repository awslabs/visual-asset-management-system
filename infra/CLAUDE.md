# CLAUDE.md -- VAMS CDK Infrastructure

This is the Claude Code steering document for the `infra/` directory. It is auto-loaded when Claude Code operates within the VAMS CDK infrastructure-as-code.

---

## Project Identity

-   **Name**: VAMS (Visual Asset Management System) -- CDK Infrastructure
-   **Version**: (tracked in `config/config.ts` as `VAMS_VERSION`)
-   **Runtime**: AWS CDK v2 (TypeScript), targeting `aws-cdk-lib`
-   **Node**: NODEJS_22_X for Lambda and CDK
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
    resourceParamKeys.ts        # SSM parameter key constants (mirrored in backend/common/resourceNames.py)
  config/
    config.ts                   # Config interfaces, getConfig(), constants
    config.json                 # Active deployment configuration
    config.template.commercial.json  # Commercial template
    config.template.govcloud.json    # GovCloud template
    config.template.eusovereign.json # EU Sovereign Cloud template
    saml-config.ts              # SAML provider settings
    csp/                        # CSP additional config (cspAdditionalConfig.json)
    docker/                     # Docker build configurations
    policy/                     # S3 additional bucket policy JSON; IAM role mappings (iamRoleConfig.json)
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
      iamRoleCustomization.ts   # Bootstrap synthesizer + iam.Role.customizeRoles wiring (app.iamRoleConfig)
      lambda.ts                 # Layer bundling commands (poetry-based)
      s3AssetBuckets.ts         # Global asset bucket registry (shared across stacks)
      security.ts               # KMS, CDK Nag, CSP, TLS enforcement, presigned URL bucket policy restrictions, audit logging setup
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
      resourceNames/
        resourceNamesBuilder-nestedStack.ts  # Publishes 39 SSM String parameters (28 DynamoDB tables, 2 S3 buckets, 9 audit log groups)
        resourceNameRegistry.ts            # Cross-stack resource name descriptor registry (ResourceNameDescriptor interface)
      auth/
        authBuilder-nestedStack.ts         # Cognito user pool, identity pool, SAML, external OAuth
        constructs/
          cognito-web-native-construct.ts
          dynamodb-authdefaults-admin-construct.ts
          dynamodb-authdefaults-ro-construct.ts
      apiLambda/
        api-nestedStack.ts                   # API nested stack: selects API implementation by config.app.api.apiType (IApiImplementation)
        apiRouteRegistry.ts                  # Cross-stack route descriptor registry + attachFunctionToApi() (apiLambda-level, implementation-agnostic)
        apiBuilder-nestedStack.ts            # Primary API routes + Lambda wiring (asset, database, metadata, auth, pipeline, workflow, etc.)
        apiBuilder2-nestedStack.ts           # Secondary API stack: self-contained domains moved to free ApiBuilder headroom (currently Tags, Tag Types, Auth Constraints)
        lambdaLayersBuilder-nestedStack.ts   # Lambda layer construction
        constructs/
          rest-api-gateway-construct.ts          # RestApiGatewayConstruct (API Gateway REST IApiImplementation) + resolveApiGatewayVpcEndpointId()
          buildOpenApiSpec.ts                    # OpenAPI spec generator from registry (REST-specific; auth + anon security schemes)
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
        physna/                            # Physna 3D/CAD geometric search sync (Phase 1)
                                             # Builds physnaFileSync, physnaAssetSync,
                                             # and physnaViewer lambdas for addon API.
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
  |     +-- ResourceNamesBuilder (publishes 39 SSM parameters; depends on Storage)
  |     |
  |     +-- AuthBuilder (depends on Storage)
  |     |     |
  |     |     +-- ApiGatewayV2Amplify (API Gateway + authorizer)
  |     |     |     |
  |     |     |     +-- ApiBuilder (primary API route Lambda wiring; includes pipeline + workflow)
  |     |     |     +-- ApiBuilder2 (secondary API stack: Tags, Tag Types, Auth Constraints; depends on ApiBuilder)
  |     |     |     +-- StaticWeb (CloudFront or ALB hosting)
  |     |     |     +-- SearchBuilder (OpenSearch)
  |     |     |     +-- PipelineBuilder (all use-case pipelines)
  |     |     |     +-- AddonBuilder (Garnet, Physna Sync)
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
        // 20+ DynamoDB tables -- see storageBuilder-nestedStack.ts lines 72-98
        appFeatureEnabledStorageTable;
        assetLinksStorageTableV2;
        assetLinksMetadataStorageTable;
        assetStorageTable;
        assetUploadsStorageTable;
        assetVersionsStorageTable;
        assetFileVersionsStorageTable;
        assetFileVersionHistoryStorageTable;
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

| Constant                          | Value                                                                                                                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `VAMS_VERSION`                    | `"2.X.0"`                                                                                                                                                                      |
| `LAMBDA_PYTHON_RUNTIME`           | `Runtime.PYTHON_3_12`                                                                                                                                                          |
| `LAMBDA_NODE_RUNTIME`             | `Runtime.NODEJS_22_X`                                                                                                                                                          |
| `LAMBDA_MEMORY_SIZE`              | `5308`                                                                                                                                                                         |
| `OPENSEARCH_VERSION`              | `OPENSEARCH_3_5` (standard partitions)                                                                                                                                         |
| `OPENSEARCH_VERSION_EUSOVEREIGN`  | `OPENSEARCH_2_19` (AWS European Sovereign Cloud `aws-eusc`; OpenSearch 3.x not yet supported there). The provisioned construct selects this when `Partition() === "aws-eusc"`. |
| `CUSTOM_AUTHORIZER_IGNORED_PATHS` | `["/api/amplify-config", "/api/version"]`                                                                                                                                      |

### ConfigPublic Interface

The `ConfigPublic` interface (~200 lines in `config/config.ts`) defines all deployment parameters. Key sections:

-   `env`: account, region, partition, coreStackName
-   `app.assetBuckets`: createNewBucket, defaultNewBucketSyncDatabaseId, externalAssetBuckets (each entry: bucketArn, baseAssetsPrefix, defaultSyncDatabaseId, and optional bucketAccountId / bucketRegion / bucketKmsKeyArn for cross-account + SSE-KMS buckets), presignedUrlNetworkRestrictions (allowedIpRanges / allowedVpceIds; empty lists = no restriction; mutually exclusive — `getConfig()` rejects setting both). Non-empty restriction lists add a bucket policy Deny (scoped to presigned `s3:authType=REST-QUERY-STRING` requests) to the created asset bucket and auxiliary bucket via `addPresignedUrlNetworkRestrictionsToBucketPolicy()` in `helper/security.ts`; imported external buckets are not policy-managed by VAMS (the bucket owner applies the equivalent statement — see external-s3-setup docs). A bucketArn may be registered multiple times under non-overlapping prefixes (validated by `validateExternalAssetBuckets()` in `getConfig()`, which rejects overlapping prefixes and inconsistent per-bucket attributes); `storageBuilder` imports each unique ARN once so the per-prefix event notifications merge into a single S3 notification configuration.
-   `app.useGlobalVpc`: enabled, useForAllLambdas, addVpcEndpoints, optionalExternalVpcId, vpcCidrRange
-   `app.openSearch`: useServerless (enabled, nextGen, allowPublic, enableStandbyReplicas, min/maxIndexingOcu, min/maxSearchOcu), useProvisioned, reindexOnCdkDeploy
-   `app.useAlb`: enabled, usePublicSubnet, domainHost, certificateArn
-   `app.useCloudFront`: enabled, customDomain (domainHost, certificateArn, optionalHostedZoneId)
-   `app.pipelines`: useConversion3dBasic, useConversionCadMeshMetadataExtraction, usePreviewPcPotreeViewer, useSplatToolbox, useGenAiMetadata3dLabeling, useRapidPipeline (useEcs, useEks), useModelOps, useIsaacLabTraining
-   `app.addons`: useGarnetFramework, usePhysnaSync
-   `app.authProvider`: useCognito (enabled, useSaml, useUserPasswordAuthFlow), useExternalOAuthIdp, authorizerOptions.allowedIpRanges
-   `app.api`: apiType (fixed `"APIGATEWAY_REST"`), apiGatewayRest (globalRateLimit default 50, globalBurstLimit default 100, endpointType `"REGIONAL"`/`"PRIVATE"`, optionalExternalPrivateApigVPCEId for PRIVATE). The REST API stage name is NOT a config field — it is the fixed constant `API_GATEWAY_STAGE_NAME` (`"api"`) in `config/config.ts`, shared with the VamsCLI endpoint constants and the web `/api/*` fronting.
-   `app.govCloud`: enabled, il6Compliant
-   `app.iamRoleConfig`: useCustomBootstrapRoles, useCustomVamsStackRoles (advanced; mappings live in `config/policy/iamRoleConfig.json`)
-   `app.webUi`: optionalBannerHtmlMessage, allowUnsafeEvalFeatures

### Config extends ConfigPublic (Internal)

Adds: `enableCdkNag`, `dockerDefaultPlatform`, `s3AdditionalBucketPolicyJSON`, `iamRoleCustomizationJSON`, `openSearchAssetIndexName`, `openSearchFileIndexName`, SSM parameter paths.

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

### Required Security Calls (Every Lambda Builder)

Every lambda builder function MUST include these calls after creating the function:

```typescript
// 1. KMS permissions (if encryption enabled)
kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

// 2. Auth table access + audit log setup
setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);

// 3. Global environment variables (Cognito auth flag)
globalLambdaEnvironmentsAndPermissions(fun, config);

// 4. Per-Lambda CDK Nag suppressions (IAM4 execution roles + wildcard KMS actions)
suppressCdkNagLambda(fun);

// 5. CDK Nag suppression for S3 grant patterns (only if the function uses grantRead/grantReadWrite)
suppressCdkNagErrorsByGrantReadWrite(scope);
```

`suppressCdkNagLambda(fun)` is REQUIRED on every authored Lambda function (including those built inside
constructs and custom resources). It replaces the old stack-wide suppression that `CoreVAMSStack` previously
applied with `applyToChildren=true` — that approach stamped the suppression metadata onto every resource in
every nested stack and bloated the synthesized CloudFormation templates. Scope the suppression to the function.

### What the Security Helpers Do

-   **`kmsKeyLambdaPermissionAddToResourcePolicy`**: Grants KMS Decrypt/Encrypt/GenerateDataKey/ReEncrypt/ListKeys/CreateGrant/ListAliases on the VAMS KMS key
-   **`setupSecurityAndLoggingEnvironmentAndPermissions`**: Grants read on auth/constraints/userRoles/roles tables. Grants CloudWatch PutLogEvents on all 9 audit log groups. **No longer injects table or log group environment variables** (non-pipeline handlers resolve these from SSM).
-   **`globalLambdaEnvironmentsAndPermissions`**: Adds `VAMS_RESOURCE_PARAM_PREFIX` env var (SSM parameter prefix for resource name resolution) and grants ssm:GetParameter, ssm:GetParameters, ssm:GetParametersByPath on the deployment's resource-name parameter prefix. Also sets `COGNITO_AUTH_ENABLED` — `TRUE` whenever Cognito is the auth provider, and `FALSE` only when Lambda functions run in the VPC **and** the partition is AWS GovCloud (US) (`aws-us-gov`) or AWS European Sovereign Cloud (`aws-eusc`), where Cognito PrivateLink is unavailable so an in-VPC Lambda cannot reach Cognito for the MFA check. `addVpcEndpoints = false` does **not** disable it: in that mode the VPC builder skips endpoint creation because the operator hand-creates the same endpoints (including `cognito-idp`/`cognito-identity`), so Cognito remains reachable and the check stays enabled. In every supported (non-GovCloud/EU-Sovereign) partition the VPC builder creates the `cognito-idp`/`cognito-identity` interface endpoints so in-VPC Lambda functions can reach Cognito.
-   **`suppressCdkNagLambda`**: Applies the standard per-Lambda IAM4/IAM5 suppressions (AWSLambdaBasicExecutionRole, AWSLambdaVPCAccessExecutionRole, wildcard KMS actions), scoped to the function instead of the whole stack
-   **`suppressCdkNagErrorsByGrantReadWrite`**: Suppresses AwsSolutions-IAM5 for S3 and resource wildcards
-   **`suppressCdkNagLambdaFrameworkResources`**: Called once on the core stack. Applies the same IAM4/IAM5 suppressions only to CDK-generated framework roles (custom-resource providers, bucket deployments, `AwsCustomResource`) and VAMS custom-resource roles that the per-function helper cannot reach

---

## API Gateway Pattern

### REST API Setup (api-nestedStack.ts + constructs/rest-api-gateway-construct.ts)

-   `ApiNestedStack` (`api-nestedStack.ts`) is implementation-agnostic: it selects an API implementation by `config.app.api.apiType` and exposes the result via `IApiImplementation` (`apiEndpoint`, `invokeUrlWithStage`, `stageName`). Today the only supported type is `API_TYPE_APIGATEWAY_REST` (`"APIGATEWAY_REST"`, the only value in `SUPPORTED_API_TYPES`); it instantiates `RestApiGatewayConstruct`. A future entry point (e.g. ALB) adds a `SUPPORTED_API_TYPES` value, a construct under `constructs/` implementing `IApiImplementation`, and a branch here — downstream consumers stay unchanged.
-   REST API (v1) built from a cross-stack route registry, materialized as a single `SpecRestApi` with an inline OpenAPI spec
-   Custom Lambda authorizer: REQUEST type, returns IAM policy with wildcard resource (for cache correctness). Authenticated routes use the `VamsAuthorizer` scheme (identity source `method.request.header.Authorization`, 30s cache TTL); anonymous/ignored routes use the `VamsAnonymousAuthorizer` scheme (identity source `context.identity.sourceIp`, 900s cache TTL) — the same Lambda still runs the IP-restriction check, so no route is left without an authorizer.
-   Explicit Deployment + Stage (stage name = the fixed constant `API_GATEWAY_STAGE_NAME` = `"api"`)
-   CORS: all origins (`*`), standard + auth headers, all HTTP methods, credentials=false. Set in three places because REST responses come from three layers: (1) the per-path OPTIONS **MOCK** method (unauthenticated — no `security` on OPTIONS) returns the preflight ACAO from `buildOpenApiSpec.ts`; (2) **GatewayResponses** (`DEFAULT_4XX`/`DEFAULT_5XX`, added in `rest-api-gateway-construct.ts`) inject ACAO on authorizer denials (401/403), missing-auth-token, and errors — these never reach a Lambda, so the handler cannot add it; (3) the Lambda handler adds ACAO to its own proxy response body (`commonHeaders()`), which API Gateway returns verbatim.
-   Resource policy: **always** written explicitly to match `endpointType` (`buildOpenApiSpec.ts`) — `aws:SourceVpce`-restricted for `PRIVATE`, public allow-all for `REGIONAL`. API Gateway does not clear a prior resource policy when an update omits one, so emitting it for both types ensures a `PRIVATE`↔`REGIONAL` switch overwrites the old policy. A stale `PRIVATE` policy left on a `REGIONAL` API denies every request (incl. the CORS preflight) with `403 AccessDeniedException` at the resource-policy layer, which a browser misreports as a CORS-preflight failure.
-   Endpoint type: `config.app.api.apiGatewayRest.endpointType` (`"REGIONAL"` default, public, never routed through a VPC endpoint; `"PRIVATE"` reachable only through the execute-api VPC interface endpoint, requires `useGlobalVpc.enabled` + either `useGlobalVpc.addVpcEndpoints` or `optionalExternalPrivateApigVPCEId`, incompatible with CloudFront, and must be fronted by an ALB in isolated (non-public) subnets — `useAlb.enabled` + `useAlb.usePublicSubnet = false`)
-   Stage name: fixed constant `API_GATEWAY_STAGE_NAME` (`"api"`) in `config/config.ts` — not a config field, because the value is also baked into the VamsCLI endpoint constants and the web `/api/*` fronting. Absorbed by CloudFront originPath / ALB redirect.
-   VPC endpoint: only a `PRIVATE` endpoint uses an execute-api interface endpoint. The VPC builder creates it (gated on `apiType === APIGATEWAY_REST` and `endpointType === "PRIVATE"`) when `config.app.useGlobalVpc.addVpcEndpoints` is enabled; otherwise the operator supplies one via `config.app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId`. `REGIONAL` ignores any endpoint. `resolveApiGatewayVpcEndpointId()` in the construct encodes this.
-   Rate limiting: `config.app.api.apiGatewayRest.globalRateLimit` (default 50) / `config.app.api.apiGatewayRest.globalBurstLimit` (default 100)
-   Access logging to CloudWatch with structured JSON format

### Route Registration (attachFunctionToApi helper)

Routes are registered across nested stacks (`apiBuilder-nestedStack.ts`, `apiBuilder2-nestedStack.ts`) using:

```typescript
attachFunctionToApi(this, lambdaFunction, {
    routePath: "/database/{databaseId}",
    method: "GET",
    registry: routeRegistry,
    allowAnonymous: false, // optional, default false
});
```

This adds a route descriptor to the `RouteRegistry` (imported from the REST API builder stack output). The REST API builder then renders all registered descriptors into a single OpenAPI spec and materializes them on the `SpecRestApi`.

For each registered route, `attachFunctionToApi`:

1. Grants the REST API's execution role invoke permission on the Lambda
2. Adds the route descriptor to the registry (path, method, function ARN, allow-anonymous flag)

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

## OpenSearch Serverless Connectivity

A **private** OpenSearch Serverless collection (`app.openSearch.useServerless.allowPublic = false`) is reached only through a VPC endpoint, and the endpoint **type is selected by the collection generation** because the two generations expose different endpoint hostnames:

-   **NEXTGEN** (`nextGen = true`) — endpoint hostname is `\{collection-id\}.aoss.\{region\}.on.aws`. Reached through a **standard EC2 interface endpoint** (`ec2.InterfaceVpcEndpoint`, service `com.amazonaws.\{region\}.aoss-data`, `privateDnsEnabled: true`). Built partition-aware via `new ec2.InterfaceVpcEndpointAwsService("aoss-data", "com.amazonaws", 443)`.
-   **CLASSIC** (`nextGen = false`) — endpoint hostname is `\{collection-id\}.\{region\}.aoss.amazonaws.com`. Reached through the OpenSearch Serverless-managed endpoint (`opensearchserverless.CfnVpcEndpoint`), which provisions its own Route 53 private hosted zone.

The chosen endpoint's id populates the network policy `SourceVPCEs`. Only the OpenSearch-facing Lambdas (search, fileIndexer, assetIndexer, crOsReindexer, and the schema-deploy custom resource) run in the VPC — `useForAllLambdas` is not required for a private collection. The schema-deploy custom resource Lambda uses a long timeout (14 min) and a readiness poll because a freshly created collection/endpoint, plus a NEXTGEN scale-to-zero cold start (10–30s), can take minutes to become reachable. Backend Lambdas sign with SigV4 service name `aoss` when `OPENSEARCH_TYPE=serverless`.

**`addVpcEndpoints` gating (NEXTGEN only).** The NEXTGEN endpoint is a standard EC2 interface endpoint, so it follows `useGlobalVpc.addVpcEndpoints` like every other interface endpoint. The construct computes `createEndpointResources = useVPCEndpoint && (!nextGen || addVpcEndpoints)`:

-   When `createEndpointResources` is true, VAMS creates the endpoint, its security group, and the VPC network access policy, and runs the schema-deploy function in the VPC.
-   When it is false (private NEXTGEN + `addVpcEndpoints = false`, the **deferred** case), VAMS skips the endpoint **and** the network policy. The schema-deploy custom resource runs **outside** the VPC, writes the SSM parameters, and skips index creation (the `DeploySSMIndexSchema` custom resource passes `deferIndexCreation: "true"`, which the handler honors by returning success without creating indexes). The operator creates the `aoss-data` endpoint and a matching network policy manually. To then create the index mappings, set `app.openSearch.useServerless.deployDeferredIndexSchema = true` for one deployment (also overridable via CDK context) — the construct computes `deferIndexCreation = deferVpcSetup && !deployDeferredIndexSchema` and `schemaDeployInVpc = createEndpointResources || (deferVpcSetup && !deferIndexCreation)`, so the schema-deploy function runs in the VPC against the operator endpoint and creates the (idempotent) indexes. Then reindex. The flag is ignored when `addVpcEndpoints = true` (nothing is deferred). CLASSIC's managed endpoint is not an EC2 interface endpoint, so it is not governed by `addVpcEndpoints` and is always created for a private collection. See `documentation/docusaurus-site/docs/developer/opensearch.md`.

---

## Development Rules

### 1. Configuration Changes

1. Add new properties to `ConfigPublic` interface in `config/config.ts`
2. Add backward-compatibility defaults in `getConfig()` (check for `undefined`)
3. Add validation logic in `getConfig()` if constraints exist
4. Update **ALL** config template files: `config.template.commercial.json`, `config.template.govcloud.json`, **and** `config.template.eusovereign.json`. A new or changed config option must be reflected in every template; a missed template silently falls back to `getConfig()` defaults and drops any operator-set value, leaving the templates inconsistent.
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
4. Apply the security calls:
    - `kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey)`
    - `setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources)`
    - `globalLambdaEnvironmentsAndPermissions(fun, config)`
    - `suppressCdkNagLambda(fun)` — required on every Lambda
    - `suppressCdkNagErrorsByGrantReadWrite(scope)` — only if the function uses grantRead/grantReadWrite
5. Wire the function to API Gateway using `attachFunctionToApi()`. Prefer `apiBuilder2-nestedStack.ts` for new endpoints (the primary `apiBuilder-nestedStack.ts` is near the CFN per-stack resource limit). Only place a function in `apiBuilder` if it must share a directly-referenced function instance defined there.

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
5. Add constant to `RESOURCE_PARAM_KEYS.dynamoTables` in `infra/common/resourceParamKeys.ts`
6. Add matching `ResourceParamKey` entry to `ResourceKeys` class in `backend/backend/common/resourceNames.py`
7. Add matching constant to `ResourceParamKeys` in `infra/deploymentDataMigration/tools/ssm_resource_lookup.py` (data-migration scripts resolve table names from these SSM parameters)
8. Register descriptor in `resourceNameRegistry` (imported in `storageBuilder-nestedStack.ts`)
9. Grant permissions (`grantReadData`, `grantReadWriteData`) in lambda builders (the `globalLambdaEnvironmentsAndPermissions` helper already grants SSM read access)
10. Update the documentation (see Rule below): add the table to `architecture/aws-resources.md` and `architecture/data-model.md`

The same three-way constants update (audit log groups included) applies to new CloudWatch audit log groups: `RESOURCE_PARAM_KEYS.cloudwatchLogGroups`, `ResourceKeys` in `resourceNames.py`, and `ResourceParamKeys` in `ssm_resource_lookup.py`. Tables that become deprecated but are retained for migration move to `RESOURCE_PARAM_KEYS.dynamoTablesLegacy` (published under `dynamoTables/legacy/`) so migration scripts can still resolve them.

### Documentation Rule: Storage Resources, Log Groups, and SSM Parameters

Whenever you **add or change** an Amazon S3 bucket, an Amazon DynamoDB table, or an Amazon CloudWatch log group, update `documentation/docusaurus-site/docs/architecture/aws-resources.md` and `documentation/docusaurus-site/docs/deployment/uninstall.md` (and the matching Kiro steering — see Rule 11 and the bidirectional-sync rule in the root `CLAUDE.md`). Document **two independent properties** for each such resource:

1. **Removal on teardown** -- `RemovalPolicy.RETAIN` (survives `cdk destroy`; needs manual deletion) vs. `RemovalPolicy.DESTROY` (removed automatically; pair S3 buckets with `autoDeleteObjects: true`).
2. **Custom name (redeploy-collision flag)** -- whether the resource sets an explicit name (`bucketName`, `tableName`, `logGroupName`, including deterministic `generateUniqueNameHash` names). Only explicitly named resources can collide by name on a redeploy into the same account with the same configuration name.

These axes are independent. **Retained + auto-named** resources (the asset, auxiliary, artefacts, and access logs buckets; all DynamoDB tables) survive teardown but do **not** block a redeploy, so they do not need to be deleted unless you intend to remove the data. **Custom/fixed-named** resources (the ALB web app bucket and its access logs bucket, named for the domain host; every `/aws/vendedlogs/...` log group) **must** be flagged so operators delete any orphaned copy before redeploying.

**SSM String parameters** (39 resource-name parameters published by ResourceNamesBuilder): All explicitly named (`parameterName` set, e.g., `/{config.name}-{baseStackName}/resourceNames/dynamoTables/assetStorage`) → redeploy-collision relevant. RemovalPolicy: default (DESTROY with stack). Parameters are String type (not SecureString) because resource names are configuration pointers, not data — an explicitly justified exception to the KMS-encryption-everywhere rule.

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
            // Handler-specific env vars only (resource names resolved from SSM)
            // OPTIONAL_HANDLER_SPECIFIC_VAR: "value",
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.myTable.grantReadWriteData(fun);

    // Required security calls
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config); // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagLambda(fun);
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

| Purpose                          | File                                                                                         |
| -------------------------------- | -------------------------------------------------------------------------------------------- |
| CDK entry point                  | `bin/infra.ts`                                                                               |
| Config & constants               | `config/config.ts`                                                                           |
| Root stack                       | `lib/core-stack.ts`                                                                          |
| Storage (DynamoDB, S3, SNS, SQS) | `lib/nestedStacks/storage/storageBuilder-nestedStack.ts`                                     |
| API routes                       | `lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts`                                       |
| API Gateway setup                | `lib/nestedStacks/apiLambda/api-nestedStack.ts` + `constructs/rest-api-gateway-construct.ts` |
| Auth (Cognito/SAML/OAuth)        | `lib/nestedStacks/auth/authBuilder-nestedStack.ts`                                           |
| Security helpers                 | `lib/helper/security.ts`                                                                     |
| Service helper (ARN/endpoint)    | `lib/helper/service-helper.ts`                                                               |
| Partition lookup                 | `lib/helper/const.ts`                                                                        |
| S3 bucket registry               | `lib/helper/s3AssetBuckets.ts`                                                               |
| Feature flags enum               | `common/vamsAppFeatures.ts`                                                                  |
| WAF stack                        | `lib/cf-waf-stack.ts`                                                                        |
| IAM role aspect                  | `lib/aspects/iam-role-transform.aspect.ts`                                                   |
| Log retention aspect             | `lib/aspects/log-retention.aspect.ts`                                                        |
| Pipeline orchestrator            | `lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts`                                  |
| Static web hosting               | `lib/nestedStacks/staticWebApp/staticWebBuilder-nestedStack.ts`                              |
| OpenSearch                       | `lib/nestedStacks/searchAndIndexing/searchBuilder-nestedStack.ts`                            |
