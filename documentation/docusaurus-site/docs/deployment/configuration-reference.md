# Configuration reference

This page documents every configuration option available in the VAMS deployment configuration file located at `infra/config/config.json`. Options are organized by functional area. For deployment instructions, see [Deploy the solution](deploy-the-solution.md).

:::info[Configuration resolution order]
Configuration values are resolved using a fallback chain: CDK context parameters (`-c key=value`) take highest priority, followed by values in `config.json`, then environment variables, and finally hardcoded defaults.
:::


## Top-level settings

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | `vams` | Base application name used in the full CDK stack name. |

## Environment (`env`)

| Field | Type | Default | Description |
|---|---|---|---|
| `env.account` | string | `null` | AWS account ID for CDK deployment. If null, pulled from `CDK_DEFAULT_ACCOUNT` environment variable. |
| `env.region` | string | `us-east-1` | AWS Region for CDK deployment. If null, pulled from `CDK_DEFAULT_REGION`, `REGION`, or defaults to `us-east-1`. |
| `env.loadContextIgnoreVPCStacks` | boolean | `false` | When `true`, skips synthesis and deployment of VPC-dependent nested stacks. Used during the first phase of an external VPC import. See [Deploy the solution](deploy-the-solution.md#step-7-import-an-external-vpc-conditional). |

:::note[Partition auto-detection]
The `env.partition` field is automatically derived from the Region and should not be set manually. VAMS supports `aws`, `aws-us-gov`, `aws-cn`, and `aws-iso` partitions.
:::


## Stack identification (`app`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.baseStackName` | string | `prod` | Stack environment name appended to resource names. Combined with the Region to form the full CloudFormation stack name (for example, `vams-core-prod-us-east-1`). Can be overridden with the `STACK_NAME` environment variable or CDK context `stack-name`. |
| `app.adminUserId` | string | `administrator` | Username for the initial super administrator account. Can be an email address. Can be overridden with the `ADMIN_USER_ID` environment variable. |
| `app.adminEmailAddress` | string | *(required)* | Email address for the initial admin account. A temporary password is sent to this address during first deployment. Can be overridden with the `ADMIN_EMAIL_ADDRESS` environment variable. |

## Asset buckets (`app.assetBuckets`)

Controls how Amazon S3 asset storage buckets are provisioned.

| Field | Type | Default | Description |
|---|---|---|---|
| `app.assetBuckets.createNewBucket` | boolean | `true` | When `true`, VAMS creates a new Amazon S3 bucket for asset storage. When `false`, you must define at least one external asset bucket. |
| `app.assetBuckets.defaultNewBucketSyncDatabaseId` | string | `default` | Database ID to synchronize with the newly created bucket. **Required** when `createNewBucket` is `true`. |
| `app.assetBuckets.externalAssetBuckets` | array | `null` | Array of external Amazon S3 bucket configurations to register with VAMS. Each bucket requires the fields described below. |

### External asset bucket object

Each element in `externalAssetBuckets` has the following fields:

| Field | Type | Description |
|---|---|---|
| `bucketArn` | string | Amazon Resource Name (ARN) of the existing Amazon S3 bucket. |
| `baseAssetsPrefix` | string | Base prefix to use for cataloging and syncing assets. Use `/` for the bucket root. Must end with `/`. |
| `defaultSyncDatabaseId` | string | Database ID to associate with asset changes synced from this bucket. If the database does not exist, VAMS creates it. |

:::tip[Adding external buckets]
External buckets can be added incrementally across deployments. Each bucket requires additional IAM bucket policies. See the [Developer Guide](../developer/setup.md) for external bucket IAM policy requirements.
:::


## Security and compliance

### WAF and FIPS (`app`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.useWaf` | boolean | `true` | Enables AWS WAF for Amazon CloudFront or ALB and Amazon API Gateway attachment points. Disabling this generates a deployment warning. |
| `app.useFips` | boolean | `false` | Enables FIPS-compliant AWS partition endpoints. Must be combined with the `AWS_USE_FIPS_ENDPOINT=true` environment variable. |
| `app.addStackCloudTrailLogs` | boolean | `true` | Creates a dedicated Amazon CloudWatch Logs group and associated AWS CloudTrail trail for this stack. |

### KMS encryption (`app.useKmsCmkEncryption`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.useKmsCmkEncryption.enabled` | boolean | `false` | Enables a customer-managed AWS KMS key for encryption at rest. The key is auto-generated and applied to Amazon S3, Amazon DynamoDB, Amazon SQS, Amazon SNS, and Amazon OpenSearch Service. |
| `app.useKmsCmkEncryption.optionalExternalCmkArn` | string | `null` | ARN of an existing customer-managed KMS key to import instead of generating a new one. The key must be in the same Region as the deployment. |

:::info[External CMK key policy]
When importing an external KMS key, the key policy must grant the following actions to the relevant service principals (Amazon S3, Amazon DynamoDB, AWS STS, Amazon SQS, Amazon SNS, Amazon ECS, Amazon EKS, Amazon CloudWatch Logs, AWS Lambda, Amazon CloudFront, Amazon OpenSearch Service):

```
kms:GenerateDataKey*
kms:Decrypt
kms:ReEncrypt*
kms:DescribeKey
kms:ListKeys
kms:CreateGrant
```
:::


### GovCloud (`app.govCloud`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.govCloud.enabled` | boolean | `false` | Enables AWS GovCloud deployment mode. Enforces: VPC must be enabled, Amazon CloudFront must be disabled, Amazon Location Service must be disabled. |
| `app.govCloud.il6Compliant` | boolean | `false` | Reserved for future use. Not yet fully implemented. |

## VPC (`app.useGlobalVpc`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.useGlobalVpc.enabled` | boolean | `false` | Creates or imports a VPC for VAMS resources. Automatically set to `true` when ALB, OpenSearch Provisioned, or any container-based pipeline is enabled. |
| `app.useGlobalVpc.useForAllLambdas` | boolean | `false` | Deploys all AWS Lambda functions inside the VPC and creates required VPC interface endpoints. Recommended only for FedRAMP or external component VPC-only access requirements. |
| `app.useGlobalVpc.addVpcEndpoints` | boolean | `true` | Creates all required VPC endpoints on the VPC (new or imported). Set to `false` if your imported VPC already has the necessary endpoints. |
| `app.useGlobalVpc.optionalExternalVpcId` | string | `null` | ID of an existing VPC to import (for example, `vpc-0123456789abcdef0`). When set, overrides internal VPC creation. Requires isolated subnet IDs to be provided. |
| `app.useGlobalVpc.optionalExternalIsolatedSubnetIds` | string | `null` | Comma-delimited list of isolated subnet IDs in the imported VPC. **Required** when using an external VPC. |
| `app.useGlobalVpc.optionalExternalPrivateSubnetIds` | string | `null` | Comma-delimited list of private subnet IDs. Required when using RapidPipeline or ModelOps with an imported VPC. |
| `app.useGlobalVpc.optionalExternalPublicSubnetIds` | string | `null` | Comma-delimited list of public subnet IDs. Required when using ALB with public subnets or RapidPipeline/ModelOps with an imported VPC. |
| `app.useGlobalVpc.vpcCidrRange` | string | `10.1.0.0/16` | CIDR range for the VAMS-created VPC. Ignored when importing an external VPC. |

:::warning[Subnet requirements]
Each subnet must reside in its own Availability Zone. Minimum Availability Zone requirements: 3 for OpenSearch Provisioned, 2 for ALB or EKS pipelines, 1 for all other configurations.
:::


## Amazon OpenSearch Service (`app.openSearch`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.openSearch.useServerless.enabled` | boolean | `false` | Deploys Amazon OpenSearch Serverless for pay-per-use search capability. |
| `app.openSearch.useProvisioned.enabled` | boolean | `false` | Deploys a provisioned Amazon OpenSearch Service domain. Requires VPC with 3+ Availability Zones. |
| `app.openSearch.useProvisioned.dataNodeInstanceType` | string | `r6g.large.search` | Instance type for the 2 data nodes in the provisioned domain. |
| `app.openSearch.useProvisioned.masterNodeInstanceType` | string | `r6g.large.search` | Instance type for the 3 dedicated master nodes. |
| `app.openSearch.useProvisioned.ebsInstanceNodeSizeGb` | number | `120` | Amazon EBS volume size in GB per data node. |
| `app.openSearch.reindexOnCdkDeploy` | boolean | `false` | Triggers automatic reindexing of all assets and files during deployment via a CloudFormation custom resource. **Important:** Enable only for a second deployment after initial deployment or version upgrade, then set back to `false`. Can be overridden with CDK context `reindexOnCdkDeploy=true`. |

:::note[Mutual exclusion]
You cannot enable both OpenSearch Serverless and OpenSearch Provisioned simultaneously. Enable at most one option, or disable both to deploy without search functionality.
:::


:::tip[OpenSearch Provisioned first deployment]
OpenSearch Provisioned creates service-linked roles that may not propagate immediately. If you encounter the error *"Before you can proceed, you must enable a service-linked role"*, wait 5 minutes and redeploy. See [Common deployment errors](deploy-the-solution.md#common-deployment-errors) for additional troubleshooting.
:::


## Amazon Location Service (`app.useLocationService`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.useLocationService.enabled` | boolean | `true` | Enables Amazon Location Service for map visualization of asset metadata with geographic coordinates. Not available in AWS GovCloud. Map views require OpenSearch to be enabled. |

## Web distribution

### Application Load Balancer (`app.useAlb`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.useAlb.enabled` | boolean | `false` | Enables ALB-based static website hosting. Required for AWS GovCloud deployments. Cannot be enabled simultaneously with Amazon CloudFront. |
| `app.useAlb.usePublicSubnet` | boolean | `false` | Places the ALB in public subnets. **Warning:** This exposes the web application to the public internet. |
| `app.useAlb.addAlbS3SpecialVpcEndpoint` | boolean | `true` | Creates the Amazon S3 VPC interface endpoint required by the ALB to serve static web files. Set to `false` only if this endpoint already exists in your VPC. |
| `app.useAlb.domainHost` | string | *(required when ALB enabled)* | Domain name for the ALB and static website Amazon S3 bucket (for example, `vams.example.com`). |
| `app.useAlb.certificateArn` | string | *(required when ALB enabled)* | ARN of the ACM certificate for HTTPS. Must be in the same Region as the deployment. |
| `app.useAlb.optionalHostedZoneId` | string | `null` | Amazon Route 53 hosted zone ID for automatic DNS alias creation. If not provided, configure DNS manually. |

### Amazon CloudFront (`app.useCloudFront`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.useCloudFront.enabled` | boolean | `true` | Enables Amazon CloudFront for static website distribution. Not available in AWS GovCloud. Cannot be enabled simultaneously with ALB. |
| `app.useCloudFront.customDomain.enabled` | boolean | `false` | Enables a custom domain name for the CloudFront distribution. When disabled, CloudFront uses an auto-generated `*.cloudfront.net` domain. |
| `app.useCloudFront.customDomain.domainHost` | string | `""` | Custom domain name (for example, `vams.example.com`). Must match the ACM certificate. Required when custom domain is enabled. |
| `app.useCloudFront.customDomain.certificateArn` | string | `""` | ACM certificate ARN. **Must be in `us-east-1`** regardless of the VAMS deployment Region. Required when custom domain is enabled. |
| `app.useCloudFront.customDomain.optionalHostedZoneId` | string | `""` | Amazon Route 53 hosted zone ID for automatic A-record alias creation. If not provided, configure DNS manually. |

:::danger[CloudFront certificate Region]
Amazon CloudFront requires the ACM certificate to be in `us-east-1`. Using a certificate in any other Region causes a deployment failure.
:::


## Authentication (`app.authProvider`)

### General authentication settings

| Field | Type | Default | Description |
|---|---|---|---|
| `app.authProvider.presignedUrlTimeoutSeconds` | number | `86400` | Timeout in seconds for Amazon S3 presigned URLs used for upload and download operations (default: 24 hours). |

### IP range restrictions (`app.authProvider.authorizerOptions`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.authProvider.authorizerOptions.allowedIpRanges` | array | `[]` | Array of IP range pairs for restricting API access. Each range is a 2-element array: `["min_ip", "max_ip"]`. Leave empty to allow all IPs. |

**Example:**

```json
"allowedIpRanges": [
    ["192.168.1.1", "192.168.1.255"],
    ["10.0.0.1", "10.0.0.255"]
]
```

### Amazon Cognito (`app.authProvider.useCognito`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.authProvider.useCognito.enabled` | boolean | `true` | Enables Amazon Cognito user pools for authentication. At least one authentication provider must be enabled. |
| `app.authProvider.useCognito.useSaml` | boolean | `false` | Enables SAML federation with an external IdP through Amazon Cognito. |
| `app.authProvider.useCognito.useUserPasswordAuthFlow` | boolean | `false` | Enables `USER_PASSWORD_AUTH` flow for non-SRP authentication. Generates a security warning. Use only when SRP libraries are unavailable for system integrations. |
| `app.authProvider.useCognito.credTokenTimeoutSeconds` | number | `3600` | Authentication token timeout in seconds for Amazon Cognito issued tokens (default: 1 hour). Refresh token is fixed at 24 hours. |

### External OAuth IdP (`app.authProvider.useExternalOAuthIdp`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.authProvider.useExternalOAuthIdp.enabled` | boolean | `false` | Enables an external OAuth 2.0 / OpenID Connect identity provider. Cannot be used simultaneously with Amazon Cognito. |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl` | string | `null` | Base URL of the external OAuth IdP (for example, `https://ping-federate.example.com`). |
| `app.authProvider.useExternalOAuthIdp.idpAuthClientId` | string | `null` | Client ID registered with the external IdP for this VAMS deployment. |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderScope` | string | `null` | OAuth scope requested by VAMS. |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderScopeMfa` | string | `null` | MFA scope attribute appended to the base scope. Set to enable MFA enforcement. |
| `app.authProvider.useExternalOAuthIdp.idpAuthPrincipalDomain` | string | `null` | Principal domain for the IdP endpoint (for example, `ping-federate.example.com`). |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderTokenEndpoint` | string | `null` | Token endpoint path (for example, `/as/token.oauth2`). |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderAuthorizationEndpoint` | string | `null` | Authorization endpoint path (for example, `/as/authorization.oauth2`). |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderDiscoveryEndpoint` | string | `null` | Discovery endpoint path (for example, `/.well-known/openid-configuration`). |
| `app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl` | string | `null` | JWT issuer URL for the custom Lambda authorizer to validate tokens. |
| `app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience` | string | `null` | JWT audience claim for token verification. |

:::warning[All fields required]
When external OAuth IdP is enabled, **all** fields in this section are required. Deployment will fail if any field is null or empty.
:::


## API throttling (`app.api`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.api.globalRateLimit` | number | `50` | Global rate limit in requests per second for the Amazon API Gateway. Must be a positive number. |
| `app.api.globalBurstLimit` | number | `100` | Global burst limit for the Amazon API Gateway. Must be greater than or equal to `globalRateLimit`. |

## Web UI (`app.webUi`)

| Field | Type | Default | Description |
|---|---|---|---|
| `app.webUi.optionalBannerHtmlMessage` | string | `""` | Optional HTML message displayed as a banner in the web interface. Use for system notifications or compliance messages (for example, `"AWS Sandbox System. Do not upload sensitive information."`). |
| `app.webUi.allowUnsafeEvalFeatures` | boolean | `false` | Allows `unsafe-eval` in the Content Security Policy for script execution. Required for certain viewer plugins (for example, Needle USD WASM viewer, ThreeJS CAD viewer). Consult your security team before enabling. |

## Metadata schema (`app.metadataSchema`)

Controls auto-loading of default metadata schemas during deployment.

| Field | Type | Default | Description |
|---|---|---|---|
| `app.metadataSchema.autoLoadDefaultAssetLinksSchema` | boolean | `true` | Creates a GLOBAL schema named `defaultAssetLinks` with Translation (XYZ), Rotation (WXYZ), Scale (XYZ), and Matrix (MATRIX4X4) fields for spatial relationship metadata. |
| `app.metadataSchema.autoLoadDefaultDatabaseSchema` | boolean | `true` | Creates a GLOBAL schema named `defaultDatabase` with a Location field (LLA - Latitude/Longitude/Altitude). |
| `app.metadataSchema.autoLoadDefaultAssetSchema` | boolean | `true` | Creates a GLOBAL schema named `defaultAsset` with a Location field (LLA - Latitude/Longitude/Altitude). |
| `app.metadataSchema.autoLoadDefaultAssetFileSchema` | boolean | `true` | Creates a GLOBAL schema named `defaultAssetFile3dModel` with a `Polygon_Count` field and file type restrictions for common 3D formats (.glb, .usd, .obj, .fbx, .gltf, .stl, .usdz). |

## Processing pipelines (`app.pipelines`)

### 3D basic conversion (`app.pipelines.useConversion3dBasic`)

Converts between STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, and XYZ formats. Does not require a VPC.

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useConversion3dBasic.enabled` | boolean | `true` | Enables the 3D basic conversion pipeline. |
| `app.pipelines.useConversion3dBasic.autoRegisterWithVAMS` | boolean | `true` | Automatically registers the pipeline and workflow in the VAMS database during deployment. |

### CAD/mesh metadata extraction (`app.pipelines.useConversionCadMeshMetadataExtraction`)

Extracts metadata from CAD and mesh file formats. Does not require a VPC.

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useConversionCadMeshMetadataExtraction.enabled` | boolean | `false` | Enables the CAD/mesh metadata extraction pipeline. |
| `app.pipelines.useConversionCadMeshMetadataExtraction.autoRegisterWithVAMS` | boolean | `true` | Automatically registers the pipeline during deployment. |
| `app.pipelines.useConversionCadMeshMetadataExtraction.autoRegisterAutoTriggerOnFileUpload` | boolean | `true` | Automatically triggers the pipeline on file uploads matching supported file types. |

### Point cloud Potree viewer (`app.pipelines.usePreviewPcPotreeViewer`)

Processes E57, LAS, and LAZ point cloud files for Potree web viewing. **Requires VPC.** Uses a GPL-licensed library.

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.usePreviewPcPotreeViewer.enabled` | boolean | `false` | Enables the point cloud Potree viewer pipeline. |
| `app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS` | boolean | `false` | Automatically registers the pipeline during deployment. |
| `app.pipelines.usePreviewPcPotreeViewer.autoRegisterAutoTriggerOnFileUpload` | boolean | `true` | Automatically triggers the pipeline on file uploads. |
| `app.pipelines.usePreviewPcPotreeViewer.sqsAutoRunOnAssetModified` | boolean | `false` | Automatically runs the pipeline via Amazon SQS when an asset is modified. |

### 3D preview thumbnail (`app.pipelines.usePreview3dThumbnail`)

Generates animated GIF and static PNG preview thumbnails from 3D mesh, point cloud, CAD, and USD files. **Requires VPC.** Uses LGPL-licensed libraries. Supports input files up to 100 GB.

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.usePreview3dThumbnail.enabled` | boolean | `false` | Enables the 3D preview thumbnail pipeline. |
| `app.pipelines.usePreview3dThumbnail.autoRegisterWithVAMS` | boolean | `false` | Automatically registers the pipeline during deployment. |
| `app.pipelines.usePreview3dThumbnail.autoRegisterAutoTriggerOnFileUpload` | boolean | `false` | Automatically triggers the pipeline on file uploads matching supported 3D file types. |

### GenAI metadata labeling (`app.pipelines.useGenAiMetadata3dLabeling`)

Uses Amazon Bedrock to generate descriptive metadata labels for GLB, FBX, and OBJ files. **Requires VPC.**

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useGenAiMetadata3dLabeling.enabled` | boolean | `false` | Enables the GenAI metadata labeling pipeline. |
| `app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId` | string | *(required when enabled)* | Amazon Bedrock model ID for inference (for example, `global.anthropic.claude-sonnet-4-5-20250929-v1:0`). |
| `app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS` | boolean | `true` | Automatically registers the pipeline during deployment. |
| `app.pipelines.useGenAiMetadata3dLabeling.autoRegisterAutoTriggerOnFileUpload` | boolean | `false` | Automatically triggers the pipeline on file uploads. |

### Gaussian splatting (`app.pipelines.useSplatToolbox`)

Generates Gaussian splat reconstructions from media files. **Requires VPC.**

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useSplatToolbox.enabled` | boolean | `false` | Enables the Gaussian splatting pipeline. |
| `app.pipelines.useSplatToolbox.autoRegisterWithVAMS` | boolean | `true` | Automatically registers the pipeline during deployment. |
| `app.pipelines.useSplatToolbox.sqsAutoRunOnAssetModified` | boolean | `false` | Automatically runs the pipeline via Amazon SQS when an asset is modified. |

### RapidPipeline on Amazon ECS (`app.pipelines.useRapidPipeline.useEcs`)

Third-party spatial data optimization. **Requires VPC and an [AWS Marketplace subscription](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi).**

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useRapidPipeline.useEcs.enabled` | boolean | `false` | Enables RapidPipeline on Amazon ECS. |
| `app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI` | string | *(required when enabled)* | Amazon ECR container image URI for the RapidPipeline container. |
| `app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS` | boolean | `true` | Automatically registers the pipeline during deployment. |

### RapidPipeline on Amazon EKS (`app.pipelines.useRapidPipeline.useEks`)

Third-party spatial data optimization on Amazon EKS. **Requires VPC with 2+ Availability Zones and an [AWS Marketplace subscription](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi).**

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useRapidPipeline.useEks.enabled` | boolean | `false` | Enables RapidPipeline on Amazon EKS. |
| `app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI` | string | *(required when enabled)* | Amazon ECR container image URI for the RapidPipeline container. |
| `app.pipelines.useRapidPipeline.useEks.autoRegisterWithVAMS` | boolean | `true` | Automatically registers the pipeline during deployment. |
| `app.pipelines.useRapidPipeline.useEks.eksClusterVersion` | string | `1.31` | Kubernetes version for the Amazon EKS cluster. |
| `app.pipelines.useRapidPipeline.useEks.nodeInstanceType` | string | `m5.2xlarge` | Amazon EC2 instance type for EKS worker nodes. |
| `app.pipelines.useRapidPipeline.useEks.minNodes` | number | `1` | Minimum worker nodes in the auto-scaling group. |
| `app.pipelines.useRapidPipeline.useEks.maxNodes` | number | `10` | Maximum worker nodes in the auto-scaling group. |
| `app.pipelines.useRapidPipeline.useEks.desiredNodes` | number | `2` | Desired worker node count under normal operation. |
| `app.pipelines.useRapidPipeline.useEks.jobTimeout` | number | `7200` | Maximum job runtime in seconds (default: 2 hours). |
| `app.pipelines.useRapidPipeline.useEks.jobMemory` | string | `16Gi` | Memory allocation per Kubernetes job pod. |
| `app.pipelines.useRapidPipeline.useEks.jobCpu` | string | `2000m` | CPU allocation per Kubernetes job pod in millicores. |
| `app.pipelines.useRapidPipeline.useEks.jobBackoffLimit` | number | `2` | Number of retries before marking a job as failed. |
| `app.pipelines.useRapidPipeline.useEks.jobTTLSecondsAfterFinished` | number | `600` | Seconds to retain completed job pods before cleanup. |
| `app.pipelines.useRapidPipeline.useEks.observability.enableControlPlaneLogs` | boolean | `false` | Enables EKS control plane logging to Amazon CloudWatch. Incurs additional costs. |
| `app.pipelines.useRapidPipeline.useEks.observability.enableContainerInsights` | boolean | `false` | Enables Amazon CloudWatch Container Insights for the cluster. Incurs additional costs. |

### ModelOps (`app.pipelines.useModelOps`)

Third-party 3D model optimization by VNTANA. **Requires VPC and an [AWS Marketplace subscription](https://aws.amazon.com/marketplace/pp/prodview-ooio3bidshgy4).**

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useModelOps.enabled` | boolean | `false` | Enables the ModelOps pipeline. |
| `app.pipelines.useModelOps.ecrContainerImageURI` | string | *(required when enabled)* | Amazon ECR container image URI for the ModelOps container. |
| `app.pipelines.useModelOps.autoRegisterWithVAMS` | boolean | `true` | Automatically registers the pipeline during deployment. |

### Isaac Lab training (`app.pipelines.useIsaacLabTraining`)

NVIDIA Isaac Lab reinforcement learning training pipeline on GPU instances. **Requires VPC.**

| Field | Type | Default | Description |
|---|---|---|---|
| `app.pipelines.useIsaacLabTraining.enabled` | boolean | `false` | Enables the Isaac Lab training pipeline. |
| `app.pipelines.useIsaacLabTraining.acceptNvidiaEula` | boolean | `false` | **Required when enabled.** Confirms acceptance of the [NVIDIA Software License Agreement](https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license). Deployment fails if not set to `true` when the pipeline is enabled. |
| `app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS` | boolean | `true` | Automatically registers training and evaluation workflows during deployment. |
| `app.pipelines.useIsaacLabTraining.keepWarmInstance` | boolean | `false` | Keeps a warm AWS Batch compute instance running to reduce cold start times. **Warning:** Incurs continuous compute costs even when no jobs are running. |

## Addons (`app.addons`)

### Garnet Framework (`app.addons.useGarnetFramework`)

Integration with the Garnet Framework external knowledge graph for NGSI-LD data synchronization.

| Field | Type | Default | Description |
|---|---|---|---|
| `app.addons.useGarnetFramework.enabled` | boolean | `false` | Enables Garnet Framework integration for automatic NGSI-LD indexing of all VAMS data changes. |
| `app.addons.useGarnetFramework.garnetApiEndpoint` | string | *(required when enabled)* | Garnet Framework API endpoint URL (for example, `https://XXX.execute-api.us-east-1.amazonaws.com`). Must be a valid URL. |
| `app.addons.useGarnetFramework.garnetApiToken` | string | *(required when enabled)* | API authentication token for the Garnet Framework. |
| `app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl` | string | *(required when enabled)* | Amazon SQS queue URL for Garnet Framework data ingestion. Format: `https://sqs.REGION.amazonaws.com/ACCOUNT/QUEUE_NAME`. |

## Example configurations

### Minimal commercial deployment

```json
{
    "name": "vams",
    "env": {
        "account": null,
        "region": "us-east-1",
        "loadContextIgnoreVPCStacks": false
    },
    "app": {
        "baseStackName": "prod",
        "assetBuckets": {
            "createNewBucket": true,
            "defaultNewBucketSyncDatabaseId": "default",
            "externalAssetBuckets": null
        },
        "adminUserId": "administrator",
        "adminEmailAddress": "admin@example.com",
        "useFips": false,
        "useWaf": true,
        "addStackCloudTrailLogs": true,
        "useKmsCmkEncryption": {
            "enabled": false,
            "optionalExternalCmkArn": null
        },
        "govCloud": {
            "enabled": false,
            "il6Compliant": false
        },
        "useGlobalVpc": {
            "enabled": false,
            "useForAllLambdas": false,
            "addVpcEndpoints": true,
            "optionalExternalVpcId": null,
            "optionalExternalIsolatedSubnetIds": null,
            "optionalExternalPrivateSubnetIds": null,
            "optionalExternalPublicSubnetIds": null,
            "vpcCidrRange": "10.1.0.0/16"
        },
        "openSearch": {
            "useServerless": { "enabled": true },
            "useProvisioned": {
                "enabled": false,
                "dataNodeInstanceType": "r6g.large.search",
                "masterNodeInstanceType": "r6g.large.search",
                "ebsInstanceNodeSizeGb": 120
            },
            "reindexOnCdkDeploy": false
        },
        "useLocationService": { "enabled": true },
        "useAlb": {
            "enabled": false,
            "usePublicSubnet": false,
            "addAlbS3SpecialVpcEndpoint": true,
            "domainHost": "",
            "certificateArn": "",
            "optionalHostedZoneId": null
        },
        "useCloudFront": {
            "enabled": true,
            "customDomain": {
                "enabled": false,
                "domainHost": "",
                "certificateArn": "",
                "optionalHostedZoneId": ""
            }
        },
        "pipelines": {
            "useConversion3dBasic": {
                "enabled": true,
                "autoRegisterWithVAMS": true
            },
            "useConversionCadMeshMetadataExtraction": {
                "enabled": false,
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": true
            },
            "usePreviewPcPotreeViewer": {
                "enabled": false,
                "autoRegisterWithVAMS": false,
                "autoRegisterAutoTriggerOnFileUpload": true,
                "sqsAutoRunOnAssetModified": false
            },
            "usePreview3dThumbnail": {
                "enabled": false,
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": true
            },
            "useGenAiMetadata3dLabeling": {
                "enabled": false,
                "bedrockModelId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": false
            },
            "useSplatToolbox": {
                "enabled": false,
                "autoRegisterWithVAMS": true
            },
            "useRapidPipeline": {
                "useEcs": {
                    "enabled": false,
                    "ecrContainerImageURI": "",
                    "autoRegisterWithVAMS": true
                },
                "useEks": {
                    "enabled": false,
                    "ecrContainerImageURI": "",
                    "autoRegisterWithVAMS": true,
                    "eksClusterVersion": "1.31",
                    "nodeInstanceType": "m5.2xlarge",
                    "minNodes": 1,
                    "maxNodes": 10,
                    "desiredNodes": 2,
                    "jobTimeout": 7200,
                    "jobMemory": "16Gi",
                    "jobCpu": "2000m",
                    "jobBackoffLimit": 2,
                    "jobTTLSecondsAfterFinished": 600,
                    "observability": {
                        "enableControlPlaneLogs": false,
                        "enableContainerInsights": false
                    }
                }
            },
            "useModelOps": {
                "enabled": false,
                "ecrContainerImageURI": "",
                "autoRegisterWithVAMS": true
            },
            "useIsaacLabTraining": {
                "enabled": false,
                "acceptNvidiaEula": false,
                "autoRegisterWithVAMS": true,
                "keepWarmInstance": false
            }
        },
        "addons": {
            "useGarnetFramework": {
                "enabled": false,
                "garnetApiEndpoint": "",
                "garnetApiToken": "",
                "garnetIngestionQueueSqsUrl": ""
            }
        },
        "authProvider": {
            "presignedUrlTimeoutSeconds": 86400,
            "authorizerOptions": { "allowedIpRanges": [] },
            "useCognito": {
                "enabled": true,
                "useSaml": false,
                "useUserPasswordAuthFlow": false,
                "credTokenTimeoutSeconds": 3600
            },
            "useExternalOAuthIdp": {
                "enabled": false,
                "idpAuthProviderUrl": null,
                "idpAuthClientId": null,
                "idpAuthProviderScope": null,
                "idpAuthProviderScopeMfa": null,
                "idpAuthPrincipalDomain": null,
                "idpAuthProviderTokenEndpoint": null,
                "idpAuthProviderAuthorizationEndpoint": null,
                "idpAuthProviderDiscoveryEndpoint": null,
                "lambdaAuthorizorJWTIssuerUrl": null,
                "lambdaAuthorizorJWTAudience": null
            }
        },
        "webUi": {
            "optionalBannerHtmlMessage": "",
            "allowUnsafeEvalFeatures": false
        },
        "api": {
            "globalRateLimit": 50,
            "globalBurstLimit": 100
        },
        "metadataSchema": {
            "autoLoadDefaultAssetLinksSchema": true,
            "autoLoadDefaultDatabaseSchema": true,
            "autoLoadDefaultAssetSchema": true,
            "autoLoadDefaultAssetFileSchema": true
        }
    }
}
```

### AWS GovCloud deployment

Key differences from the commercial template:

```json
{
    "app": {
        "useFips": true,
        "useWaf": true,
        "useKmsCmkEncryption": { "enabled": true },
        "govCloud": { "enabled": true, "il6Compliant": false },
        "useGlobalVpc": {
            "enabled": true,
            "useForAllLambdas": true,
            "addVpcEndpoints": true,
            "vpcCidrRange": "10.1.0.0/16"
        },
        "useLocationService": { "enabled": false },
        "useAlb": {
            "enabled": true,
            "usePublicSubnet": false,
            "domainHost": "vams.example.gov",
            "certificateArn": "arn:aws-us-gov:acm:REGION:ACCOUNT:certificate/ID"
        },
        "useCloudFront": { "enabled": false },
        "authProvider": {
            "useCognito": { "enabled": true }
        }
    }
}
```

### Commercial with all pipelines enabled

Key pipeline section for enabling all available pipelines:

```json
{
    "app": {
        "useGlobalVpc": { "enabled": true, "vpcCidrRange": "10.1.0.0/16" },
        "webUi": { "allowUnsafeEvalFeatures": true },
        "pipelines": {
            "useConversion3dBasic": { "enabled": true, "autoRegisterWithVAMS": true },
            "useConversionCadMeshMetadataExtraction": {
                "enabled": true,
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": true
            },
            "usePreviewPcPotreeViewer": {
                "enabled": true,
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": true,
                "sqsAutoRunOnAssetModified": false
            },
            "usePreview3dThumbnail": {
                "enabled": true,
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": true
            },
            "useGenAiMetadata3dLabeling": {
                "enabled": true,
                "bedrockModelId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": true
            },
            "useSplatToolbox": { "enabled": true, "autoRegisterWithVAMS": true }
        }
    }
}
```

:::tip[VPC auto-enablement]
When any container-based pipeline is enabled, the VPC is automatically enabled even if `useGlobalVpc.enabled` is set to `false` in your configuration.
:::


## Additional configuration files

Beyond `config.json`, VAMS supports several supplementary configuration files:

| File | Purpose |
|---|---|
| `infra/config/policy/s3AdditionalBucketPolicyConfig.json` | Additional IAM policy statements applied to all Amazon S3 buckets. Controls presigned URL and STS credential access restrictions. |
| `infra/config/csp/cspAdditionalConfig.json` | Additional Content Security Policy (CSP) sources for external APIs, scripts, images, media, fonts, and styles. |
| `infra/config/saml-config.ts` | SAML identity provider settings for Amazon Cognito federation. Required when `authProvider.useCognito.useSaml` is `true`. See [Security Architecture](../architecture/security.md#saml-federation). |
| `infra/config/docker/Dockerfile-customDependencyBuildConfig` | Custom Docker build configuration for Lambda layer packaging. Useful for adding custom SSL certificates for HTTPS proxy environments. |
| `infra/cdk.json` (`environments.common`) | Key-value pairs applied as tags on all stack resources. |
| `infra/cdk.json` (`environments.aws`) | `PermissionBoundaryArn` and `IamRoleNamePrefix` for IAM role customization. |

### CDK environment settings (`infra/cdk.json`)

The `infra/cdk.json` file supports two environment configuration sections that apply additional controls to the deployed stack.

#### Resource tagging (`environments.common`)

Any non-empty key-value pair added to the `environments.common` object is applied as a tag on all resources deployed in the VAMS core stack. This is useful for cost allocation, organizational tagging, and compliance tracking.

```json
{
    "context": {
        "environments": {
            "common": {
                "SolutionName": "AWSVisualAssetManagementSystem",
                "Owner": "your-team",
                "CostCenter": "12345",
                "BusinessUnit": "Engineering"
            }
        }
    }
}
```

#### IAM role customization (`environments.aws`)

The following settings control IAM role naming and permission boundaries for all roles created by the VAMS core stack:

| Field | Type | Default | Description |
|---|---|---|---|
| `PermissionBoundaryArn` | string | `""` | ARN of an IAM permission boundary to apply to all roles created by the VAMS core stack. Leave empty to skip permission boundaries. |
| `IamRoleNamePrefix` | string | `""` | Prefix string applied to all newly created IAM role names. |

:::warning[Role name length limit]
The total IAM role name character count limit is 64 characters. Long prefixes may affect role name uniqueness and cause deployment failures. Prefixes of 8 characters or fewer are recommended.
:::


```json
{
    "context": {
        "environments": {
            "aws": {
                "PermissionBoundaryArn": "arn:aws:iam::123456789012:policy/MyBoundary",
                "IamRoleNamePrefix": "VAMS"
            }
        }
    }
}
```

### Amazon S3 additional bucket policy (`infra/config/policy/s3AdditionalBucketPolicyConfig.json`)

This file allows you to add an additional JSON-formatted IAM policy statement that is applied to all Amazon S3 buckets created by VAMS. The `Resource` field in the policy statement is automatically overridden at deployment time to reference each respective bucket and its objects. An empty file means no additional policy statement is added beyond the default TLS enforcement.

This configuration also controls the ability to allow or deny access to presigned Amazon S3 URLs and AWS STS credentials that VAMS generates for asset upload and download operations.

:::tip[ViaAWSService condition]
When restricting access, add an `aws:ViaAWSService` condition set to `false` to restrict only direct user calls, since AWS services also need to access these buckets internally.
:::


The following examples demonstrate common bucket policy patterns. See the [AWS Knowledge Center article on restricting S3 traffic](https://repost.aws/knowledge-center/block-s3-traffic-vpc-ip) for additional guidance.

**Restrict access outside of a VPC interface endpoint:**

```json
{
    "Sid": "VPCe",
    "Action": "s3:*",
    "Effect": "Deny",
    "Resource": ["*"],
    "Condition": {
        "StringNotEquals": {
            "aws:SourceVpce": ["vpce-XXXXXXXX", "vpce-YYYYYYYY"]
        },
        "BoolIfExists": { "aws:ViaAWSService": "false" }
    },
    "Principal": "*"
}
```

**Restrict access outside of a VPC private IP range:**

```json
{
    "Sid": "VpcSourceIp",
    "Action": "s3:*",
    "Effect": "Deny",
    "Resource": ["*"],
    "Condition": {
        "NotIpAddressIfExists": {
            "aws:VpcSourceIp": ["10.1.1.1/32", "172.1.1.1/32"]
        },
        "BoolIfExists": { "aws:ViaAWSService": "false" }
    },
    "Principal": "*"
}
```

**Restrict access outside of a source IP range:**

```json
{
    "Sid": "SourceIP",
    "Action": "s3:*",
    "Effect": "Deny",
    "Resource": ["*"],
    "Condition": {
        "NotIpAddressIfExists": {
            "aws:SourceIp": ["11.11.11.11/32", "22.22.22.22/32"]
        },
        "BoolIfExists": { "aws:ViaAWSService": "false" }
    },
    "Principal": "*"
}
```

### Content Security Policy (`infra/config/csp/cspAdditionalConfig.json`)

VAMS supports configurable Content Security Policy (CSP) settings through this JSON file. This allows organizations to add their specific external API endpoints and resources without modifying core code.

The file supports the following categories:

| Category | Description |
|---|---|
| `connectSrc` | External APIs and services the application can connect to via `XMLHttpRequest`, `WebSocket`, `EventSource` |
| `scriptSrc` | External JavaScript libraries or CDNs that can be executed |
| `workerSrc` | Web Worker and Service Worker sources |
| `imgSrc` | External image sources that can be loaded |
| `mediaSrc` | External media sources (audio/video) that can be loaded |
| `fontSrc` | External font sources (for example, Google Fonts) |
| `styleSrc` | External stylesheet sources that can be loaded |

**Behavior:**
- **File not found** -- VAMS uses default CSP settings without failing the build.
- **Invalid JSON** -- Logs a warning and uses default CSP settings.
- **Empty arrays** -- Ignored; only default CSP sources are used for those categories.
- **Duplicate prevention** -- Additional sources are merged with existing ones, avoiding duplicates.

:::warning[CSP security]
Only add trusted domains to your CSP configuration. Avoid using wildcards (`*`) as they compromise security. Regularly audit your CSP configuration and test changes in a development environment before deploying to production.
:::
