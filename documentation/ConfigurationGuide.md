# Visual Asset Management System(VAMS) - Configuration

VAMS backend allows for basic to advanced configuration based on the environment and use-case need.

Configuration files can be found in `/infra/config` with `config.json` being the primary file used during deployment. Additional config template files exist for common environment setups for Commercial or GovCloud deployments.

Recommended minimum fields to update are `region`, `adminEmailAddress`, and `baseStackName` when using the default provided templates.

Some configuration options can be overriden at time of deployment with either environment variables or cdk context parameters (--context X) used with `cdk deploy`

## Configuration Options

-   `name` | default: vams | #Base application name to use in the full CDK stack name

-   `env.account` | default: NULL | #AWS Account to use for CDK deployment. If null, pulled from CDK environment.
-   `env.region` | default: us-east-1 | #AWS Region to use for CDK deployment. If null, pulled from CDK environment.
-   `env.loadContextIgnoreVPCStacks` | default: false | #Mode to ignore synth and deployments of any nested stack that needs a VPC in order to first load context through a first synth run.

-   `app.baseStackName` | default: prod | #Base stack stage environment name to use when creating full CDK stack name.
-   `app.adminUserId` | default: < administrator > | #Administrator username to use for the initial super admin account. This can also be in the form of an email address.
-   `app.adminEmailAddress` | default: < adminEmail@example.com > | #Administrator email address to use for the initial super admin account. A temporary password will be sent here for an initial solution standup.

-   `app.assetBuckets.createNewBucket` | default: true | #Controls whether to create a new S3 bucket for assets. If set to false, you must define external asset buckets.
-   `app.assetBuckets.defaultNewBucketSyncDatabaseId` | default: default | #Specifies the database ID to sync with the new bucket. Required when createNewBucket is true.
-   `app.assetBuckets.externalAssetBuckets[]` | default: NULL | #Configuration for defining use of preeixsting external asset buckets (array). Buckets can be added over time through deployments. External buckets need additional IAM bucket policies added, see DeveloperGuide.md for more information.
-   `app.assetBuckets.externalAssetBuckets[].bucketArn` | default: NULL | #The ARN of the existing bucket to add to VAMS
-   `app.assetBuckets.externalAssetBuckets[].baseAssetsPrefix` | default: NULL | #The base prefix to start using for catalogging and syncing assets. If at the base of the S3 bucket, use `/`. Prefix must end in a `/`.
-   `app.assetBuckets.externalAssetBuckets[].defaultSyncDatabaseId` | default: NULL | #The database ID to sync asset changes to if adding new asset folders direct to S3. If database ID does
    not exist when syncing, the system will attempt to create a new database with this ID and bucket/prefix.

-   `app.useWaf` | default: true | #Feature to turn use of Amazon Web Application Firewall on/off for VAMS deployment. This is used for Cloudfront or ALB + API Gateway attachment points. Warning: We reccomend you keep this on unless your organization has other firewalls in-use.
-   `app.useFips` | default: false | #Feature to use FIPS compliant AWS partition endpoints. Must combine with AWS CLI FIPS Environment variable `AWS_USE_FIPS_ENDPOINT`.
-   `app.addStackCloudTrailLogs` | default: true | #Feature to turn the creating of a new CloudWatch logs group and associated CloudTrail trail for this stack deployment.
-   `app.useKmsCmkEncryption.enabled` | default: false | #Feature to use a custom customer managed encryption key (KMS). Key and associated VAMS permissions will be auto-generated for the deployment without providing an external key. KMS key (generated or imported) will be used for S3*, DynamoDB, SQS/SNS, and OpenSearch data at rest. If false, use default or AWS-managed (as-available) encryption settings for all data-at-rest services.*The WebAppBucket S3 bucket will not use the KMS CMK key as the the ALB has no way to provide SigV4 signature to S3 without authentication and CloudFront OAC KMS encryption hasn't been implemented yet with native CDK.
-   `app.useKmsCmkEncryption.optionalExternalCmkArn` | default: NULL | #Ability to import an optional external custom customer managed encryption key (KMS) if KMS encryption is true. ARN must be provided of key imported to KMS in the same region as the VAMS deployment. See additional configuration notes on the permission policy to have on the key.
-   `app.govCloud.enabled` | default: false | #Feature to deploy to the AWS GovCloud partitions. Will turn certain VAMS features on/off based on service support and/or throw errors for bad configurations (see below on additional configuration notes).
-   `app.govCloud.il6Compliant` | default: false | #Feature to check for AWS GovCloud IL6 partition compliance. Will turn certain VAMS features on/off based on service support and/or throw errors for bad configurations (see below on additional configuration notes).
-   `app.useGlobalVpc.enabled` | default: false | #Will create a global VPC to use for various configuration feature options. Using an ALB, OpenSearch Provisioned, or the Point Cloud Visualization Pipeline will force this setting to true. All options under this section only apply if this setting is set/force to 'true'.
-   `app.useGlobalVpc.useForAllLambdas` | default: false | #Feature will deploy all lambdas created behind the VPC and create needed interface endpoints to support communications. Reccomended only for select deployments based on security (FedRamp) or external component VPC-only access (e.g. RDS).
-   `app.useGlobalVpc.addVpcEndpoints` | default: true | #Will generate all needed VPC endpoints on either newly created VPC or imported VPC (if VPC enabled). Note: ALB S3 VPCe will be created if using an ALB regardless of this setting due to unique setup nature of that VPCe and ALB listeners tie.
-   `app.useGlobalVpc.optionalExternalVpcId` | default: NULL | #Specify an existing VPC ID to import from the given region instead of creating a new VPC. If specified, will override any internal generation and will require `app.useGlobalVpc.optionalExternalPrivateSubnetIds` and/or `app.useGlobalVpc.optionalExternalPublicSubnetIds` to be provided.
-   `app.useGlobalVpc.optionalExternalPrivateSubnetIds` | default: NULL | #Comma deliminated list of private subnet IDs in the provided optional VPC to use. Must provide at least 1 private subnet, 2 if using an ALB (non-public subnet), and 3 if using opensearch provisioned.
-   `app.useGlobalVpc.optionalExternalPublicSubnetIds` | default: NULL | #Comma deliminated list of public subnet IDs in the provided optional VPC to use. Will only be looked at if `app.useAlb.usePublicSubnet` is true. Required to have minimum of 2 public subnets for ALB.
-   `app.useGlobalVpc.vpcCidrRange` | default: 10.1.0.0/16 | #Specifies the CIDR range to use for the new VPC created. Ignored if importing an external VPC.

-   `app.openSearch.useServerless.enabled` | default: true | #Feature to deploy opensearch serverless (default).
-   `app.openSearch.useProvisioned.enabled` | default: false | #Feature to deploy opensearch provisioned. When deploying with opeensearch provisioned, this will enable the use of the global VPC option. A minimum of 3 AZs will be used to deploy opensearch provisioned.
-   `app.openSearch.useProvisioned.dataNodeInstanceType` | default: r6g.large.search | #When using OpenSearch Provisioned, the Instance type to use for the data nodes (2x nodes deployed)
-   `app.openSearch.useProvisioned.masterNodeInstanceType` | default: r6g.large.search | #When using OpenSearch Provisioned, the Instance type to use for the master nodes (3x nodes deploy)
-   `app.openSearch.useProvisioned.ebsInstanceNodeSizeGb` | default: 120 | #When using OpenSearch Provisioned, the EBS volume size to deploy per data instance node in GB
-   `app.openSearch.reindexOnCdkDeploy` | default: false | #Feature to trigger automatic reindexing of all assets and files during CDK deployment via a CloudFormation custom resource. **IMPORTANT**: This should only be enabled for a second deployment after an initial deployment or version upgrade has completed. Set to true to reindex during deployment, then set back to false afterwards to prevent reindexing on every deployment. Can be set/overridden with CDK context parameter `reindexOnCdkDeploy=true`. Requires either OpenSearch Serverless or Provisioned to be enabled.

-   `app.useLocationService.enabled` | default: true | #Feature to use location services to display maps data for asset metadata types that store global position coordinates. Note that currently map view won't show up if OpenSearch is not enabled.

-   `app.useAlb.enabled` | default: false | #Feature to swap in a Application Load Balancer instead of a CloudFront Deployment for the static website deployment (neither can also be chosen). This will 1) disable static webpage caching, 2) require a fixed web domain to be specified, 3) require a SSL/TLS certicate to be registered in AWS Certifcate Manager, 4) have a S3 bucket name available in the partition that matches the domain name for the static website contents, 5) prevent some cross-site-scripting security preventions / secure HTTPOnly cookies from taking full affect and 6) cause a modified A/B stack deployment scenario on VAMS upgrades due to the common S3 bucket name used by the ALB.
-   `app.useAlb.usePublicSubnet` | default: false | #Specifies if the ALB should use a public subnet. If creating a new VPC, will create seperate public subnets for ALB. If importing an existing VPC, will require `app.useGlobalVpc.optionalExternalPublicSubnetIds` to be filled out.
-   `app.useAlb.addAlbS3SpecialVpcEndpoint` | default: true | #Creates the special S3 VPC endpoint needed by the ALB to serve S3 static web files. If turned false, this will need to be manualyl created afterwards. See the DeveloperGuide for more information.
-   `app.useAlb.domainHost` | default: vams1.example.com | #Specifies the domain to use for the ALB and static webpage S3 bucket. Required to be filled out to use ALB.
-   `app.useAlb.certificateARN` | default: arn:aws-us-gov:acm:<REGION>:<ACCOUNTID>:certificate/<CERTIFICATEID> | #Specifies the existing ACM certificate to use for the ALB for HTTPS connections. ACM certificate must be for the `domainHost` specified and reside in the same region being deployed to. Required to be filled out to use ALB.
-   `app.useAlb.optionalHostedZoneID` | default: NULL | #Optional route53 zone host ID to automatically create an alias for the `domainHost` specified to the created ALB.

-   `app.useCloudFront.enabled` | default: true | #Feature to enable deploying the VAMS static website to Cloudfront (not GovCloud supported). Either this option or app.useAlb.enabled should be used. Both cannot be turned on but neither can be selected to have a API-only deployment.
-   `app.useCloudFront.customDomain.enabled` | default: false | #Feature to enable custom domain name for CloudFront distribution. When disabled, CloudFront will use the auto-generated domain name. When enabled, requires certificateArn and domainHost to be specified.
-   `app.useCloudFront.customDomain.domainHost` | default: "" | #Specifies the custom domain name to use for the CloudFront distribution (e.g., vams.example.com). Required when customDomain.enabled is true. The domain must match the certificate provided.
-   `app.useCloudFront.customDomain.certificateArn` | default: "" | #Specifies the ACM certificate ARN to use for HTTPS connections on the custom domain. **IMPORTANT**: Certificate must be in the us-east-1 region regardless of where VAMS is deployed, as this is a CloudFront requirement. Required when customDomain.enabled is true.
-   `app.useCloudFront.customDomain.optionalHostedZoneId` | default: "" | #Optional Route53 hosted zone ID to automatically create a DNS alias record for the custom domain pointing to the CloudFront distribution. If not provided, you must manually configure DNS.

-   `app.pipelines.usePreviewPcPotreeViewer.enabled` | default: false | #Feature to create a point cloud potree viewer processing pipeline to support point cloud file type viewing within the VAMS web UI. This will enable the global VPC option and all pipeline components will be put behind the VPC. NOTICE: This feature uses a third-party open-source library with a GPL license, refer to your legal team before enabling.
-   `app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS` | default: false | #Feature to automatically register the point cloud potree viewer pipeline and associated workflow in the global VAMS database during CDK deployment. When enabled, the pipeline will be available immediately after deployment without manual registration through the UI.
-   `app.pipelines.usePreviewPcPotreeViewer.autoRegisterAutoTriggerOnFileUpload` | default: true | #Feature to automatically trigger the pipeline when files are uploaded to VAMS. When enabled along with autoRegisterWithVAMS, the pipeline will automatically execute on file uploads matching the pipeline's supported file types.
-   `app.pipelines.useGenAiMetadata3dLabeling.enabled` | default: false | #Feature to create a generative AI metadata labeling pipeline for glb, fbx, and obj files. This will enable the global VPC option and all pipeline components will be put behind the VPC.
-   `app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId` #Which bedrock GenAI model ID to use for inferencing such as `global.anthropic.claude-sonnet-4-5-20250929-v1:0`
-   `app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS` | default: true | #Feature to automatically register the generative AI metadata labeling pipeline and associated workflow in the global VAMS database during CDK deployment. When enabled, the pipeline will be available immediately after deployment without manual registration through the UI.
-   `app.pipelines.useGenAiMetadata3dLabeling.autoRegisterAutoTriggerOnFileUpload` | default: false | #Feature to automatically trigger the pipeline when files are uploaded to VAMS. When enabled along with autoRegisterWithVAMS, the pipeline will automatically execute on file uploads matching the pipeline's supported file types.
-   `app.pipelines.useConversionCadMeshMetadataExtraction.enabled` | default: false | #Feature to create a CAD mesh metadata extraction pipeline for CAD file formats. This pipeline does not need a VPC to operate.
-   `app.pipelines.useConversionCadMeshMetadataExtraction.autoRegisterWithVAMS` | default: true | #Feature to automatically register the CAD mesh metadata extraction pipeline and associated workflow in the global VAMS database during CDK deployment. When enabled, the pipeline will be available immediately after deployment without manual registration through the UI.
-   `app.pipelines.useConversionCadMeshMetadataExtraction.autoRegisterAutoTriggerOnFileUpload` | default: true | #Feature to automatically trigger the pipeline when files are uploaded to VAMS. When enabled along with autoRegisterWithVAMS, the pipeline will automatically execute on file uploads matching the pipeline's supported file types.
-   `app.pipelines.useConversion3dBasic.enabled` | default: true | #Feature to create a file converter pipeline between STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, and XYZ file format types. This pipeline does not need a VPC to operate.
-   `app.pipelines.useConversion3dBasic.autoRegisterWithVAMS` | default: true | #Feature to automatically register the 3D basic conversion pipeline and associated workflow in the global VAMS database during CDK deployment. When enabled, the pipeline will be available immediately after deployment without manual registration through the UI.
-   `app.pipelines.useRapidPipeline.useEcs.enabled` | default: false | #Feature to use DGG's RapidPipeline solution within VAMS deployed on ECS (Elastic Container Service). This solution requires an active subscription to RapidPipeline 3D Processor on AWS Marketplace. [Click here](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi?sr=0-1&ref_=beagle&applicationId=AWSMPContessa) to find the AWS Marketplace listing, and then select **Continue to Subscribe**. This will enable the global VPC option and all pipeline components will be put behind the VPC.
-   `app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI` | default: <ACCOUNTID>.dkr.ecr.<REGION>.amazonaws.com/<ECR-REPOSITORY>/<IMAGE-ID>:<IMAGE-TAG> | #The ECR container image URI for the RapidPipeline ECS deployment. Must be updated with your AWS account ID, region, and the RapidPipeline container image details from AWS Marketplace.
-   `app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS` | default: true | #Feature to automatically register the RapidPipeline ECS solution and associated workflows in the global VAMS database during CDK deployment. When enabled, the pipeline will be available immediately after deployment without manual registration through the UI.
-   `app.pipelines.useRapidPipeline.useEks.enabled` | default: false | #Feature to use DGG's RapidPipeline solution within VAMS deployed on EKS (Elastic Kubernetes Service). This solution requires an active subscription to RapidPipeline 3D Processor on AWS Marketplace. [Click here](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi?sr=0-1&ref_=beagle&applicationId=AWSMPContessa) to find the AWS Marketplace listing, and then select **Continue to Subscribe**. This will enable the global VPC option and all pipeline components will be put behind the VPC. A minimum of 2 AZs are required for VPC subnets.
-   `app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI` | default: <ACCOUNTID>.dkr.ecr.<REGION>.amazonaws.com/<ECR-REPOSITORY>/<IMAGE-ID>:<IMAGE-TAG> | #The ECR container image URI for the RapidPipeline EKS deployment. Must be updated with your AWS account ID, region, and the RapidPipeline container image details from AWS Marketplace.
-   `app.pipelines.useRapidPipeline.useEks.autoRegisterWithVAMS` | default: true | #Feature to automatically register the RapidPipeline EKS solution and associated workflows in the global VAMS database during CDK deployment. When enabled, the pipeline will be available immediately after deployment without manual registration through the UI.
-   `app.pipelines.useRapidPipeline.useEks.eksClusterVersion` | default: 1.31 | #The Kubernetes version to use for the EKS cluster. Specify as a string (e.g., "1.31").
-   `app.pipelines.useRapidPipeline.useEks.nodeInstanceType` | default: m5.2xlarge | #The EC2 instance type to use for EKS worker nodes. Choose an instance type with sufficient CPU and memory for 3D processing workloads.
-   `app.pipelines.useRapidPipeline.useEks.minNodes` | default: 1 | #Minimum number of worker nodes in the EKS node group. The cluster will scale down to this number during low usage.
-   `app.pipelines.useRapidPipeline.useEks.maxNodes` | default: 10 | #Maximum number of worker nodes in the EKS node group. The cluster will scale up to this number during high demand.
-   `app.pipelines.useRapidPipeline.useEks.desiredNodes` | default: 2 | #Desired number of worker nodes to maintain in the EKS node group under normal operation.
-   `app.pipelines.useRapidPipeline.useEks.jobTimeout` | default: 7200 | #Maximum time in seconds that a Kubernetes job can run before being terminated (default: 2 hours). Increase for large file processing.
-   `app.pipelines.useRapidPipeline.useEks.jobMemory` | default: 16Gi | #Memory allocation for each Kubernetes job pod. Specify with units (e.g., "16Gi" for 16 gigabytes). Adjust based on file size and complexity.
-   `app.pipelines.useRapidPipeline.useEks.jobCpu` | default: 2000m | #CPU allocation for each Kubernetes job pod. Specify in millicores (e.g., "2000m" for 2 CPU cores). Adjust based on processing requirements.
-   `app.pipelines.useRapidPipeline.useEks.jobBackoffLimit` | default: 2 | #Number of retries for failed Kubernetes jobs before marking as permanently failed.
-   `app.pipelines.useRapidPipeline.useEks.jobTTLSecondsAfterFinished` | default: 600 | #Time in seconds to keep completed or failed job pods before automatic cleanup (default: 10 minutes). Useful for debugging.
-   `app.pipelines.useRapidPipeline.useEks.observability.enableControlPlaneLogs` | default: false | #Feature to enable EKS control plane logging to CloudWatch. Logs include API server, audit, authenticator, controller manager, and scheduler logs. Useful for troubleshooting but incurs additional CloudWatch costs.
-   `app.pipelines.useRapidPipeline.useEks.observability.enableContainerInsights` | default: false | #Feature to enable CloudWatch Container Insights for the EKS cluster. Provides detailed metrics and logs for cluster, node, pod, and container performance. Incurs additional CloudWatch costs.
-   `app.pipelines.useModelOps.enabled` | default: false | #Feature to use VNTANA's ModelOps solution within VAMS. This solution requires an active subscription to VNTANA Intelligent 3D Optimization Engine Container on AWS Marketplace. [Click here](https://aws.amazon.com/marketplace/pp/prodview-ooio3bidshgy4?applicationId=AWSMPContessa&ref_=beagle&sr=0-1) to find the AWS Marketplace listing, and then select **Continue to Subscribe**.
-   `app.pipelines.useModelOps.autoRegisterWithVAMS` | default: true | #Feature to automatically register the ModelOps solution and associated workflows in the global VAMS database during CDK deployment. When enabled, the pipeline will be available immediately after deployment without manual registration through the UI.
-   `app.pipelines.useIsaacLabTraining.enabled` | default: false | #Feature to enable the Isaac Lab reinforcement learning training pipeline for robotics simulation. This pipeline uses NVIDIA Isaac Lab on AWS Batch with GPU instances (G6, G6E, or G5) to train and evaluate RL policies. Requires GPU-enabled instances and will incur compute costs when jobs are running. See `backendPipelines/simulation/isaacLabTraining/README.md` for detailed usage documentation.
-   `app.pipelines.useIsaacLabTraining.acceptNvidiaEula` | default: false | #**Required when enabled.** You must review and accept the [NVIDIA Software License Agreement](https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license) before deploying this pipeline. Set to `true` to indicate acceptance of the NVIDIA EULA for Isaac Sim container images. Deployment will fail if this is not set to `true` when the pipeline is enabled.
-   `app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS` | default: true | #Feature to automatically register the Isaac Lab training and evaluation pipelines and associated workflows in the global VAMS database during CDK deployment. When enabled, two workflows will be available: `isaaclab-training` for training new policies and `isaaclab-evaluation` for evaluating trained policies.
-   `app.pipelines.useIsaacLabTraining.keepWarmInstance` | default: false | #Feature to keep a warm AWS Batch compute instance running to reduce cold start times for training jobs. When enabled, an instance will remain available, reducing job startup time from ~5-10 minutes to under 1 minute. **Warning**: This incurs continuous compute costs even when no jobs are running. Recommended only for frequent training workloads.

-   `app.addons.useGarnetFramework.enabled` | default: false | #Feature to enable Garnet Framework integration. When enabled, VAMS will automatically index all data changes to the external Garnet Framework knowledge graph in NGSI-LD format.
-   `app.addons.useGarnetFramework.garnetApiEndpoint` | default: UNDEFINED | #The Garnet Framework API endpoint URL (e.g., https://XXX.execute-api.us-east-1.amazonaws.com). Must be a valid URL. Required when Garnet Framework is enabled.
-   `app.addons.useGarnetFramework.garnetApiToken` | default: UNDEFINED | #The API authentication token for the Garnet Framework. Used for authenticated API calls to Garnet. Required when Garnet Framework is enabled.
-   `app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl` | default: UNDEFINED | #The SQS queue URL for Garnet Framework data ingestion. Must be a valid SQS URL in the format: https://sqs.region.amazonaws.com/account/queue-name. VAMS will send NGSI-LD formatted entities to this queue for ingestion into Garnet. Required when Garnet Framework is enabled.

-   `app.authProvider.presignedUrlTimeoutSeconds` | default: 86400 | #Used to specify timeouts for upload/download presigned URLs.
-   `app.authProvider.authorizerOptions.allowedIpRanges` | default: [] | #Optional array of IP range pairs for restricting API access. Each range is defined as ["min_ip", "max_ip"]. Example: [["192.168.1.1", "192.168.1.255"], ["10.0.0.1", "10.0.0.255"]]. Leave empty to allow all IPs. IP validation is performed before skipped paths check and JWT authentication for security and performance optimization.
-   `app.authProvider.useCognito.enabled` | default: true | #Feature to use Cognito Use Pools should be used for VAMS user management and authentication. At least 1 authProvider must be enabled in the configuration.
-   `app.authProvider.useCognito.useSaml` | default: false | #Specifies if Cognito User Pools use a federated SAML from an external IDP integration.
-   `app.authProvider.useCognito.useUserPasswordAuthFlow` | default: false | #Specifies if Cognito User Pools enable `USER_PASSWORD_AUTH` authentication flow that allow USERNAME/PASSWORD to be sent directly for authentication verses using only SRP caluclated authentication. Some organizations may use this when cognito SRP calculation libraries are not available for system-to-system integrations or user interfaces.
-   `app.authProvider.useCognito.credTokenTimeoutSeconds` | default: 3600 | #Used to specify authentication token timeouts for cognito issued tokens. Refresh token is fixed to 24 hours.
-   `app.authProvider.useExternalOauthIdp.enabled` | default: false | Feature to use an external OAUTH IDP. Switches front-end web to use new IDP from Cognito. Cannot currently use location services with this option. Switches API gateway authorizers to an external JWT authorizer hook. At least 1 authProvider must be enabled in the configuration.
-   `app.authProvider.useExternalOauthIdp.idpAuthProviderUrl` | default: NULL | URL for external OAUTH IDP authentication endpoint such as https://ping-federate.com
-   `app.authProvider.useExternalOauthIdp.idpAuthClientId` | default: NULL | The clientId provided by the external IDP system to recognize this application deployment
-   `app.authProvider.useExternalOauthIdp.idpAuthProviderScope` | default: NULL | The external OAuth IDP scope this application is requesting
-   `app.authProvider.useExternalOauthIdp.idpAuthProviderScopeMfa` | default: NULL | The external OAuth IDP Scope attribute for MFA that would be appended to the scope this application is requesting. Leaving null keeps MFA off for external OAUTH IDP.
-   `app.authProvider.useExternalOauthIdp.idpAuthPrincipalDomain` | default: NULL | Principal domain for the IDP endpoint for use in role authorization permissions ping-federate.com
-   `app.authProvider.useExternalOauthIdp.idpAuthProviderTokenEndpoint` | default: NULL | The external OAuth IDP Token Endpoint path, such as /as/token.oauth2
-   `app.authProvider.useExternalOauthIdp.idpAuthProviderAuthorizationEndpoint` | default: NULL | The external OAuth IDP Authorization Endpoint path, such as /as/authorization.oauth2
-   `app.authProvider.useExternalOauthIdp.idpAuthProviderDiscoveryEndpoint` | default: NULL | The external OAuth IDP Discovery Endpoint path, such as /.well-known/openid-configuration
-   `app.authProvider.useExternalOauthIdp.lambdaAuthorizorJWTIssuerUrl` | default: NULL | URL for external OAUTH IDP authentication endpoint for authorizer verification
-   `app.authProvider.useExternalOauthIdp.lambdaAuthorizorJWTAudience` | default: NULL | The audience provided by the external IDP system to recognize this application deployment for JWT token verification

-   `app.webUi.optionalBannerHtmlMessage` | default: NULL | #Optional HTML message to display as a banner in the web UI. Can be used for system notifications or compliance messages.
-   `app.webUi.allowUnsafeEvalFeatures` | default: false | #Allow for features and web CSP policy that allow 'unsafe-eval' policy for script execution. Confirm with your security teams before turning this on.

-   `app.api.globalRateLimit` | default: 50 | #Sets the global rate limit (requests per second) for the API Gateway throttling. Must be a positive number greater than 0. Can be overridden with environment variable `GLOBAL_RATE_LIMIT` or CDK context parameter `globalRateLimit`.
-   `app.api.globalBurstLimit` | default: 100 | #Sets the global burst limit for the API Gateway throttling. Must be a positive number greater than or equal to the rate limit. Can be overridden with environment variable `GLOBAL_BURST_LIMIT` or CDK context parameter `globalBurstLimit`.

-   `app.metadataSchema.autoLoadDefaultAssetLinksSchema` | default: true | #Feature to automatically load default metadata schema for asset links during CDK deployment. When enabled, creates a GLOBAL schema named "defaultAssetLinks" with fields for Translation (XYZ), Rotation (WXYZ), Scale (XYZ), and Matrix (MATRIX4X4). These fields support spatial relationship metadata between linked assets.
-   `app.metadataSchema.autoLoadDefaultDatabaseSchema` | default: true | #Feature to automatically load default metadata schema for databases during CDK deployment. When enabled, creates a GLOBAL schema named "defaultDatabase" with a Location field (LLA - Latitude/Longitude/Altitude) for geographic positioning of database entities.
-   `app.metadataSchema.autoLoadDefaultAssetSchema` | default: true | #Feature to automatically load default metadata schema for assets during CDK deployment. When enabled, creates a GLOBAL schema named "defaultAsset" with a Location field (LLA - Latitude/Longitude/Altitude) for geographic positioning of assets.
-   `app.metadataSchema.autoLoadDefaultAssetFileSchema` | default: true | #Feature to automatically load default metadata schema for 3D model files during CDK deployment. When enabled, creates a GLOBAL schema named "defaultAssetFile3dModel" with a Polygon_Count field (STRING) and file type restrictions for common 3D formats (.glb, .usd, .obj, .fbx, .gltf, .stl, .usdz). This schema is automatically applied to matching file types for metadata collection.

### Additional configuration notes

-   `GovCloud` - This will check for Use Global VPC (enabled), Use ALB (enabled - other configuration needed, see ALB section), use Cloudfront (disabled), and Use Location Services (disabled). Additionally does some small implementation changes for components that are different in GovCloud partitions.
-   `GovCloud - IL6 Compliant` - This will check for Use Cognito (disabled), Use WAF (enabled), Use VPC for all Lambdas (enabled), and Use KMS CMK Encryption (enabled). Additionally does some small implementation changes for components that are different in GovCloud IL6 partition.
-   `OpenSearch` - If both serverless and provisioned are not enabled, no OpenSearch will be enabled which will reduce the functionality in the application to not have any search capabilities on assets. All authorized assets will be returned always on the assets page.
-   `OpenSearch - Provisioned` - This service is very sensitive to VPC Subnet Availabilty Zone selection. If using an external VPC, make sure the provided private subnets are a minimum of 3 and are each in their own availability zone. OpenSearch Provisioned CDK creates service-linked roles althoguh sometimes these don't get recognized right away during a first-time deployment by receiving the following error: `Invalid request provided: Before you can proceed, you must enable a service-linked role to give Amazon OpenSearch Service permissions to access your VPC.`. Wait 5 minutes minutes after your first run and then re-run your deployment (after clearing out any previous stack in CloudFormation). If you continue seeing issues, run the following CLI command manually to try to create these roles by hand: `aws iam create-service-linked-role --aws-service-name es.amazonaws.com`, `aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com`
-   `OpenSearch - Reindexing` - The `reindexOnCdkDeploy` configuration option triggers automatic reindexing during CDK deployment. This is useful for migrations (e.g., v2.2 to v2.3) or when you need to rebuild indexes. **Two-Step Process Required**: (1) First deploy v2.3 to create the reindexer Lambda function, (2) Then enable `reindexOnCdkDeploy` and deploy again to trigger reindexing. After reindexing completes, set this back to `false` to prevent reindexing on every deployment. For manual reindexing without CDK deployment, use the reindex utility script at `infra/deploymentDataMigration/tools/reindex_utility.py` which directly invokes the deployed Lambda function.

-   `Global VPC` - Will auto be enabled if ALB, OpenSearch Provisioned, or Use-case Pipelines is enabled. OpenSearch Serverless endpoints and associated lambdas will also be put behind the VPC if toggling on the VPC and using for all lambdas.
-   -   IPs Used per Feature:
-   -   ALB: 8 IPs per subnet (public or private) [needs up to 8 during runtime to scale, may be less during low-use]
-   -   Use-case Pipelines: ~2 IPs per subnet deployed running an active pipeline workflow execution (based on application demand)
-   -   Lambda in VPC: 1 IP per deployed CDK lambda functions per subnet (v2.0: ~66)
-   -   VPC Interface Endpoints: 1 IP per endpoint per subnet (based on overall configuration needs, see `Global VPC Endpoints` below)
-   `Global VPC Subnets` - Each Subnet to subnet-type (relevent to public or private) used should reside in it's own AZ within the region. Subnets should be configured for IPv4. CDK will probably deploy/create to the amount of AZs and related subnets. When importing an existing VPC/subnets, make sure each subnet provided is located within its own AZ (otherwise errors may occur). The minimum amount of AZs/Subnets needed are (use the higher number): 3 - when using Open Search Provisioned, 2 - when using ALB or EKS pipelines, 1 - for all other configurations. Reccomended to have at least 128 IPs (IPv4) available per subnet for deployment to support current VAMS usage (max configuration) and growth.
-   `Global VPC Endpoints` - When using a Global VPC, interface/gateway endpoints are needed. The following is the below chart of VPC Endpoints created (when using addVpcEndpoints config option) or are needed otherwise. Some endpoints have special creation conditions that are noted below.
-   -   (Interface) CloudWatch Logs - Deployed/used with Global VPC enablement
-   -   (Interface) SNS - Deployed/used with Global VPC enablement
-   -   (Interface) SQS - Deployed/used with Global VPC enablement
-   -   (Interface) SFN - Deployed/used with Global VPC enablement
-   -   (Interface) APIGateway - Deployed/used with Global VPC enablement
-   -   (Interface) SSM - Deployed/used with Global VPC enablement
-   -   (Interface) Lambda - Deployed/used with Global VPC enablement
-   -   (Interface) STS - Deployed/used with Global VPC enablement
-   -   (Interface) (AWS- NOT SUPPORTED YET) Cognito IDP - Deployed/used with Global VPC enablement [not implemented until AWS implements cognito IDP VPCe]
-   -   (Interface) KMS - Deployed/used with Use KMS CMK Encryption Features
-   -   (Interface) KMS FIPS - Deployed/used with Use KMS CMK Encryption and Use FIPS Features
-   -   (Interface) ECR - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features (Isolated + Private for AWS Marketplace pipelines)
-   -   (Interface) OpenSearch Serverless - Deployed/used with OpenSearch Serverless Feature
-   -   (Interface) ECR Docker - Deployed/used with "Use with All Lambda" and Use-casePipeline Features
-   -   (Interface) Bedrock Runtime - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) Rekognition - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) Batch - Deployed/used with Use-case Pipeline Feature
-   -   (Interface) EFS - Deployed/used with Isaac Lab Training Pipeline Feature
-   -   (Interface) S3 (ALB-Special) - Created on VPC when using ALB as it's specially setup with the ALB IPs and targets. Separate configuration option to create this under the ALB setting.
-   -   (Gateway) S3 - Due to no pricing implications, deployed/used across all features that require VPC
-   -   (Gateway) DynamoDB - Due to no pricing implications, deployed/used across all features that require VPC

APIGateway, SSM, Lambda, STS, Cloudwatch Logs, SNS, SQS

-   `KMS Encryption - External CMK` - When importing an external CMK KMS key to use for encryption the VAMS deployment, ensure the CMK key is located in the same region as the deployment and has the following permissions.
-   -   Actions: `["kms:GenerateDataKey*", "kms:Decrypt", "kms:ReEncrypt*", "kms:DescribeKey",  "kms:ListKeys", "kms:CreateGrant"]`
-   -   Resources: `["*"]`
-   -   Principals: `S3, DYNAMODB, STS, SQS, SNS, ECS, ECS_TASKS, EKS, LOGS, LAMBDA, CLOUDFRONT, ES, AOSS` - Note: ES: OpenSearch Provisioned, AOSS - OpenSearch Serverless

-   `Asset Buckets` - If `app.assetBuckets.createNewBucket` is set to false, you must define at least one external asset bucket in `app.assetBuckets.externalAssetBuckets`. Each external bucket configuration requires a bucketArn, baseAssetsPrefix, and defaultSyncDatabaseId.

### Misc Troubleshooting

-   `Global VPC External Import` + `Global VPC Endpoints` - if you receive a stack deployment error like `route table rtb-XXXX already has a route with destination-prefix-list-id pl-XXXXX`, this means your VPC you have provided already has some of the VPC endpoints created and/or route table entries for those services.
-   -   Turn `app.useGlobalVpc.addVpcEndpoints` to `false` and manually add any missing endpoints based on the endpoints listed in the additional configuration notes.
-   -   Note that the ALB endpoint (if using an ALB) is always auto-created regardless of this flag. If this errors, you may have to manually modify the VAMS CDK code to not add this endpoint and by hand add/update ALB targets with the endpoint IPs.
-   `ALB Enabled` - if you receive a stack deployment error like `'Properties validation failed for resource WebAppWebAppALBTargetGroupXXXXX with message: #/Targets: array items are not unique'`, this unforuntaely means that AWS CloudFormation had an unexpected and rare hiccup when trying to set the VPC S3 Interface endpoint IPs to the ALB target. This can happen every once in a while and the cause has yet to be identified.
-   -   No changes are needed. Re-run the stack deployment to mitigate.

## Additional Configuration Environment Options

Additional deployment environment configuration settings can be set in the `./infra/cdk.json` file.

Any non-empty-key key-value-pair in `{environments: {common: {}}}` will be added as tags on all assets deployed in the VAMS Core stack.

The following additional settings can be added under `{environments: {aws: {}}}`:

-   `PermissionBoundaryArn` - If Permission Boundary ARN defined, applies the Permission boundary to apply to all roles created by VAMS Core stack.
-   `IamRoleNamePrefix` - If role name prefix string defined, applies the prefix in front of all newly created roles by VAMS Core stack. Warning: The total role name character count limit is 64 so long prefixes may affect rolename uniqueness and cause deployment issues. Reccomend prefixs no longer than 8 characters.

## Additional Configuration Policy Options

Additional IAM and resource permission policies can be added to AWS components created to further lock down or open up a component.

An empty file means no additional policy statement will be added besides the default security restrictions. Currently only a single statement to add is supported.

The `./infra/config/policy/s3AdditionalBucketPolicyConfig.json` allows you to add a additional JSON-formated IAM policy statement that will get added to all S3 buckets created. This also controls the ability to ALLOW or DENY access to PreSigned S3 URLS and STS credentials that are generated by VAMS for Asset Upload and Download. Note: Resource will be overriden at deployment run-time to include each respective bucket and their objects.

## Additional Configuration CSP Options

VAMS supports configurable Content Security Policy (CSP) settings through the `./infra/config/csp/cspAdditionalConfig.json` file. This allows organizations to add their specific external API endpoints and resources without modifying core code.

### CSP Configuration File Structure

The CSP configuration file supports the following categories:

```json
{
    "connectSrc": ["https://api.example.com"],
    "scriptSrc": ["https://cdn.example.com"],
    "imgSrc": ["https://images.example.com"],
    "mediaSrc": ["https://media.example.com"],
    "fontSrc": ["https://fonts.example.com"],
    "styleSrc": ["https://styles.example.com"]
}
```

### CSP Configuration Categories

-   **`connectSrc`** - External APIs and services that the application can connect to via XMLHttpRequest, WebSocket, EventSource, etc.
-   **`scriptSrc`** - External JavaScript libraries or CDNs that can be executed
-   **`imgSrc`** - External image sources that can be loaded
-   **`mediaSrc`** - External media sources (audio/video) that can be loaded
-   **`fontSrc`** - External font sources (e.g., Google Fonts)
-   **`styleSrc`** - External stylesheet sources that can be loaded

### Configuration Behavior

-   **File Not Found**: VAMS uses default CSP settings without failing the build
-   **Invalid JSON**: Logs warning and uses default CSP settings
-   **Empty Arrays**: Ignored, only default CSP sources used for those categories
-   **Invalid Entries**: Non-string or empty entries are filtered out with warnings
-   **Duplicate Prevention**: Additional sources are merged with existing ones, avoiding duplicates

### Common Configuration Examples

**External API Integration:**

```json
{
    "connectSrc": ["https://api.mapbox.com", "https://api.openweathermap.org"]
}
```

**CDN Resources:**

```json
{
    "scriptSrc": ["https://cdn.jsdelivr.net"],
    "styleSrc": ["https://fonts.googleapis.com"],
    "fontSrc": ["https://fonts.gstatic.com"]
}
```

**Image Services:**

```json
{
    "imgSrc": ["https://images.unsplash.com", "https://cdn.example.com"]
}
```

### Security Considerations

-   Only add trusted domains to your CSP configuration
-   Avoid using wildcards (`*`) as they compromise security
-   Regularly audit your CSP configuration
-   Test configuration changes in development before production deployment
-   Monitor browser console for CSP violations

Some example policy statements are below based on <https://repost.aws/knowledge-center/block-s3-traffic-vpc-ip>. Recomend adding `aws:ViaAWSService` condition as `false` to restrict only on direct user calls as services will also need to access these buckets.

Example: Restrict access outside of a VPC Interface Endpoint (fill in existing VPC Interface Endpoint ID)

```
{
    "Sid": "VPCe",
    "Action": "s3:*",
    "Effect": "Deny",
    "Resource": ["*"],
    "Condition": {
    "StringNotEquals": {
        "aws:SourceVpce": [
        "vpce-XXXXXXXX",
        "vpce-YYYYYYYY"
        ]
    },
    "BoolIfExists": {"aws:ViaAWSService": "false"}
    },
    "Principal": "*"
}
```

Example: Restrict access outside of a VPC Private IP (Fill in IP addresses and/or CIDR ranges)

```
{
    "Sid": "VpcSourceIp",
    "Action": "s3:*",
    "Effect": "Deny",
    "Resource": ["*"],
    "Condition": {
    "NotIpAddressIfExists": {
        "aws:VpcSourceIp": [
        "10.1.1.1/32",
        "172.1.1.1/32"
        ]
    },
    "BoolIfExists": {"aws:ViaAWSService": "false"}
    },
    "Principal": "*"
}
```

Example: Restrict access outside of a source IP (Fill in IP addresses and/or CIDR ranges)

```
{
    "Sid": "SourceIP",
    "Action": "s3:*",
    "Effect": "Deny",
    "Resource": ["*"],
    "Condition": {
    "NotIpAddressIfExists": {
        "aws:SourceIp": [
        "11.11.11.11/32",
        "22.22.22.22/32"
        ]
    },
    "BoolIfExists": {"aws:ViaAWSService": "false"}
    },
    "Principal": "*"
}
```

## CloudFront Custom Domain Configuration

VAMS supports custom domain names for CloudFront distributions, allowing you to serve the web application from your own branded domain (e.g., `vams.example.com`) instead of the auto-generated CloudFront domain name.

### Prerequisites

Before enabling custom domain support for CloudFront, ensure you have:

1. **ACM Certificate in us-east-1**: An AWS Certificate Manager (ACM) certificate for your domain **must be in the us-east-1 region**, regardless of where you're deploying VAMS. This is a CloudFront requirement.

    - The certificate must be issued for the exact domain you plan to use (e.g., `vams.example.com`)
    - Wildcard certificates (e.g., `*.example.com`) are supported
    - Certificate must be in `ISSUED` status

2. **Domain Name**: A registered domain name that you control

    - Can be an apex domain (e.g., `example.com`) or subdomain (e.g., `vams.example.com`)
    - Must match the certificate's domain

3. **Route53 Hosted Zone (Optional)**: If you want automatic DNS configuration
    - Hosted zone must be for the root domain (e.g., `example.com` for subdomain `vams.example.com`)
    - You'll need the Hosted Zone ID

If you didn't provide `optionalHostedZoneId`, manually create a DNS record:

**For Route53:**

-   Create an A record (Alias) pointing to the CloudFront distribution domain name
-   The CloudFront domain name is available in the stack outputs

**For Other DNS Providers:**

-   Create a CNAME record pointing to the CloudFront distribution domain name
-   Note: CNAME records cannot be used for apex domains (use Route53 or another provider that supports ALIAS records)

### Configuration Options Explained

**`enabled`**:

-   Set to `true` to use a custom domain
-   Set to `false` (default) to use the auto-generated CloudFront domain
-   When disabled, the other custom domain settings are ignored

**`domainHost`**:

-   The fully qualified domain name (FQDN) for your VAMS deployment
-   Examples: `vams.example.com`, `assets.company.com`, `example.com`
-   Must exactly match the domain in your ACM certificate

**`certificateArn`**:

-   The ARN of your ACM certificate in us-east-1
-   Format: `arn:aws:acm:us-east-1:ACCOUNT_ID:certificate/CERTIFICATE_ID`
-   **Critical**: Must be in us-east-1 region, even if VAMS is deployed elsewhere

**`optionalHostedZoneId`**:

-   The Route53 Hosted Zone ID for automatic DNS configuration
-   Format: `Z1234567890ABC`
-   If provided, VAMS will automatically create an A record (Alias) pointing to CloudFront
-   If not provided, you must manually configure DNS

CloudFormation Outputs:

-   `CloudFrontDistributionUrl`: Auto-generated CloudFront URL (always available)
-   `CloudFrontCustomDomainUrl`: Your custom domain URL (when enabled)
-   `CloudFrontDistributionDomainName`: CloudFront domain for DNS configuration

## Cloudfront TLS 1.2 Enforcement

Amazon CloudFront is deployed using the default CloudFront domain name and TLS certificate. To use a later TLS version (1.2), use your own custom domain name and custom SSL certificate. For more information, refer to using alternate domain names and HTTPS in the Amazon CloudFront Developer Guide.

Custom domain support is now available as part of the VAMS CDK Configuration through the `app.useCloudFront.customDomain` settings. See the CloudFront Custom Domain Configuration section above for detailed instructions.

https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/distribution-web-values-specify.html#DownloadDistValues-security-policy

## Multi-Factor Authentication (MFA) Support

The solution in some cases can support MFA checks with role support. Roles can be toggled to allow only if a user is logged in with MFA.

However, this check is only supported on a user for each API call if the following is true:

1. External IDP Auth is used (see External IDP MFA Check in LoginProfile section)
2. Cognito IDP Auth is used with:
   2.a. OpenSearch Serverless (CDK Config Flag)
   2.b. Lambdas not behind a VPC (CDK Config Flag)

If Cognito AWS VPC Interface Endpoints are supported in the future, the top cognito restrictions fall away.

## Additional Configuration Docker Options

See [CDK SSL Deploy in the developer guide](./DeveloperGuide.md#CDK-Deploy-with-Custom-SSL-Cert-Proxy) for information on customized docker settings for CDK deployment builds

## Garnet Framework Integration

VAMS supports integration with the Garnet Framework, an external knowledge graph tracking solution that provides advanced semantic search and relationship querying capabilities across your asset management data.

When enabled, VAMS automatically synchronizes all data changes (databases, assets, asset links, files, and metadata) to the Garnet Framework in real-time using NGSI-LD format, the open standard for context information management.

### What Gets Indexed

When Garnet Framework integration is enabled, VAMS automatically creates and maintains NGSI-LD entities for:

1. **Databases** - Complete database records including bucket associations and custom metadata
2. **Assets** - Full asset information including relationships, versions, and custom metadata
3. **Asset Links** - Relationship entities connecting assets (parent-child, related) with metadata
4. **Files** - Individual file entities with S3 information, attributes, and custom metadata

### NGSI-LD Entity Types

VAMS creates the following NGSI-LD entity types in Garnet Framework:

-   **VAMSDatabase** - Database entities with URN format: `urn:vams:database:{databaseId}`
-   **VAMSAsset** - Asset entities with URN format: `urn:vams:asset:{databaseId}:{assetId}`
-   **VAMSAssetLink** - Asset link relationship entities with URN format: `urn:vams:assetlink:{assetLinkId}`
-   **VAMSFile** - File entities with URN format: `urn:vams:file:{databaseId}:{assetId}:{encodedFilePath}`

### Real-Time Synchronization

VAMS uses DynamoDB Streams and S3 event notifications to capture all data changes and automatically:

-   Creates new entities in Garnet when data is created in VAMS
-   Updates entities in Garnet when data is modified in VAMS
-   Deletes entities in Garnet when data is deleted in VAMS
-   Maintains bidirectional relationships between entities
-   Includes all custom metadata fields as NGSI-LD properties

### Event Flow

Data changes flow through the following architecture:

1. **DynamoDB Streams** � SNS Topics � SQS Queues � Garnet Indexer Lambdas
2. **S3 Events** � SNS Topics � SQS Queues � Garnet File Indexer Lambda
3. **Garnet Indexer Lambdas** � Convert to NGSI-LD � External Garnet Ingestion SQS Queue

### Configuration Requirements

To enable Garnet Framework integration:

1. **Deploy Garnet Framework** in your AWS environment (separate from VAMS)
2. **Update VAMS Configuration** with the three required parameters
3. **Deploy VAMS** with the updated configuration
4. **Existing VAMS Deployment Note** - If you need all current VAMS data in an existing deployment, use the re-index utility in the migratino scripts (don't clear OpenSearch indexes) to trigger a full data re-index in the global notification queues. This will re-index all VAMS relevant data with the Garnet Framework.

### IAM Permissions

VAMS automatically configures the necessary IAM permissions for:

-   Reading from DynamoDB tables (databases, assets, files, metadata, links)
-   Sending messages to the external Garnet ingestion SQS queue
-   Processing DynamoDB stream events and S3 notifications

## Additional Configuration LoginProfile Updating

See [LoginProfile in the developer guide](./DeveloperGuide.md#loginprofile-custom-organizational-updates) for information on customized user loginprofile override code for lambdas when login profile information needs to be fetched externally or overwritten otherwise

See [External IDP MFA Check in the developer guide](./DeveloperGuide.md#mfacheck-custom-organizational-updates) for information on how to setup a customized Multi-Factor Authentication (MFA) check when using the external OAUTH IDP settings.
