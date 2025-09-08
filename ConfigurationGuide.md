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

-   `app.useLocationService.enabled` | default: true | #Feature to use location services to display maps data for asset metadata types that store global position coordinates. Note that currently map view won't show up if OpenSearch is not enabled.

-   `app.useAlb.enabled` | default: false | #Feature to swap in a Application Load Balancer instead of a CloudFront Deployment. This will 1) disable static webpage caching, 2) require a fixed web domain to be specified, 3) require a SSL/TLS certicate to be registered in AWS Certifcate Manager, 4) have a S3 bucket name available in the partition that matches the domain name for the static website contents, 5) prevent some cross-site-scripting security preventions / secure HTTPOnly cookies from taking full affect and 6) cause a modified A/B stack deployment scenario on VAMS upgrades due to the common S3 bucket name used by the ALB.
-   `app.useAlb.usePublicSubnet` | default: false | #Specifies if the ALB should use a public subnet. If creating a new VPC, will create seperate public subnets for ALB. If importing an existing VPC, will require `app.useGlobalVpc.optionalExternalPublicSubnetIds` to be filled out.
-   `app.useAlb.addAlbS3SpecialVpcEndpoint` | default: true | #Creates the special S3 VPC endpoint needed by the ALB to serve S3 static web files. If turned false, this will need to be manualyl created afterwards. See the DeveloperGuide for more information.
-   `app.useAlb.domainHost` | default: vams1.example.com | #Specifies the domain to use for the ALB and static webpage S3 bucket. Required to be filled out to use ALB.
-   `app.useAlb.certificateARN` | default: arn:aws-us-gov:acm:<REGION>:<ACCOUNTID>:certificate/<CERTIFICATEID> | #Specifies the existing ACM certificate to use for the ALB for HTTPS connections. ACM certificate must be for the `domainHost` specified and reside in the same region being deployed to. Required to be filled out to use ALB.
-   `app.useAlb.optionalHostedZoneID` | default: NULL | #Optional route53 zone host ID to automatically create an alias for the `domainHost` specified to the created ALB.

-   `app.pipelines.usePreviewPcPotreeViewer.enabled` | default: false | #Feature to create a point cloud potree viewer processing pipeline to support point cloud file type viewing within the VAMS web UI. This will enable the global VPC option and all pipeline components will be put behind the VPC. NOTICE: This feature uses a third-party open-source library with a GPL license, refer to your legal team before enabling.
-   `app.pipelines.useGenAiMetadata3dLabeling.enabled` | default: false | #Feature to create a generative AI metadata labeling pipeline for glb, fbx, and obj files. This will enable the global VPC option and all pipeline components will be put behind the VPC.
-   `app.pipelines.useConversion3dBasic.enabled` | default: true | #Feature to create a file converter pipeline between STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, and XYZ file format types. This pipeline does not need a VPC to operate.
-   `app.pipelines.useRapidPipeline.enabled` | default: false | #Feature to use DGG's RapidPipeline solution within VAMS. This solution requires an active subscription to RapidPipeline 3D Processor on AWS Marketplace. [Click here](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi?sr=0-1&ref_=beagle&applicationId=AWSMPContessa) to find the AWS Marketplace listing, and then select **Continue to Subscribe**.
-   `app.pipelines.useModelOps.enabled` | default: false | #Feature to use VNTANA's ModelOps solution within VAMS. This solution requires an active subscription to VNTANA Intelligent 3D Optimization Engine Container on AWS Marketplace. [Click here](https://aws.amazon.com/marketplace/pp/prodview-ooio3bidshgy4?applicationId=AWSMPContessa&ref_=beagle&sr=0-1) to find the AWS Marketplace listing, and then select **Continue to Subscribe**.

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

### Additional configuration notes

-   `Gov Cloud` - This will check for Use Global VPC, Use ALB, Use OpenSearch Provisioned, and Use Location Services. Additionally does some small implementation changes for components that are different in GovCloud partitions.
-   `Gov Cloud - IL6 Compliant` - This will check for Use Cognito, Use WAF, Use VPC for all Lambdas, and Use KMS CMK Encryption. Additionally does some small implementation changes for components that are different in GovCloud IL6 partition.
-   `OpenSearch` - If both serverless and provisioned are not enabled, no OpenSearch will be enabled which will reduce the functionality in the application to not have any search capabilities on assets. All authorized assets will be returned always on the assets page.
-   `OpenSearch - Provisioned` - This service is very sensitive to VPC Subnet Availabilty Zone selection. If using an external VPC, make sure the provided private subnets are a minimum of 3 and are each in their own availability zone. OpenSearch Provisioned CDK creates service-linked roles althoguh sometimes these don't get recognized right away during a first-time deployment by receiving the following error: `Invalid request provided: Before you can proceed, you must enable a service-linked role to give Amazon OpenSearch Service permissions to access your VPC.`. Wait 5 minutes minutes after your first run and then re-run your deployment (after clearing out any previous stack in CloudFormation). If you continue seeing issues, run the following CLI command manually to try to create these roles by hand: `aws iam create-service-linked-role --aws-service-name es.amazonaws.com`, `aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com`

-   `Global VPC` - Will auto be enabled if ALB, OpenSearch Provisioned, or Use-case Pipelines is enabled. OpenSearch Serverless endpoints and associated lambdas will also be put behind the VPC if toggling on the VPC and using for all lambdas.
-   -   IPs Used per Feature:
-   -   ALB: 8 IPs per subnet (public or private) [needs up to 8 during runtime to scale, may be less during low-use]
-   -   Use-case Pipelines: ~2 IPs per subnet deployed running an active pipeline workflow execution (based on application demand)
-   -   Lambda in VPC: 1 IP per deployed CDK lambda functions per subnet (v2.0: ~66)
-   -   VPC Interface Endpoints: 1 IP per endpoint per subnet (based on overall configuration needs, see `Global VPC Endpoints` below)
-   `Global VPC Subnets` - Each Subnet to subnet-type (relevent to public or private) used should reside in it's own AZ within the region. Subnets should be configured for IPv4. CDK will probably deploy/create to the amount of AZs and related subnets. When importing an existing VPC/subnets, make sure each subnet provided is located within its own AZ (otherwise errors may occur). The minimum amount of AZs/Subnets needed are (use the higher number): 3 - when using Open Search Provisioned, 2 - when using ALB, 1 - for all other configurations. Reccomended to have at least 128 IPs (IPv4) available per subnet for deployment to support current VAMS usage (max configuration) and growth.
-   `Global VPC Endpoints` - When using a Global VPC, interface/gateway endpoints are needed. The following is the below chart of VPC Endpoints created (when using addVpcEndpoints config option) or are needed otherwise. Some endpoints have special creation conditions that are noted below.
-   -   (Interface) KMS - Deployed/used with Use KMS CMK Encryption Features
-   -   (Interface) KMS FIPS - Deployed/used with Use KMS CMK Encryption and Use FIPS Features
-   -   (Interface) ECR - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) ECR Docker - Deployed/used with "Use with All Lambda" and Use-casePipeline Features
-   -   (Interface) CloudWatch Logs - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) SNS - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) SFN - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) Bedrock Runtime - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) Rekognition - Deployed/used with "Use with All Lambda" and Use-case Pipeline Features
-   -   (Interface) SSM - Deployed/used with "Use with All Lambda" and Open Search Provisioned Features
-   -   (Interface) Lambda - Deployed/used with "Use with All Lambda" Feature
-   -   (Interface) STS - Deployed/used with "Use with All Lambda" Feature
-   -   (Interface) Batch - Deployed/used with Use-case Pipeline Feature
-   -   (Interface) OpenSearch Serverless - Deployed/used with OpenSearch Serverless Feature
-   -   (Interface) S3 (ALB-Special) - Created on VPC when using ALB as it's specially setup with the ALB IPs and targets. Separate configuration option to create this under the ALB setting.
-   -   (Gateway) S3 - Due to no pricing implications, deployed/used across all features that require VPC
-   -   (Gateway) DynamoDB - Due to no pricing implications, deployed/used across all features that require VPC

-   `KMS Encryption - External CMK` - When importing an external CMK KMS key to use for encryption the VAMS deployment, ensure the CMK key is located in the same region as the deployment and has the following permissions.
-   -   Actions: `["kms:GenerateDataKey*", "kms:Decrypt", "kms:ReEncrypt*", "kms:DescribeKey",  "kms:ListKeys", "kms:CreateGrant"]`
-   -   Resources: `["*"]`
-   -   Principals: `S3, DYNAMODB, STS, SQS, SNS, ECS, ECS_TASKS, LOGS, LAMBDA, CLOUDFRONT, ES, AOSS` - Note: ES: OpenSearch Provisioned, AOSS - OpenSearch Serverless

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

## Cloudfront TLS 1.2 Enforcement

Amazon CloudFront is deployed using the default CloudFront domain name and TLS certificate. To use a later TLS version (1.2), use your own custom domain name and custom SSL certificate. For more information, refer to using alternate domain names and HTTPS in the Amazon CloudFront Developer Guide.

This is a post-configuration option or modification to the CDK and not a feature currently available as part of the VAMS CDK Configuration. Domains are currently only supported as part of VAMS CDK configuration with a web ALB deployment.

https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/distribution-web-values-specify.html#DownloadDistValues-security-policy

## Additional Configuration Docker Options

See [CDK SSL Deploy in the developer guide](./DeveloperGuide.md#CDK-Deploy-with-Custom-SSL-Cert-Proxy) for information on customized docker settings for CDK deployment builds

## Additional Configuration LoginProfile Updatng

See [LoginProfile in the developer guide](./DeveloperGuide.md#loginprofile-custom-organizational-updates) for information on customized user loginprofile override code for lambdas when login profile information needs to be fetched externally or overwritten otherwise

See [External IDP MFA Check in the developer guide](./DeveloperGuide.md#mfacheck-custom-organizational-updates) for information on how to setup a customized Multi-Factor Authentication (MFA) check when using the external OAUTH IDP settings.
