# AWS Resources Inventory

This page provides a comprehensive inventory of all AWS resources deployed by VAMS. Resources are organized by service. Some resources are conditionally deployed based on the deployment configuration.

## Amazon DynamoDB Tables

VAMS deploys 51 Amazon DynamoDB tables for persistent data storage — 46 read by Lambda handlers and 5 migration source tables. All tables use on-demand (PAY_PER_REQUEST) billing, point-in-time recovery, and optional AWS KMS customer-managed key encryption.

All tables use a `RETAIN` removal policy, so they and their data survive `cdk destroy` and require manual deletion. Because every table is auto-named by AWS CloudFormation (no explicit `tableName`), a retained orphan never collides with the freshly named table a redeploy creates. See [Uninstall the solution — Step 3: Delete DynamoDB tables](../deployment/uninstall.md#step-3-delete-dynamodb-tables) for cleanup steps.

### Core Data Tables

| Table                          | Partition Key (PK)   | Sort Key (SK)              | Streams   | GSIs                                                                                                                                                                           | Purpose                                                        |
| ------------------------------ | -------------------- | -------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| AssetStorageTable              | `databaseId`         | `assetId`                  | NEW_IMAGE | `BucketIdGSI` (PK: bucketId, SK: assetId), `assetIdGSI` (PK: assetId, SK: databaseId)                                                                                          | Asset records                                                  |
| DatabaseStorageTable           | `databaseId`         | --                         | NEW_IMAGE | --                                                                                                                                                                             | Database (collection) records                                  |
| PipelineStorageTable           | `databaseId`         | `pipelineId`               | --        | --                                                                                                                                                                             | Pipeline definitions (legacy; retained migration source)       |
| WorkflowStorageTable           | `databaseId`         | `workflowId`               | --        | --                                                                                                                                                                             | Workflow definitions (legacy; retained migration source)       |
| WorkflowExecutionsStorageTable | `databaseId:assetId` | `executionId`              | --        | `WorkflowLSI` (LSI, SK: workflowDatabaseId:workflowId), `WorkflowGSI` (PK: workflowDatabaseId:workflowId, SK: executionId), `ExecutionIdGSI` (PK: workflowId, SK: executionId) | Workflow execution records (legacy; retained migration source) |
| CommentStorageTable            | `assetId`            | `assetVersionId:commentId` | --        | --                                                                                                                                                                             | Asset comments                                                 |

### Pipeline and Workflow Tables (V2 data model)

Pipelines and workflows are database-scoped: the partition key is the `databaseId` and the sort key is the entity id, so `(databaseId, id)` is unique. Each entity has a per-database by-date GSI, a per-category GSI, and a cross-database by-date GSI (constant `allListPartition` partition, so the global list resolves as a single newest-first query rather than a table scan). Templates, template tag schemas, and triggers hang off these entities.

An asset's execution history is served by two indexes, one per direction: `WorkflowExecInputsByAssetGSI` on `WorkflowExecutionInputsStorageTable` for executions that read the asset, and `WorkflowExecConfigByOutputAssetGSI` on `WorkflowExecutionConfigurationStorageTable` for executions that wrote to it. Both are sorted by `executionStartDate`, so each half resolves newest-first and the two merge into one ordered list.

| Table                                 | Partition Key (PK)              | Sort Key (SK)                              | GSIs                                                                                                                                                                                           | Purpose                                                                                                                                                                                                                                                                                                              |
| ------------------------------------- | ------------------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PipelineStorageTableV2                | `databaseId`                    | `pipelineId`                               | `PipelinesByDatabaseGSI` (PK: databaseId, SK: dateModified), `PipelinesByCategoryGSI` (PK: databaseId:category, SK: pipelineId), `PipelinesByDateGSI` (PK: allListPartition, SK: dateModified) | Pipeline definitions                                                                                                                                                                                                                                                                                                 |
| PipelineTemplatesStorageTable         | `pipelineDatabaseId:pipelineId` | `templateId`                               | --                                                                                                                                                                                             | Pipeline templates (one row per template)                                                                                                                                                                                                                                                                            |
| PipelineTemplateTagSchemaStorageTable | `tagSchemaId`                   | `pipelineDatabaseId:pipelineId:templateId` | `TagSchemaByTemplateGSI` (PK: pipelineDatabaseId:pipelineId:templateId, SK: tagSchemaId)                                                                                                       | Template tag-field schema (inline JSON)                                                                                                                                                                                                                                                                              |
| WorkflowStorageTableV2                | `databaseId`                    | `workflowId`                               | `WorkflowsByDatabaseGSI` (PK: databaseId, SK: dateModified), `WorkflowsByCategoryGSI` (PK: databaseId:category, SK: workflowId), `WorkflowsByDateGSI` (PK: allListPartition, SK: dateModified) | Workflow definitions                                                                                                                                                                                                                                                                                                 |
| WorkflowTriggersStorageTable          | `workflowDatabaseId:workflowId` | `triggerType`                              | `TriggersByBaseTypeGSI` (PK: triggerBaseType, SK: workflowDatabaseId:workflowId)                                                                                                               | Workflow triggers (e.g. file upload). The sort key is the bare type for a workflow's first trigger of that type and `<type>#<triggerId>` for an additional one, so a workflow can carry several triggers of one type; `triggerBaseType` carries the bare type for the by-type index, whose lookup is an exact match. |

### Workflow Execution Tables (V2 data model)

Executions are workflow-keyed; asset and database linkage lives in the workflow/pipeline input tables. The main execution row carries a constant `allListPartition` partition attribute so the global executions list resolves as a single newest-first query bounded by an `executionStartDate` key condition.

| Table                                           | Partition Key (PK)    | Sort Key (SK)                          | GSIs                                                                                                                                                                                                                                                               | Purpose                                |
| ----------------------------------------------- | --------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------- |
| WorkflowExecutionsStorageTableV2                | `workflowExecutionId` | `workflowDatabaseId:workflowId`        | `WorkflowExecutionsByWorkflowGSI` (PK: workflowDatabaseId:workflowId, SK: executionStartDate), `WorkflowExecutionsByGroupGSI` (PK: executionGroupId, SK: executionStartDate; sparse), `WorkflowExecutionsByDateGSI` (PK: allListPartition, SK: executionStartDate) | Main workflow execution records        |
| PipelineExecutionsStorageTable                  | `pipelineExecutionId` | `workflowExecutionId`                  | `PipelineExecByWorkflowExecGSI` (PK: workflowExecutionId, SK: pipelineDatabaseId:pipelineId), `PipelineExecChainGSI` (PK: workflowExecutionId, SK: from_pipeline_execution_id), `PipelineExecEndStateGSI` (PK: workflowExecutionId, SK: endStatePipeline)          | Per-pipeline execution records         |
| PipelineExecutionInputFilesStorageTable         | `pipelineExecutionId` | `databaseId:assetId:inputAssetFileKey` | `InputFilesByAssetGSI` (PK: databaseId:assetId, SK: pipelineExecutionId)                                                                                                                                                                                           | Pipeline input file records            |
| PipelineExecutionInputMetadataStorageTable      | `pipelineExecutionId` | `databaseId:assetId:filePath`          | --                                                                                                                                                                                                                                                                 | Pipeline input metadata records        |
| PipelineExecutionInputConfigurationStorageTable | `pipelineExecutionId` | `recordType`                           | --                                                                                                                                                                                                                                                                 | Pipeline input configuration records   |
| PipelineExecutionOutputFilesStorageTable        | `pipelineExecutionId` | `fileType:relativeFilePath`            | --                                                                                                                                                                                                                                                                 | Pipeline output file records           |
| PipelineExecutionOutputMetadataStorageTable     | `pipelineExecutionId` | `targetFilePath:metadataKey`           | --                                                                                                                                                                                                                                                                 | Pipeline output metadata records       |
| PipelineExecutionOutputResultsStorageTable      | `pipelineExecutionId` | `relativeFilePath`                     | --                                                                                                                                                                                                                                                                 | Pipeline output results records        |
| PipelineExecutionLogsStorageTable               | `pipelineExecutionId` | `logType`                              | --                                                                                                                                                                                                                                                                 | Pipeline execution logs                |
| WorkflowExecutionInputsStorageTable             | `workflowExecutionId` | `databaseId:assetId:inputAssetFileKey` | `WorkflowExecInputsByAssetGSI` (PK: databaseId:assetId, SK: executionStartDate)                                                                                                                                                                                    | Workflow-level input file records      |
| WorkflowExecutionConfigurationStorageTable      | `workflowExecutionId` | `recordType`                           | `WorkflowExecConfigByOutputAssetGSI` (PK: outputDatabaseId:outputAssetId, SK: executionStartDate; sparse — written only for asset-output executions)                                                                                                               | Workflow-level execution configuration |

### Asset Version Tables

| Table                                 | Partition Key (PK)                  | Sort Key (SK)               | GSIs                                                                                                                                                                                                      | Purpose                                |
| ------------------------------------- | ----------------------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| AssetVersionsStorageTable (V2)        | `databaseId:assetId`                | `assetVersionId`            | --                                                                                                                                                                                                        | Asset version records                  |
| AssetFileVersionsStorageTable (V2)    | `databaseId:assetId:assetVersionId` | `fileKey`                   | `databaseIdAssetIdIndex` (PK: databaseId:assetId)                                                                                                                                                         | File version records per asset version |
| AssetFileMetadataVersionsStorageTable | `databaseId:assetId:assetVersionId` | `type:filePath:metadataKey` | `databaseIdAssetIdIndex` (PK: databaseId:assetId)                                                                                                                                                         | Metadata snapshot per asset version    |
| AssetFileVersionHistoryStorageTable   | `databaseId:assetId:filePath`       | `versionId`                 | `DatabaseIdAssetIdIndex` (PK: databaseId:assetId, SK: versionId), `WorkflowExecutionIdIndex` (PK: changeWorkflowExecutionId, SK: databaseId:assetId:filePath; sparse)                                     | Per-version file change provenance     |
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

### Migration Source Tables

These tables are read only by the data-migration tooling, never by a Lambda handler. Their names are published under the `dynamoTables/legacy/` SSM parameter prefix.

| Table                         | Partition Key (PK)       | Sort Key (SK)    | Streams   | GSIs                                                               | Purpose                               |
| ----------------------------- | ------------------------ | ---------------- | --------- | ------------------------------------------------------------------ | ------------------------------------- |
| MetadataStorageTable          | `databaseId`             | `assetId`        | NEW_IMAGE | --                                                                 | Asset-level metadata migration source |
| MetadataSchemaStorageTable    | `databaseId`             | `field`          | --        | --                                                                 | Metadata schema migration source      |
| AssetVersionsStorageTable     | `assetId`                | `assetVersionId` | --        | --                                                                 | Asset version migration source        |
| AssetFileVersionsStorageTable | `assetId:assetVersionId` | `fileKey`        | --        | --                                                                 | File version migration source         |
| AssetLinksStorageTable        | `assetIdFrom`            | `assetIdTo`      | --        | `AssetIdFromGSI` (PK: assetIdFrom), `AssetIdToGSI` (PK: assetIdTo) | Asset relationship migration source   |

### Classification and Configuration Tables

| Table                         | Partition Key (PK) | Sort Key (SK)                 | Purpose                                                |
| ----------------------------- | ------------------ | ----------------------------- | ------------------------------------------------------ |
| TagStorageTable               | `tagName`          | --                            | Tag definitions                                        |
| TagTypeStorageTable           | `tagTypeName`      | --                            | Tag type (category) definitions                        |
| SubscriptionsStorageTable     | `eventName`        | `entityName_entityId`         | Event notification subscriptions                       |
| AppFeatureEnabledStorageTable | `featureName`      | --                            | Enabled feature flags                                  |
| S3AssetBucketsStorageTable    | `bucketId`         | `bucketName:baseAssetsPrefix` | Registered asset bucket records (GSI: `bucketNameGSI`) |

## Amazon S3 Buckets

| Bucket                         | Versioned | CORS | Access Logging                  | Removal on teardown     | Custom name (redeploy collision)     | Purpose                                                                                          |
| ------------------------------ | --------- | ---- | ------------------------------- | ----------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------ |
| **Asset Bucket(s)**            | Yes       | Yes  | Yes (to Access Logs)            | Retained                | No (auto-named)                      | Primary asset file storage. One auto-created bucket plus optional external buckets.              |
| **Asset Auxiliary Bucket**     | Yes       | Yes  | Yes (to Access Logs)            | Retained                | No (auto-named)                      | Auto-generated previews, visualizer files, pipeline temporary storage.                           |
| **Artefacts Bucket**           | Yes       | No   | Yes (to Access Logs)            | Retained                | No (auto-named)                      | Template notebooks, deployment artefacts, and pipeline registration bundles under `vamsSchema/`. |
| **Access Logs Bucket**         | Yes       | No   | No (self-referencing prevented) | Retained                | No (auto-named)                      | Server access logs for all other buckets. 90-day lifecycle expiration.                           |
| **Web App Bucket**             | Yes       | No   | Yes (to Web App Access Logs)    | Deleted (emptied first) | ALB only (named for the domain host) | Built frontend static assets (CloudFront/ALB origin).                                            |
| **Web App Access Logs Bucket** | Yes       | No   | No (self-referencing prevented) | Deleted (emptied first) | ALB only (named for the domain host) | Access logs for the web app bucket and ALB. 30-day lifecycle expiration.                         |
| **Model Cache Bucket(s)**      | No        | No   | No                              | Retained                | No (auto-named)                      | Cached model weights for the NVIDIA Cosmos and NVIDIA GR00T pipelines.                           |

:::note[Asset Bucket Configuration]
VAMS supports multiple asset buckets. The `createNewBucket` configuration option creates a VAMS-managed bucket. The `externalAssetBuckets` configuration option registers pre-existing buckets by ARN. Each external bucket requires a `defaultSyncDatabaseId` and optional `baseAssetsPrefix`.
:::

:::note[Removal policy and redeploy collisions are separate concerns]
Two independent properties matter when tearing down or redeploying VAMS:

-   **Removal on teardown** — The asset, auxiliary, artefacts, access logs, and model cache buckets use a `RETAIN` removal policy, so they (and their contents) survive `cdk destroy` and require manual deletion. This protects against accidental data loss. The web app bucket and its access logs bucket use a `DESTROY` removal policy with automatic object deletion, so they are emptied and removed during teardown.
-   **Custom name (redeploy collision)** — Only buckets with an explicit, fixed name can block a redeploy with a same-name conflict. The asset, auxiliary, artefacts, access logs, and model cache buckets are **auto-named** by AWS CloudFormation, so even though they are retained they do **not** need to be deleted before redeploying with the same configuration. Under ALB deployments, the web app bucket and its access logs bucket are named for the configured domain host; if a teardown fails and leaves them behind, delete them before redeploying with the same domain host.

See [Uninstall the solution](../deployment/uninstall.md) for the full cleanup procedure.
:::

## AWS Lambda Functions

VAMS deploys Lambda functions across builder files. All functions use Python 3.12 runtime, 5308 MB memory, and 15-minute timeout.

### API Handler Functions

| Builder File                 | Functions                                                                                                                                                                                                                                                             | Domain                            |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| `assetFunctions.ts`          | createAsset, uploadFile, streamAuxiliaryPreviewAsset, downloadAsset, assetVersions, streamAsset, sqsUploadFileLarge, ingestAsset, assetHistory                                                                                                                        | Asset CRUD, file upload/download  |
| `assetsLinkFunctions.ts`     | createAssetLink, assetLinksMetadata                                                                                                                                                                                                                                   | Asset relationship management     |
| `authFunctions.ts`           | authConstraints, authConstraintsTemplate, apiKeyService, apiGatewayAuthorizerRest                                                                                                                                                                                     | Authentication and authorization  |
| `commentFunctions.ts`        | addComment, editComment                                                                                                                                                                                                                                               | Asset comments                    |
| `configFunctions.ts`         | configService                                                                                                                                                                                                                                                         | System configuration              |
| `databaseFunctions.ts`       | createDatabase                                                                                                                                                                                                                                                        | Database CRUD                     |
| `metadataFunctions.ts`       | metadataService                                                                                                                                                                                                                                                       | Metadata CRUD                     |
| `metadataSchemaFunctions.ts` | metadataSchemaService                                                                                                                                                                                                                                                 | Metadata schema management        |
| `pipelineFunctions.ts`       | pipelineService, pipelineTemplateService                                                                                                                                                                                                                              | Pipeline and template management  |
| `roleFunctions.ts`           | createRole                                                                                                                                                                                                                                                            | Role CRUD                         |
| `sendEmailFunctions.ts`      | sendEmail                                                                                                                                                                                                                                                             | Email notifications               |
| `subscriptionFunctions.ts`   | subscriptionService, checkSubscription, unSubscribe                                                                                                                                                                                                                   | Event subscriptions               |
| `tagFunctions.ts`            | createTag                                                                                                                                                                                                                                                             | Tag CRUD                          |
| `tagTypeFunctions.ts`        | createTagType                                                                                                                                                                                                                                                         | Tag type CRUD                     |
| `userRoleFunctions.ts`       | userRolesService                                                                                                                                                                                                                                                      | User-role assignment              |
| `workflowFunctions.ts`       | workflowService, workflowTriggerService, executionService, executeWorkflow, workflowTriggerDispatch, processWorkflowExecutionOutput, interimPipelineTracking, handleExecutionError, registerPipelineExecution, deadlineCloudJobCallback, importGlobalPipelineWorkflow | Workflow management and execution |

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

| Queue                                  | Purpose                                                                                                                                                         |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **WorkflowTriggerDispatchQueue**       | Buffers file-upload trigger events fanned out to workflow executions                                                                                            |
| **WorkflowTriggerDispatchDLQ**         | Dead-letter queue for trigger events that fail three delivery attempts                                                                                          |
| **DeadlineCloudJobCallbackDLQ**        | Dead-letter queue for Deadline Cloud job-status events the callback Lambda could not process (conditional on `app.pipelines.deadlineCloudExecutionTypeEnabled`) |
| **LargeFileProcessingQueue**           | Buffers large multi-part upload finalization work (5-day retention, no dead-letter queue)                                                                       |
| **BucketSyncCreated** (per bucket)     | Processes S3 ObjectCreated events for bucket synchronization                                                                                                    |
| **BucketSyncDeleted** (per bucket)     | Processes S3 ObjectRemoved events for bucket synchronization                                                                                                    |
| **File/Asset/Database Indexer Queues** | Buffer indexing events between Amazon SNS and indexer Lambdas                                                                                                   |
| **Physna File/Asset Sync Queues**      | Buffer sync events for the Physna addon (conditional on `app.addons.usePhysnaSync`)                                                                             |
| **Garnet File/Asset/Database Queues**  | Buffer indexing events for the Garnet Framework addon (conditional on `app.addons.useGarnetFramework`)                                                          |

All Amazon SQS queues enforce SSL and use optional AWS KMS encryption. The workflow trigger dispatch and Deadline Cloud callback queues are auto-named by AWS CloudFormation. The large file processing, bucket sync, indexer, Physna sync, and Garnet queues carry explicit names derived from the configuration name, so an orphaned copy left by a failed teardown blocks a redeploy with the same configuration name until it is deleted.

## Amazon EventBridge

| Resource                         | Purpose                                                                   |
| -------------------------------- | ------------------------------------------------------------------------- |
| **Orchestration Bus**            | Top-level custom event bus for event-driven VAMS features                 |
| **Orchestration Bus Audit Rule** | Routes all events from the deployment's sources to a CloudWatch log group |

The bus name and event source prefix are deployment-unique, so multiple VAMS deployments can coexist in one AWS Region. The bus uses optional AWS KMS customer-managed-key encryption in the commercial AWS partition. Amazon EventBridge does not support customer managed keys on event buses in the AWS GovCloud (US) or AWS European Sovereign Cloud partitions, so in those partitions the bus uses EventBridge's default AWS owned key encryption at rest regardless of the `useKmsCmkEncryption` setting.

## Amazon CloudWatch

VAMS creates explicitly named Amazon CloudWatch log groups under the `/aws/vendedlogs/` namespace. Each name ends with a deterministic hash suffix derived from the stack name, account ID, and a resource identifier, so the same configuration redeployed into the same account regenerates the same log group names. Because these log groups are explicitly named, an orphaned group left from a prior deployment can block a redeploy with a name conflict. See [Uninstall the solution](../deployment/uninstall.md) for cleanup steps.

### Audit Log Groups

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

| Log Group Name                                     | Purpose                                                                                                              | Condition                | Removal Policy | Custom Name |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------ | -------------- | ----------- |
| REST API access logs                               | REST API access logs (structured JSON). CloudFormation-auto-named (no fixed name), so it never collides on redeploy. | Always                   | DESTROY        | No          |
| `/aws/vendedlogs/vamsPipelineWorkflows<hash>`      | Workflow Step Functions execution logs, shared by every workflow state machine                                       | Always                   | DESTROY        | Yes         |
| `/aws/vendedlogs/VAMSOrchestrationBusAudit-<hash>` | EventBridge orchestration bus audit rule target                                                                      | Always                   | DESTROY        | Yes         |
| `/aws/vendedlogs/VAMSCloudWatchVPCLogs<hash>`      | VPC flow logs                                                                                                        | `useGlobalVpc`           | DESTROY        | Yes         |
| `/aws/vendedlogs/VAMSCloudTrailLogs<hash>`         | AWS CloudTrail logs                                                                                                  | `addStackCloudTrailLogs` | DESTROY        | Yes         |

The hyphen before the hash is part of the identifier rather than a fixed convention, so it is present on the audit and orchestration bus groups and absent on the workflow, VPC flow log, and AWS CloudTrail groups. Match on the identifier prefix when searching for a group.

### Pipeline Log Groups (per enabled pipeline)

Each enabled pipeline's Step Functions state machine logs to `/aws/vendedlogs/VAMSstateMachine-<PipelineName>[-<modelKey>]<hash>` or `/aws/vendedlogs/VAMSStateMachine-<PipelineName><hash>` — the case of `stateMachine` varies by pipeline, and Amazon CloudWatch log group names are case sensitive, so a search must cover both spellings. Examples: `VAMSstateMachine-SplatToolboxPipeline`, `VAMSstateMachine-Preview3dThumbnailPipeline`, `VAMSstateMachine-CosmosPredict-<modelKey>`, `VAMSStateMachine-CoordTransform`, `VAMSStateMachine-Metadata3dLabelingPipeline`. Container-based pipelines (RapidPipeline, ModelOps) additionally create `/aws/vendedlogs/Pipelines/<containerName>` groups.

:::note[Log Retention]
A CDK aspect (`LogRetentionAspect`) sets one-year retention on every CloudWatch log group in the stack, including the audit groups.
:::

:::warning[Named log groups are retained and block redeploys]
All VAMS log groups use the `DESTROY` removal policy and are deleted when the stack is destroyed cleanly. However, if a stack deletion fails partway, or a log group is recreated by an AWS service (such as a Lambda function writing logs) after the stack is gone, the orphaned, deterministically named group will conflict with the same-named group on a subsequent redeploy. Delete any remaining `/aws/vendedlogs/...` groups for the deployment before redeploying with the same configuration name and account. This is most common with the conditional AWS CloudTrail and VPC flow log groups.
:::

## AWS Systems Manager Parameter Store

VAMS publishes deployment configuration values as explicitly named SSM `String` parameters.

| Parameter Group                                               | Count  | Purpose                                                                     |
| ------------------------------------------------------------- | ------ | --------------------------------------------------------------------------- |
| `/<name>-<baseStackName>/resourceNames/dynamoTables/*`        | 46     | DynamoDB table names resolved by Lambda functions at cold start             |
| `/<name>-<baseStackName>/resourceNames/dynamoTables/legacy/*` | 5      | Migration source table names, read by the data-migration tooling only       |
| `/<name>-<baseStackName>/resourceNames/s3Buckets/*`           | 2      | Asset auxiliary and artefacts bucket names                                  |
| `/<name>-<baseStackName>/resourceNames/cloudwatchLogGroups/*` | 9      | Audit log group names                                                       |
| `/<name>-<baseStackName>/resourceNames/lambdaFunctions/*`     | 1      | OpenSearch reindexer function name, read by the data-migration tooling only |
| `/<name>-<baseStackName>/aos/*`                               | 3      | OpenSearch endpoint and index names (when search is enabled)                |
| `/<name>-<baseStackName>/web/deployedUrl`                     | 1      | Deployed web application URL                                                |
| `/<name>-<baseStackName>/location/apiKeyArn`                  | 1      | Amazon Location Service API key ARN (when Location Service is enabled)      |
| `waf_acl_arn_<wafStackName>`                                  | 1 or 2 | AWS WAF Web ACL ARN, one per web ACL stack (when `useWaf` is enabled)       |

The `ResourceNamesBuilder` nested stack materializes 62 of the 63 `resourceNames` parameters from descriptors registered by the storage builder. The search stack publishes `lambdaFunctions/crOsReindexer` on its own because it builds after the registry is materialized. Every Lambda function receives the prefix in the `VAMS_RESOURCE_PARAM_PREFIX` environment variable and resolves the values through `backend/common/resourceNames.py` (environment-variable override, then a cached batched Parameter Store fetch). Resource names are configuration pointers rather than data, so the parameters use the `String` type without KMS encryption.

The `resourceNames`, `web/deployedUrl`, `location/apiKeyArn`, and `waf_acl_arn_*` parameters are AWS CloudFormation resources with the `DESTROY` removal policy and are deleted with their stack. The three `aos/*` parameters are written at deploy time by the OpenSearch schema-deploy custom resource rather than declared as stack resources, so they survive teardown and are removed by the manual cleanup step. Amazon Cognito deployments add three further parameters holding the user pool, identity pool, and web client ids; these are auto-named by AWS CloudFormation, so they never collide on redeploy.

:::warning[Named parameters block redeploys]
Because these parameters are explicitly named, an orphaned parameter left from a failed teardown conflicts with the same-named parameter on a subsequent redeploy. Delete every remaining parameter under the deployment's `/<name>-<baseStackName>/` prefix — all 63 `resourceNames` parameters, not only the table names — plus any `waf_acl_arn_<wafStackName>` parameter, before redeploying with the same configuration name and account. Enumerate them with a recursive `get-parameters-by-path` call on the prefix rather than from a list, so a parameter group added by a later release is not missed.
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
