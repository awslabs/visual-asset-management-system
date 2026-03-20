# AWS Resources Inventory

This page provides a comprehensive inventory of all AWS resources deployed by VAMS. Resources are organized by service. Some resources are conditionally deployed based on the deployment configuration.

## Amazon DynamoDB Tables

VAMS deploys 28 Amazon DynamoDB tables for persistent data storage. All tables use on-demand (PAY_PER_REQUEST) billing, point-in-time recovery, and optional AWS KMS customer-managed key encryption.

### Core Data Tables

| Table | Partition Key (PK) | Sort Key (SK) | Streams | GSIs | Purpose |
|---|---|---|---|---|---|
| AssetStorageTable | `databaseId` | `assetId` | NEW_IMAGE | `BucketIdGSI` (PK: bucketId, SK: assetId), `assetIdGSI` (PK: assetId, SK: databaseId) | Asset records |
| DatabaseStorageTable | `databaseId` | -- | NEW_IMAGE | -- | Database (collection) records |
| PipelineStorageTable | `databaseId` | `pipelineId` | -- | -- | Pipeline definitions |
| WorkflowStorageTable | `databaseId` | `workflowId` | -- | -- | Workflow definitions |
| WorkflowExecutionsStorageTable | `databaseId:assetId` | `executionId` | -- | `WorkflowLSI` (LSI, SK: workflowDatabaseId:workflowId), `WorkflowGSI` (PK: workflowDatabaseId:workflowId, SK: executionId), `ExecutionIdGSI` (PK: workflowId, SK: executionId) | Workflow execution records |
| CommentStorageTable | `assetId` | `assetVersionId:commentId` | -- | -- | Asset comments |

### Asset Version Tables

| Table | Partition Key (PK) | Sort Key (SK) | GSIs | Purpose |
|---|---|---|---|---|
| AssetVersionsStorageTable (V2) | `databaseId:assetId` | `assetVersionId` | -- | Asset version records |
| AssetFileVersionsStorageTable (V2) | `databaseId:assetId:assetVersionId` | `fileKey` | `databaseIdAssetIdIndex` (PK: databaseId:assetId) | File version records per asset version |
| AssetFileMetadataVersionsStorageTable | `databaseId:assetId:assetVersionId` | `type:filePath:metadataKey` | `databaseIdAssetIdIndex` (PK: databaseId:assetId) | Metadata snapshot per asset version |
| AssetUploadsStorageTable | `uploadId` | `assetId` | `AssetIdGSI` (PK: assetId), `DatabaseIdGSI` (PK: databaseId), `UserIdGSI` (PK: UserId, SK: createdAt) | In-progress upload tracking |

### Metadata and Attribute Tables

| Table | Partition Key (PK) | Sort Key (SK) | Streams | GSIs | Purpose |
|---|---|---|---|---|---|
| DatabaseMetadataStorageTable (V2) | `metadataKey` | `databaseId` | NEW_IMAGE | `DatabaseIdIndex` (PK: databaseId, SK: metadataKey) | Database-level metadata |
| AssetFileMetadataStorageTable (V2) | `metadataKey` | `databaseId:assetId:filePath` | NEW_IMAGE | `DatabaseIdAssetIdFilePathIndex` (PK: databaseId:assetId:filePath, SK: metadataKey), `DatabaseIdAssetIdIndex` (PK: databaseId:assetId, SK: metadataKey) | File-level metadata |
| FileAttributeStorageTable (V2) | `attributeKey` | `databaseId:assetId:filePath` | NEW_IMAGE | `DatabaseIdAssetIdFilePathIndex` (PK: databaseId:assetId:filePath, SK: attributeKey), `DatabaseIdAssetIdIndex` (PK: databaseId:assetId, SK: attributeKey) | File attributes (system-generated) |
| MetadataSchemaStorageTable (V2) | `metadataSchemaId` | `databaseId:metadataEntityType` | -- | `DatabaseIdMetadataEntityTypeIndex`, `MetadataEntityTypeIndex`, `DatabaseIdIndex` | Metadata schema definitions |

### Asset Links Tables

| Table | Partition Key (PK) | Sort Key (SK) | Streams | GSIs | Purpose |
|---|---|---|---|---|---|
| AssetLinksStorageTable (V2) | `assetLinkId` | -- | NEW_IMAGE | `fromAssetGSI` (PK: fromAssetDatabaseId:fromAssetId, SK: toAssetDatabaseId:toAssetId), `toAssetGSI` (PK: toAssetDatabaseId:toAssetId, SK: fromAssetDatabaseId:fromAssetId) | Asset relationships (parent/child/related) |
| AssetLinksMetadataStorageTable | `assetLinkId` | `metadataKey` | NEW_IMAGE | -- | Metadata attached to asset links |

### Authorization Tables

| Table | Partition Key (PK) | Sort Key (SK) | GSIs | Purpose |
|---|---|---|---|---|
| AuthEntitiesStorageTable | `entityType` | `sk` | -- | Auth entity records |
| ConstraintsStorageTable | `constraintId` | -- | `GroupPermissionsIndex` (PK: groupId, SK: objectType), `UserPermissionsIndex` (PK: userId, SK: objectType), `ObjectTypeIndex` (PK: objectType, SK: constraintId) | Permission constraints (Casbin policies) |
| RolesStorageTable | `roleName` | -- | -- | Role definitions |
| UserRolesStorageTable | `userId` | `roleName` | -- | User-role assignments |
| UserStorageTable | `userId` | -- | -- | User profile records |
| ApiKeyStorageTable | `apiKeyId` | -- | `apiKeyHashIndex` (PK: apiKeyHash), `userIdIndex` (PK: userId, SK: apiKeyId) | API key records |

### Classification and Configuration Tables

| Table | Partition Key (PK) | Sort Key (SK) | Purpose |
|---|---|---|---|
| TagStorageTable | `tagName` | -- | Tag definitions |
| TagTypeStorageTable | `tagTypeName` | -- | Tag type (category) definitions |
| SubscriptionsStorageTable | `eventName` | `entityName_entityId` | Event notification subscriptions |
| AppFeatureEnabledStorageTable | `featureName` | -- | Enabled feature flags |
| S3AssetBucketsStorageTable | `bucketId` | `bucketName:baseAssetsPrefix` | Registered asset bucket records (GSI: `bucketNameGSI`) |

## Amazon S3 Buckets

| Bucket | Versioned | CORS | Access Logging | Purpose |
|---|---|---|---|---|
| **Asset Bucket(s)** | Yes | Yes | Yes (to Access Logs) | Primary asset file storage. One auto-created bucket plus optional external buckets. |
| **Asset Auxiliary Bucket** | Yes | Yes | Yes (to Access Logs) | Auto-generated previews, visualizer files, pipeline temporary storage. |
| **Artefacts Bucket** | Yes | No | Yes (to Access Logs) | Template notebooks and deployment artefacts. |
| **Access Logs Bucket** | Yes | No | No (self-referencing prevented) | Server access logs for all other buckets. 90-day lifecycle expiration. |
| **Web App Bucket** | Yes | No | No | Built frontend static assets (CloudFront/ALB origin). |

:::note[Asset Bucket Configuration]
VAMS supports multiple asset buckets. The `createNewBucket` configuration option creates a VAMS-managed bucket. The `externalAssetBuckets` configuration option registers pre-existing buckets by ARN. Each external bucket requires a `defaultSyncDatabaseId` and optional `baseAssetsPrefix`.
:::


## AWS Lambda Functions

VAMS deploys approximately 50 Lambda functions across 17 builder files. All functions use Python 3.12 runtime, 5308 MB memory, and 15-minute timeout.

### API Handler Functions

| Builder File | Functions | Domain |
|---|---|---|
| `assetFunctions.ts` | createAsset, uploadFile, streamAuxiliaryPreviewAsset, downloadAsset, assetVersions, streamAsset, sqsUploadFileLarge, ingestAsset | Asset CRUD, file upload/download |
| `assetsLinkFunctions.ts` | createAssetLink, assetLinksMetadata | Asset relationship management |
| `authFunctions.ts` | authConstraints, authConstraintsTemplate, apiKeyService, apiGatewayAuthorizerHttp, apiGatewayAuthorizerWebsocket | Authentication and authorization |
| `commentFunctions.ts` | addComment, editComment | Asset comments |
| `configFunctions.ts` | configService | System configuration |
| `databaseFunctions.ts` | createDatabase | Database CRUD |
| `metadataFunctions.ts` | metadataService | Metadata CRUD |
| `metadataSchemaFunctions.ts` | metadataSchemaService | Metadata schema management |
| `pipelineFunctions.ts` | createPipeline, enablePipeline | Pipeline management |
| `roleFunctions.ts` | createRole | Role CRUD |
| `sendEmailFunctions.ts` | sendEmail | Email notifications |
| `subscriptionFunctions.ts` | subscriptionService, checkSubscription, unSubscribe | Event subscriptions |
| `tagFunctions.ts` | createTag | Tag CRUD |
| `tagTypeFunctions.ts` | createTagType | Tag type CRUD |
| `userRoleFunctions.ts` | userRolesService | User-role assignment |
| `workflowFunctions.ts` | listWorkflowExecutions, createWorkflow, executeWorkflow, sqsAutoExecuteWorkflow, processWorkflowExecutionOutput, importGlobalPipelineWorkflow | Workflow management and execution |

### Search and Indexing Functions

| Builder File | Functions | Purpose |
|---|---|---|
| `searchIndexBucketSyncFunctions.ts` | searchFunction, fileIndexing, assetIndexing, sqsBucketSync (created/deleted per bucket), reindexer, fileIndexerSnsQueuing, assetIndexerSnsQueuing, databaseIndexerSnsQueuing | OpenSearch indexing and S3 bucket synchronization |

### Infrastructure Functions

| Function | Purpose |
|---|---|
| Amplify Config Lambda | Serves `/api/amplify-config` (unauthenticated) |
| VAMS Version Lambda | Serves `/api/version` (unauthenticated) |
| Schema Deploy Lambda (Node.js 20.x) | Custom resource for OpenSearch index creation |
| Populate S3 Asset Buckets Lambda | Custom resource for bucket table population |

## Amazon API Gateway

| Resource | Configuration |
|---|---|
| **API Type** | HTTP API (API Gateway V2) |
| **Authorizer** | Custom Lambda authorizer (SIMPLE response, 30s cache TTL) |
| **Identity Source** | `$request.header.Authorization` |
| **CORS** | All origins (`*`), all standard HTTP methods, credentials disabled |
| **Rate Limiting** | Default 50 requests/second rate, 100 burst (configurable) |
| **Access Logging** | CloudWatch Logs with structured JSON format |
| **Unauthenticated Paths** | `/api/amplify-config`, `/api/version` |

## AWS Step Functions

VAMS creates Step Functions state machines dynamically for each workflow definition. State machines orchestrate pipeline execution steps and handle output processing between steps.

## Amazon OpenSearch Service

| Configuration | Serverless | Provisioned |
|---|---|---|
| **Deployment** | OpenSearch Serverless collection | OpenSearch Service domain (v2.7) |
| **Indexes** | Asset index + File index (dual-index architecture) | Asset index + File index |
| **Access** | IAM-based access policies | VPC-based access (3 AZ) |
| **Configuration** | `openSearch.useServerless.enabled` | `openSearch.useProvisioned.enabled` |

:::info[No OpenSearch Mode]
Both OpenSearch modes can be disabled. When neither is enabled, the `NOOPENSEARCH` feature flag is set and search functionality is unavailable in the UI.
:::


## Amazon Cognito

Deployed when `authProvider.useCognito.enabled = true`:

| Resource | Purpose |
|---|---|
| **User Pool** | User identity management with password policies |
| **User Pool Client** | Web application client for authentication |
| **Identity Pool** | Federated identity for temporary AWS credentials |
| **SAML Provider** | Optional SAML federation (when `useSaml = true`) |

## Amazon SNS Topics

| Topic | Purpose |
|---|---|
| **EventEmailSubscriptionTopic** | Email notification subscriptions for asset events |
| **FileIndexerSnsTopic** | Routes DynamoDB Stream events to file indexer |
| **AssetIndexerSnsTopic** | Routes DynamoDB Stream events to asset indexer |
| **DatabaseIndexerSnsTopic** | Routes DynamoDB Stream events to database indexer |
| **S3ObjectCreatedTopic** (per bucket) | Amazon S3 object creation events per asset bucket |
| **S3ObjectRemovedTopic** (per bucket) | Amazon S3 object deletion events per asset bucket |

All Amazon SNS topics enforce SSL and use optional AWS KMS encryption.

## Amazon SQS Queues

| Queue | Purpose |
|---|---|
| **WorkflowAutoExecuteQueue** | Triggers automatic workflow execution on file upload |
| **BucketSyncCreated** (per bucket) | Processes S3 ObjectCreated events for bucket synchronization |
| **BucketSyncDeleted** (per bucket) | Processes S3 ObjectRemoved events for bucket synchronization |
| **File/Asset/Database Indexer Queues** | Buffer indexing events between Amazon SNS and indexer Lambdas |

All Amazon SQS queues enforce SSL and use optional AWS KMS encryption.

## Amazon CloudWatch

### Audit Log Groups (10-Year Retention)

| Log Group | Events Captured |
|---|---|
| `VAMSAuditAuthentication` | Login attempts, token validation |
| `VAMSAuditAuthorization` | Authorization decisions (allow/deny) |
| `VAMSAuditFileUpload` | File upload operations |
| `VAMSAuditFileDownload` | File download operations |
| `VAMSAuditFileDownloadStreamed` | Streamed file downloads |
| `VAMSAuditAuthOther` | Other authentication events |
| `VAMSAuditAuthChanges` | Role/constraint modifications |
| `VAMSAuditActions` | General CRUD actions |
| `VAMSAuditErrors` | Application errors |

### Infrastructure Log Groups (1-Year Retention)

| Log Group | Purpose |
|---|---|
| `VAMS-API-AccessLogs` | API Gateway access logs (structured JSON) |
| `VAMSCloudWatchVPCLogs` | VPC flow logs (when VPC enabled) |
| `VAMSCloudTrailLogs` | AWS CloudTrail logs (when enabled) |

:::note[Log Retention]
A CDK aspect (`LogRetentionAspect`) forces one-year retention on all CloudWatch Log Groups in the stack. Audit log groups are explicitly set to 10-year retention.
:::


## AWS KMS

Deployed when `useKmsCmkEncryption.enabled = true`:

| Resource | Purpose |
|---|---|
| **VAMS Encryption KMS Key** | Customer-managed key for all VAMS data encryption |

The KMS key policy grants access to the following service principals: Amazon S3, Amazon DynamoDB, Amazon SQS, Amazon SNS, Amazon ECS, Amazon EKS, Amazon ECS Tasks, Amazon CloudWatch Logs, AWS Lambda, AWS STS, and AWS CloudFormation. Conditionally, Amazon CloudFront, Amazon OpenSearch Service, and Amazon OpenSearch Serverless principals are also added.

An external CMK can be imported via `useKmsCmkEncryption.optionalExternalCmkArn`.

## Amazon VPC Resources

Deployed when `useGlobalVpc.enabled = true`:

| Resource | Configuration |
|---|---|
| **VPC** | VAMS-managed or imported external VPC |
| **Isolated Subnets** | Lambda functions, VPC endpoints (CIDR mask /23) |
| **Private Subnets** | Pipeline compute with egress (CIDR mask /26, conditional) |
| **Public Subnets** | ALB, pipeline compute (CIDR mask /26, conditional) |
| **VPC Endpoint Security Group** | Allows HTTPS (443) and DNS (53 TCP/UDP) from VPC CIDR |
| **VPC Flow Logs** | Sent to Amazon CloudWatch Logs |

See the [Network Architecture](networking.md) page for full VPC endpoint details.

## AWS WAF

Deployed when `useWaf = true`:

| Resource | Purpose |
|---|---|
| **WAFv2 Web ACL** | Web application firewall for Amazon CloudFront or Application Load Balancer |

For Amazon CloudFront deployments, the WAF stack is deployed in `us-east-1`. For Application Load Balancer deployments, the WAF is regional.

## AWS Batch

Deployed conditionally for each enabled pipeline:

| Resource | Configuration |
|---|---|
| **Compute Environment** | Fargate or Fargate with GPU (per pipeline) |
| **Job Queue** | Per-pipeline job queue |
| **Job Definition** | Container definitions with pipeline-specific configuration |
| **Security Groups** | Pipeline-specific security groups within VPC |

## AWS CloudTrail

Deployed when `addStackCloudTrailLogs = true`:

| Resource | Configuration |
|---|---|
| **Trail** | Single-region trail logging Lambda data events and S3 data events |
| **S3 Destination** | Access Logs bucket with `cloudtrail-logs/` prefix |
| **CloudWatch** | Logs sent to `VAMSCloudTrailLogs` log group |

## Web Hosting

### Amazon CloudFront (Commercial)

| Resource | Purpose |
|---|---|
| **Distribution** | Global CDN for web application and API proxy |
| **S3 Origin** | Web app bucket as origin |
| **API Origin** | API Gateway endpoint as origin |

### Application Load Balancer (GovCloud / ALB Mode)

| Resource | Purpose |
|---|---|
| **ALB** | Regional load balancer for web application |
| **Target Group** | Amazon S3 web bucket as target |
| **HTTPS Listener** | TLS termination with ACM certificate |

## Next Steps

- [Security Architecture](security.md) -- How these resources are secured
- [Network Architecture](networking.md) -- VPC endpoints and connectivity
- [Data Model](data-model.md) -- Amazon DynamoDB schemas and Amazon OpenSearch index mappings
