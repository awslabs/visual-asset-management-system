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
  bin/infra.ts                  # CDK app entry point
  common/
    vamsAppFeatures.ts          # VAMS_APP_FEATURES enum
    resourceParamKeys.ts        # SSM key constants (mirrored in backend/common/resourceNames.py)
  config/
    config.ts                   # Config interfaces, getConfig(), constants
    config.json                 # Active deployment configuration
    config.template.{commercial,govcloud,eusovereign}.json
    saml-config.ts              # SAML provider settings
    csp/ docker/ policy/        # CSP additional config, Docker build, S3 bucket + IAM role + WAF rule policy (wafPolicyConfig.json) JSON
  gen/genEndpoints.ts           # Endpoint generation utility
  lib/
    core-stack.ts               # CoreVAMSStack -- root stack orchestrator
    cf-waf-stack.ts             # WAF (regional ACL for API GW/ALB; CLOUDFRONT ACL in us-east-1 when CloudFront on); rules built from config/policy/wafPolicyConfig.json
    aspects/                    # iam-role-transform.aspect.ts, log-retention.aspect.ts (1-year retention)
    constructs/wafv2-basic-construct.ts  # Builds the WAF Web ACL from wafPolicyConfig.json: managed rule groups (block or count-only per `block`) + per-rule `ruleActionOverrides` (e.g. SizeRestrictions_BODY -> count) + rate-based rules (FORWARDED_IP aggregation + `forwardedIPConfig`, and a 429 custom-response body); count-only Common Rule Set fallback when no policy supplied
    helper/
      const.ts                  # SERVICE_LOOKUP: partition-aware endpoints (aws, aws-us-gov, aws-cn, aws-iso, aws-eusc)
      iamRoleCustomization.ts   # Bootstrap synthesizer + iam.Role.customizeRoles wiring
      lambda.ts                 # Layer bundling commands
      s3AssetBuckets.ts         # Global asset bucket registry
      security.ts               # KMS, CDK Nag, CSP, TLS enforcement, audit logging setup
      service-helper.ts         # ServiceFormatter: ARN(), Endpoint, Principal
    lambdaBuilder/              # 17 builder files, ~40+ function builders (asset, database, metadata, auth, comment,
                                # config, pipeline, workflow, role, userRole, tag, tagType, subscription, sendEmail,
                                # metadataSchema, assetsLink, searchIndexBucketSync)
    nestedStacks/
      vpc/vpcBuilder-nestedStack.ts      # VPC, subnets, VPC endpoints
      storage/
        storageBuilder-nestedStack.ts    # ~2700 lines: DynamoDB, S3, SNS, SQS, EventBridge, KMS, CloudWatch
        customResources/populateS3AssetBucketsTable.ts
      resourceNames/
        resourceNamesBuilder-nestedStack.ts  # Publishes 62 SSM String parameters (57 resource names + 5 legacy)
        resourceNameRegistry.ts              # ResourceNameDescriptor cross-stack registry
      auth/
        authBuilder-nestedStack.ts       # Cognito user pool, identity pool, SAML, external OAuth
        constructs/                      # cognito-web-native, dynamodb-authdefaults-{admin,ro}
      apiLambda/
        api-nestedStack.ts                 # Selects impl by config.app.api.apiType
        apiRouteRegistry.ts                # Cross-stack route registry + attachFunctionToApi()
        apiBuilder-nestedStack.ts          # Primary API routes + Lambda wiring
        apiBuilder2-nestedStack.ts         # Secondary API stack: Tags, Tag Types, Auth Constraints,
                                           # asset history, and the pipeline / pipeline template /
                                           # workflow / workflow trigger / execution routes
        lambdaLayersBuilder-nestedStack.ts
        constructs/                        # rest-api-gateway-construct, buildOpenApiSpec, amplify-config-lambda,
                                           # vams-version-lambda, dynamodb-metadataschema-defaults
      staticWebApp/
        staticWebBuilder-nestedStack.ts    # S3 + CloudFront or ALB web hosting
        constructs/                        # cloudfront-s3-website, alb-s3-website-albDeploy, gateway-albDeploy, custom-cognito-config
      searchAndIndexing/
        searchBuilder-nestedStack.ts       # OpenSearch serverless or provisioned
        constructs/                        # opensearch-serverless, opensearch-provisioned, schemaDeploy/deployschema.ts
      pipelines/                           # Pipeline stacks — see pipelines/CLAUDE.md
        pipelineBuilder-nestedStack.ts     # Pipeline orchestrator
        constructs/                        # batch-fargate-pipeline, batch-gpu-pipeline,
                                           # securitygroup-gateway-pipeline, vamsSchemaRegistration
        conversion/{3dBasic,meshCadMetadataExtraction,coordinateTransform}/
        preview/{pcPotreeViewer,3dThumbnail}/
        3dRecon/splatToolbox/  genAi/{metadata3dLabeling,nvidia/{cosmos,gr00t}}/
        multi/{modelOps,rapidPipeline,rapidPipelineEKS}/  simulation/isaacLabTraining/
      featureEnabled/custom-featureEnabled-config-nestedStack.ts
      locationService/location-service-nestedStack.ts    # Amazon Location Service (commercial only)
      addon/
        addonBuilder-nestedStack.ts        # Addon orchestrator
        garnetFramework/                   # Garnet NGSI-LD digital twin framework
        physna/                            # Physna 3D/CAD geometric search sync (builds physnaFileSync, physnaAssetSync, physnaViewer lambdas for addon API)
  test/                          # Jest suites: route registry, OpenAPI spec, config-builder drift, WAF,
                                 # lambda grants, presigned-URL policy, migration tooling, plus the legacy
                                 # infra.test.ts snapshot (uses the outdated @aws-cdk/assert)
  deploymentDataMigration/
    tools/ssm_resource_lookup.py                 # Resolves resource names from the SSM parameters
    v2.4_to_v2.5/upgrade/                        # Backfills databaseId + databaseId:assetId on asset versions
    v2.5_to_v2.6/upgrade/                        # Transforms pipeline/workflow/execution rows into the V2 tables
```

---

## Architecture Overview

### Nested Stack Dependency Chain

Each arrow is an explicit `addDependency()` call in `core-stack.ts` (Rule 9), so the chain reads bottom-up: a stack is created after everything it points to.

```
CoreVAMSStack (root)
  +-- VPCBuilder (conditional: useGlobalVpc.enabled)
  +-- LambdaLayers
  +-- StorageResourcesBuilder (DynamoDB, S3, SNS, SQS, EventBridge, KMS, CloudWatch — foundation)
  |     +-- ResourceNamesBuilder (publishes 62 SSM parameters)
  |     +-- AuthBuilder (Cognito, SAML, external OAuth)          -> storage, resourceNames
  |     +-- ApiBuilder (primary API routes)                      -> storage, resourceNames
  |     +-- ApiBuilder2 (secondary routes)                       -> storage, resourceNames, ApiBuilder
  |     +-- SearchBuilder (OpenSearch)                           -> storage, resourceNames
  |     +-- PipelineBuilder (all use-case pipelines)             -> storage, ApiBuilder2
  |     |                                                           (its vamsSchema registration custom
  |     |                                                            resources invoke an ApiBuilder2 Lambda)
  |     +-- AddonBuilder (Garnet, Physna Sync)                   -> storage, resourceNames
  |     +-- RestApi (ApiNestedStack: API Gateway + authorizer)   -> storage, AuthBuilder, ApiBuilder,
  |     |                                                           ApiBuilder2, SearchBuilder, AddonBuilder
  |     +-- StaticWeb (CloudFront or ALB hosting)                -> storage
  +-- LocationService (conditional: useLocationService.enabled)
  +-- CustomFeatureEnabledConfig (writes enabled features to DynamoDB)
```

`RestApi` materializes the routes every API stack registered into `RouteRegistry`, which is why it depends on all of them rather than the reverse.

### Cross-Stack Shared Interfaces

**`storageResources`** (`storageBuilder-nestedStack.ts`): `encryption.kmsKey`; `s3.{assetAuxiliaryBucket, artefactsBucket, accessLogsBucket}`; `sns.{eventEmailSubscriptionTopic, fileIndexerSnsTopic, assetIndexerSnsTopic, databaseIndexerSnsTopic}`; `eventBridge.{orchestrationBus, orchestrationBusAuditLogGroup, eventSourcePrefix}` (deployment-unique source prefix, e.g. `"vams.prod-us-east-1"`); `cloudWatchAuditLogGroups.{authentication, authorization, fileUpload, fileDownload, fileDownloadStreamed, authOther, authChanges, actions, errors}`; and `dynamo.*` — 46 DynamoDB tables (see the interface at the top of `storageBuilder-nestedStack.ts`). There is no `sqs` member: the two Amazon SQS queues the builder creates buffer S3 object-created/deleted notifications for the indexers and are wired locally, and each workflow trigger Lambda owns its own queue + DLQ in `lib/lambdaBuilder/workflowFunctions.ts`. Notable GSIs: `apiKeyStorageTable` has `apiKeyHashIndex` (PK: apiKeyHash) and `userIdIndex` (PK: userId); `assetVersionsStorageTable` has `databaseIdAssetIdIndex` (PK: databaseId:assetId, SK: assetVersionId); the pipeline, workflow, and workflow-execution V2 tables each carry a `*ByDateGSI` on the constant `allListPartition` attribute, which backs the global (all-databases) list endpoints as a query rather than a scan — every write path must set that attribute or the row is invisible to those lists.

**`authResources`** (`authBuilder-nestedStack.ts`): `roles.unAuthenticatedRole`; `cognito.{userPool, webClientUserPool, userPoolId, identityPoolId, webClientId}`.

---

## Configuration System

Configuration values resolve in order: CDK context (`-c key=value`) → `config/config.json` → environment variables → hardcoded defaults. `bin/infra.ts` calls `Config.getConfig(app)` then `Service.SetConfig(config)`.

### Key Constants (config/config.ts)

| Constant                          | Value                                                                                                                             |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `VAMS_VERSION`                    | `"2.X.0"`                                                                                                                         |
| `LAMBDA_PYTHON_RUNTIME`           | `Runtime.PYTHON_3_12`                                                                                                             |
| `LAMBDA_NODE_RUNTIME`             | `Runtime.NODEJS_22_X`                                                                                                             |
| `LAMBDA_MEMORY_SIZE`              | `5308`                                                                                                                            |
| `OPENSEARCH_VERSION`              | `OPENSEARCH_3_5` (standard partitions)                                                                                            |
| `OPENSEARCH_VERSION_EUSOVEREIGN`  | `OPENSEARCH_2_19` — provisioned construct selects this when `Partition() === "aws-eusc"` (OpenSearch 3.x not yet supported there) |
| `CUSTOM_AUTHORIZER_IGNORED_PATHS` | `["/api/amplify-config", "/api/version"]`                                                                                         |
| `API_GATEWAY_STAGE_NAME`          | `"api"` (fixed; baked into VamsCLI endpoint constants and web `/api/*` fronting)                                                  |

### ConfigPublic Interface

`ConfigPublic` (~200 lines in `config/config.ts`) defines all deployment parameters. Key sections:

-   `env`: account, region, partition, coreStackName
-   `app.assetBuckets`: createNewBucket, defaultNewBucketSyncDatabaseId, externalAssetBuckets (bucketArn, baseAssetsPrefix, defaultSyncDatabaseId; optional bucketAccountId / bucketRegion / bucketKmsKeyArn for cross-account + SSE-KMS), presignedUrlNetworkRestrictions (allowedIpRanges / allowedVpceIds; mutually exclusive; empty = no restriction). Non-empty restrictions add a bucket policy Deny scoped to presigned `s3:authType=REST-QUERY-STRING` requests on the created asset + auxiliary bucket via `addPresignedUrlNetworkRestrictionsToBucketPolicy()`; imported external buckets are not policy-managed by VAMS. A bucketArn may be registered multiple times under non-overlapping prefixes (validated by `validateExternalAssetBuckets()`, which rejects overlapping prefixes and inconsistent per-bucket attributes); `storageBuilder` imports each unique ARN once so per-prefix event notifications merge into one S3 notification configuration.
-   `app.useGlobalVpc`: enabled, useForAllLambdas, addVpcEndpoints, optionalExternalVpcId, vpcCidrRange
-   `app.openSearch`: useServerless (enabled, nextGen, allowPublic, enableStandbyReplicas, min/maxIndexingOcu, min/maxSearchOcu, deployDeferredIndexSchema), useProvisioned, reindexOnCdkDeploy
-   `app.useAlb`: enabled, usePublicSubnet, domainHost, certificateArn
-   `app.useCloudFront`: enabled, customDomain (domainHost, certificateArn, optionalHostedZoneId)
-   `app.pipelines`: deadlineCloudExecutionTypeEnabled, useConversion3dBasic, useConversionCadMeshMetadataExtraction, usePreviewPcPotreeViewer, useSplatToolbox, useGenAiMetadata3dLabeling, useRapidPipeline (useEcs, useEks), useModelOps, useIsaacLabTraining
-   `app.addons`: useGarnetFramework, usePhysnaSync
-   `app.authProvider`: useCognito (enabled, useSaml, useUserPasswordAuthFlow), useExternalOAuthIdp, authorizerOptions.allowedIpRanges
-   `app.api`: apiType (fixed `"APIGATEWAY_REST"`); apiGatewayRest (globalRateLimit default 50, globalBurstLimit default 100, endpointType `"REGIONAL"`/`"PRIVATE"`, optionalExternalPrivateApigVPCEId for PRIVATE, apiGatewayTimeoutTime default 29 / max 300 — integration timeout in seconds, applied as `timeoutInMillis` on every route integration in `buildOpenApiSpec.ts`; above 29 requires an approved account `L-E5AE38E3` quota increase)
-   `app.govCloud` (enabled, il6Compliant); `app.iamRoleConfig` (useCustomBootstrapRoles, useCustomVamsStackRoles — mappings in `config/policy/iamRoleConfig.json`); `app.webUi` (optionalBannerHtmlMessage, allowUnsafeEvalFeatures)
-   `app.useWaf` (boolean): when true, the Web ACL rules load from `config/policy/wafPolicyConfig.json` — `managedRuleGroups` (block or count-only per `block`, plus optional per-rule `ruleActionOverrides` such as `SizeRestrictions_BODY -> count` so large upload bodies up to the API Gateway REST 10 MB limit are not blocked, and `SizeRestrictions_QUERYSTRING -> count` so the SuperSplat viewer's presigned-URL `?load=` parameter is not blocked above 2048 bytes) and `rateBasedRules` (per-entry `limit`, `aggregateKeyType` `IP`/`FORWARDED_IP` with optional `forwardedIPConfig`, and `blockResponseCode` default 429). `getConfig()` loads the file into `config.wafPolicyJSON` (undefined = legacy count-only Common Rule Set). Not part of `config.json`/ConfigPublic beyond the boolean, so it is outside ConfigBuilder + the config templates.

`Config` extends `ConfigPublic` internally with `enableCdkNag`, `dockerDefaultPlatform`, `s3AdditionalBucketPolicyJSON`, `iamRoleCustomizationJSON`, `openSearchAssetIndexName`, `openSearchFileIndexName`, and SSM parameter paths.

### Feature Flags (common/vamsAppFeatures.ts)

`VAMS_APP_FEATURES` enum: `GOVCLOUD`, `ALLOWUNSAFEEVAL`, `LOCATIONSERVICES`, `ALBDEPLOY`, `CLOUDFRONTDEPLOY`, `NOOPENSEARCH`, `AUTHPROVIDER_COGNITO`, `AUTHPROVIDER_COGNITO_SAML`, `AUTHPROVIDER_EXTERNALOAUTHIDP`, `PHYSNA_ADDON`, `DEADLINECLOUD_PIPELINES`. Features are tracked in the `enabledFeatures` array on `CoreVAMSStack` and persisted to DynamoDB by `CustomFeatureEnabledConfigNestedStack`.

---

## Lambda Builder Pattern

All 17 lambda builder files in `lib/lambdaBuilder/` follow a strict, consistent pattern. Every function builder:

### Standard Function Signature + Configuration

```typescript
export function buildSomeFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "functionName";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, "../../../backend/backend")),
        handler: `handlers.{category}.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas ? vpc : undefined,
        vpcSubnets: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas ? { subnets } : undefined,
        environment: { /* handler-specific env vars only; resource names resolve via SSM */ },
    });
```

### Required Security Calls (Every Lambda Builder)

After creating the function, every builder MUST call these five helpers, in order:

```typescript
kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey); // 1. KMS
setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources); // 2. Auth tables + audit logs
globalLambdaEnvironmentsAndPermissions(fun, config); // 3. VAMS_RESOURCE_PARAM_PREFIX + SSM grant
suppressCdkNagLambda(fun); // 4. Per-Lambda IAM4/IAM5 + wildcard KMS
suppressCdkNagErrorsByGrantReadWrite(scope); // 5. Only if using grantRead/grantReadWrite
```

`suppressCdkNagLambda(fun)` is required on every authored Lambda (including those built inside constructs and custom resources). It replaces a stack-wide suppression that bloated synthesized CloudFormation templates by stamping metadata onto every nested-stack resource. Scope the suppression to the function.

### What the Security Helpers Do

-   **`kmsKeyLambdaPermissionAddToResourcePolicy`**: Grants KMS Decrypt/Encrypt/GenerateDataKey/ReEncrypt/ListKeys/CreateGrant/ListAliases on the VAMS KMS key.
-   **`setupSecurityAndLoggingEnvironmentAndPermissions`**: Grants read on auth/constraints/userRoles/roles tables and CloudWatch PutLogEvents on all 9 audit log groups. **Does not inject table or log group environment variables** — non-pipeline handlers resolve those from SSM.
-   **`globalLambdaEnvironmentsAndPermissions`**: Adds `VAMS_RESOURCE_PARAM_PREFIX` env var and grants `ssm:GetParameter[s]`, `ssm:GetParametersByPath` on the deployment's resource-name parameter prefix.
-   **`isCognitoMfaCheckEnabled`** (authorizer builder only): computes whether the API Gateway authorizer can reach Cognito for the MFA-preference check — `TRUE` whenever Cognito is the auth provider and the authorizer runs **outside** the VPC, `FALSE` when Lambdas run in the VPC (`useForAllLambdas`), regardless of partition. VAMS does not create Cognito VPC interface endpoints, so an in-VPC authorizer has no path to Amazon Cognito. Set as `COGNITO_AUTH_ENABLED` on the **authorizer Lambda only** — the authorizer resolves MFA status (`AdminGetUser`, cached per sign-in session) and passes it to handler Lambdas via the `vams:mfaEnabled` authorizer context value, so handlers need no Cognito access.
-   **`suppressCdkNagLambda`**: Standard per-Lambda IAM4/IAM5 suppressions (AWSLambdaBasicExecutionRole, AWSLambdaVPCAccessExecutionRole, wildcard KMS), scoped to the function.
-   **`suppressCdkNagErrorsByGrantReadWrite`**: Suppresses AwsSolutions-IAM5 for S3 and resource wildcards.
-   **`suppressCdkNagLambdaFrameworkResources`**: Called once on the core stack. Applies IAM4/IAM5 suppressions to CDK-generated framework roles (custom-resource providers, bucket deployments, `AwsCustomResource`) and VAMS custom-resource roles that the per-function helper cannot reach.

---

## API Gateway Pattern

### REST API Setup (api-nestedStack.ts + constructs/rest-api-gateway-construct.ts)

-   `ApiNestedStack` is implementation-agnostic: it selects an API implementation by `config.app.api.apiType` and exposes the result via `IApiImplementation` (`apiEndpoint`, `invokeUrlWithStage`, `stageName`). The only supported type today is `API_TYPE_APIGATEWAY_REST` (the only value in `SUPPORTED_API_TYPES`); it instantiates `RestApiGatewayConstruct`. A future entry point (e.g. ALB) adds a `SUPPORTED_API_TYPES` value, a construct under `constructs/` implementing `IApiImplementation`, and a branch here — downstream consumers stay unchanged.
-   REST API (v1) built from a cross-stack route registry, materialized as a single `SpecRestApi` with an inline OpenAPI spec. Explicit Deployment + Stage (name = the fixed constant `API_GATEWAY_STAGE_NAME` = `"api"`). Access logging to CloudWatch with structured JSON. Rate limiting: `globalRateLimit` (default 50) / `globalBurstLimit` (default 100). Integration timeout: `apiGatewayTimeoutTime` (default 29s, max 300s) rendered as `timeoutInMillis` on each route's `x-amazon-apigateway-integration` (the CORS OPTIONS MOCK is excluded).
-   Custom Lambda authorizer: REQUEST type, returns IAM policy with wildcard resource (for cache correctness). Authenticated routes use the `VamsAuthorizer` scheme (identity source `method.request.header.Authorization`, 30s cache TTL); anonymous/ignored routes use `VamsAnonymousAuthorizer` (identity source `context.identity.sourceIp`, 900s cache TTL) — the same Lambda still runs the IP-restriction check, so no route is left without an authorizer.
-   CORS: all origins (`*`), standard + auth headers, all HTTP methods, credentials=false. Set in three places because REST responses come from three layers: (1) the per-path OPTIONS **MOCK** method (unauthenticated — no `security` on OPTIONS) returns the preflight ACAO from `buildOpenApiSpec.ts`; (2) **GatewayResponses** (`DEFAULT_4XX`/`DEFAULT_5XX`, added in `rest-api-gateway-construct.ts`) inject ACAO on authorizer denials (401/403), missing-auth-token, and errors — these never reach a Lambda; (3) the Lambda handler adds ACAO to its own proxy response body (`commonHeaders()`), which API Gateway returns verbatim.
-   Resource policy: **always** written explicitly to match `endpointType` (`buildOpenApiSpec.ts`) — `aws:SourceVpce`-restricted for `PRIVATE`, public allow-all for `REGIONAL`. API Gateway does not clear a prior resource policy when an update omits one, so emitting it for both types ensures a `PRIVATE`↔`REGIONAL` switch overwrites the old policy. A stale `PRIVATE` policy left on a `REGIONAL` API denies every request (incl. the CORS preflight) with `403 AccessDeniedException` at the resource-policy layer, which a browser misreports as a CORS-preflight failure.
-   Endpoint type: `endpointType` `"REGIONAL"` (default, public) or `"PRIVATE"` (reachable only through the execute-api VPC interface endpoint; requires `useGlobalVpc.enabled` + either `addVpcEndpoints` or `optionalExternalPrivateApigVPCEId`; incompatible with CloudFront; must be fronted by an ALB in isolated non-public subnets — `useAlb.enabled` + `useAlb.usePublicSubnet = false`). Only `PRIVATE` uses an execute-api interface endpoint (created by the VPC builder when `addVpcEndpoints` is enabled, else supplied via `optionalExternalPrivateApigVPCEId`); `REGIONAL` ignores any endpoint. `resolveApiGatewayVpcEndpointId()` encodes this.

### Route Registration (attachFunctionToApi helper)

Routes are registered across nested stacks (`apiBuilder-nestedStack.ts`, `apiBuilder2-nestedStack.ts`) via `attachFunctionToApi(this, lambdaFunction, { routePath, method, registry, allowAnonymous? })`. The pipeline, pipeline-template, workflow, workflow-trigger, and execution routes all live in `apiBuilder2`. For each route this (1) grants the REST API's execution role invoke permission on the Lambda, and (2) adds a descriptor (path, method, function ARN, allow-anonymous flag) to `RouteRegistry`. The REST API builder then renders all descriptors into a single OpenAPI spec and materializes them on the `SpecRestApi`.

### RESTful Route Convention

Routes use path parameters: `/database/{databaseId}/assets/{assetId}`. Asset version subresource routes include `PUT .../assetversions/{assetVersionId}` (update alias/comment), `POST .../{assetVersionId}/archive`, and `POST .../{assetVersionId}/unarchive`. Unauthenticated paths (no authorizer): `/api/amplify-config`, `/api/version`.

---

## Service Helper (Partition-Aware ARN/Endpoint Generation)

**Critical initialization** — in `bin/infra.ts`, `Service.SetConfig(config)` MUST be called at startup after `Config.getConfig(app)`, before any `Service()` call.

**ServiceFormatter** (`lib/helper/service-helper.ts`):

```typescript
Service(name: SERVICE, useFipsOverride?: boolean): ServiceFormatter
//   .ARN(resource, resourceName?)  -- partition-aware ARN
//   .Endpoint                      -- hostname (FIPS-aware)
//   .Principal / .PrincipalString  -- iam.ServicePrincipal / string

IAMArn(name: string): { role, policy, statemachine, statemachineExecution,
    stateMachineEvents, lambda, subnet, vpc, securitygroup, ssm, loggroup,
    geomap, geoapi }

Partition(): string  // Returns current partition
```

**Partition lookup** (`lib/helper/const.ts`): a lookup table supporting 5 partitions — `aws` (commercial), `aws-us-gov` (GovCloud), `aws-cn` (China), `aws-iso` (isolated), `aws-eusc` (EU Sovereign Cloud; region `eusc-de-east-1`, DNS suffix `.amazonaws.eu`). Each entry contains `arn`, `hostname`, `fipsHostname`, `principal`.

---

## Security Patterns

**CDK Nag (always enabled).** `bin/infra.ts` sets `config.enableCdkNag = true` and applies `Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }))`. Suppressions are applied at three levels: **stack** (AwsSolutions-COG3 for GovCloud, IAM4/IAM5 for Lambda execution roles), **resource** (`suppressCdkNagErrorsByGrantReadWrite()` in every lambda builder), and **path** (specific workflow IAM roles).

**KMS encryption.** Optional CMK via `config.app.useKmsCmkEncryption`. `kmsKeyLambdaPermissionAddToResourcePolicy()` grants Lambda access; `kmsKeyPolicyStatementPrincipalGenerator()` creates key policy with service principals (S3, DynamoDB, SQS, SNS, ECS, EKS, Lambda, etc.).

**S3 TLS enforcement.** Every S3 bucket gets `requireTLSAndAdditionalPolicyAddToResourcePolicy(bucket, config)` — Deny policy for `s3:*` when `aws:SecureTransport=false`, plus optional additional policy from `config/policy/s3AdditionalBucketPolicyConfig.json`.

**Content Security Policy.** `generateContentSecurityPolicy()` in `security.ts` builds CSP headers: base sources (self, blob, data, API URL, S3 endpoint); conditional sources (Cognito IDP/Identity, Location Service, unsafe-eval); extensible via `config/csp/cspAdditionalConfig.json`.

**WAF rule policy.** When `config.app.useWaf` is true, `Wafv2BasicConstruct` (`constructs/wafv2-basic-construct.ts`) builds the Web ACL rules from `config/policy/wafPolicyConfig.json` via `buildRulesFromPolicy()`: managed rule groups (`overrideAction` count vs none per `block`), optional per-rule `ruleActionOverrides` mapped to `managedRuleGroupStatement.ruleActionOverrides` (`actionToUse` count/block/allow), and rate-based rules. The shipped policy overrides two Common Rule Set rules to `count`. `SizeRestrictions_BODY` is the only Common Rule Set rule that blocks on body size (>8 KB), so counting it lets multi-part upload bodies up to the API Gateway REST 10 MB payload cap pass while every other managed rule keeps blocking (the remaining body-inspecting rules use `oversizeHandling: CONTINUE`, matching on attack signatures, not size). No `AssociationConfig` body-inspection override is needed for the 10 MB guarantee. `SizeRestrictions_QUERYSTRING` is likewise overridden to `count`: it blocks query strings over 2048 bytes, and the SuperSplat viewer loads a file by passing a presigned Amazon S3 URL in a `?load=` parameter. A presigned URL carrying a session security token already approaches that limit, and the viewer requires the value double-encoded to survive its own two decode passes, which roughly doubles it again — so the iframe request for the static viewer page was blocked with a 403 before it ever reached S3.

**WAF rate-based rules.** Each `rateBasedRules` entry sets `limit` (per 5-min window) and `aggregateKeyType`. The shipped `VAMS-RateLimit` uses `FORWARDED_IP` (with `forwardedIPConfig.headerName` = `X-Forwarded-For`, `fallbackBehavior` = `NO_MATCH`) so it counts the real client IP behind CloudFront/ALB/NAT rather than a shared upstream address; the same policy applies to both the CloudFront-scoped and regional ACLs. Rate blocks return a `429` (via `blockResponseCode`, default 429) with a shared `CustomResponseBody` (`VamsRateLimitBody`, `APPLICATION_JSON`) registered on the ACL when any rate rule exists — distinct from the `403` used for auth denials. The web `apiClient` and the VAMS CLI both treat `429` as retryable (honor `Retry-After`, back off) rather than an auth failure. Managed-group blocks keep the WAF default 403. Test: `test/wafRateLimit.test.ts`.

**IAM aspects.** `IamRoleTransform` applies role name prefixes and permission boundaries (from `cdk.json` "aws" environment settings). `LogRetentionAspect` forces `RetentionDays.ONE_YEAR` on all `CfnLogGroup` resources.

---

## GovCloud Considerations

**Required when `config.app.govCloud.enabled = true`:** `useGlobalVpc.enabled` MUST be `true`; `useCloudFront.enabled` MUST be `false` (no CloudFront in GovCloud); `useLocationService.enabled` MUST be `false`.

**Additional when `config.app.govCloud.il6Compliant = true`:** Cognito MUST be disabled (`useCognito.enabled = false`); WAF MUST be disabled (`useWaf = false`); KMS CMK encryption MUST be enabled (`useKmsCmkEncryption.enabled = true`).

**GovCloud-specific behavior:** FIPS endpoints via `config.app.useFips` (used by ServiceFormatter); `AwsSolutions-COG3` suppressed (AdvancedSecurityMode unavailable); EventSourceMapping tags removed via `addPropertyDeletionOverride` (some resources don't support tags in GovCloud); VPC endpoints conditional on feature flags; ALB deployment instead of CloudFront for static web hosting.

---

## Partition Portability (Commercial / GovCloud / EU Sovereign)

VAMS deploys to `aws`, `aws-us-gov`, `aws-eusc` (EU Sovereign Cloud, region `eusc-de-east-1`), and potentially `aws-cn` / `aws-iso*`. A partition defect is expensive in a way ordinary bugs are not: it is invisible in commercial synth, invisible in unit tests, and typically surfaces as a `CREATE_FAILED` **mid-deploy**, which rolls back the whole core stack (~30 min). Treat the checklist below as part of writing any new construct.

### `govCloud.enabled` is the restricted-partition flag, not a GovCloud flag

**Both the govcloud and the eusovereign config templates set `app.govCloud.enabled: true`** (`config.template.govcloud.json`, `config.template.eusovereign.json`); only commercial sets `false`. Read it as "this partition has reduced service/feature capability." Gating a capability downgrade on it therefore covers EU Sovereign automatically — which is why the EventSourceMapping tag strip, the Cognito feature downgrades, and the API Gateway TLS-policy skip are all keyed on this one flag.

Choose between the two forms deliberately:

| Use                                    | When                                                                                                                                                                                                                                                                                               |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `config.app.govCloud.enabled`          | A capability downgrade shared by every restricted partition. Matches every existing EventSourceMapping tag strip and the Cognito/TLS branches.                                                                                                                                                     |
| `config.env.partition` / `Partition()` | The decision must hold regardless of operator flag hygiene, or it is genuinely partition-specific. Used for the commercial-only EventBridge bus CMK (`storageBuilder-nestedStack.ts`), the SAML and Deadline Cloud `=== "aws"` gates in `getConfig()`, and the `aws-eusc` OpenSearch version pick. |

When writing a partition deny-list, **name every restricted partition explicitly** — the VPC builder's Cognito-PrivateLink check is the model to copy, because it excludes `aws-us-gov`, `aws-eusc`, and `aws-iso*` while still allowing `aws-cn`, where the service does exist. A check written as `Partition() === "aws-us-gov"` would silently miss EU Sovereign.

> **Known gap:** nothing validates that `app.govCloud.enabled` agrees with `config.env.partition`. Deploying to GovCloud/EU Sovereign while leaving the flag `false` is representable, passes synth, and then fails at the first EventSourceMapping with "Tags not supported in request."

### Checklist for new infrastructure

1. **Event source mappings — never call `fun.addEventSource()` unconditionally.** CDK's high-level construct stamps the stack tags onto the underlying `AWS::Lambda::EventSourceMapping`, and GovCloud/EU Sovereign Lambda rejects Tags outright. Every mapping needs the branch below. This applies to SQS (`eventSourceArn`) and DynamoDB streams (`tableStreamArn`) alike; `AWS::Lambda::EventSourceMapping` is the only CFN resource type in VAMS that needs a property stripped for partition reasons.

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

    There is no CDK aspect that can do this for you: the L1 is created lazily inside `addEventSource()`, after an aspect has finished visiting the construct tree. Regression coverage: `test/eventSourceMappingGovCloudTags.test.ts`.

2. **Never hardcode a partition, DNS suffix, or region.** Use `Service("X").ARN(...)` / `.Endpoint` / `.Principal`, `IAMArn(name)`, and `Partition()`. The DNS suffix differs per partition (`.amazonaws.com`, `.amazonaws.com.cn`, **`.amazonaws.eu`** for `aws-eusc`, `.c2s.ic.gov`, …), while service **principals** stay `.amazonaws.com` in `aws-us-gov` and `aws-eusc` — so a literal `ServicePrincipal("lambda.amazonaws.com")` happens to work in those two but is wrong in `aws-cn`/ISO. Prefer `Service("LAMBDA").Principal` in new code.

3. **Adding a `Service()` call means checking `SERVICE_LOOKUP` coverage.** `ServiceFormatter` **throws** at synth (`Service ${name} not found in partition ${partition}`) when the service has no entry for the deployment's partition. Several entries are commercial-only or commercial+GovCloud (`AOSS`, `GEO`, `CLOUDFRONT`, `COGNITO_HOSTED_UI`). Two ways to resolve it, and the choice is a product decision, not a mechanical one: **add the partition entry** in `lib/helper/const.ts` if the service exists there, or **forbid the feature in `getConfig()`** if it does not. Prefer the validation when the service is genuinely unavailable — a `Service ${name} not found` throw names the service rather than the configuration field that caused it, which sends the operator to the wrong file. `AOSS` in `aws-eusc` is the worked example: OpenSearch Serverless is not offered in the EU Sovereign Cloud, so `getConfig()` rejects `openSearch.useServerless.enabled` there and points at `useProvisioned` instead of leaving the operator with an opaque service-lookup failure.

4. **A new service/feature may not exist everywhere.** Add the validation to `getConfig()` (Rule 1 / Rule 3) so a bad combination fails at synth with a clear message rather than mid-deploy. Mirror it into `ConfigBuilder/validation.ts` by hand.

5. **Service versions can differ.** `OPENSEARCH_VERSION_EUSOVEREIGN` (2.19 vs 3.5) is selected on `Partition() === "aws-eusc"`; the Bedrock model id is downgraded in both restricted templates. Check availability before pinning a version or a model.

6. **All three config templates must be updated together** (Rule 1 step 4) — `commercial`, `govcloud`, `eusovereign`. They are structurally identical and differ only in values; a missed template silently falls back to a `getConfig()` default and drops the operator's value. Note `useFips` is the one capability flag where the two restricted templates disagree (`true` for GovCloud, `false` for EU Sovereign).

7. **No internet egress at build time.** A restricted-partition build host generally cannot reach commercial endpoints, so a `curl`/download inside a Docker bundling command hardcoded to a commercial S3 host will fail there.

8. **IAM resource matching is case-sensitive.** Log-group grants for `/aws/vendedlogs/*` are explicit allow-lists, and the pipeline constructs are split across two casings (`VAMSStateMachine-*` and `VAMSstateMachine-*`), both of which `workflowFunctions.ts` grants. A new pipeline that invents a third casing silently loses log-read access — reuse an existing casing.

### How to verify a partition change before shipping

Synth is the only cheap check, and it must be a **paired** one — assert the restricted output AND the commercial output, or you cannot tell a correct strip from a resource that was never emitted:

```bash
# Temporarily point config.json at the govcloud template (set env.region/account, then restore it)
npx cdk synth --all -o /tmp/gcsynth
# Inspect the emitted nested template, not the construct tree
node -e "const t=require('/abs/path/to/gcsynth/<stack>.nested.template.json');
  Object.entries(t.Resources).filter(([,v])=>v.Type==='AWS::Lambda::EventSourceMapping')
    .forEach(([k,v])=>console.log(k, 'Tags?', 'Tags' in v.Properties));"
```

A `cdk synth` against a placeholder account still emits templates (the context-lookup and CDK-Nag errors it prints are artifacts of the fake account); the templates are what matter. Prefer encoding the check as a Jest test over a one-off synth — see `test/eventSourceMappingGovCloudTags.test.ts`, which tags its test stack the way `core-stack.ts` does so the assertion is load-bearing rather than vacuous.

---

## OpenSearch Serverless Connectivity

A **private** OpenSearch Serverless collection (`allowPublic = false`) is reached only through a VPC endpoint whose **type is selected by the collection generation**:

-   **NEXTGEN** (`nextGen = true`) — hostname `\{collection-id\}.aoss.\{region\}.on.aws`. Reached through a **standard EC2 interface endpoint** (service `com.amazonaws.\{region\}.aoss-data`, `privateDnsEnabled: true`).
-   **CLASSIC** (`nextGen = false`) — hostname `\{collection-id\}.\{region\}.aoss.amazonaws.com`. Reached through the OpenSearch Serverless-managed endpoint (`opensearchserverless.CfnVpcEndpoint`) with its own Route 53 private hosted zone.

The chosen endpoint's id populates the network policy `SourceVPCEs`. Only OpenSearch-facing Lambdas (search, fileIndexer, assetIndexer, crOsReindexer, schema-deploy custom resource) run in the VPC — `useForAllLambdas` is not required. Schema-deploy uses a 14-min timeout + readiness poll because a fresh collection/endpoint plus NEXTGEN scale-to-zero cold start (10–30s) can take minutes to become reachable. Backend Lambdas sign SigV4 with service name `aoss` when `OPENSEARCH_TYPE=serverless`.

**`addVpcEndpoints` gating (NEXTGEN only).** NEXTGEN's endpoint is a standard EC2 interface endpoint, so it follows `useGlobalVpc.addVpcEndpoints`. The construct computes `createEndpointResources = useVPCEndpoint && (!nextGen || addVpcEndpoints)`:

-   True: VAMS creates the endpoint, its security group, and the VPC network policy, and runs schema-deploy in the VPC.
-   False (private NEXTGEN + `addVpcEndpoints = false`, the **deferred** case): VAMS skips the endpoint **and** the network policy. Schema-deploy runs **outside** the VPC, writes SSM parameters, and skips index creation (`DeploySSMIndexSchema` passes `deferIndexCreation: "true"`). Operator creates the `aoss-data` endpoint and matching network policy manually. To then create index mappings, set `deployDeferredIndexSchema = true` for one deployment (CDK context override honored) — the construct then computes `deferIndexCreation = deferVpcSetup && !deployDeferredIndexSchema` and `schemaDeployInVpc = createEndpointResources || (deferVpcSetup && !deferIndexCreation)`, so schema-deploy runs in the VPC against the operator endpoint and creates the (idempotent) indexes. Then reindex. Ignored when `addVpcEndpoints = true`.

CLASSIC's managed endpoint is not an EC2 interface endpoint and is always created for a private collection. See `documentation/docusaurus-site/docs/developer/opensearch.md`.

---

## Development Rules

### 1. Configuration Changes

1. Add properties to `ConfigPublic` interface in `config/config.ts`
2. Add backward-compatibility defaults in `getConfig()` (check for `undefined`)
3. Add validation logic in `getConfig()` if constraints exist
4. Update **ALL** config template files: `config.template.{commercial,govcloud,eusovereign}.json`. A missed template silently falls back to `getConfig()` defaults and drops any operator-set value.
5. Update `config.json` for the active deployment
6. Document the option in `documentation/docusaurus-site/docs/deployment/configuration-reference.md`
7. Mirror the **field** into the interactive **ConfigBuilder** component (`documentation/docusaurus-site/src/components/ConfigBuilder/`) — `schema.ts` (the field) and `defaults.ts` (the presets); see its `README.md`. Then run the `infra/test/configBuilderSync.test.ts` drift check (part of `npm test`), which covers these two files.
8. **Mirror every `getConfig()` VALIDATION rule into `ConfigBuilder/validation.ts` — by hand, in the same change.** This is the step the tooling cannot catch: the drift check verifies `schema.ts` and `defaults.ts` only, so a `throw new Error(...)` added to `getConfig()` without a matching `validation.ts` rule leaves the ConfigBuilder silently approving a configuration that fails at `cdk synth`. That is worse than no validation, because the operator has been told the config is valid.

    Each rule is a `Rule` entry carrying `id`, `severity` (`error` | `warning`), `fieldPaths` (so the UI can highlight the offending fields), an `appliesWhen` predicate that returns **true when the rule is VIOLATED**, and a `message`. Keep the `// ----- Section (config.ts:NNN-NNN) -----` comment with the source line range — it is what makes the two files diffable later. Port a `console.warn` as `severity: "warning"`.

    Two exclusions, so the mirror is not chased pointlessly: rules that read a value the browser cannot see are out of scope — notably the `app.iamRoleConfig` checks, which validate the contents of `infra/config/policy/iamRoleConfig.json`, a file the ConfigBuilder never loads. Everything derivable from `config.json` itself belongs in `validation.ts`.

    Verify the port by reading both sides rather than trusting a text search: `getConfig()` and `validation.ts` word the same rule differently, so matching on message text under-reports. Compare the config field paths each rule references.

### 2. Adding a New Lambda Function

1. Create the builder function in `lib/lambdaBuilder/`
2. Follow the standard pattern exactly (see [Lambda Builder Pattern](#lambda-builder-pattern)): `lambda.Code.fromAsset(path.join(__dirname, '../../../backend/backend'))`, `handler: handlers.{category}.${name}.lambda_handler`, `LAMBDA_PYTHON_RUNTIME`, `Duration.minutes(15)`, `Config.LAMBDA_MEMORY_SIZE`, VPC conditional on `config.app.useGlobalVpc.enabled && useForAllLambdas`
3. Grant DynamoDB table permissions (grantReadData or grantReadWriteData)
4. Apply the 5 security calls: `kmsKeyLambdaPermissionAddToResourcePolicy`, `setupSecurityAndLoggingEnvironmentAndPermissions`, `globalLambdaEnvironmentsAndPermissions`, `suppressCdkNagLambda`, and `suppressCdkNagErrorsByGrantReadWrite` (last only if using `grantRead*`)
5. Wire the function via `attachFunctionToApi()`. Prefer `apiBuilder2-nestedStack.ts` for new endpoints (primary `apiBuilder-nestedStack.ts` is near the CFN per-stack resource limit). Only place a function in `apiBuilder` if it must share a directly-referenced function instance defined there.

### 3. Adding a New Nested Stack

1. Create `lib/nestedStacks/{name}/{name}Builder-nestedStack.ts` extending `NestedStack`
2. Accept `config`, `storageResources`, and other shared resources as constructor params
3. Instantiate in `core-stack.ts` with `addDependency(storageResourcesNestedStack)`
4. Export any resources needed by other stacks via public properties

### 4. Adding a New DynamoDB Table

1. Add to `storageResources` interface + create the table in `storageResourcesBuilder()` in `storageBuilder-nestedStack.ts`
2. Apply KMS encryption if `config.app.useKmsCmkEncryption.enabled`; the shared `dynamodbDefaultProps` sets `RemovalPolicy.RETAIN` (current pattern — retained tables survive teardown; all tables are auto-named so retained orphans never collide on redeploy)
3. Add constant to `RESOURCE_PARAM_KEYS.dynamoTables` in `infra/common/resourceParamKeys.ts`
4. Add matching `ResourceParamKey` entry to `ResourceKeys` in `backend/backend/common/resourceNames.py`
5. Add matching constant to `ResourceParamKeys` in `infra/deploymentDataMigration/tools/ssm_resource_lookup.py`
6. Register descriptor in `resourceNameRegistry` (imported in `storageBuilder-nestedStack.ts`)
7. Grant permissions (`grantReadData`, `grantReadWriteData`) in lambda builders (SSM read is already granted by `globalLambdaEnvironmentsAndPermissions`)
8. Document the table in `architecture/aws-resources.md` and `architecture/data-model.md`

The same three-way constants update applies to new CloudWatch audit log groups: `RESOURCE_PARAM_KEYS.cloudwatchLogGroups`, `ResourceKeys` in `resourceNames.py`, and `ResourceParamKeys` in `ssm_resource_lookup.py`. Deprecated-but-retained tables move to `RESOURCE_PARAM_KEYS.dynamoTablesLegacy` (published under `dynamoTables/legacy/`).

### Documentation Rule: Storage Resources, Log Groups, and SSM Parameters

Whenever you **add or change** an S3 bucket, DynamoDB table, or CloudWatch log group, update `documentation/docusaurus-site/docs/architecture/aws-resources.md` and `documentation/docusaurus-site/docs/deployment/uninstall.md` (and the matching Kiro steering — see Rule 11 + the bidirectional-sync rule in root `CLAUDE.md`). Document **two independent properties**:

1. **Removal on teardown** — `RemovalPolicy.RETAIN` (survives `cdk destroy`; manual delete) vs. `RemovalPolicy.DESTROY` (auto; pair S3 with `autoDeleteObjects: true`).
2. **Custom name (redeploy-collision flag)** — whether the resource sets an explicit name (`bucketName`, `tableName`, `logGroupName`, including deterministic `generateUniqueNameHash` names). Only explicitly named resources can collide on a redeploy into the same account/configuration.

These axes are independent. **Retained + auto-named** resources (asset, auxiliary, artefacts, access logs buckets; all DynamoDB tables) survive teardown but do **not** block redeploy. **Custom/fixed-named** resources (the ALB web app bucket and its access logs bucket, named for the domain host; every `/aws/vendedlogs/...` log group) **must** be flagged so operators delete any orphaned copy before redeploying.

**SSM String parameters** (62 resource-name parameters published by ResourceNamesBuilder, including the 10 workflow-execution V2 data-model tables and the 6 pipeline/workflow V2 data-model tables): All explicitly named (`parameterName` set, e.g., `/{config.name}-{baseStackName}/resourceNames/dynamoTables/assetStorage`) → redeploy-collision relevant. RemovalPolicy: default (DESTROY with stack). String type (not SecureString) because resource names are configuration pointers, not data — an explicitly justified exception to the KMS-everywhere rule.

### 5. Service Helper Usage

Always use the `Service()` helper for partition-aware resources:

```typescript
// Correct -- partition-aware
Service("S3").Endpoint;
Service("DYNAMODB").ARN("table/myTable");
Service("LAMBDA").Principal;
```

Never hardcode `arn:aws:...` — the system supports aws, aws-us-gov, aws-cn, aws-iso, and aws-eusc.

---

## Anti-Patterns to Avoid

Most correspond to a Development Rule above; the rule text is the full guidance.

1. **Hardcoding ARN partitions** (`arn:aws:...`) — see Rule 5. Use `Service()` / `Service.Partition()`.
2. **Skipping security calls** — see Rule 2. Missing `setupSecurityAndLoggingEnvironmentAndPermissions` breaks handler auth checks.
3. **Forgetting VPC conditional** — see Rule 2. Attach VPC conditionally on `useGlobalVpc.enabled && useForAllLambdas`.
4. **Hardcoding Lambda runtime/memory** — see Rule 2. Always use `LAMBDA_PYTHON_RUNTIME` and `Config.LAMBDA_MEMORY_SIZE`.
5. **Missing backward compatibility** — see Rule 1. Add `undefined` checks in `getConfig()` for old config files.
6. **Not calling `Service.SetConfig()`**: module-level `config` must be initialized in `bin/infra.ts` before any `Service()` call — otherwise every partition-aware lookup fails at synth.
7. **Creating resources without CDK Nag suppression**: CDK Nag is always enabled; new IAM policies, S3 buckets, or Lambdas fail synthesis without appropriate suppressions.
8. **Ignoring GovCloud constraints**: features conditional on GovCloud (CloudFront, Location Service, Cognito AdvancedSecurityMode) must be checked before use.
9. **Forgetting stack dependencies** — see Rule 3. Stacks using `storageResources` must call `addDependency(storageResourcesNestedStack)`.
10. **Using `grantReadWrite` without Nag suppression** — see Rule 2. Pair with `suppressCdkNagErrorsByGrantReadWrite(scope)`.
11. **Calling `fun.addEventSource()` without the `govCloud.enabled` branch** — see Partition Portability item 1. CDK tags the underlying `AWS::Lambda::EventSourceMapping`, which GovCloud and EU Sovereign reject, failing the deploy and rolling back the core stack.
12. **Writing a partition check as `Partition() === "aws-us-gov"`** — see Partition Portability. It misses EU Sovereign (`aws-eusc`); name every restricted partition, or gate on `config.app.govCloud.enabled`, which both restricted templates set.

---

## Templates

Copy-paste scaffolds for new lambda builders, API routes, nested stacks, and config properties live in `infra/TEMPLATES.md`. Gold-standard reference files: `lib/lambdaBuilder/assetFunctions.ts` and `lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts`.

---

## Pipeline Stacks

Pipeline nested stacks, their required `backendPipelines/{name}/lambda/` layout, the three VPC builder condition blocks that new Batch/ECS/Fargate pipelines must be added to, and the S3 output path conventions live in `lib/nestedStacks/pipelines/CLAUDE.md` (auto-loaded when editing under that directory).

---

## Build and Deploy

```bash
cd infra
npm install
npx cdk synth        # Synthesize CloudFormation
npx cdk deploy       # Deploy to AWS
npx cdk diff         # Show pending changes
npx cdk destroy      # Tear down stack
```

Note: `test/infra.test.ts` uses legacy `@aws-cdk/assert` with an outdated mock config. Tests may need updates when adding features.

### Platform-Specific Native Bindings in the Lockfile

`esbuild` is a direct dependency here because `NodejsFunction` bundling runs it at synth (e.g. the OpenSearch schema-deploy Lambda). npm records only the compiled binary matching the platform that generated the lockfile ([npm/cli#4828](https://github.com/npm/cli/issues/4828)), and a later `npm install` on a different platform does **not** add the missing one — so a lockfile written on Windows leaves a Linux CI runner without `@esbuild/linux-x64`. `infra/package.json` declares the other platforms explicitly:

```json
"optionalDependencies": {
    "@esbuild/darwin-arm64": "^0.28.1",
    "@esbuild/darwin-x64": "^0.28.1",
    "@esbuild/linux-x64": "^0.28.1"
}
```

Each package carries its own `os`/`cpu` constraints, so only the matching binary is ever installed. **The versions are coupled to `esbuild` and no test catches drift** — when bumping it, re-pin these to the version `npm ls esbuild` reports, then confirm `darwin`, `linux`, and `win32` are all still recorded:

```bash
node -e "const l=require('./package-lock.json');Object.entries(l.packages).filter(([,v])=>v.os).forEach(([k,v])=>console.log(k,v.os))"
```

`npm install --force` and the `--os`/`--cpu` flags do **not** repair an already-pruned lockfile; use `npm install --package-lock-only --save-optional <pkg>@<version>`. `web/` needs the same treatment for rolldown and esbuild — see `web/CLAUDE.md` Rule 5.

---

## Key Files Quick Reference

| Purpose                           | File                                                                                         |
| --------------------------------- | -------------------------------------------------------------------------------------------- |
| CDK entry point                   | `bin/infra.ts`                                                                               |
| Config & constants                | `config/config.ts`                                                                           |
| Root stack                        | `lib/core-stack.ts`                                                                          |
| Storage (DynamoDB, S3, SNS, SQS)  | `lib/nestedStacks/storage/storageBuilder-nestedStack.ts`                                     |
| API routes                        | `lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` + `apiBuilder2-nestedStack.ts`        |
| API Gateway setup                 | `lib/nestedStacks/apiLambda/api-nestedStack.ts` + `constructs/rest-api-gateway-construct.ts` |
| Auth (Cognito/SAML/OAuth)         | `lib/nestedStacks/auth/authBuilder-nestedStack.ts`                                           |
| Security / Service / Partition    | `lib/helper/{security,service-helper,const}.ts`                                              |
| S3 bucket registry                | `lib/helper/s3AssetBuckets.ts`                                                               |
| Feature flags enum                | `common/vamsAppFeatures.ts`                                                                  |
| WAF stack                         | `lib/cf-waf-stack.ts`                                                                        |
| WAF construct + rule policy       | `lib/constructs/wafv2-basic-construct.ts` + `config/policy/wafPolicyConfig.json`             |
| Aspects (IAM role, log retention) | `lib/aspects/{iam-role-transform,log-retention}.aspect.ts`                                   |
| Pipeline orchestrator             | `lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts`                                  |
| Static web hosting                | `lib/nestedStacks/staticWebApp/staticWebBuilder-nestedStack.ts`                              |
| OpenSearch                        | `lib/nestedStacks/searchAndIndexing/searchBuilder-nestedStack.ts`                            |
| Templates (scaffolds)             | `infra/TEMPLATES.md`                                                                         |
| Pipeline stack pattern            | `lib/nestedStacks/pipelines/CLAUDE.md`                                                       |
