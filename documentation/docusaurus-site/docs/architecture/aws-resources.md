# AWS Resources Inventory

This page provides a comprehensive inventory of all AWS resources deployed by VAMS. Resources are organized by service. Some resources are conditionally deployed based on the deployment configuration.

## Amazon DynamoDB Tables

VAMS deploys Amazon DynamoDB tables for persistent data storage. All tables use on-demand (PAY_PER_REQUEST) billing, point-in-time recovery, and optional AWS KMS customer-managed key encryption.

### Core Data Tables

| Table                          | Partition Key (PK)   | Sort Key (SK)              | Streams   | GSIs                                                                                                                                                                           | Purpose                       |
| ------------------------------ | -------------------- | -------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- |
| AssetStorageTable              | `databaseId`         | `assetId`                  | NEW_IMAGE | `BucketIdGSI` (PK: bucketId, SK: assetId), `assetIdGSI` (PK: assetId, SK: databaseId)                                                                                          | Asset records                 |
| DatabaseStorageTable           | `databaseId`         | --                         | NEW_IMAGE | --                                                                                                                                                                             | Database (collection) records |
| PipelineStorageTable           | `databaseId`         | `pipelineId`               | --        | --                                                                                                                                                                             | Pipeline definitions          |
| WorkflowStorageTable           | `databaseId`         | `workflowId`               | --        | --                                                                                                                                                                             | Workflow definitions          |
| WorkflowExecutionsStorageTable | `databaseId:assetId` | `executionId`              | --        | `WorkflowLSI` (LSI, SK: workflowDatabaseId:workflowId), `WorkflowGSI` (PK: workflowDatabaseId:workflowId, SK: executionId), `ExecutionIdGSI` (PK: workflowId, SK: executionId) | Workflow execution records    |
| CommentStorageTable            | `assetId`            | `assetVersionId:commentId` | --        | --                                                                                                                                                                             | Asset comments                |

### Asset Version Tables

| Table                                 | Partition Key (PK)                  | Sort Key (SK)               | GSIs                                                                                                                                                                                                      | Purpose                                |
| ------------------------------------- | ----------------------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| AssetVersionsStorageTable (V2)        | `databaseId:assetId`                | `assetVersionId`            | --                                                                                                                                                                                                        | Asset version records                  |
| AssetFileVersionsStorageTable (V2)    | `databaseId:assetId:assetVersionId` | `fileKey`                   | `databaseIdAssetIdIndex` (PK: databaseId:assetId)                                                                                                                                                         | File version records per asset version |
| AssetFileMetadataVersionsStorageTable | `databaseId:assetId:assetVersionId` | `type:filePath:metadataKey` | `databaseIdAssetIdIndex` (PK: databaseId:assetId)                                                                                                                                                         | Metadata snapshot per asset version    |
| AssetFileVersionHistoryStorageTable   | `databaseId:assetId:filePath`       | `versionId`                 | `DatabaseIdAssetIdIndex` (PK: databaseId:assetId, SK: versionId)                                                                                                                                          | Per-version file change provenance     |
| AssetHistoryStorageTable              | `databaseId:assetId`                | `historyRecordId`           | --                                                                                                                                                                                                        | Permanent asset lifecycle history      |
| SyncTrackingOutboundStorageTable      | `objectId`                          | `syncRecordId`              | `DatabaseIdIndex` (PK: databaseId, SK: syncRecordId), `DatabaseSystemIndex` (PK: databaseId:systemType:systemUniqueId, SK: syncRecordId), `SystemIndex` (PK: systemType:systemUniqueId, SK: syncRecordId) | Outbound external-system sync records  |
| AssetUploadsStorageTable              | `uploadId`                          | `assetId`                   | `AssetIdGSI` (PK: assetId), `DatabaseIdGSI` (PK: databaseId), `UserIdGSI` (PK: UserId, SK: createdAt)                                                                                                     | In-progress upload tracking            |

### Metadata and Attribute Tables

| Table                              | Partition Key (PK) | Sort Key (SK)                   | Streams   | GSIs                                                                                                                                                      | Purpose                            |
| ---------------------------------- | ------------------ | ------------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| DatabaseMetadataStorageTable (V2)  | `metadataKey`      | `databaseId`                    | NEW_IMAGE | `DatabaseIdIndex` (PK: databaseId, SK: metadataKey)                                                                                                       | Database-level metadata            |
| AssetFileMetadataStorageTable (V2) | `metadataKey`      | `databaseId:assetId:filePath`   | NEW_IMAGE | `DatabaseIdAssetIdFilePathIndex` (PK: databaseId:assetId:filePath, SK: metadataKey), `DatabaseIdAssetIdIndex` (PK: databaseId:assetId, SK: metadataKey)   | File-level metadata                |
| FileAttributeStorageTable (V2)     | `attributeKey`     | `databaseId:assetId:filePath`   | NEW_IMAGE | `DatabaseIdAssetIdFilePathIndex` (PK: databaseId:assetId:filePath, SK: attributeKey), `DatabaseIdAssetIdIndex` (PK: databaseId:assetId, SK: attributeKey) | File attributes (system-generated) |
| MetadataSchemaStorageTable (V2)    | `metadataSchemaId` | `databaseId:metadataEntityType` | --        | `DatabaseIdMetadataEntityTypeIndex`, `MetadataEntityTypeIndex`, `DatabaseIdIndex`                                                                         | Metadata schema definitions        |

### Asset Links Tables

| Table                          | Partition Key (PK) | Sort Key (SK) | Streams   | GSIs                                                                                                                                                                       | Purpose                                    |
| ------------------------------ | ------------------ | ------------- | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| AssetLinksStorageTable (V2)    | `assetLinkId`      | --            | NEW_IMAGE | `fromAssetGSI` (PK: fromAssetDatabaseId:fromAssetId, SK: toAssetDatabaseId:toAssetId), `toAssetGSI` (PK: toAssetDatabaseId:toAssetId, SK: fromAssetDatabaseId:fromAssetId) | Asset relationships (parent/child/related) |
| AssetLinksMetadataStorageTable | `assetLinkId`      | `metadataKey` | NEW_IMAGE | --                                                                                                                                                                         | Metadata attached to asset links           |

### Authorization Tables

| Table                    | Partition Key (PK) | Sort Key (SK) | GSIs                                                                                                                                                             | Purpose                                  |
| ------------------------ | ------------------ | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| AuthEntitiesStorageTable | `entityType`       | `sk`          | --                                                                                                                                                               | Auth entity records                      |
| ConstraintsStorageTable  | `constraintId`     | --            | `GroupPermissionsIndex` (PK: groupId, SK: objectType), `UserPermissionsIndex` (PK: userId, SK: objectType), `ObjectTypeIndex` (PK: objectType, SK: constraintId) | Permission constraints (Casbin policies) |
| RolesStorageTable        | `roleName`         | --            | --                                                                                                                                                               | Role definitions                         |
| UserRolesStorageTable    | `userId`           | `roleName`    | --                                                                                                                                                               | User-role assignments                    |
| UserStorageTable         | `userId`           | --            | --                                                                                                                                                               | User profile records                     |
| ApiKeyStorageTable       | `apiKeyId`         | --            | `apiKeyHashIndex` (PK: apiKeyHash), `userIdIndex` (PK: userId, SK: apiKeyId)                                                                                     | API key records                          |

### Classification and Configuration Tables

| Table                         | Partition Key (PK) | Sort Key (SK)                 | Purpose                                                |
| ----------------------------- | ------------------ | ----------------------------- | ------------------------------------------------------ |
| TagStorageTable               | `tagName`          | --                            | Tag definitions                                        |
| TagTypeStorageTable           | `tagTypeName`      | --                            | Tag type (category) definitions                        |
| SubscriptionsStorageTable     | `eventName`        | `entityName_entityId`         | Event notification subscriptions                       |
| AppFeatureEnabledStorageTable | `featureName`      | --                            | Enabled feature flags                                  |
| S3AssetBucketsStorageTable    | `bucketId`         | `bucketName:baseAssetsPrefix` | Registered asset bucket records (GSI: `bucketNameGSI`) |

## Amazon S3 Buckets

| Bucket                         | Versioned | CORS | Access Logging                  | Removal on teardown     | Custom name (redeploy collision)     | Purpose                                                                             |
| ------------------------------ | --------- | ---- | ------------------------------- | ----------------------- | ------------------------------------ | ----------------------------------------------------------------------------------- |
| **Asset Bucket(s)**            | Yes       | Yes  | Yes (to Access Logs)            | Retained                | No (auto-named)                      | Primary asset file storage. One auto-created bucket plus optional external buckets. |
| **Asset Auxiliary Bucket**     | Yes       | Yes  | Yes (to Access Logs)            | Retained                | No (auto-named)                      | Auto-generated previews, visualizer files, pipeline temporary storage.              |
| **Artefacts Bucket**           | Yes       | No   | Yes (to Access Logs)            | Retained                | No (auto-named)                      | Template notebooks and deployment artefacts.                                        |
| **Access Logs Bucket**         | Yes       | No   | No (self-referencing prevented) | Retained                | No (auto-named)                      | Server access logs for all other buckets. 90-day lifecycle expiration.              |
| **Web App Bucket**             | Yes       | No   | Yes (to Web App Access Logs)    | Deleted (emptied first) | ALB only (named for the domain host) | Built frontend static assets (CloudFront/ALB origin).                               |
| **Web App Access Logs Bucket** | Yes       | No   | No (self-referencing prevented) | Deleted (emptied first) | ALB only (named for the domain host) | Access logs for the web app bucket and ALB. 30-day lifecycle expiration.            |

:::note[Asset Bucket Configuration]
VAMS supports multiple asset buckets. The `createNewBucket` configuration option creates a VAMS-managed bucket. The `externalAssetBuckets` configuration option registers pre-existing buckets by ARN. Each external bucket requires a `defaultSyncDatabaseId` and optional `baseAssetsPrefix`.
:::

:::note[Removal policy and redeploy collisions are separate concerns]
Two independent properties matter when tearing down or redeploying VAMS:

-   **Removal on teardown** — The asset, auxiliary, artefacts, and access logs buckets use a `RETAIN` removal policy, so they (and their contents) survive `cdk destroy` and require manual deletion. This protects against accidental data loss. The web app bucket and its access logs bucket use a `DESTROY` removal policy with automatic object deletion, so they are emptied and removed during teardown.
-   **Custom name (redeploy collision)** — Only buckets with an explicit, fixed name can block a redeploy with a same-name conflict. The asset, auxiliary, artefacts, and access logs buckets are **auto-named** by AWS CloudFormation, so even though they are retained they do **not** need to be deleted before redeploying with the same configuration. Under ALB deployments, the web app bucket and its access logs bucket are named for the configured domain host; if a teardown fails and leaves them behind, delete them before redeploying with the same domain host.

See [Uninstall the solution](../deployment/uninstall.md) for the full cleanup procedure.
:::

## AWS Lambda Functions

VAMS deploys Lambda functions across builder files. All functions use Python 3.12 runtime, 5308 MB memory, and 15-minute timeout.

### API Handler Functions

| Builder File                 | Functions                                                                                                                                      | Domain                            |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| `assetFunctions.ts`          | createAsset, uploadFile, streamAuxiliaryPreviewAsset, downloadAsset, assetVersions, streamAsset, sqsUploadFileLarge, ingestAsset, assetHistory | Asset CRUD, file upload/download  |
| `assetsLinkFunctions.ts`     | createAssetLink, assetLinksMetadata                                                                                                            | Asset relationship management     |
| `authFunctions.ts`           | authConstraints, authConstraintsTemplate, apiKeyService, apiGatewayAuthorizerRest                                                              | Authentication and authorization  |
| `commentFunctions.ts`        | addComment, editComment                                                                                                                        | Asset comments                    |
| `configFunctions.ts`         | configService                                                                                                                                  | System configuration              |
| `databaseFunctions.ts`       | createDatabase                                                                                                                                 | Database CRUD                     |
| `metadataFunctions.ts`       | metadataService                                                                                                                                | Metadata CRUD                     |
| `metadataSchemaFunctions.ts` | metadataSchemaService                                                                                                                          | Metadata schema management        |
| `pipelineFunctions.ts`       | createPipeline, enablePipeline                                                                                                                 | Pipeline management               |
| `roleFunctions.ts`           | createRole                                                                                                                                     | Role CRUD                         |
| `sendEmailFunctions.ts`      | sendEmail                                                                                                                                      | Email notifications               |
| `subscriptionFunctions.ts`   | subscriptionService, checkSubscription, unSubscribe                                                                                            | Event subscriptions               |
| `tagFunctions.ts`            | createTag                                                                                                                                      | Tag CRUD                          |
| `tagTypeFunctions.ts`        | createTagType                                                                                                                                  | Tag type CRUD                     |
| `userRoleFunctions.ts`       | userRolesService                                                                                                                               | User-role assignment              |
| `workflowFunctions.ts`       | listWorkflowExecutions, createWorkflow, executeWorkflow, sqsAutoExecuteWorkflow, processWorkflowExecutionOutput, importGlobalPipelineWorkflow  | Workflow management and execution |

### Search and Indexing Functions

| Builder File                        | Functions                                                                                                                                                                    | Purpose                                           |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `searchIndexBucketSyncFunctions.ts` | searchFunction, fileIndexing, assetIndexing, sqsBucketSync (created/deleted per bucket), reindexer, fileIndexerSnsQueuing, assetIndexerSnsQueuing, databaseIndexerSnsQueuing | OpenSearch indexing and S3 bucket synchronization |

### Infrastructure Functions

| Function                            | Purpose                                        |
| ----------------------------------- | ---------------------------------------------- |
| Amplify Config Lambda               | Serves `/api/amplify-config` (unauthenticated) |
| VAMS Version Lambda                 | Serves `/api/version` (unauthenticated)        |
| Schema Deploy Lambda (Node.js 22.x) | Custom resource for OpenSearch index creation  |
| Populate S3 Asset Buckets Lambda    | Custom resource for bucket table population    |

## Amazon API Gateway

| Resource                  | Configuration                                                                                                                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **API Type**              | REST API (API Gateway v1). Selected by `app.api.apiType` (`"APIGATEWAY_REST"`).                                                                                                                     |
| **Endpoint Type**         | `REGIONAL` (public, no VPC endpoint) or `PRIVATE` (VPC interface endpoint only). Configurable via `app.api.apiGatewayRest.endpointType`.                                                            |
| **Stage Name**            | Fixed internal value `api` (not configurable; shared with the VamsCLI endpoint constants). The stage path is absorbed by the CloudFront originPath or ALB redirect, so client URLs remain `/api/*`. |
| **Authorizer**            | Custom Lambda authorizer (REQUEST type, returns IAM policy with wildcard resource for cache correctness). Validates JWT (Cognito/external OAuth), API keys, and optional IP allowlist.              |
| **Identity Source**       | `method.request.header.Authorization`                                                                                                                                                               |
| **CORS**                  | All origins (`*`), all standard HTTP methods, credentials disabled                                                                                                                                  |
| **Rate Limiting**         | Default 50 requests/second rate, 100 burst (configurable via `app.api.apiGatewayRest.globalRateLimit` and `app.api.apiGatewayRest.globalBurstLimit`)                                                |
| **Access Logging**        | CloudWatch Logs with structured JSON format (CloudFormation-auto-named log group)                                                                                                                   |
| **Unauthenticated Paths** | `/api/amplify-config`, `/api/version`                                                                                                                                                               |

## AWS Step Functions

VAMS creates Step Functions state machines dynamically for each workflow definition. State machines orchestrate pipeline execution steps and handle output processing between steps.

## Amazon OpenSearch Service

| Configuration     | Serverless                                                                                                                             | Provisioned                         |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **Deployment**    | OpenSearch Serverless collection in a collection group with configurable OCU capacity (the group generation is `CLASSIC` or `NEXTGEN`) | OpenSearch Service domain (v3.5)    |
| **Indexes**       | Asset index + File index (dual-index architecture)                                                                                     | Asset index + File index            |
| **Access**        | IAM-based access policies; public or VPC-endpoint-private network access (`allowPublic`)                                               | VPC-based access (2 or 3 AZ)        |
| **Configuration** | `openSearch.useServerless.enabled`                                                                                                     | `openSearch.useProvisioned.enabled` |

:::info[No OpenSearch Mode]
Both OpenSearch modes can be disabled. When neither is enabled, the `NOOPENSEARCH` feature flag is set and search functionality is unavailable in the UI.
:::

:::warning[Provisioned is for advanced deployments only]
OpenSearch Serverless is the recommended option for most VAMS deployments. The provisioned option requires a 3-AZ VPC, performs blue/green updates on domain configuration changes (instance type, EBS size, engine version) that can exceed the AWS CloudFormation custom-resource timeout, and may need a deploy-disabled-then-re-enabled recovery during major engine-version upgrades (for example, 2.7 to 3.5 in v2.6). Use it only when dedicated capacity, custom instance sizing, or features unsupported by Serverless are required. See the [OpenSearch configuration reference](../deployment/configuration-reference.md#amazon-opensearch-service-appopensearch) for the full caveat list.
:::

## Amazon Cognito

Deployed when `authProvider.useCognito.enabled = true`:

| Resource             | Purpose                                          |
| -------------------- | ------------------------------------------------ |
| **User Pool**        | User identity management with password policies  |
| **User Pool Client** | Web application client for authentication        |
| **Identity Pool**    | Federated identity for temporary AWS credentials |
| **SAML Provider**    | Optional SAML federation (when `useSaml = true`) |

## Amazon SNS Topics

| Topic                                 | Purpose                                           |
| ------------------------------------- | ------------------------------------------------- |
| **EventEmailSubscriptionTopic**       | Email notification subscriptions for asset events |
| **FileIndexerSnsTopic**               | Routes DynamoDB Stream events to file indexer     |
| **AssetIndexerSnsTopic**              | Routes DynamoDB Stream events to asset indexer    |
| **DatabaseIndexerSnsTopic**           | Routes DynamoDB Stream events to database indexer |
| **S3ObjectCreatedTopic** (per bucket) | Amazon S3 object creation events per asset bucket |
| **S3ObjectRemovedTopic** (per bucket) | Amazon S3 object deletion events per asset bucket |

All Amazon SNS topics enforce SSL and use optional AWS KMS encryption.

## Amazon SQS Queues

| Queue                                  | Purpose                                                       |
| -------------------------------------- | ------------------------------------------------------------- |
| **WorkflowAutoExecuteQueue**           | Triggers automatic workflow execution on file upload          |
| **BucketSyncCreated** (per bucket)     | Processes S3 ObjectCreated events for bucket synchronization  |
| **BucketSyncDeleted** (per bucket)     | Processes S3 ObjectRemoved events for bucket synchronization  |
| **File/Asset/Database Indexer Queues** | Buffer indexing events between Amazon SNS and indexer Lambdas |

All Amazon SQS queues enforce SSL and use optional AWS KMS encryption.

## Amazon EventBridge

| Resource                         | Purpose                                                                   |
| -------------------------------- | ------------------------------------------------------------------------- |
| **Orchestration Bus**            | Top-level custom event bus for event-driven VAMS features                 |
| **Orchestration Bus Audit Rule** | Routes all events from the deployment's sources to a CloudWatch log group |

The bus name and event source prefix are deployment-unique, so multiple VAMS deployments can coexist in one AWS Region. The bus uses optional AWS KMS customer-managed-key encryption in the commercial AWS partition. Amazon EventBridge does not support customer managed keys on event buses in the AWS GovCloud (US) or AWS European Sovereign Cloud partitions, so in those partitions the bus uses EventBridge's default AWS owned key encryption at rest regardless of the `useKmsCmkEncryption` setting.

## Amazon CloudWatch

VAMS creates explicitly named Amazon CloudWatch log groups under the `/aws/vendedlogs/` namespace. Each name ends with a deterministic hash suffix derived from the stack name, account ID, and a resource identifier, so the same configuration redeployed into the same account regenerates the same log group names. Because these log groups are explicitly named, an orphaned group left from a prior deployment can block a redeploy with a name conflict. See [Uninstall the solution](../deployment/uninstall.md) for cleanup steps.

### Audit Log Groups (10-Year Retention)

Named `/aws/vendedlogs/<identifier>-<hash>`:

| Log Group Identifier            | Events Captured                      |
| ------------------------------- | ------------------------------------ |
| `VAMSAuditAuthentication`       | Login attempts, token validation     |
| `VAMSAuditAuthorization`        | Authorization decisions (allow/deny) |
| `VAMSAuditFileUpload`           | File upload operations               |
| `VAMSAuditFileDownload`         | File download operations             |
| `VAMSAuditFileDownloadStreamed` | Streamed file downloads              |
| `VAMSAuditAuthOther`            | Other authentication events          |
| `VAMSAuditAuthChanges`          | Role/constraint modifications        |
| `VAMSAuditActions`              | General CRUD actions                 |
| `VAMSAuditErrors`               | Application errors                   |

### Infrastructure and Orchestration Log Groups

Named `/aws/vendedlogs/<identifier>-<hash>`:

| Log Group Identifier        | Purpose                                                                                                              | Condition                | Removal Policy | Custom Name |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------ | -------------- | ----------- |
| REST API access logs        | REST API access logs (structured JSON). CloudFormation-auto-named (no fixed name), so it never collides on redeploy. | Always                   | DESTROY        | No          |
| `vamsPipelineWorkflows`     | Workflow Step Functions execution logs                                                                               | Always                   | DESTROY        | Yes         |
| `VAMSOrchestrationBusAudit` | EventBridge orchestration bus audit rule target                                                                      | Always                   | DESTROY        | Yes         |
| `VAMSCloudWatchVPCLogs`     | VPC flow logs                                                                                                        | `useGlobalVpc`           | DESTROY        | Yes         |
| `VAMSCloudTrailLogs`        | AWS CloudTrail logs                                                                                                  | `addStackCloudTrailLogs` | DESTROY        | Yes         |

### Pipeline Log Groups (per enabled pipeline)

Named `/aws/vendedlogs/VAMSstateMachine-<PipelineName>[-<modelKey>]-<hash>` for each enabled pipeline's Step Functions state machine (for example, `VAMSstateMachine-SplatToolboxPipeline`, `VAMSstateMachine-Preview3dThumbnailPipeline`, `VAMSstateMachine-CosmosPredict-<modelKey>`). Container-based pipelines (RapidPipeline, ModelOps) additionally create `/aws/vendedlogs/Pipelines/<containerName>` groups.

:::note[Log Retention]
A CDK aspect (`LogRetentionAspect`) forces one-year retention on all CloudWatch Log Groups in the stack. Audit log groups are explicitly set to 10-year retention.
:::

:::warning[Named log groups are retained and block redeploys]
All VAMS log groups use the `DESTROY` removal policy and are deleted when the stack is destroyed cleanly. However, if a stack deletion fails partway, or a log group is recreated by an AWS service (such as a Lambda function writing logs) after the stack is gone, the orphaned, deterministically named group will conflict with the same-named group on a subsequent redeploy. Delete any remaining `/aws/vendedlogs/...` groups for the deployment before redeploying with the same configuration name and account. This is most common with the conditional AWS CloudTrail and VPC flow log groups.
:::

## AWS Systems Manager Parameter Store

VAMS publishes deployment configuration values as explicitly named SSM `String` parameters. All parameters use the `DESTROY` removal policy and are deleted with the stack.

| Parameter Group                                               | Count | Purpose                                                                |
| ------------------------------------------------------------- | ----- | ---------------------------------------------------------------------- |
| `/<name>-<baseStackName>/resourceNames/dynamoTables/*`        | 28    | DynamoDB table names resolved by Lambda functions at cold start        |
| `/<name>-<baseStackName>/resourceNames/s3Buckets/*`           | 2     | Asset auxiliary and artefacts bucket names                             |
| `/<name>-<baseStackName>/resourceNames/cloudwatchLogGroups/*` | 9     | Audit log group names                                                  |
| `/<name>-<baseStackName>/aos/*`                               | 3     | OpenSearch endpoint and index names (when search is enabled)           |
| `/<name>-<baseStackName>/web/deployedUrl`                     | 1     | Deployed web application URL                                           |
| `/<name>-<baseStackName>/location/apiKeyArn`                  | 1     | Amazon Location Service API key ARN (when Location Service is enabled) |

The `resourceNames` parameters are materialized by a dedicated nested stack (`ResourceNamesBuilder`) from descriptors registered by the storage builder. Every Lambda function receives the prefix in the `VAMS_RESOURCE_PARAM_PREFIX` environment variable and resolves the values through `backend/common/resourceNames.py` (environment-variable override, then a cached batched Parameter Store fetch). Resource names are configuration pointers rather than data, so the parameters use the `String` type without KMS encryption.

:::warning[Named parameters block redeploys]
Because these parameters are explicitly named, an orphaned parameter left from a failed teardown conflicts with the same-named parameter on a subsequent redeploy. Delete any remaining parameters under the deployment's prefix before redeploying with the same configuration name and account.
:::

## AWS KMS

Deployed when `useKmsCmkEncryption.enabled = true`:

| Resource                    | Purpose                                           |
| --------------------------- | ------------------------------------------------- |
| **VAMS Encryption KMS Key** | Customer-managed key for all VAMS data encryption |

The KMS key policy grants access to the following service principals: Amazon S3, Amazon DynamoDB, Amazon SQS, Amazon SNS, Amazon ECS, Amazon EKS, Amazon ECS Tasks, Amazon CloudWatch Logs, AWS Lambda, AWS STS, and AWS CloudFormation. Conditionally, Amazon CloudFront, Amazon OpenSearch Service, and Amazon OpenSearch Serverless principals are also added.

An external CMK can be imported via `useKmsCmkEncryption.optionalExternalCmkArn`.

## Amazon VPC Resources

Deployed when `useGlobalVpc.enabled = true`:

| Resource                        | Configuration                                             |
| ------------------------------- | --------------------------------------------------------- |
| **VPC**                         | VAMS-managed or imported external VPC                     |
| **Isolated Subnets**            | Lambda functions, VPC endpoints (CIDR mask /23)           |
| **Private Subnets**             | Pipeline compute with egress (CIDR mask /26, conditional) |
| **Public Subnets**              | ALB, pipeline compute (CIDR mask /26, conditional)        |
| **VPC Endpoint Security Group** | Allows HTTPS (443) and DNS (53 TCP/UDP) from VPC CIDR     |
| **VPC Flow Logs**               | Sent to Amazon CloudWatch Logs                            |

See the [Network Architecture](networking.md) page for full VPC endpoint details.

## AWS WAF

Deployed when `useWaf = true`:

| Resource                       | Scope        | Region            | Attached to                                                        |
| ------------------------------ | ------------ | ----------------- | ------------------------------------------------------------------ |
| **WAFv2 Web ACL (regional)**   | `REGIONAL`   | Deployment Region | Amazon API Gateway stage; Application Load Balancer (when enabled) |
| **WAFv2 Web ACL (CloudFront)** | `CLOUDFRONT` | `us-east-1`       | Amazon CloudFront distribution (only when CloudFront is enabled)   |

A regional web ACL is always created and associated with the API Gateway stage (for both `REGIONAL` and `PRIVATE` endpoint types), so the API's `execute-api` endpoint is protected in every fronting configuration. When Amazon CloudFront is enabled, a second `CLOUDFRONT`-scoped web ACL is created in `us-east-1` for the distribution — AWS WAF requires a separate scope for CloudFront, and a CloudFront-associated web ACL cannot be shared with any other resource type. Both web ACLs use the same `config/policy/wafPolicyConfig.json` rule policy.

The web ACLs are separate CloudFormation stacks. When CloudFront is disabled, the regional stack is `{name}-waf-{baseStackName}`. When CloudFront is enabled, the regional stack is `{name}-waf-regional-{baseStackName}` and the CloudFront stack is `{name}-waf-{baseStackName}` (in `us-east-1`).

## AWS Batch

Deployed conditionally for each enabled pipeline:

| Resource                | Configuration                                              |
| ----------------------- | ---------------------------------------------------------- |
| **Compute Environment** | Fargate or Fargate with GPU (per pipeline)                 |
| **Job Queue**           | Per-pipeline job queue                                     |
| **Job Definition**      | Container definitions with pipeline-specific configuration |
| **Security Groups**     | Pipeline-specific security groups within VPC               |

## AWS CloudTrail

Deployed when `addStackCloudTrailLogs = true`:

| Resource           | Configuration                                                     |
| ------------------ | ----------------------------------------------------------------- |
| **Trail**          | Single-region trail logging Lambda data events and S3 data events |
| **S3 Destination** | Access Logs bucket with `cloudtrail-logs/` prefix                 |
| **CloudWatch**     | Logs sent to `VAMSCloudTrailLogs` log group                       |

## Web Hosting

### Amazon CloudFront (Commercial)

| Resource         | Purpose                                      |
| ---------------- | -------------------------------------------- |
| **Distribution** | Global CDN for web application and API proxy |
| **S3 Origin**    | Web app bucket as origin                     |
| **API Origin**   | API Gateway endpoint as origin               |

### Application Load Balancer (GovCloud / ALB Mode)

| Resource           | Purpose                                    |
| ------------------ | ------------------------------------------ |
| **ALB**            | Regional load balancer for web application |
| **Target Group**   | Amazon S3 web bucket as target             |
| **HTTPS Listener** | TLS termination with ACM certificate       |

## Next Steps

-   [Security Architecture](security.md) -- How these resources are secured
-   [Network Architecture](networking.md) -- VPC endpoints and connectivity
-   [Data Model](data-model.md) -- Amazon DynamoDB schemas and Amazon OpenSearch index mappings
