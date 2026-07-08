# Detailed Architecture

This page describes the key architectural flows within VAMS, including authentication, data indexing, file upload, pipeline execution, and the configuration propagation system.

## Authentication Flow

VAMS supports multiple authentication providers: Amazon Cognito (with optional SAML federation), external OAuth identity providers, and API keys. Regardless of the provider, all requests pass through the same custom Lambda authorizer.

```mermaid
sequenceDiagram
    participant User
    participant IDP as Cognito / External OAuth IDP
    participant APIGW as API Gateway REST API
    participant Authorizer as Custom Lambda Authorizer
    participant DDB as DynamoDB (Auth Tables)
    participant Handler as Lambda Handler

    User->>IDP: Authenticate (username/password, SAML, OAuth)
    IDP-->>User: ID Token (JWT)
    User->>APIGW: API Request + Authorization Header
    APIGW->>Authorizer: Invoke (JWT in header)
    Authorizer->>Authorizer: Decode and Validate JWT
    alt Cognito Auth
        Authorizer->>IDP: Verify Token Signature
    else External OAuth
        Authorizer->>Authorizer: Validate JWKS
    else API Key
        Authorizer->>DDB: Lookup API Key Hash
    end
    Authorizer->>Authorizer: Check IP Allowlist (if configured)
    Authorizer-->>APIGW: ALLOW with Claims
    APIGW->>Handler: Invoke with User Claims
    Handler->>DDB: Load User Roles and Constraints
    Handler->>Handler: Tier 1 - API Route Authorization (Casbin)
    Handler->>Handler: Tier 2 - Object Entity Authorization (Casbin)
    Handler-->>User: Response
```

### Authorization Tiers

The Casbin policy engine enforces two authorization tiers within every Lambda handler:

| Tier       | Scope       | What It Controls                             | Casbin Method          |
| ---------- | ----------- | -------------------------------------------- | ---------------------- |
| **Tier 1** | API Route   | Can this role call this endpoint?            | `enforceAPI(event)`    |
| **Tier 2** | Data Entity | Can this user access this specific resource? | `enforce(event, item)` |

Both tiers must allow for the request to succeed. Tier 1 is evaluated using `api` and `web` object type constraints. Tier 2 is evaluated against entity-type constraints (`database`, `asset`, `pipeline`, `workflow`, etc.).

:::warning[Object Type Annotation]
Before calling Tier 2 enforcement, handlers must annotate the data object with its `object__type` field (e.g., `item['object__type'] = 'asset'`). Failing to set this field causes the authorization check to silently deny access.
:::

### Supported Authentication Providers

| Provider                | Configuration                                     | Use Case                                                          |
| ----------------------- | ------------------------------------------------- | ----------------------------------------------------------------- |
| Amazon Cognito (native) | `authProvider.useCognito.enabled = true`          | Default. Managed user pool with password auth.                    |
| Amazon Cognito + SAML   | `authProvider.useCognito.useSaml = true`          | Enterprise SSO via SAML federation.                               |
| External OAuth IDP      | `authProvider.useExternalOAuthIdp.enabled = true` | Third-party identity providers (Okta, Azure AD, etc.).            |
| API Keys                | Always available                                  | Machine-to-machine authentication. Keys stored as SHA-256 hashes. |

## Data Indexing Flow

![Data Queue Architecture](/img/dataQueues_MainFlow.png)

VAMS maintains search indexes in Amazon OpenSearch that mirror data from Amazon DynamoDB. The indexing pipeline uses Amazon DynamoDB Streams, Amazon SNS, and Amazon SQS to decouple producers from consumers.

```mermaid
graph LR
    subgraph DynamoDB Tables with Streams
        AT["Asset Table"]
        MT["Asset File Metadata Table"]
        FT["File Attribute Table"]
        DT["Database Table"]
        DMT["Database Metadata Table"]
        LT["Asset Links Table"]
        LMT["Asset Links Metadata Table"]
    end

    subgraph SNS Queuing Lambdas
        FQL["File Indexer<br/>SNS Queuing"]
        AQL["Asset Indexer<br/>SNS Queuing"]
        DQL["Database Indexer<br/>SNS Queuing"]
    end

    subgraph SNS Topics
        FSNS["File Indexer SNS"]
        ASNS["Asset Indexer SNS"]
        DSNS["Database Indexer SNS"]
    end

    subgraph SQS + Indexer Lambdas
        FSQS["File SQS"] --> FI["File Indexer"]
        ASQS["Asset SQS"] --> AI["Asset Indexer"]
    end

    subgraph OpenSearch
        FIdx["File Index"]
        AIdx["Asset Index"]
    end

    MT -->|Stream| FQL
    FT -->|Stream| FQL
    FQL --> FSNS
    FSNS --> FSQS
    FI --> FIdx

    AT -->|Stream| AQL
    MT -->|Stream| AQL
    LT -->|Stream| AQL
    LMT -->|Stream| AQL
    AQL --> ASNS
    ASNS --> ASQS
    AI --> AIdx

    DT -->|Stream| DQL
    DMT -->|Stream| DQL
    DQL --> DSNS
```

:::info[Dual Index Architecture]
VAMS uses a dual-index architecture with separate **file index** and **asset index** in Amazon OpenSearch. The file index stores per-file metadata, attributes, and S3 information. The asset index stores per-asset metadata, version information, tags, and relationship flags. Both indexes use `flat_object` fields for dynamic metadata and attributes to prevent field explosion.
:::

## File Upload Flow

File uploads to VAMS use Amazon S3 presigned URLs for direct browser-to-S3 transfers. After upload, Amazon S3 event notifications trigger automatic indexing and optional workflow execution.

```mermaid
sequenceDiagram
    participant Web as Web Application
    participant API as API Gateway
    participant Upload as Upload Lambda
    participant S3 as Amazon S3 (Asset Bucket)
    participant SNS as Amazon SNS
    participant SQS as Amazon SQS
    participant Sync as Bucket Sync Lambda
    participant WF as Workflow Auto-Execute

    Web->>API: POST /upload (file metadata)
    API->>Upload: Generate Presigned URL
    Upload-->>Web: Presigned URL + Upload ID
    Web->>S3: PUT Object (multipart upload)
    S3->>SNS: S3 ObjectCreated Event
    SNS->>SQS: Forward to Bucket Sync Queue
    SQS->>Sync: Trigger Bucket Sync Lambda
    Sync->>Sync: Index file metadata in DynamoDB
    Sync->>SQS: Send to Workflow Auto-Execute Queue
    SQS->>WF: Trigger Auto-Execute Lambda
    WF->>WF: Execute matching workflows
```

### Upload Process Details

1. The web application requests a presigned URL from the upload API endpoint, providing file metadata (name, size, content type).
2. The Lambda handler validates the file against blocked extension and MIME type lists, then generates an Amazon S3 presigned URL.
3. The browser uploads the file directly to Amazon S3 using the presigned URL (supporting multipart for large files).
4. Amazon S3 emits an `ObjectCreated` event to the bucket-specific Amazon SNS topic.
5. The Amazon SNS topic fans out to an Amazon SQS queue subscribed by the bucket sync Lambda.
6. The bucket sync Lambda creates or updates file records in Amazon DynamoDB and optionally queues workflow auto-execution.

## Pipeline Execution Flow

VAMS supports three pipeline execution types: **Lambda** (synchronous or asynchronous invocation), **SQS** (asynchronous message delivery), and **EventBridge** (asynchronous event delivery). All pipeline types are orchestrated through AWS Step Functions.

```mermaid
graph TD
    subgraph Trigger
        API["API Request"]
        AUTO["Auto-Execute<br/>(File Upload)"]
    end

    subgraph Step Functions Workflow
        START["Start Execution"]
        EXEC["Execute Pipeline Step"]
        PROCESS["Process Output"]
        NEXT{Next Step?}
        DONE["Complete"]
    end

    subgraph Pipeline Execution Types
        LB["Lambda<br/>(Sync/Async)"]
        SQ["Amazon SQS<br/>(Async + Callback)"]
        EB["Amazon EventBridge<br/>(Async + Callback)"]
    end

    subgraph Compute
        BATCH["AWS Batch<br/>(Fargate / GPU)"]
        CONTAINER["Container<br/>(Processing)"]
    end

    API --> START
    AUTO --> START
    START --> EXEC
    EXEC --> LB
    EXEC --> SQ
    EXEC --> EB
    LB --> BATCH
    SQ --> BATCH
    EB --> BATCH
    BATCH --> CONTAINER
    CONTAINER --> PROCESS
    PROCESS --> NEXT
    NEXT -->|Yes| EXEC
    NEXT -->|No| DONE
```

### Pipeline S3 Output Paths

Each pipeline step in a workflow receives designated Amazon S3 output paths from the workflow state machine:

| Path Variable                          | Target Bucket    | Purpose                                                              |
| -------------------------------------- | ---------------- | -------------------------------------------------------------------- |
| `outputS3AssetFilesPath`               | Asset bucket     | File-level outputs including `.previewFile.*` thumbnails (versioned) |
| `outputS3AssetPreviewPath`             | Asset bucket     | Asset-level preview images only (versioned)                          |
| `outputS3AssetMetadataPath`            | Asset bucket     | Metadata files produced by the pipeline (versioned)                  |
| `inputOutputS3AssetAuxiliaryFilesPath` | Auxiliary bucket | Temporary working files or non-versioned viewer data                 |

### Available Pipelines

| Pipeline                           | Compute             | Description                                                              |
| ---------------------------------- | ------------------- | ------------------------------------------------------------------------ |
| 3D Basic Conversion                | AWS Batch (Fargate) | Convert 3D file formats                                                  |
| CAD/Mesh Metadata Extraction       | AWS Batch (Fargate) | Extract metadata from CAD and mesh files                                 |
| Point Cloud Potree Viewer          | AWS Batch (Fargate) | Generate Potree octree data for point cloud visualization                |
| 3D Preview Thumbnail               | AWS Batch (Fargate) | Generate GIF/JPG/PNG preview thumbnails for 3D files                     |
| Gaussian Splatting (Splat Toolbox) | AWS Batch (Fargate) | Generate Gaussian splat reconstructions                                  |
| GenAI Metadata 3D Labeling         | AWS Batch (Fargate) | AI-powered metadata labeling using Amazon Bedrock and Amazon Rekognition |
| Model Optimization (ModelOps)      | AWS Batch (Fargate) | Optimize 3D models for web delivery                                      |
| RapidPipeline (ECS)                | AWS Batch (Fargate) | RapidPipeline integration via Amazon ECS                                 |
| RapidPipeline (EKS)                | Amazon EKS          | RapidPipeline integration via Amazon EKS                                 |
| Isaac Lab Training                 | AWS Batch (GPU)     | NVIDIA Isaac Lab simulation training                                     |

## Configuration Flow

VAMS uses a three-stage configuration system that flows from CDK deployment configuration through Amazon DynamoDB to the frontend at runtime.

```mermaid
graph LR
    subgraph CDK Deployment
        CONFIG["config.json"]
        CDK["CDK Stacks"]
        CR["Custom Resource"]
    end

    subgraph Runtime Storage
        DDB["DynamoDB<br/>AppFeatureEnabled Table"]
    end

    subgraph Frontend Runtime
        SECAPI["/api/secure-config"]
        WEBAPP["React App<br/>Feature-Gated UI"]
    end

    CONFIG -->|Drives| CDK
    CDK -->|Deploys| CR
    CR -->|Writes features| DDB
    DDB -->|Reads| SECAPI
    SECAPI -->|Returns config| WEBAPP
```

### Configuration Resolution Order

Configuration values resolve through a four-tier fallback chain:

1. **CDK context** (`-c key=value` on command line)
2. **config.json** file (`infra/config/config.json`)
3. **Environment variables**
4. **Hardcoded defaults** (in `getConfig()`)

### Feature Flags

| Feature Flag                    | Description                                                                          |
| ------------------------------- | ------------------------------------------------------------------------------------ |
| `GOVCLOUD`                      | AWS GovCloud deployment mode (also set for AWS European Sovereign Cloud deployments) |
| `ALLOWUNSAFEEVAL`               | Allow `unsafe-eval` in Content Security Policy                                       |
| `LOCATIONSERVICES`              | Amazon Location Service enabled                                                      |
| `ALBDEPLOY`                     | Application Load Balancer deployment mode                                            |
| `CLOUDFRONTDEPLOY`              | Amazon CloudFront deployment mode                                                    |
| `NOOPENSEARCH`                  | Amazon OpenSearch disabled                                                           |
| `AUTHPROVIDER_COGNITO`          | Amazon Cognito authentication provider                                               |
| `AUTHPROVIDER_COGNITO_SAML`     | Amazon Cognito with SAML federation                                                  |
| `AUTHPROVIDER_EXTERNALOAUTHIDP` | External OAuth identity provider                                                     |

## Nested Stack Dependency Chain

The following diagram shows the complete dependency ordering between VAMS nested stacks.

```mermaid
graph TD
    Core["CoreVAMSStack"]

    Core --> VPC["VPCBuilder<br/><i>Conditional</i>"]
    Core --> LL["LambdaLayers"]
    Core --> SRB["StorageResourcesBuilder"]

    SRB --> AB["AuthBuilder"]

    AB --> APIGW["ApiGatewayV2Amplify"]

    APIGW --> APIBuild["ApiBuilder"]
    APIGW --> SW["StaticWeb"]
    APIGW --> SearchB["SearchBuilder"]
    APIGW --> PB["PipelineBuilder"]
    APIGW --> Addon["AddonBuilder"]

    Core --> LS["LocationService<br/><i>Conditional</i>"]
    Core --> FE["CustomFeatureEnabledConfig"]
```

## Resource Name Resolution

VAMS Lambda functions resolve AWS resource names (Amazon DynamoDB tables, Amazon S3 buckets, Amazon CloudWatch log groups) from AWS Systems Manager Parameter Store at cold start. The CDK deployment publishes 39 SSM String parameters under `/{config.name}-{baseStackName}/resourceNames/` with resource names (28 DynamoDB tables, 2 S3 buckets, 9 audit log groups). Non-pipeline handlers receive a single `VAMS_RESOURCE_PARAM_PREFIX` environment variable pointing to this SSM prefix, plus AWS IAM permissions for `ssm:GetParameter`, `ssm:GetParameters`, and `ssm:GetParametersByPath`.

At cold start, each handler calls `get_table_name(ResourceKeys.*)`, `get_bucket_name(ResourceKeys.*)`, or `get_log_group_name(ResourceKeys.*)` from `backend/backend/common/resourceNames.py`, which caches the parameter fetch for 60 minutes. This centralizes name management, enables environment variable overrides for testing, and reduces CDK template size by removing per-handler table/bucket/log-group environment variables (pipelines in `backendPipelines/` retain their direct environment variables).

### Resolution Order

1. **Environment variable override** — check for a legacy-style env var (e.g., `ASSET_STORAGE_TABLE_NAME`), used by tests and local utilities
2. **In-module cache** — 60-minute TTL per resource key
3. **SSM GetParametersByPath** — one paginated call fetching all parameters under the prefix on first access

:::tip[Lambda Builder Pattern]
Every Lambda function is constructed by a builder function in `infra/lib/lambdaBuilder/`. Non-pipeline builders inject only handler-specific environment variables (e.g., `PRESIGNED_URL_TIMEOUT_SECONDS`); resource names are resolved from SSM. Each builder calls four required security helpers: `kmsKeyLambdaPermissionAddToResourcePolicy`, `setupSecurityAndLoggingEnvironmentAndPermissions`, `globalLambdaEnvironmentsAndPermissions` (injects `VAMS_RESOURCE_PARAM_PREFIX` and grants SSM read), and `suppressCdkNagErrorsByGrantReadWrite`.
:::

## Next Steps

-   [AWS Resources](aws-resources.md) -- Complete inventory of all deployed AWS resources
-   [Security Architecture](security.md) -- Encryption, authorization, and compliance details
-   [Network Architecture](networking.md) -- VPC, endpoints, and deployment connectivity
-   [Data Model](data-model.md) -- Amazon DynamoDB table schemas and Amazon OpenSearch index mappings
