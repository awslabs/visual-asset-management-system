# Features

This page provides a comprehensive catalog of Visual Asset Management System (VAMS) features, organized by component. VAMS includes capabilities spanning the web interface, REST API, command line interface, backend services, processing pipelines, and infrastructure.

---

## Web Interface Features

The VAMS web interface is a React 17 application built with Vite and the AWS Cloudscape Design System. It provides a complete browser-based experience for asset management and visualization.

### Viewer Plugins

VAMS includes 17 built-in viewer plugins across five categories (3D, Media, Document, Data, and Preview). The plugin-based architecture supports lazy loading, per-plugin dependency management, automatic viewer selection based on file extension, and fullscreen mode. Two additional licensed viewers (VNTANA and VEERUM) provide commercial-grade rendering for GLB models and point clouds.

For the complete list of supported file viewers and extensions, see [File Viewers](../concepts/viewers.md).

### Asset Management

-   **Database organization** -- Create and manage multiple databases, each with their own Amazon S3 bucket, metadata configuration, and access controls
-   **Asset versioning** -- Create, browse, compare, edit, archive, and unarchive asset versions with alias naming and comment fields
-   **Version selector** -- Filter the file manager and metadata views to display data from a specific stored version (read-only)
-   **Asset preview thumbnails** -- Display generated preview images in asset detail pages and search results
-   **Cross-database asset linking** -- Create relationships between assets across different databases
-   **File manager** -- Hierarchical file tree with folder expansion, file selection, copy, move, and rename operations
-   **Drag-and-drop upload** -- Upload files and folders directly through the browser with progress tracking
-   **Metadata management** -- View and edit asset-level and file-level metadata with configurable schemas
-   **Tag management** -- Assign tags to assets using custom tag types for classification
-   **Comments** -- Rich text comments with collaborative review workflows
-   **Subscriptions** -- Subscribe to asset change notifications

### Search

-   **Full-text search** -- Search across asset names, descriptions, metadata fields, and file attributes
-   **Asset and file search** -- Separate search scopes for assets and files with column-specific filters
-   **Preview thumbnails in results** -- Visual asset identification directly in search result listings
-   **Result paging** -- Full result counts with proper pagination

### User Interface

-   **Dark and light themes** -- Toggle between dark mode (default) and light mode from the top navigation settings
-   **Responsive layout** -- AWS Cloudscape Design System components with consistent AWS console styling
-   **Split navigation** -- Admin sections split into "Admin - Auth" and "Admin - Data" categories
-   **Share URLs** -- Generate shareable URLs with toggle between URLs (Embedded Auth) for time-limited presigned access and URLs (API Stream) for long-lasting authorization token URIs
-   **Configurable display names** -- Customize terminology for "Asset", "Database", and "Comment" through the synonyms system
-   **Custom banner messages** -- Display organizational announcements via the `optionalBannerHtmlMessage` configuration

### Administration

-   **Amazon Cognito user management** -- Add, update, remove, and reset passwords for Amazon Cognito users directly from the web interface (no AWS Console required)
-   **API key management** -- Create, update, and delete API keys with user ID impersonation for application-to-application integration
-   **Role management** -- Create and manage roles with two-tier permission constraints
-   **Permission constraint management** -- Define, import, and manage ABAC/RBAC constraints with bulk JSON template import
-   **Pipeline management** -- Create, edit, and delete processing pipelines with execution type selection (Lambda, SQS, EventBridge)
-   **Workflow management** -- Design multi-step processing workflows with pipeline chaining
-   **Metadata schema management** -- Define and manage metadata schemas for assets, files, databases, and asset links

---

## API Features

VAMS exposes a REST API through Amazon API Gateway V2 HttpApi, secured by a custom Lambda authorizer.

### Core API Capabilities

| Domain           | Endpoints                              | Description                                                      |
| ---------------- | -------------------------------------- | ---------------------------------------------------------------- |
| Assets           | CRUD + download + stream               | Asset lifecycle management with version-aware operations         |
| Asset Versions   | Create, update, archive, unarchive     | Version management with alias naming and metadata restoration    |
| Asset Links      | CRUD                                   | Cross-database asset relationships                               |
| Databases        | CRUD                                   | Database lifecycle with metadata and Amazon S3 bucket management |
| Files            | Upload, download, copy, move, delete   | File operations with presigned URL generation                    |
| Metadata         | CRUD                                   | Asset-level and file-level metadata with version support         |
| Metadata Schemas | CRUD                                   | Schema definitions for structured metadata validation            |
| Tags             | CRUD                                   | Tag assignment and management                                    |
| Tag Types        | CRUD                                   | Custom tag type definitions                                      |
| Pipelines        | CRUD                                   | Pipeline registration and configuration                          |
| Workflows        | CRUD + execute                         | Workflow design and execution                                    |
| Search           | Query                                  | Full-text and attribute-based search                             |
| Comments         | CRUD                                   | Asset-level comments                                             |
| Subscriptions    | CRUD                                   | Change notification subscriptions                                |
| Auth             | Routes, constraints, roles, user-roles | Permission and authorization management                          |
| Cognito Users    | CRUD + reset password                  | User management (Amazon Cognito mode only)                       |
| API Keys         | CRUD                                   | API key lifecycle management                                     |
| Config           | Amplify config, secure config, version | Runtime configuration and feature flags                          |

### API Security

-   **Custom Lambda authorizer** with JWT token validation and optional IP range restrictions
-   **Two-tier authorization** enforcement on every request (API-level and object-level)
-   **Configurable rate limiting** with `globalRateLimit` (default: 50 requests per second) and `globalBurstLimit` (default: 100 requests per second)
-   **Presigned URL generation** for secure direct Amazon S3 access with configurable timeout
-   **CORS support** for cross-origin browser requests

### API Access Patterns

-   **Streaming downloads** via `GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}` with optional `?versionId=` and `?assetVersionId=` query parameters
-   **Presigned URL downloads** for large file transfers
-   **Pagination** using `NextToken`-based continuation for list endpoints
-   **Bulk constraint import** via `POST /auth/constraintsTemplateImport` with JSON templates and server-side variable substitution

---

## CLI Features

The VamsCLI is a Python-based command line tool built on the Click framework. It supports profile-based multi-environment configuration and machine-readable JSON output.

### Command Groups

| Command Group     | Commands                                      | Description                                  |
| ----------------- | --------------------------------------------- | -------------------------------------------- |
| `assets`          | list, get, create, delete, download           | Asset lifecycle operations                   |
| `asset-links`     | list, create, delete                          | Cross-database asset relationship management |
| `asset-version`   | list, get, create, update, archive, unarchive | Asset version management                     |
| `database`        | list, get, create, delete                     | Database lifecycle operations                |
| `file`            | list, upload, download, delete, copy, move    | File operations with chunked upload          |
| `metadata`        | get, update                                   | Metadata read and write                      |
| `metadata-schema` | list, get, create, update, delete             | Metadata schema management                   |
| `search`          | assets, files                                 | Search assets and files                      |
| `tag`             | list, create, delete                          | Tag management                               |
| `tag-type`        | list, create, delete                          | Tag type management                          |
| `pipeline`        | list, get                                     | Pipeline information                         |
| `workflow`        | list, get, execute                            | Workflow management and execution            |
| `role-constraint` | list, create, delete, template import         | Permission constraint management             |
| `user`            | list, add, update, remove, reset-password     | Amazon Cognito user management               |
| `auth`            | features                                      | Authentication feature queries               |
| `apikey`          | list, create, update, delete                  | API key management                           |
| `profile`         | list, create, delete, use                     | Multi-environment profile management         |
| `setup`           | configure                                     | Initial CLI configuration                    |

### CLI Capabilities

-   **Profile management** -- Configure and switch between multiple VAMS environments
-   **JSON output mode** -- Use `--json-output` flag for machine-readable output in automation scripts
-   **Chunked file upload** -- Large file uploads with progress monitoring and retry logic
-   **Bulk operations** -- Efficient batch processing of assets, files, and metadata
-   **Permission template import** -- Import JSON constraint templates with `vamscli role-constraint template import`
-   **CI/CD integration** -- Headless operation mode for build pipeline integration

---

## Backend Features

### Authorization System

-   **Two-tier ABAC/RBAC** -- Attribute-Based and Role-Based Access Control using Casbin policy enforcement
-   **Tier 1 (API-level)** -- Controls access to API routes and web navigation paths
-   **Tier 2 (Object-level)** -- Controls access to specific data entities (databases, assets, pipelines, tags, tag types)
-   **GLOBAL keyword** -- Apply constraints across all databases or resources
-   **Deny overlay** -- Layer deny constraints on top of allow constraints for exception-based access patterns
-   **Pre-built templates** -- Five pre-built permission profiles: database-admin, database-user, database-readonly, global-readonly, deny-tagged-assets

### Metadata System

-   **Configurable schemas** -- Define metadata schemas for assets, files, databases, and asset links
-   **Auto-loaded defaults** -- Default schemas auto-loaded on deployment (configurable)
-   **Version-aware metadata** -- Metadata is versioned alongside asset versions
-   **Metadata on copy/move** -- File metadata is automatically carried forward during copy and move operations

### Audit and Logging

-   **Amazon CloudWatch audit log groups** -- Nine dedicated audit log groups for authentication, authorization, file upload, file download, file download (streamed), auth changes, auth other, actions, and errors
-   **AWS CloudTrail** -- Optional stack-level AWS CloudTrail logging (enabled by default)
-   **Structured logging** -- AWS Lambda Powertools for consistent log formatting and correlation

### Search Indexing

-   **Dual-index architecture** -- Separate Amazon OpenSearch indexes for assets (`vams-assets-v2`) and files (`vams-files-v2`)
-   **Event-driven indexing** -- Amazon SNS and Amazon SQS-based automatic index synchronization on asset and file changes
-   **Preview file indexing** -- `str_previewfilekey` and `str_assetlocationkey` fields in search indexes for optimized UI rendering
-   **Re-index on deploy** -- Optional `reindexOnCdkDeploy` flag for full index rebuild during deployment

---

## Pipeline Features

### Execution Types

Pipelines support three execution types for integration with different processing backends:

| Execution Type  | Invocation                                        | Callback Support                              | Use Case                               |
| --------------- | ------------------------------------------------- | --------------------------------------------- | -------------------------------------- |
| **Lambda**      | Synchronous or asynchronous AWS Lambda invocation | Yes (native)                                  | Lightweight processing tasks           |
| **SQS**         | Asynchronous message to an Amazon SQS queue       | Optional (via AWS Step Functions Task Tokens) | External processing system integration |
| **EventBridge** | Asynchronous event to an Amazon EventBridge bus   | Optional (via AWS Step Functions Task Tokens) | Event-driven architecture integration  |

### Built-In Pipelines

VAMS includes twelve built-in processing pipelines, each deployable through configuration flags:

| Pipeline                     | Config Flag                              | Description                                                                                                                                                                                   | Default  |
| ---------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| 3D Conversion Basic          | `useConversion3dBasic`                   | Format conversion using Trimesh and Blender                                                                                                                                                   | Enabled  |
| CAD/Mesh Metadata Extraction | `useConversionCadMeshMetadataExtraction` | Geometric metadata extraction using CADQuery                                                                                                                                                  | Disabled |
| Point Cloud Potree Viewer    | `usePreviewPcPotreeViewer`               | Potree octree generation for browser streaming                                                                                                                                                | Disabled |
| Gaussian Splat Toolbox       | `useSplatToolbox`                        | 3D Gaussian splat generation from media files                                                                                                                                                 | Disabled |
| GenAI Metadata 3D Labeling   | `useGenAiMetadata3dLabeling`             | AI-powered metadata labeling via Amazon Bedrock                                                                                                                                               | Disabled |
| 3D Preview Thumbnail         | `usePreview3dThumbnail`                  | Animated GIF or static image preview generation                                                                                                                                               | Disabled |
| NVIDIA Cosmos Predict        | `useNvidiaCosmos.modelsPredict`          | GPU-accelerated video generation from text or image/video using NVIDIA Cosmos-Predict1 (v1) and Cosmos-Predict2.5 (v2.5) world foundation models with 7B (v1), 2B, and 14B (v2.5) model sizes | Disabled |
| NVIDIA Cosmos Reason         | `useNvidiaCosmos.modelsReason`           | Vision Language Model for video/image analysis generating text-based captions, descriptions, and reasoning with Cosmos-Reason2 (2B, 8B) models                                                | Disabled |
| NVIDIA Cosmos Transfer       | `useNvidiaCosmos.modelsTransfer`         | Video transformation with control signal conditioning using Cosmos-Transfer2.5-2B for style transfer and content transformation                                                               | Disabled |
| RapidPipeline (ECS/EKS)      | `useRapidPipeline`                       | Licensed spatial data optimization                                                                                                                                                            | Disabled |
| VNTANA ModelOps              | `useModelOps`                            | Licensed ModelOps optimization                                                                                                                                                                | Disabled |
| NVIDIA Isaac Lab Training    | `useIsaacLabTraining`                    | Reinforcement learning training and evaluation                                                                                                                                                | Disabled |

### Pipeline Capabilities

-   **Auto-registration** -- Pipelines can auto-register with VAMS on deployment via CDK custom resources
-   **Auto-trigger on upload** -- Configurable automatic pipeline execution when new files are uploaded
-   **Workflow chaining** -- Chain multiple pipelines into multi-step workflows orchestrated by AWS Step Functions
-   **Custom pipeline support** -- Register custom pipelines using Lambda, SQS, or EventBridge execution types

:::note[VPC Requirement]
Pipelines that use AWS Batch Fargate containers require `useGlobalVpc.enabled` to be set to `true`. VPC endpoints for AWS Batch, Amazon ECR, and Amazon ECR Docker are automatically created when pipelines are enabled.
:::

---

## Infrastructure Features

### Deployment Options

| Feature                       | Configuration                        | Description                                                                 |
| ----------------------------- | ------------------------------------ | --------------------------------------------------------------------------- |
| **Amazon CloudFront**         | `useCloudFront.enabled`              | Default web distribution with AWS-managed TLS certificate                   |
| **CloudFront Custom Domain**  | `useCloudFront.customDomain`         | Custom domain with ACM certificate and optional Amazon Route 53 hosted zone |
| **Application Load Balancer** | `useAlb.enabled`                     | Alternative web distribution for GovCloud and VPC-isolated deployments      |
| **VPC**                       | `useGlobalVpc.enabled`               | Shared VPC with configurable CIDR range or external VPC import              |
| **VPC Endpoints**             | `useGlobalVpc.addVpcEndpoints`       | Automatic VPC endpoint creation for all required AWS services               |
| **External VPC Import**       | `useGlobalVpc.optionalExternalVpcId` | Import existing VPC with isolated, private, and public subnets              |

### Security

| Feature                     | Configuration                                | Description                                                                            |
| --------------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------- |
| **AWS KMS CMK Encryption**  | `useKmsCmkEncryption.enabled`                | Customer-managed KMS key for all storage resources                                     |
| **External KMS Key**        | `useKmsCmkEncryption.optionalExternalCmkArn` | Import an existing AWS KMS CMK                                                         |
| **AWS WAF**                 | `useWaf`                                     | Web Application Firewall protection for Amazon CloudFront or Application Load Balancer |
| **FIPS Endpoints**          | `useFips`                                    | Federal Information Processing Standards compliant endpoints                           |
| **IP Range Restrictions**   | `authorizerOptions.allowedIpRanges`          | Network-level access control via the custom Lambda authorizer                          |
| **TLS Enforcement**         | Always on                                    | All Amazon S3 buckets deny non-TLS connections                                         |
| **CDK Nag**                 | Always on                                    | AWS Solutions security compliance checks on all resources                              |
| **AWS CloudTrail**          | `addStackCloudTrailLogs`                     | API-level audit logging (enabled by default)                                           |
| **Content Security Policy** | Dynamic                                      | CSP headers generated based on deployment configuration                                |

### Authentication Providers

| Provider                     | Configuration                              | Description                                      |
| ---------------------------- | ------------------------------------------ | ------------------------------------------------ |
| **Amazon Cognito**           | `authProvider.useCognito.enabled`          | Default authentication with user pool management |
| **Amazon Cognito with SAML** | `authProvider.useCognito.useSaml`          | SAML federation with Amazon Cognito              |
| **External OAuth2**          | `authProvider.useExternalOAuthIdp.enabled` | External identity provider with PKCE flow        |

### Feature Flags

VAMS uses a feature flag system to conditionally enable capabilities at deployment time. Feature flags are persisted to Amazon DynamoDB and read by the web interface at runtime.

| Feature Flag                    | Description                                                        |
| ------------------------------- | ------------------------------------------------------------------ |
| `GOVCLOUD`                      | Indicates AWS GovCloud deployment mode                             |
| `ALLOWUNSAFEEVAL`               | Enables viewers requiring `unsafe-eval` CSP (CesiumJS, Needle USD) |
| `LOCATIONSERVICES`              | Enables Amazon Location Service integration for map views          |
| `ALBDEPLOY`                     | Indicates Application Load Balancer web distribution               |
| `CLOUDFRONTDEPLOY`              | Indicates Amazon CloudFront web distribution                       |
| `NOOPENSEARCH`                  | Indicates Amazon OpenSearch is disabled                            |
| `AUTHPROVIDER_COGNITO`          | Indicates Amazon Cognito authentication                            |
| `AUTHPROVIDER_COGNITO_SAML`     | Indicates Amazon Cognito with SAML federation                      |
| `AUTHPROVIDER_EXTERNALOAUTHIDP` | Indicates external OAuth2 authentication                           |

### Additional Configuration

-   **API rate limiting** -- Configurable `globalRateLimit` and `globalBurstLimit` on Amazon API Gateway
-   **Presigned URL timeout** -- Configurable expiration for Amazon S3 presigned URLs (default: 86400 seconds)
-   **Token timeout** -- Configurable credential token timeout for Amazon Cognito (default: 3600 seconds)
-   **Metadata schema auto-loading** -- Control which default metadata schemas are loaded on deployment
-   **External asset buckets** -- Register existing Amazon S3 buckets with VAMS for asset management
-   **Custom Amazon S3 bucket policies** -- Additional bucket policy statements via `s3AdditionalBucketPolicyConfig.json`
-   **Addon framework** -- Garnet Framework integration for NGSI-LD digital twin data synchronization
