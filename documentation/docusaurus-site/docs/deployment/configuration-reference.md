# Configuration reference

This page documents every configuration option available in the VAMS deployment configuration file located at `infra/config/config.json`. Options are organized by functional area. For deployment instructions, see [Deploy the solution](deploy-the-solution.md).

:::info[Configuration resolution order]
Configuration values are resolved using a fallback chain: CDK context parameters (`-c key=value`) take highest priority, followed by values in `config.json`, then environment variables, and finally hardcoded defaults.
:::

## Top-level settings

| Field  | Type   | Default | Description                                            |
| ------ | ------ | ------- | ------------------------------------------------------ |
| `name` | string | `vams`  | Base application name used in the full CDK stack name. |

## Environment (`env`)

| Field                            | Type    | Default     | Description                                                                                                                                                                                                                     |
| -------------------------------- | ------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `env.account`                    | string  | `null`      | AWS account ID for CDK deployment. If null, pulled from `CDK_DEFAULT_ACCOUNT` environment variable.                                                                                                                             |
| `env.region`                     | string  | `us-east-1` | AWS Region for CDK deployment. If null, pulled from `CDK_DEFAULT_REGION`, `REGION`, or defaults to `us-east-1`.                                                                                                                 |
| `env.loadContextIgnoreVPCStacks` | boolean | `false`     | When `true`, skips synthesis and deployment of VPC-dependent nested stacks. Used during the first phase of an external VPC import. See [Deploy the solution](deploy-the-solution.md#step-7-import-an-external-vpc-conditional). |

:::note[Partition auto-detection]
The `env.partition` field is automatically derived from the Region and should not be set manually. VAMS supports `aws`, `aws-us-gov`, `aws-cn`, and `aws-iso` partitions.
:::

## Stack identification (`app`)

| Field                   | Type   | Default         | Description                                                                                                                                                                                                                                                 |
| ----------------------- | ------ | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.baseStackName`     | string | `prod`          | Stack environment name appended to resource names. Combined with the Region to form the full CloudFormation stack name (for example, `vams-core-prod-us-east-1`). Can be overridden with the `STACK_NAME` environment variable or CDK context `stack-name`. |
| `app.adminUserId`       | string | `administrator` | Username for the initial super administrator account. Can be an email address. Can be overridden with the `ADMIN_USER_ID` environment variable.                                                                                                             |
| `app.adminEmailAddress` | string | _(required)_    | Email address for the initial admin account. A temporary password is sent to this address during first deployment. Can be overridden with the `ADMIN_EMAIL_ADDRESS` environment variable.                                                                   |

## Asset buckets (`app.assetBuckets`)

Controls how Amazon S3 asset storage buckets are provisioned.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts` (`StorageResourcesBuilderNestedStack`) — Amazon S3 asset buckets plus a DynamoDB bucket registry populated by the custom resource `customResources/populateS3AssetBucketsTable.ts`.
:::

| Field                                              | Type    | Default                                     | Description                                                                                                                                                                                                                                                                            |
| -------------------------------------------------- | ------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.assetBuckets.createNewBucket`                 | boolean | `true`                                      | When `true`, VAMS creates a new Amazon S3 bucket for asset storage. When `false`, you must define at least one external asset bucket.                                                                                                                                                  |
| `app.assetBuckets.defaultNewBucketSyncDatabaseId`  | string  | `default`                                   | Database ID to synchronize with the newly created bucket. **Required** when `createNewBucket` is `true`.                                                                                                                                                                               |
| `app.assetBuckets.externalAssetBuckets`            | array   | `null`                                      | Array of external Amazon S3 bucket configurations to register with VAMS. Each bucket requires the fields described below.                                                                                                                                                              |
| `app.assetBuckets.presignedUrlNetworkRestrictions` | object  | `{allowedIpRanges: [], allowedVpceIds: []}` | Optional network restrictions on presigned URLs for the VAMS-created asset bucket and the auxiliary bucket. Uses a bucket policy deny statement that applies only to presigned (query-string authenticated) requests; backend operations unaffected. See the restriction object below. |

### External asset bucket object

Each element in `externalAssetBuckets` has the following fields:

| Field                   | Type    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bucketArn`             | string  | Amazon Resource Name (ARN) of the existing Amazon S3 bucket. Must use the same partition as the deployment. May be repeated to register the bucket under multiple non-overlapping prefixes.                                                                                                                                                                                                                                                   |
| `baseAssetsPrefix`      | string  | Base prefix to use for cataloging and syncing assets. Use `/` for the bucket root. Must end with `/`.                                                                                                                                                                                                                                                                                                                                         |
| `defaultSyncDatabaseId` | string  | Database ID to associate with asset changes synced from this bucket. If the database does not exist, VAMS creates it.                                                                                                                                                                                                                                                                                                                         |
| `isDefault`             | boolean | Optional. Marks this bucket as the VAMS default asset bucket, which holds all pipeline template bodies and execution run I/O (manifests, config, auxiliary output) under the `pipelines/` prefix. At most one entry may set `isDefault` to `true`. When `createNewBucket` is `false`, exactly one entry must set it to `true`; when `createNewBucket` is `true`, an entry that sets it to `true` overrides the created bucket as the default. |
| `bucketAccountId`       | string  | Optional. The 12-digit AWS account ID that owns the bucket. Set for cross-account buckets so VAMS imports them as cross-account and scopes notification source policies.                                                                                                                                                                                                                                                                      |
| `bucketRegion`          | string  | Optional. The AWS Region of the bucket. Defaults to the deployment Region when omitted.                                                                                                                                                                                                                                                                                                                                                       |
| `bucketKmsKeyArn`       | string  | Optional. ARN of the AWS KMS key the bucket is encrypted with. When set, VAMS grants this key to its Lambda and pipeline roles. Required if the bucket uses SSE-KMS.                                                                                                                                                                                                                                                                          |

### Presigned URL restriction object

Optional network restrictions on presigned URL access. Used in `app.assetBuckets.presignedUrlNetworkRestrictions` for the created asset bucket and auxiliary bucket.

| Field             | Type  | Default | Description                                                                                                                                                                                                                                      |
| ----------------- | ----- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `allowedIpRanges` | array | `[]`    | Array of IPv4 and IPv6 CIDR blocks (e.g., `["192.168.1.0/24", "2001:db8::/32"]`) permitted to use presigned URLs. Empty array means no IP restrictions. Mutually exclusive with `allowedVpceIds`.                                                |
| `allowedVpceIds`  | array | `[]`    | Array of Amazon S3 VPC endpoint IDs (e.g., `["vpce-1234abcd"]`) permitted to use presigned URLs. Accepts both interface and gateway VPC endpoint IDs. Empty array means no VPC endpoint restrictions. Mutually exclusive with `allowedIpRanges`. |

**Example configuration with restrictions:**

```json
{
    "app": {
        "assetBuckets": {
            "createNewBucket": true,
            "defaultNewBucketSyncDatabaseId": "default",
            "presignedUrlNetworkRestrictions": {
                "allowedIpRanges": ["203.0.113.0/24"],
                "allowedVpceIds": []
            },
            "externalAssetBuckets": null
        }
    }
}
```

Restrict on one network dimension per deployment: configuration validation rejects setting both `allowedIpRanges` and `allowedVpceIds` (a request arrives either over the public path, evaluated against `aws:SourceIp`, or through a VPC endpoint, evaluated against `aws:SourceVpce`). Restrictions are enforced at URL use time through bucket policy deny statements. Restriction changes applied through a redeployment take effect immediately for both newly issued URLs and previously issued URLs that have not yet expired.

:::tip[Adding external buckets]
External buckets can be added incrementally across deployments. Each bucket requires additional IAM bucket policies. A bucket ARN may be registered more than once to map multiple databases to non-overlapping prefixes within it. See [External Amazon S3 bucket setup](external-s3-setup.md) for the full bucket policy, KMS, cross-account requirements, and (if desired) how to restrict presigned URLs on external buckets.
:::

## Security and compliance

### WAF and FIPS (`app`)

| Field                        | Type    | Default | Description                                                                                                                                                                                    |
| ---------------------------- | ------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.useWaf`                 | boolean | `true`  | Enables AWS WAF. Always protects the Amazon API Gateway API and, when present, the Amazon CloudFront distribution or Application Load Balancer. Disabling this generates a deployment warning. |
| `app.useFips`                | boolean | `false` | Enables FIPS-compliant AWS partition endpoints. Must be combined with the `AWS_USE_FIPS_ENDPOINT=true` environment variable.                                                                   |
| `app.addStackCloudTrailLogs` | boolean | `true`  | Creates a dedicated Amazon CloudWatch Logs group and associated AWS CloudTrail trail for this stack.                                                                                           |

:::info[Implemented by]
These three keys do **not** map to a single nested stack:

-   `app.useWaf` — standalone WAF stack(s) `infra/lib/cf-waf-stack.ts` (`CfWafStack`), gated in `infra/bin/infra.ts`. The regional web ACL attaches to the API Gateway stage in `apiLambda/constructs/rest-api-gateway-construct.ts` and to the ALB in `staticWebApp/staticWebBuilder-nestedStack.ts`; the CloudFront web ACL attaches to the distribution in `staticWebApp/constructs/cloudfront-s3-website-construct.ts`.
-   `app.useFips` — global endpoint resolution in `infra/lib/helper/service-helper.ts` (no stack of its own).
-   `app.addStackCloudTrailLogs` — created inline in the root stack `infra/lib/core-stack.ts` (`CoreVAMSStack`).
    :::

#### How many web ACLs are created

When `app.useWaf` is enabled, VAMS always creates a **regional-scoped** web ACL in the deployment Region and associates it with the API Gateway API stage — for both `REGIONAL` and `PRIVATE` endpoint types, and regardless of whether a CloudFront distribution or ALB fronts the application. This protects the API's `execute-api` endpoint, which remains directly reachable in every fronting configuration.

The number of web ACLs depends on the front-end distribution:

| Front-end (`app.useCloudFront` / `app.useAlb`) | Web ACLs created                                                                  | Attached to                                |
| ---------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------ |
| CloudFront enabled                             | **2** — a regional ACL (deployment Region) **and** a CloudFront ACL (`us-east-1`) | API Gateway stage; CloudFront distribution |
| ALB enabled                                    | 1 — a regional ACL (deployment Region)                                            | API Gateway stage; ALB                     |
| Neither                                        | 1 — a regional ACL (deployment Region)                                            | API Gateway stage                          |

AWS WAF scopes are not interchangeable, so a **CloudFront deployment requires two web ACLs**. A web ACL associated with a CloudFront distribution is `CLOUDFRONT`-scoped, lives in `us-east-1`, and — per AWS WAF — cannot be associated with any other resource type. API Gateway and ALB require a `REGIONAL`-scoped web ACL in the deployment Region. This holds even when the deployment Region is `us-east-1`: the CloudFront ACL and the regional ACL are different scopes, so a single ACL cannot cover both the distribution and the API Gateway. Both web ACLs are built from the same `wafPolicyConfig.json` policy, so their rule sets are identical.

The two web ACLs are created as separate CloudFormation stacks. The regional stack is named `{name}-waf-{baseStackName}` when CloudFront is disabled, or `{name}-waf-regional-{baseStackName}` when CloudFront is enabled; the CloudFront stack (when present) is named `{name}-waf-{baseStackName}` and deployed to `us-east-1`.

#### WAF rule policy (`config/policy/wafPolicyConfig.json`)

When `app.useWaf` is enabled, the rules attached to the web ACL(s) are defined by the file `infra/config/policy/wafPolicyConfig.json`. This keeps the firewall posture in a dedicated policy file, separate from the main `config.json`, alongside the S3 bucket-policy and IAM-role customization files. The same policy file is applied to both the regional and CloudFront web ACLs.

The file has two sections:

| Section             | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `managedRuleGroups` | AWS or third-party managed rule groups to attach. Each entry sets `name`, `vendorName`, `managedRuleGroupName`, `priority`, and `block` (`true` applies the group's own block actions; `false` runs the group in count-only mode). An optional `ruleActionOverrides` array changes individual rules inside the group without disabling the whole group: each override sets `name` (a rule within the group, for example `SizeRestrictions_BODY`) and `action` (`count`, `allow`, or `block`). This runs a single rule in `count` mode while every other rule in the group still blocks. |
| `rateBasedRules`    | Rate-based rules for L7 DDoS and brute-force throttling. Each entry sets `name`, `priority`, `limit` (requests per 5-minute window per aggregate key), and `aggregateKeyType` (`IP` or `FORWARDED_IP`). When `aggregateKeyType` is `FORWARDED_IP`, an optional `forwardedIPConfig` sets the `headerName` (default `X-Forwarded-For`) and `fallbackBehavior` (`MATCH` or `NO_MATCH`, default `NO_MATCH`) used to read the true client IP. An optional `blockResponseCode` (default `429`) sets the HTTP status returned when the rule blocks.                                            |

The shipped file applies the AWS Common Rule Set, Known Bad Inputs, and Amazon IP Reputation List in block mode, plus a rate-based rule limiting each client to 10,000 requests per 5-minute window. Within the AWS Common Rule Set, two rules are overridden to `count` (through `ruleActionOverrides`) while every other rule continues to block:

-   **`SizeRestrictions_BODY`** — so that large request bodies, such as the multi-part upload initialize and complete requests up to the Amazon API Gateway REST API maximum payload of 10 MB, are observed rather than blocked. This is the only Common Rule Set rule that acts on body size alone; the remaining body-inspecting rules match on attack signatures, not size, so leaving them in block mode does not affect large payloads.
-   **`SizeRestrictions_QUERYSTRING`** — so that requests with a long query string are observed rather than blocked. The rule blocks any query string over 2048 bytes. The SuperSplat viewer loads a file by passing a presigned Amazon S3 URL in a `?load=` parameter, and a presigned URL that carries a session security token exceeds that threshold, so the request for the viewer page is rejected with an HTTP 403 before the file is ever fetched. The web ACL default action remains `allow`, so only requests matching a rule are blocked or counted.

The rate-based rule aggregates on `FORWARDED_IP` (the `X-Forwarded-For` client IP) so it counts each real end-user rather than a shared upstream address — important when VAMS is fronted by Amazon CloudFront or an Application Load Balancer, or when many users reach the deployment through a shared corporate NAT gateway or VPN egress IP. The same policy applies to both the CloudFront-scoped and regional web ACLs. VAMS is chatty per active user (the executions board polls for live status, uploads issue multi-part requests, and viewers stream large files), so the limit is set well above a single user's normal request rate while still stopping request floods. When the rule blocks, it returns HTTP `429 Too Many Requests` with a small JSON body — the correct throttle status, distinct from the `403` returned for an authorization denial — so clients can recognize throttling and retry. The VAMS web application and the VAMS CLI both treat a `429` as a transient, retryable condition: they honor the `Retry-After` header and retry with backoff rather than surfacing it as an authentication or permission failure.

If the file is empty or absent, VAMS applies its baseline rule set: a single AWS Common Rule Set in count-only mode. Populate the file to enable enforced protection.

:::tip[Validate before enabling block mode]
Managed rule groups can match legitimate traffic (for example, large multipart uploads or presigned-URL flows). Set a rule group's `block` to `false` to observe its matches in Amazon CloudWatch first, then switch to `true` once you confirm normal VAMS traffic is not caught, adding scoped rule exclusions for any false positives. When only a single rule is the source of false positives, prefer a `ruleActionOverrides` entry that sets that rule's `action` to `count` over dropping the whole group to count mode — this is how the shipped policy handles `SizeRestrictions_BODY`, allowing multi-part upload bodies up to the Amazon API Gateway REST 10 MB limit while the rest of the Common Rule Set keeps blocking.
:::

### KMS encryption (`app.useKmsCmkEncryption`)

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts` (`StorageResourcesBuilderNestedStack`) — provisions (or imports) the AWS KMS CMK and applies it to all Amazon S3, DynamoDB, SQS, SNS, and OpenSearch resources.
:::

| Field                                            | Type    | Default | Description                                                                                                                                                                                |
| ------------------------------------------------ | ------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app.useKmsCmkEncryption.enabled`                | boolean | `false` | Enables a customer-managed AWS KMS key for encryption at rest. The key is auto-generated and applied to Amazon S3, Amazon DynamoDB, Amazon SQS, Amazon SNS, and Amazon OpenSearch Service. |
| `app.useKmsCmkEncryption.optionalExternalCmkArn` | string  | `null`  | ARN of an existing customer-managed KMS key to import instead of generating a new one. The key must be in the same Region as the deployment.                                               |

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

:::info[Implemented by]
GovCloud is a cross-cutting switch, not a dedicated nested stack. It is validated in `getConfig()` (`infra/config/config.ts`) and applied as feature flags and partition selection in the root stack `infra/lib/core-stack.ts` (`CoreVAMSStack`), which in turn constrains the VPC, web distribution, and Location Service stacks.
:::

| Field                       | Type    | Default | Description                                                                                                                                        |
| --------------------------- | ------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.govCloud.enabled`      | boolean | `false` | Enables restricted-partition deployment mode. Enforces: VPC must be enabled, Amazon CloudFront must be disabled, Amazon Location Service must be disabled, and AWS Deadline Cloud must be disabled. **Required to be `true`** when the deployment Region belongs to the AWS GovCloud (`aws-us-gov`), AWS European Sovereign Cloud (`aws-eusc`), or an ISO (`aws-iso*`) partition; configuration validation rejects the deployment otherwise. The flag also gates the resource-level adjustments those partitions require, such as removing tags from Amazon EventBridge event source mappings, so leaving it `false` produces resources the partition rejects during stack creation. |
| `app.govCloud.il6Compliant` | boolean | `false` | Applies the additional DoD Impact Level 6 control set: Amazon Cognito must be disabled (`app.authProvider.useCognito.enabled = false`), AWS WAF must be disabled (`app.useWaf = false`), and customer managed KMS encryption must be enabled (`app.useKmsCmkEncryption.enabled = true`). **Required to be `true`** when the deployment Region belongs to an ISO (`aws-iso*`) partition. Optional in the AWS GovCloud and AWS European Sovereign Cloud partitions.                                                                                                                                                                                                          |

:::note[AWS European Sovereign Cloud]
For now, also set `app.govCloud.enabled = true` when deploying to the AWS European Sovereign Cloud (Region `eusc-de-east-1`). The European Sovereign Cloud is a separate, isolated partition (`aws-eusc`) with the same constraints that the GovCloud mode already enforces — VPC required, no Amazon CloudFront (use the ALB web deployment), and no Amazon Location Service — so the existing GovCloud guardrails apply. A dedicated EU Sovereign Cloud deployment mode may be introduced in a future release; until then, the GovCloud flag is the supported way to enable these constraints, and the [`config.template.eusovereign.json`](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/config/config.template.eusovereign.json) template sets it accordingly.
:::

### IAM role customization (`app.iamRoleConfig`)

These options support environments that restrict or centrally manage AWS Identity and Access Management (IAM) role creation. Both default to `false`, and when both are `false` VAMS manages all IAM roles itself (the recommended default). Each flag toggles whether VAMS reads its corresponding mappings from the separate `infra/config/policy/iamRoleConfig.json` file, keeping the long role ARNs and construct-path maps out of the main configuration.

| Field                                       | Type    | Default | Description                                                                                                                                                                                  |
| ------------------------------------------- | ------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.iamRoleConfig.useCustomBootstrapRoles` | boolean | `false` | When `true`, VAMS configures the CDK stack synthesizer from the `bootstrap` section of `iamRoleConfig.json` to use pre-created CDK bootstrap roles (or no bootstrap roles at all).           |
| `app.iamRoleConfig.useCustomVamsStackRoles` | boolean | `false` | When `true`, VAMS applies `iam.Role.customizeRoles` using the `vamsStacks` section of `iamRoleConfig.json` to generate an IAM policy report and/or substitute pre-created application roles. |

:::warning[Advanced configuration]
Letting VAMS manage IAM roles is the recommended default — grants stay automatically in sync with the resources they protect. Use these options only in environments where IAM role creation is centralized or restricted. See [Advanced IAM role customization](#advanced-iam-role-customization-appiamroleconfig) for the full workflow, the structure of `iamRoleConfig.json`, and how the settings apply across the VAMS WAF stack, core stack, and nested stacks.
:::

## VPC (`app.useGlobalVpc`)

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts` (`VPCBuilderNestedStack`) — Amazon VPC, subnets, VPC interface/gateway endpoints, and the shared security group.
:::

| Field                                                | Type    | Default       | Description                                                                                                                                                                    |
| ---------------------------------------------------- | ------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app.useGlobalVpc.enabled`                           | boolean | `false`       | Creates or imports a VPC for VAMS resources. Automatically set to `true` when ALB, OpenSearch Provisioned, or any container-based pipeline is enabled.                         |
| `app.useGlobalVpc.useForAllLambdas`                  | boolean | `false`       | Deploys all AWS Lambda functions inside the VPC and creates required VPC interface endpoints. Recommended only for FedRAMP or external component VPC-only access requirements. |
| `app.useGlobalVpc.addVpcEndpoints`                   | boolean | `true`        | Creates all required VPC endpoints on the VPC (new or imported). Set to `false` if your imported VPC already has the necessary endpoints.                                      |
| `app.useGlobalVpc.optionalExternalVpcId`             | string  | `null`        | ID of an existing VPC to import (for example, `vpc-0123456789abcdef0`). When set, overrides internal VPC creation. Requires isolated subnet IDs to be provided.                |
| `app.useGlobalVpc.optionalExternalIsolatedSubnetIds` | string  | `null`        | Comma-delimited list of isolated subnet IDs in the imported VPC. **Required** when using an external VPC.                                                                      |
| `app.useGlobalVpc.optionalExternalPrivateSubnetIds`  | string  | `null`        | Comma-delimited list of private subnet IDs. Required when using RapidPipeline or ModelOps with an imported VPC.                                                                |
| `app.useGlobalVpc.optionalExternalPublicSubnetIds`   | string  | `null`        | Comma-delimited list of public subnet IDs. Required when using ALB with public subnets or RapidPipeline/ModelOps with an imported VPC.                                         |
| `app.useGlobalVpc.vpcCidrRange`                      | string  | `10.1.0.0/16` | CIDR range for the VAMS-created VPC. Ignored when importing an external VPC.                                                                                                   |

:::warning[Subnet requirements]
Each subnet must reside in its own Availability Zone. Minimum Availability Zone requirements: 3 for OpenSearch Provisioned, 2 for ALB or EKS pipelines, 1 for all other configurations.
:::

### VPC Resource Usage by Feature

The following table shows which VPC resources are created based on enabled features and pipelines.

#### Subnet Requirements

VAMS provisions every subnet type across a fixed Availability Zone count (a baseline of 2) so that toggling individual features does not add or remove subnets between deployments. Amazon OpenSearch Service (Provisioned) sets the count from `availabilityZoneCount` (2 or 3).

| Feature / Pipeline                                        | Private Subnets            | Public Subnets | Min AZs                          | Notes                           |
| --------------------------------------------------------- | -------------------------- | -------------- | -------------------------------- | ------------------------------- |
| ALB (`useAlb.enabled`)                                    | Yes (if `usePublicSubnet`) | Yes            | 2                                | Public subnets for ALB          |
| RapidPipeline ECS (`useRapidPipeline.useEcs`)             | Yes                        | Yes            | 2                                | Batch compute                   |
| RapidPipeline EKS (`useRapidPipeline.useEks`)             | Yes                        | Yes            | 2                                | EKS cluster                     |
| ModelOps (`useModelOps`)                                  | Yes                        | Yes            | 2                                | Batch compute                   |
| Gaussian Splatting (`useSplatToolbox`)                    | Yes                        | Yes            | 2                                | Batch compute + CodeBuild       |
| Coordinate Transform (`useConversionCoordinateTransform`) | Yes                        | Yes            | 2                                | Batch compute                   |
| Isaac Lab Training (`useIsaacLabTraining`)                | Yes                        | Yes            | 2                                | Batch compute + CodeBuild       |
| NVIDIA Cosmos (`useNvidiaCosmos`)                         | Yes                        | Yes            | 2                                | Batch compute + EFS + CodeBuild |
| NVIDIA Gr00t (`useNvidiaGr00t`)                           | Yes                        | Yes            | 2                                | Batch compute + EFS + CodeBuild |
| OpenSearch Provisioned (`openSearch.useProvisioned`)      | No                         | No             | `availabilityZoneCount` (2 or 3) | Zone-aware Multi-AZ domain      |
| All other features                                        | Isolated only              | No             | 2                                | Lambda VPC endpoints            |

#### VPC Interface Endpoints

| Endpoint        | Created When                                                        | Subnet Type                     |
| --------------- | ------------------------------------------------------------------- | ------------------------------- |
| API Gateway     | `addVpcEndpoints=true`                                              | Isolated                        |
| SSM             | `addVpcEndpoints=true`                                              | Isolated                        |
| Lambda          | `addVpcEndpoints=true`                                              | Isolated                        |
| STS             | `addVpcEndpoints=true`                                              | Isolated                        |
| CloudWatch Logs | `addVpcEndpoints=true`                                              | Isolated                        |
| Step Functions  | `addVpcEndpoints=true`                                              | Isolated                        |
| SNS             | `addVpcEndpoints=true`                                              | Isolated                        |
| SQS             | `addVpcEndpoints=true`                                              | Isolated                        |
| KMS             | `useKmsCmkEncryption.enabled=true`                                  | Isolated                        |
| KMS FIPS        | `useKmsCmkEncryption.enabled=true` + `useFips=true`                 | Isolated                        |
| AWS Batch       | Any pipeline enabled                                                | Isolated                        |
| ECR API         | Any pipeline enabled                                                | Isolated                        |
| ECR Docker      | Any pipeline enabled                                                | Isolated                        |
| EFS             | `useNvidiaCosmos.enabled=true`                                      | Isolated                        |
| ECS             | Pipelines with Batch compute                                        | Private (preferred) or Isolated |
| ECS Agent       | `useIsaacLabTraining.enabled=true`                                  | Isolated                        |
| ECS Telemetry   | `useIsaacLabTraining.enabled=true`                                  | Isolated                        |
| Bedrock Runtime | `useGenAiMetadata3dLabeling.enabled=true` + `useForAllLambdas=true` | Isolated                        |
| Rekognition     | `useGenAiMetadata3dLabeling.enabled=true` + `useForAllLambdas=true` | Isolated                        |

#### Gateway Endpoints (Always Created)

| Endpoint | Notes                                         |
| -------- | --------------------------------------------- |
| S3       | Created when `addVpcEndpoints=true` (no cost) |
| DynamoDB | Created when `addVpcEndpoints=true` (no cost) |

:::note
Only one Amazon ECS interface endpoint can exist per VPC when private DNS is enabled. VAMS consolidates ECS endpoint subnets across pipeline types, with private subnets taking priority over isolated subnets when both are needed.
:::

:::warning[Cognito MFA requires the authorizer to run outside the VPC]
VAMS does not create Amazon Cognito VPC interface endpoints. When Lambda functions run in the VPC (`useForAllLambdas=true`), the API Gateway authorizer has no path to Amazon Cognito, so VAMS disables the Cognito MFA check and `mfaRequired` on a role has no effect. The MFA check and MFA-aware role enforcement apply only when the authorizer runs outside the VPC.
:::

## Amazon OpenSearch Service (`app.openSearch`)

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/searchAndIndexing/searchBuilder-nestedStack.ts` (`SearchBuilderNestedStack`) — Amazon OpenSearch Serverless collection or a provisioned OpenSearch Service domain.
:::

| Field                                                    | Type    | Default            | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------------------------------------------------- | ------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.openSearch.useServerless.enabled`                   | boolean | `false`            | Deploys Amazon OpenSearch Serverless for pay-per-use search capability. Not available in the AWS European Sovereign Cloud, where Amazon OpenSearch Serverless is not offered — use `app.openSearch.useProvisioned` there. Configuration validation rejects the combination.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `app.openSearch.useServerless.nextGen`                   | boolean | `true`             | Sets the generation of the Serverless collection group. `true` uses the `NEXTGEN` generation, which supports scaling compute to zero (a minimum OCU of `0`); `false` uses the `CLASSIC` generation. The collection is placed in a collection group in both cases. Defaults to `true` for commercial partitions and must be `false` for AWS GovCloud and the AWS European Sovereign Cloud, where the next-generation generation is not available.                                                                                                                                                                                                                                                                                                                                      |
| `app.openSearch.useServerless.allowPublic`               | boolean | `true`             | Controls whether the Serverless collection accepts public network access. When `true`, the collection is reachable over the public internet (subject to data-access policies). When `false`, the collection is reachable only through a VPC endpoint, which requires `app.useGlobalVpc.enabled` to be `true`. As with provisioned OpenSearch, only the OpenSearch-facing Lambda functions (search and indexers) are placed in the VPC — `app.useGlobalVpc.useForAllLambdas` does **not** need to be `true`. Set to `false` for production deployments. A fully network-isolated deployment (`app.useGlobalVpc.enabled` and `app.useGlobalVpc.useForAllLambdas` both `true`) must set `allowPublic` to `false`; configuration validation rejects a public collection in that topology. |
| `app.openSearch.useServerless.enableStandbyReplicas`     | boolean | tracks `nextGen`   | Enables standby replicas on the collection group for cross-Availability-Zone redundancy. **Required** for the `NEXTGEN` generation — when `nextGen` is `true`, this must be `true` (OpenSearch Serverless rejects a `NEXTGEN` collection group with standby replicas disabled), and configuration validation enforces it. For the `CLASSIC` generation it is optional: `false` favors lower cost, `true` adds production high availability. Defaults to the value of `nextGen` (`true` for `NEXTGEN`, `false` for `CLASSIC`).                                                                                                                                                                                                                                                         |
| `app.openSearch.useServerless.minIndexingOcu`            | number  | `2`                | Minimum indexing capacity in OpenSearch Compute Units (OCU) for the collection group. Must be one of the allowed OCU values: `0`, `2`, `4`, `8`, `16`, or any multiple of `16`. A value of `0` enables scale-to-zero and is supported only when `nextGen` is `true` (the `NEXTGEN` generation).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `app.openSearch.useServerless.maxIndexingOcu`            | number  | `16`               | Maximum indexing capacity in OCU for the collection group. Must be one of the allowed OCU values: `2`, `4`, `8`, `16`, or any multiple of `16` (and at least `minIndexingOcu`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `app.openSearch.useServerless.minSearchOcu`              | number  | `2`                | Minimum search capacity in OCU for the collection group. Must be one of the allowed OCU values: `0`, `2`, `4`, `8`, `16`, or any multiple of `16`. A value of `0` enables scale-to-zero and is supported only when `nextGen` is `true` (the `NEXTGEN` generation).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `app.openSearch.useServerless.maxSearchOcu`              | number  | `16`               | Maximum search capacity in OCU for the collection group. Must be one of the allowed OCU values: `2`, `4`, `8`, `16`, or any multiple of `16` (and at least `minSearchOcu`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `app.openSearch.useServerless.deployDeferredIndexSchema` | boolean | `false`            | Used only to finish a **deferred** private next-gen setup (a deployment made with `allowPublic=false`, `nextGen=true`, and `app.useGlobalVpc.addVpcEndpoints=false`, where VAMS skipped index creation). After you manually create the `aoss-data` VPC endpoint and network policy, set this to `true` for one deployment so the schema-deploy resource creates the index mappings against the now-reachable collection, then set it back to `false`. Ignored when `addVpcEndpoints=true` (nothing is deferred). Can be overridden with CDK context `deployDeferredIndexSchema=true`. See [OpenSearch — deferred next-gen setup](../developer/opensearch.md#deferred-next-gen-setup-manual-vpc-endpoint).                                                                             |
| `app.openSearch.useProvisioned.enabled`                  | boolean | `false`            | Deploys a provisioned Amazon OpenSearch Service domain. Requires a VPC with at least `availabilityZoneCount` Availability Zones.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `app.openSearch.useProvisioned.availabilityZoneCount`    | number  | `2`                | Number of Availability Zones the zone-aware provisioned domain and its VPC subnets span (one data node per zone). Must be `2` or `3`. At `2` the domain runs Multi-AZ **without** Standby; at `3` it runs Multi-AZ **with** Standby (the asset/file indexes are created with two replicas to give the multiple-of-three copies Standby requires). Switching an existing domain to `3` in place is rejected by the service — a 3-AZ Standby domain must be created fresh (disable and re-enable OpenSearch, then reindex). Keep `2` for Regions or partitions that expose only two Availability Zones, such as the AWS European Sovereign Cloud Region `eusc-de-east-1`.                                                                                                               |
| `app.openSearch.useProvisioned.numberOfShards`           | number  | `1`                | Number of primary shards per provisioned OpenSearch index (asset and file). Must be an integer of `1` or greater. Defaults to `1`. Increase for large indexes — as a guideline, an index expected to exceed roughly 60 GB (about 3 million asset or file records for VAMS) should use more than one shard. Changing this value requires re-creating the index: disable and re-enable OpenSearch (or otherwise recreate the domain), then reindex. Existing indexes are not re-sharded in place.                                                                                                                                                                                                                                                                                       |
| `app.openSearch.useProvisioned.dataNodeInstanceType`     | string  | `r7g.large.search` | Instance type for the data nodes in the provisioned domain (one data node per Availability Zone by default).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `app.openSearch.useProvisioned.masterNodeInstanceType`   | string  | `r7g.large.search` | Instance type for the 3 dedicated master nodes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `app.openSearch.useProvisioned.ebsInstanceNodeSizeGb`    | number  | `120`              | Amazon EBS volume size in GB per data node.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `app.openSearch.reindexOnCdkDeploy`                      | boolean | `false`            | Triggers automatic reindexing of all assets and files during deployment via a CloudFormation custom resource. **Important:** Enable only for a second deployment after initial deployment or version upgrade, then set back to `false`. Can be overridden with CDK context `reindexOnCdkDeploy=true`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

:::note[Mutual exclusion]
You cannot enable both OpenSearch Serverless and OpenSearch Provisioned simultaneously. Enable at most one option, or disable both to deploy without search functionality.
:::

:::tip[Scale-to-zero and cold starts]
On next-generation Serverless, setting `minIndexingOcu` and `minSearchOcu` to `0` lets the collection scale its compute down to zero when idle, which removes the standing OCU cost of an always-on collection. The trade-off is a cold start: after roughly 10 minutes without activity, the first search or indexing request incurs an added latency of about 10–20 seconds while capacity scales back up. Keep the minimums at `1` or greater to avoid cold starts when consistent low latency matters more than cost.
:::

:::warning[Private Serverless requires a VPC across two Availability Zones]
A private collection (`allowPublic=false`) is reachable only through its VPC endpoint, which is placed across two Availability Zones for high availability. A private Serverless deployment therefore requires `app.useGlobalVpc.enabled` to be `true`, and the VPC provides at least two Availability Zones. As with provisioned OpenSearch, only the OpenSearch-facing Lambda functions (search and indexers) are placed in the VPC, so `app.useGlobalVpc.useForAllLambdas` does not need to be enabled. Configuration validation rejects a private collection when `app.useGlobalVpc.enabled` is `false`.

The VPC endpoint type is selected automatically by the collection generation, because the two generations expose different collection endpoint hostnames. A next-generation collection (`nextGen=true`) serves its endpoint on `\{collection-id\}.aoss.\{region\}.on.aws` and is reached through a standard AWS PrivateLink interface endpoint (service `com.amazonaws.\{region\}.aoss-data`) with private DNS enabled. A classic collection (`nextGen=false`) serves its endpoint on `\{collection-id\}.\{region\}.aoss.amazonaws.com` and is reached through the Amazon OpenSearch Serverless-managed VPC endpoint, which provisions its own Amazon Route 53 private hosted zone. VAMS creates the correct endpoint type for the configured generation; the in-VPC Lambda functions connect over private DNS on port 443 using SigV4 signing with service name `aoss`.

The next-generation endpoint is a standard Amazon EC2 interface endpoint, so it follows `app.useGlobalVpc.addVpcEndpoints` like every other interface endpoint: when that is `false`, VAMS does not create the endpoint or the collection's VPC network access policy, and you must create them manually after deployment (the classic managed endpoint is not governed by this flag and is always created). See [OpenSearch — deferred next-gen setup](../developer/opensearch.md#deferred-next-gen-setup-manual-vpc-endpoint) for the manual setup procedure.
:::

:::note[OpenSearch engine version by partition]
Provisioned domains deploy the OpenSearch engine version pinned in `config.ts`. Commercial AWS, AWS GovCloud, and other partitions use the standard version (`OPENSEARCH_VERSION`, currently OpenSearch 3.x). The **AWS European Sovereign Cloud** (partition `aws-eusc`, Region `eusc-de-east-1`) does not yet support OpenSearch 3.x, so VAMS automatically deploys `OPENSEARCH_VERSION_EUSOVEREIGN` (OpenSearch 2.x) there instead. The selection is partition-based and requires no configuration.
:::

:::tip[OpenSearch Provisioned service-linked role]
A provisioned domain in a VPC requires the `AWSServiceRoleForAmazonOpenSearchService` service-linked role to exist in the account. AWS normally creates it automatically, but in some accounts it is missing, which fails the deploy with _"Before you can proceed, you must enable a service-linked role"_. VAMS now creates this role idempotently during deployment (it is created if missing and left unchanged if it already exists), so this error should no longer occur. The role is account-wide and is not removed on stack teardown. See [Common deployment errors](deploy-the-solution.md#common-deployment-errors) for additional troubleshooting.
:::

:::warning[OpenSearch Provisioned is for advanced deployments]
Amazon OpenSearch Serverless is the recommended option for most VAMS deployments. The provisioned option is intended for advanced deployments that require dedicated capacity, custom instance sizing, or features unsupported by Serverless, and it introduces several operational considerations that can disrupt AWS CloudFormation stack deployments:

-   **VPC requirement** -- A VPC with at least 3 Availability Zones must already exist or be created by the same deploy.
-   **Fragile AWS CloudFormation updates** -- Domain configuration changes (instance type, EBS size, engine version) trigger blue/green updates that can take 30+ minutes and occasionally exceed the AWS CloudFormation custom-resource timeout. Major engine-version upgrades (for example 2.7 to 3.5 in v2.6) sometimes fail in place and require redeploying with OpenSearch disabled, then re-enabling, before the upgrade succeeds.
-   **Service-linked role** -- A provisioned domain in a VPC requires the OpenSearch Service service-linked role. VAMS creates it idempotently during deployment (created if missing, left unchanged if present), so the _"you must enable a service-linked role"_ error should no longer require a manual retry.
-   **Reindex required after index-name or schema bumps** -- Provisioned domains do not auto-populate new indexes; you must run the version-specific data migration script to repopulate them. See [Update the solution](update-the-solution.md) and the migration READMEs under `infra/deploymentDataMigration/`.

If you do not have a specific requirement that mandates Provisioned, prefer `app.openSearch.useServerless.enabled = true`.
:::

## Amazon Location Service (`app.useLocationService`)

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/locationService/location-service-nestedStack.ts` (`LocationServiceNestedStack`) — Amazon Location Service map resources (commercial partitions only).
:::

| Field                            | Type    | Default | Description                                                                                                                                                                     |
| -------------------------------- | ------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.useLocationService.enabled` | boolean | `true`  | Enables Amazon Location Service for map visualization of asset metadata with geographic coordinates. Not available in AWS GovCloud. Map views require OpenSearch to be enabled. |

## Web distribution

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/staticWebApp/staticWebBuilder-nestedStack.ts` (`StaticWebBuilderNestedStack`) — an Amazon S3 web bucket fronted by either Amazon CloudFront (`useCloudFront`) or an Application Load Balancer (`useAlb`). These two options are mutually exclusive.
:::

### Application Load Balancer (`app.useAlb`)

| Field                                   | Type    | Default                       | Description                                                                                                                                                  |
| --------------------------------------- | ------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app.useAlb.enabled`                    | boolean | `false`                       | Enables ALB-based static website hosting. Required for AWS GovCloud deployments. Cannot be enabled simultaneously with Amazon CloudFront.                    |
| `app.useAlb.usePublicSubnet`            | boolean | `false`                       | Places the ALB in public subnets. **Warning:** This exposes the web application to the public internet.                                                      |
| `app.useAlb.addAlbS3SpecialVpcEndpoint` | boolean | `true`                        | Creates the Amazon S3 VPC interface endpoint required by the ALB to serve static web files. Set to `false` only if this endpoint already exists in your VPC. |
| `app.useAlb.domainHost`                 | string  | _(required when ALB enabled)_ | Domain name for the ALB and static website Amazon S3 bucket (for example, `vams.example.com`).                                                               |
| `app.useAlb.certificateArn`             | string  | _(required when ALB enabled)_ | ARN of the ACM certificate for HTTPS. Must be in the same Region as the deployment.                                                                          |
| `app.useAlb.optionalHostedZoneId`       | string  | `null`                        | Amazon Route 53 hosted zone ID for automatic DNS alias creation. If not provided, configure DNS manually.                                                    |

### Amazon CloudFront (`app.useCloudFront`)

| Field                                                 | Type    | Default | Description                                                                                                                               |
| ----------------------------------------------------- | ------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `app.useCloudFront.enabled`                           | boolean | `true`  | Enables Amazon CloudFront for static website distribution. Not available in AWS GovCloud. Cannot be enabled simultaneously with ALB.      |
| `app.useCloudFront.customDomain.enabled`              | boolean | `false` | Enables a custom domain name for the CloudFront distribution. When disabled, CloudFront uses an auto-generated `*.cloudfront.net` domain. |
| `app.useCloudFront.customDomain.domainHost`           | string  | `""`    | Custom domain name (for example, `vams.example.com`). Must match the ACM certificate. Required when custom domain is enabled.             |
| `app.useCloudFront.customDomain.certificateArn`       | string  | `""`    | ACM certificate ARN. **Must be in `us-east-1`** regardless of the VAMS deployment Region. Required when custom domain is enabled.         |
| `app.useCloudFront.customDomain.optionalHostedZoneId` | string  | `""`    | Amazon Route 53 hosted zone ID for automatic A-record alias creation. If not provided, configure DNS manually.                            |

:::danger[CloudFront certificate Region]
Amazon CloudFront requires the ACM certificate to be in `us-east-1`. Using a certificate in any other Region causes a deployment failure.
:::

## Authentication (`app.authProvider`)

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/auth/authBuilder-nestedStack.ts` (`AuthBuilderNestedStack`) — Amazon Cognito user and identity pools, SAML federation, and external OAuth IdP wiring. IP-range restrictions (`authorizerOptions.allowedIpRanges`) are enforced by the custom Lambda authorizer in `apiLambda/apigatewayv2-amplify-nestedStack.ts`.
:::

### General authentication settings

| Field                                         | Type   | Default | Description                                                                                                  |
| --------------------------------------------- | ------ | ------- | ------------------------------------------------------------------------------------------------------------ |
| `app.authProvider.presignedUrlTimeoutSeconds` | number | `86400` | Timeout in seconds for Amazon S3 presigned URLs used for upload and download operations (default: 24 hours). |

### IP range restrictions (`app.authProvider.authorizerOptions`)

| Field                                                | Type  | Default | Description                                                                                                                                |
| ---------------------------------------------------- | ----- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `app.authProvider.authorizerOptions.allowedIpRanges` | array | `[]`    | Array of IP range pairs for restricting API access. Each range is a 2-element array: `["min_ip", "max_ip"]`. Leave empty to allow all IPs. |

**Example:**

```json
"allowedIpRanges": [
    ["192.168.1.1", "192.168.1.255"],
    ["10.0.0.1", "10.0.0.255"]
]
```

### Amazon Cognito (`app.authProvider.useCognito`)

| Field                                                 | Type    | Default | Description                                                                                                                                                      |
| ----------------------------------------------------- | ------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.authProvider.useCognito.enabled`                 | boolean | `true`  | Enables Amazon Cognito user pools for authentication. At least one authentication provider must be enabled.                                                      |
| `app.authProvider.useCognito.useSaml`                 | boolean | `false` | Enables SAML federation with an external IdP through Amazon Cognito.                                                                                             |
| `app.authProvider.useCognito.useUserPasswordAuthFlow` | boolean | `false` | Enables `USER_PASSWORD_AUTH` flow for non-SRP authentication. Generates a security warning. Use only when SRP libraries are unavailable for system integrations. |
| `app.authProvider.useCognito.credTokenTimeoutSeconds` | number  | `3600`  | Authentication token timeout in seconds for Amazon Cognito issued tokens (default: 1 hour). Refresh token is fixed at 24 hours.                                  |

### External OAuth IdP (`app.authProvider.useExternalOAuthIdp`)

| Field                                                                       | Type    | Default | Description                                                                                                          |
| --------------------------------------------------------------------------- | ------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| `app.authProvider.useExternalOAuthIdp.enabled`                              | boolean | `false` | Enables an external OAuth 2.0 / OpenID Connect identity provider. Cannot be used simultaneously with Amazon Cognito. |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl`                   | string  | `null`  | Base URL of the external OAuth IdP (for example, `https://ping-federate.example.com`).                               |
| `app.authProvider.useExternalOAuthIdp.idpAuthClientId`                      | string  | `null`  | Client ID registered with the external IdP for this VAMS deployment.                                                 |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderScope`                 | string  | `null`  | OAuth scope requested by VAMS.                                                                                       |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderScopeMfa`              | string  | `null`  | MFA scope attribute appended to the base scope. Set to enable MFA enforcement.                                       |
| `app.authProvider.useExternalOAuthIdp.idpAuthPrincipalDomain`               | string  | `null`  | Principal domain for the IdP endpoint (for example, `ping-federate.example.com`).                                    |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderTokenEndpoint`         | string  | `null`  | Token endpoint path (for example, `/as/token.oauth2`).                                                               |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderAuthorizationEndpoint` | string  | `null`  | Authorization endpoint path (for example, `/as/authorization.oauth2`).                                               |
| `app.authProvider.useExternalOAuthIdp.idpAuthProviderDiscoveryEndpoint`     | string  | `null`  | Discovery endpoint path (for example, `/.well-known/openid-configuration`).                                          |
| `app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl`         | string  | `null`  | JWT issuer URL for the custom Lambda authorizer to validate tokens.                                                  |
| `app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience`          | string  | `null`  | JWT audience claim for token verification.                                                                           |

:::warning[All fields required]
When external OAuth IdP is enabled, **all** fields in this section are required. Deployment will fail if any field is null or empty.
:::

## API configuration (`app.api`)

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/apiLambda/api-nestedStack.ts` (`ApiNestedStack`) — builds the Amazon API Gateway REST API through `RestApiGatewayConstruct`, including the endpoint type (`REGIONAL`/`PRIVATE`) and stage throttling (rate and burst limits).
:::

| Field                                                      | Type   | Default             | Description                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------------------------------------- | ------ | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.api.apiType`                                          | string | `"APIGATEWAY_REST"` | Backend API implementation type. Only `"APIGATEWAY_REST"` (an Amazon API Gateway REST API) is supported; any other value fails configuration validation.                                                                                                                                                                                                                                                                           |
| `app.api.apiGatewayRest.endpointType`                      | string | `"REGIONAL"`        | API Gateway endpoint type. `"REGIONAL"` creates a public regional REST API (default) that does not route through any VPC endpoint. `"PRIVATE"` creates a private REST API reachable only through an execute-api VPC interface endpoint; it requires `useGlobalVpc.enabled` and either `useGlobalVpc.addVpcEndpoints = true` or `optionalExternalPrivateApigVPCEId` set, and is incompatible with Amazon CloudFront (requires ALB). |
| `app.api.apiGatewayRest.globalRateLimit`                   | number | `50`                | Global rate limit in requests per second for the Amazon API Gateway. Must be a positive number.                                                                                                                                                                                                                                                                                                                                    |
| `app.api.apiGatewayRest.globalBurstLimit`                  | number | `100`               | Global burst limit for the Amazon API Gateway. Must be greater than or equal to `globalRateLimit`.                                                                                                                                                                                                                                                                                                                                 |
| `app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId` | string | `""`                | Id of a pre-existing execute-api interface VPC endpoint to use for a `"PRIVATE"` endpoint when VAMS does not create one (`useGlobalVpc.addVpcEndpoints = false`). Applies only to `"PRIVATE"`; it is ignored (with a configuration warning) for a `"REGIONAL"` endpoint.                                                                                                                                                           |
| `app.api.apiGatewayRest.apiGatewayTimeoutTime`             | number | `29`                | Integration timeout in seconds — how long Amazon API Gateway waits for a backend Lambda function to respond before returning a `504`. Must be a whole number between `29` and `300`. Applies to every API route, for both `"REGIONAL"` and `"PRIVATE"` endpoint types. Values above `29` require an approved account-level quota increase first (see the warning below).                                                           |

:::warning[PRIVATE endpoint requirements]
Setting `app.api.apiGatewayRest.endpointType` to `"PRIVATE"` requires `useGlobalVpc.enabled = true` and an execute-api interface VPC endpoint: either set `useGlobalVpc.addVpcEndpoints = true` so VAMS creates one, or set `app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId` to an existing endpoint id. A `PRIVATE` endpoint is incompatible with Amazon CloudFront (which cannot reach a private API); you must front it with the ALB (`useCloudFront.enabled = false`, `useAlb.enabled = true`), and that ALB must run in isolated (non-public) subnets (`useAlb.usePublicSubnet = false`). A public-subnet ALB would expose an internet-facing path to the private API, defeating its isolation. Configuration validation enforces all of these.
:::

:::warning[Raising the integration timeout requires an AWS quota increase first]
`app.api.apiGatewayRest.apiGatewayTimeoutTime` defaults to `29` seconds, the Amazon API Gateway default integration timeout. Setting it higher requires an approved increase to the account-level **Integration timeout** quota (`L-E5AE38E3`) in the deployment Region, requested through the AWS Service Quotas console or AWS Support. Request and receive the increase **before** deploying with a higher value — Amazon API Gateway rejects an integration timeout above the account's approved quota, which fails the deployment.

The increase applies to both `"REGIONAL"` and `"PRIVATE"` endpoint types, which are the two types VAMS supports. Raising this quota may require a compensating reduction in the Region-level request throttle quota for the account, so review both quotas together. A configuration warning is emitted at synthesis time whenever the value exceeds `29` seconds as a reminder.

A longer timeout lets operations on assets with many files or many relationships complete within a single synchronous request instead of returning a `504` while the Lambda function continues working in the background. The AWS Lambda function timeout (15 minutes) remains the outer bound, so a value above the `300`-second maximum would not extend the useful window for a synchronous request.
:::

:::note[Execute-API VPC endpoint]
A `REGIONAL` endpoint is public and does not route through a VPC endpoint, even when a VPC and its endpoints are enabled. Only a `PRIVATE` endpoint uses the execute-api interface VPC endpoint: VAMS creates it when `useGlobalVpc.addVpcEndpoints = true`, otherwise supply an existing one through `app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId`.
:::

:::warning[Switching `endpointType` between `PRIVATE` and `REGIONAL` on an existing deployment]
Changing `app.api.apiGatewayRest.endpointType` on a deployment that already exists is fully supported. A `PRIVATE` endpoint carries an API Gateway resource policy that only allows invocation through the execute-api VPC interface endpoint (an `aws:SourceVpce` condition); a `REGIONAL` endpoint uses a public allow-all resource policy. VAMS sets the correct resource policy for each endpoint type on every deployment, so switching in either direction updates the policy — no manual action is required.

Amazon API Gateway itself does **not** remove a previously-set resource policy when an update simply stops supplying one, which is why VAMS always writes an explicit policy: switching `PRIVATE` → `REGIONAL` overwrites the `aws:SourceVpce`-restricted policy with the public allow-all policy, and `REGIONAL` → `PRIVATE` re-applies the VPC-endpoint restriction. If a resource policy left over from an out-of-band change ever remains in place after a switch (for example, a `PRIVATE` policy on a now-public endpoint), every request — including the CORS preflight — is denied at the resource-policy layer with `403 AccessDeniedException` ("no resource-based policy allows the execute-api:Invoke action"). Because that denial precedes the CORS response, a browser reports it as a missing `Access-Control-Allow-Origin` / failed-preflight error rather than an authorization error. Re-running the VAMS deployment restores the correct policy for the configured `endpointType`.
:::

:::note[REST API TLS security policy]
VAMS sets the minimum TLS version and cipher suite on the REST API itself, so it applies to the default `execute-api` endpoint. The policy is derived from the deployment configuration and is not a separate configuration option.

| Deployment                                               | Security policy                          | TLS versions accepted |
| -------------------------------------------------------- | ---------------------------------------- | --------------------- |
| Commercial                                               | `SecurityPolicy_TLS13_1_2_2021_06`       | TLS 1.3, TLS 1.2      |
| GovCloud and EU Sovereign Cloud (`app.govCloud.enabled`) | Partition and Region default (unchanged) | TLS 1.3, TLS 1.2      |

In the commercial partition, a Regional REST API would otherwise default to the `TLS_1_0` policy, which accepts TLS 1.0 and TLS 1.1. VAMS raises the floor to `SecurityPolicy_TLS13_1_2_2021_06` and sets the required endpoint access mode to `BASIC`, so the Amazon CloudFront origin request, the ALB redirect to `execute-api`, and direct `execute-api` access all continue to work. A TLS 1.3-only policy is not used because CloudFront negotiates at most TLS 1.2 to a custom origin.

The GovCloud mode, which AWS European Sovereign Cloud deployments also enable, leaves the policy unset so the API keeps its partition and Region default. Those partitions do not offer the `TLS_1_0` policy for Regional APIs and their APIs are FIPS-compliant by default, so the minimum version is already TLS 1.2.

A security policy change takes about 15 minutes to propagate, and the API stays invocable while its status is `UPDATING`. See [Security](../architecture/security.md) for the full description.
:::

## Web UI (`app.webUi`)

:::note[Implemented by]
Consumed by the static web hosting stack `infra/lib/nestedStacks/staticWebApp/staticWebBuilder-nestedStack.ts` (`StaticWebBuilderNestedStack`). `allowUnsafeEvalFeatures` feeds Content Security Policy generation in `infra/lib/helper/security.ts`.
:::

| Field                                 | Type    | Default | Description                                                                                                                                                                                                          |
| ------------------------------------- | ------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.webUi.optionalBannerHtmlMessage` | string  | `""`    | Optional HTML message displayed as a banner in the web interface. Use for system notifications or compliance messages (for example, `"AWS Sandbox System. Do not upload sensitive information."`).                   |
| `app.webUi.allowUnsafeEvalFeatures`   | boolean | `false` | Allows `unsafe-eval` in the Content Security Policy for script execution. Required for certain viewer plugins (Needle USD, SuperSplat Editor, ThatOpen IFC BIM, and the Three.js CAD formats). Consult your security team before enabling. |

## Metadata schema (`app.metadataSchema`)

Controls auto-loading of default metadata schemas during deployment.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` (`ApiBuilderNestedStack`) — a default-schema seeding custom resource that writes to the metadata-schema DynamoDB table.
:::

| Field                                                | Type    | Default | Description                                                                                                                                                                         |
| ---------------------------------------------------- | ------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.metadataSchema.autoLoadDefaultAssetLinksSchema` | boolean | `true`  | Creates a GLOBAL schema named `defaultAssetLinks` with Translation (XYZ), Rotation (WXYZ), Scale (XYZ), and Matrix (MATRIX4X4) fields for spatial relationship metadata.            |
| `app.metadataSchema.autoLoadDefaultDatabaseSchema`   | boolean | `true`  | Creates a GLOBAL schema named `defaultDatabase` with a Location field (LLA - Latitude/Longitude/Altitude).                                                                          |
| `app.metadataSchema.autoLoadDefaultAssetSchema`      | boolean | `true`  | Creates a GLOBAL schema named `defaultAsset` with a Location field (LLA - Latitude/Longitude/Altitude).                                                                             |
| `app.metadataSchema.autoLoadDefaultAssetFileSchema`  | boolean | `true`  | Creates a GLOBAL schema named `defaultAssetFile3dModel` with a `Polygon_Count` field and file type restrictions for common 3D formats (.glb, .usd, .obj, .fbx, .gltf, .stl, .usdz). |

## Processing pipelines (`app.pipelines`)

:::note[Implemented by]
All pipelines are orchestrated by `infra/lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts` (`PipelineBuilderNestedStack`). Each enabled pipeline below is conditionally instantiated as its own child nested stack (named in each section).
:::

The **Default** column in the pipeline tables is the value the shipped `config.template.*.json` files carry. When a pipeline block is present but omits a field, `getConfig()` fills in a fallback that may be more conservative than the template: `enabled` falls back to `false` (`useConversion3dBasic.enabled` falls back to `true`), `autoRegisterWithVAMS` falls back to `true`, and `autoRegisterAutoTriggerOnFileUpload` falls back to `false` so an omitted key never arms an upload trigger. A pipeline block that is absent entirely is disabled, so its remaining fields have no effect.

### 3D basic conversion (`app.pipelines.useConversion3dBasic`)

Converts between STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, and XYZ formats. Does not require a VPC.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/conversion/3dBasic/conversion3dBasicBuilder-nestedStack.ts` (`Conversion3dBasicNestedStack`) — AWS Batch on Fargate.
:::

| Field                                                     | Type    | Default | Description                                                                               |
| --------------------------------------------------------- | ------- | ------- | ----------------------------------------------------------------------------------------- |
| `app.pipelines.useConversion3dBasic.enabled`              | boolean | `true`  | Enables the 3D basic conversion pipeline.                                                 |
| `app.pipelines.useConversion3dBasic.autoRegisterWithVAMS` | boolean | `true`  | Automatically registers the pipeline and workflow in the VAMS database during deployment. |

### CAD/mesh metadata extraction (`app.pipelines.useConversionCadMeshMetadataExtraction`)

Extracts metadata from CAD and mesh file formats. Does not require a VPC.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/conversion/meshCadMetadataExtraction/conversionMeshCadMetadataExtractionBuilder-nestedStack.ts` (`ConversionMeshCadMetadataExtractionNestedStack`).
:::

| Field                                                                                      | Type    | Default | Description                                                                        |
| ------------------------------------------------------------------------------------------ | ------- | ------- | ---------------------------------------------------------------------------------- |
| `app.pipelines.useConversionCadMeshMetadataExtraction.enabled`                             | boolean | `false` | Enables the CAD/mesh metadata extraction pipeline.                                 |
| `app.pipelines.useConversionCadMeshMetadataExtraction.autoRegisterWithVAMS`                | boolean | `true`  | Automatically registers the pipeline during deployment.                            |
| `app.pipelines.useConversionCadMeshMetadataExtraction.autoRegisterAutoTriggerOnFileUpload` | boolean | `true`  | Automatically triggers the pipeline on file uploads matching supported file types. |

### Point cloud coordinate transform (`app.pipelines.useConversionCoordinateTransform`)

Reprojects E57, LAS, LAZ, and PLY point cloud files between coordinate reference systems using PDAL and pyproj. Runs on AWS Batch with AWS Fargate compute. **Requires VPC.** See the [Coordinate Transform pipeline](../pipelines/coordinate-transform.md) page for input parameters and per-asset metadata overrides.

| Field                                                                                | Type    | Default | Description                                                                                                                                                                            |
| ------------------------------------------------------------------------------------ | ------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useConversionCoordinateTransform.enabled`                             | boolean | `false` | Enables the point cloud coordinate transform pipeline.                                                                                                                                 |
| `app.pipelines.useConversionCoordinateTransform.useCodeBuild`                        | boolean | `false` | Builds the container image with AWS CodeBuild and Amazon ECR during deployment instead of a local Docker build. The CodeBuild project runs outside the VPC to pull public base images. |
| `app.pipelines.useConversionCoordinateTransform.autoRegisterWithVAMS`                | boolean | `true`  | Automatically registers the pipeline and workflow in the VAMS database during deployment.                                                                                              |
| `app.pipelines.useConversionCoordinateTransform.autoRegisterAutoTriggerOnFileUpload` | boolean | `false` | Automatically triggers the pipeline when supported point cloud files are uploaded. Requires `autoRegisterWithVAMS` to be enabled.                                                      |

### Point cloud Potree viewer (`app.pipelines.usePreviewPcPotreeViewer`)

Processes E57, LAS, and LAZ point cloud files for Potree web viewing. **Requires VPC.** Uses a GPL-licensed library.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/preview/pcPotreeViewer/pcPotreeViewerBuilder-nestedStack.ts` (`PcPotreeViewerBuilderNestedStack`).
:::

| Field                                                                        | Type    | Default | Description                                             |
| ---------------------------------------------------------------------------- | ------- | ------- | ------------------------------------------------------- |
| `app.pipelines.usePreviewPcPotreeViewer.enabled`                             | boolean | `false` | Enables the point cloud Potree viewer pipeline.         |
| `app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS`                | boolean | `true`  | Automatically registers the pipeline during deployment. |
| `app.pipelines.usePreviewPcPotreeViewer.autoRegisterAutoTriggerOnFileUpload` | boolean | `true`  | Automatically triggers the pipeline on file uploads.    |

### 3D preview thumbnail (`app.pipelines.usePreview3dThumbnail`)

Generates animated GIF and static PNG preview thumbnails from 3D mesh, point cloud, CAD, and USD files. **Requires VPC.** Uses LGPL-licensed libraries. Supports input files up to 100 GB.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/preview/3dThumbnail/preview3dThumbnailBuilder-nestedStack.ts` (`Preview3dThumbnailBuilderNestedStack`).
:::

| Field                                                                     | Type    | Default | Description                                                                           |
| ------------------------------------------------------------------------- | ------- | ------- | ------------------------------------------------------------------------------------- |
| `app.pipelines.usePreview3dThumbnail.enabled`                             | boolean | `false` | Enables the 3D preview thumbnail pipeline.                                            |
| `app.pipelines.usePreview3dThumbnail.autoRegisterWithVAMS`                | boolean | `true`  | Automatically registers the pipeline during deployment.                               |
| `app.pipelines.usePreview3dThumbnail.autoRegisterAutoTriggerOnFileUpload` | boolean | `true`  | Automatically triggers the pipeline on file uploads matching supported 3D file types. |

### GenAI metadata labeling (`app.pipelines.useGenAiMetadata3dLabeling`)

Uses Amazon Bedrock to generate descriptive metadata labels for GLB, FBX, and OBJ files. **Requires VPC.**

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/genAi/metadata3dLabeling/metadata3dLabelingBuilder-nestedStack.ts` (`Metadata3dLabelingNestedStack`) — AWS Batch with Amazon Bedrock inference.
:::

| Field                                                                          | Type    | Default                   | Description                                                                                              |
| ------------------------------------------------------------------------------ | ------- | ------------------------- | -------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useGenAiMetadata3dLabeling.enabled`                             | boolean | `false`                   | Enables the GenAI metadata labeling pipeline.                                                            |
| `app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId`                      | string  | _(required when enabled)_ | Amazon Bedrock model ID for inference (for example, `global.anthropic.claude-sonnet-4-5-20250929-v1:0`). |
| `app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS`                | boolean | `true`                    | Automatically registers the pipeline during deployment.                                                  |
| `app.pipelines.useGenAiMetadata3dLabeling.autoRegisterAutoTriggerOnFileUpload` | boolean | `false`                   | Automatically triggers the pipeline on file uploads.                                                     |

### Gaussian splatting (`app.pipelines.useSplatToolbox`)

Generates Gaussian splat reconstructions from media files. **Requires VPC.**

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/3dRecon/splatToolbox/splatToolboxBuilder-nestedStack.ts` (`SplatToolboxBuilderNestedStack`) — AWS Batch on GPU instances.
:::

| Field                                                | Type    | Default | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------------------------------- | ------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useSplatToolbox.enabled`              | boolean | `false` | Enables the Gaussian splatting pipeline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `app.pipelines.useSplatToolbox.useCodeBuild`         | boolean | `false` | Build the container image via AWS CodeBuild + Amazon ECR instead of a local Docker build. Recommended for this pipeline — the image is a large CUDA/PyTorch build. CodeBuild runs in the same private VPC subnets as the pipeline Batch compute environments, with NAT Gateway egress. Builds run asynchronously and continue after the deployment finishes; if a build fails, check the CodeBuild project name in the CDK stack outputs. When `false`, the image is built locally with a CDK `DockerImageAsset` (requires local Docker). |
| `app.pipelines.useSplatToolbox.autoRegisterWithVAMS` | boolean | `true`  | Automatically registers the pipeline during deployment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

### Mesh to Gaussian Splat (`app.pipelines.useMesh2Splat`)

Converts GLB mesh files to 3D Gaussian Splat PLY files using GPU-accelerated conversion. **Requires VPC.**

:::warning[Implemented by]
No `useMesh2Splat` configuration key or nested stack currently exists in the infrastructure code (`infra/config/config.ts`, `infra/lib/nestedStacks/pipelines/`). This section documents a planned pipeline that is not yet implemented.
:::

| Field                                                             | Type    | Default | Description                                                         |
| ----------------------------------------------------------------- | ------- | ------- | ------------------------------------------------------------------- |
| `app.pipelines.useMesh2Splat.enabled`                             | boolean | `false` | Enables the Mesh2Splat pipeline.                                    |
| `app.pipelines.useMesh2Splat.autoRegisterWithVAMS`                | boolean | `true`  | Automatically registers the pipeline during deployment.             |
| `app.pipelines.useMesh2Splat.autoRegisterAutoTriggerOnFileUpload` | boolean | `false` | Automatically triggers the pipeline when `.glb` files are uploaded. |

### RapidPipeline on Amazon ECS (`app.pipelines.useRapidPipeline.useEcs`)

Third-party spatial data optimization. **Requires VPC and an [AWS Marketplace subscription](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi).**

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/multi/rapidPipeline/rapidPipeline-nestedStack.ts` (`RapidPipelineNestedStack`) — Amazon ECS.
:::

| Field                                                        | Type    | Default                   | Description                                                     |
| ------------------------------------------------------------ | ------- | ------------------------- | --------------------------------------------------------------- |
| `app.pipelines.useRapidPipeline.useEcs.enabled`              | boolean | `false`                   | Enables RapidPipeline on Amazon ECS.                            |
| `app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI` | string  | _(required when enabled)_ | Amazon ECR container image URI for the RapidPipeline container. |
| `app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS` | boolean | `true`                    | Automatically registers the pipeline during deployment.         |

### RapidPipeline on Amazon EKS (`app.pipelines.useRapidPipeline.useEks`)

Third-party spatial data optimization on Amazon EKS. **Requires VPC with 2+ Availability Zones and an [AWS Marketplace subscription](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi).**

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/multi/rapidPipelineEKS/rapidPipelineEKS-nestedStack.ts` (`RapidPipelineEKSNestedStack`) — Amazon EKS.
:::

| Field                                                                         | Type    | Default                   | Description                                                                            |
| ----------------------------------------------------------------------------- | ------- | ------------------------- | -------------------------------------------------------------------------------------- |
| `app.pipelines.useRapidPipeline.useEks.enabled`                               | boolean | `false`                   | Enables RapidPipeline on Amazon EKS.                                                   |
| `app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI`                  | string  | _(required when enabled)_ | Amazon ECR container image URI for the RapidPipeline container.                        |
| `app.pipelines.useRapidPipeline.useEks.autoRegisterWithVAMS`                  | boolean | `true`                    | Automatically registers the pipeline during deployment.                                |
| `app.pipelines.useRapidPipeline.useEks.eksClusterVersion`                     | string  | `1.31`                    | Kubernetes version for the Amazon EKS cluster.                                         |
| `app.pipelines.useRapidPipeline.useEks.nodeInstanceType`                      | string  | `m5.2xlarge`              | Amazon EC2 instance type for EKS worker nodes.                                         |
| `app.pipelines.useRapidPipeline.useEks.minNodes`                              | number  | `1`                       | Minimum worker nodes in the auto-scaling group.                                        |
| `app.pipelines.useRapidPipeline.useEks.maxNodes`                              | number  | `10`                      | Maximum worker nodes in the auto-scaling group.                                        |
| `app.pipelines.useRapidPipeline.useEks.desiredNodes`                          | number  | `2`                       | Desired worker node count under normal operation.                                      |
| `app.pipelines.useRapidPipeline.useEks.jobTimeout`                            | number  | `7200`                    | Maximum job runtime in seconds (default: 2 hours).                                     |
| `app.pipelines.useRapidPipeline.useEks.jobMemory`                             | string  | `16Gi`                    | Memory allocation per Kubernetes job pod.                                              |
| `app.pipelines.useRapidPipeline.useEks.jobCpu`                                | string  | `2000m`                   | CPU allocation per Kubernetes job pod in millicores.                                   |
| `app.pipelines.useRapidPipeline.useEks.jobBackoffLimit`                       | number  | `2`                       | Number of retries before marking a job as failed.                                      |
| `app.pipelines.useRapidPipeline.useEks.jobTTLSecondsAfterFinished`            | number  | `600`                     | Seconds to retain completed job pods before cleanup.                                   |
| `app.pipelines.useRapidPipeline.useEks.observability.enableControlPlaneLogs`  | boolean | `false`                   | Enables EKS control plane logging to Amazon CloudWatch. Incurs additional costs.       |
| `app.pipelines.useRapidPipeline.useEks.observability.enableContainerInsights` | boolean | `false`                   | Enables Amazon CloudWatch Container Insights for the cluster. Incurs additional costs. |

### ModelOps (`app.pipelines.useModelOps`)

Third-party 3D model optimization by VNTANA. **Requires VPC and an [AWS Marketplace subscription](https://aws.amazon.com/marketplace/pp/prodview-ooio3bidshgy4).**

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/multi/modelOps/modelOps-nestedStack.ts` (`ModelOpsNestedStack`) — AWS Batch.
:::

| Field                                            | Type    | Default                   | Description                                                |
| ------------------------------------------------ | ------- | ------------------------- | ---------------------------------------------------------- |
| `app.pipelines.useModelOps.enabled`              | boolean | `false`                   | Enables the ModelOps pipeline.                             |
| `app.pipelines.useModelOps.ecrContainerImageURI` | string  | _(required when enabled)_ | Amazon ECR container image URI for the ModelOps container. |
| `app.pipelines.useModelOps.autoRegisterWithVAMS` | boolean | `true`                    | Automatically registers the pipeline during deployment.    |

### Isaac Lab training (`app.pipelines.useIsaacLabTraining`)

NVIDIA Isaac Lab reinforcement learning training pipeline on GPU instances. **Requires VPC.**

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/simulation/isaacLabTraining/isaacLabTrainingBuilder-nestedStack.ts` (`IsaacLabTrainingBuilderNestedStack`) — AWS Batch on GPU instances.
:::

| Field                                                    | Type    | Default | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| -------------------------------------------------------- | ------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useIsaacLabTraining.enabled`              | boolean | `false` | Enables the Isaac Lab training pipeline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `app.pipelines.useIsaacLabTraining.acceptNvidiaEula`     | boolean | `false` | **Required when enabled.** Confirms acceptance of the [NVIDIA Software License Agreement](https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license). Deployment fails if not set to `true` when the pipeline is enabled.                                                                                                                                                                                                                                                                                   |
| `app.pipelines.useIsaacLabTraining.useCodeBuild`         | boolean | `false` | When true, the Isaac Lab container is built using AWS CodeBuild in the cloud and pushed to ECR. When false (default), the container is built locally during CDK deployment using DockerImageAsset. CodeBuild runs in the same private VPC subnets as the pipeline Batch compute environments, with NAT Gateway egress for internet access, and forwards the `acceptNvidiaEula` flag as the `ACCEPT_EULA` Docker build argument. CodeBuild builds run asynchronously — if a build fails, check the CodeBuild project name in CDK stack outputs. |
| `app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS` | boolean | `true`  | Automatically registers training and evaluation workflows during deployment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `app.pipelines.useIsaacLabTraining.keepWarmInstance`     | boolean | `false` | Keeps a warm AWS Batch compute instance running to reduce cold start times. **Warning:** Incurs continuous compute costs even when no jobs are running.                                                                                                                                                                                                                                                                                                                                                                                        |

### NVIDIA Cosmos Predict (`app.pipelines.useNvidiaCosmos`)

NVIDIA Cosmos world foundation models for generating videos from text prompts (Text2World) and from images/videos (Video2World). **Requires VPC** and internet access for HuggingFace model downloads.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/genAi/nvidia/cosmos/cosmosBuilder-nestedStack.ts` (`CosmosBuilderNestedStack`) — AWS Batch on GPU instances. This single stack implements all NVIDIA Cosmos models: Predict, Reason, and Transfer.
:::

| Field                                                                                             | Type    | Default                                          | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------- | ------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useNvidiaCosmos.enabled`                                                           | boolean | `false`                                          | Enables the NVIDIA Cosmos Predict pipeline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `app.pipelines.useNvidiaCosmos.huggingFaceToken`                                                  | string  | `""`                                             | HuggingFace Read access token value (e.g., `hf_xxxx`). CDK stores this in AWS Secrets Manager during deployment. Must have access to all 6 required Cosmos models. **Required when enabled.**                                                                                                                                                                                                                                                                                                                                             |
| `app.pipelines.useNvidiaCosmos.useCodeBuild`                                                      | boolean | `false`                                          | When true, Cosmos pipeline containers are built using AWS CodeBuild in the cloud. When false (default), containers are built locally during CDK deployment using DockerImageAsset. CodeBuild runs in the same private VPC subnets as the pipeline Batch compute environments, with NAT Gateway egress for internet access. CodeBuild builds run asynchronously — if a build fails, check the CodeBuild project name in CDK stack outputs. Consider configuring Docker Hub authentication credentials to avoid rate limiting (429 errors). |
| `app.pipelines.useNvidiaCosmos.useWarmInstances`                                                  | boolean | `false`                                          | Keeps GPU instances running when idle for instant pipeline starts. When `false`, scales to zero after job completion (~5-10 min cold start). **Warning:** Warm instances incur continuous compute costs (~$5.67/hr per g5.12xlarge).                                                                                                                                                                                                                                                                                                      |
| `app.pipelines.useNvidiaCosmos.warmInstanceCount`                                                 | number  | `1`                                              | Number of warm GPU instances to keep running when `useWarmInstances` is `true`.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2.enabled`                             | boolean | `false`                                          | Enables Cosmos-Predict2.5-2B-Text2World for generating ~4-second videos from text prompts using the v2.5 flow-matching architecture.                                                                                                                                                                                                                                                                                                                                                                                                      |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2.autoRegisterWithVAMS`                | boolean | `true`                                           | Automatically registers the Text2World 2B v2.5 pipeline during deployment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2.instanceTypes`                       | array   | `["g6e.12xlarge", "g5.12xlarge", "g5.48xlarge"]` | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires 4 GPUs with 24GB+ VRAM. 2B model runs without CPU offloading.                                                                                                                                                                                                                                                                                                                                                                                               |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2.maxVCpus`                            | number  | `192`                                            | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world14B_v2.enabled`                            | boolean | `false`                                          | Enables Cosmos-Predict2.5-14B-Text2World for generating ~4-second videos from text prompts using the v2.5 flow-matching architecture. Requires P-series instances.                                                                                                                                                                                                                                                                                                                                                                        |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world14B_v2.autoRegisterWithVAMS`               | boolean | `true`                                           | Automatically registers the Text2World 14B v2.5 pipeline during deployment.                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world14B_v2.instanceTypes`                      | array   | `["g6e.48xlarge", "p5.48xlarge"]`                | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). 14B models use 8-GPU context parallelism via torchrun. g6e.48xlarge (8x L40S 48GB) recommended; p5.48xlarge (8x H100 80GB) as fallback. **Note:** p4d instances are not supported due to older CUDA driver incompatibilities.                                                                                                                                                                                                                                        |
| `app.pipelines.useNvidiaCosmos.modelsPredict.text2world14B_v2.maxVCpus`                           | number  | `192`                                            | Maximum vCPUs for the AWS Batch compute environment (g6e.48xlarge and p5.48xlarge both have 192 vCPUs).                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world2B_v2.enabled`                            | boolean | `false`                                          | Enables Cosmos-Predict2.5-2B-Video2World for generating ~4-second videos from image/video inputs with optional text guidance using the v2.5 flow-matching architecture.                                                                                                                                                                                                                                                                                                                                                                   |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world2B_v2.autoRegisterWithVAMS`               | boolean | `true`                                           | Automatically registers the Video2World 2B v2.5 pipeline during deployment.                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world2B_v2.autoTriggerOnFileExtensionsUpload`  | string  | `""`                                             | Comma-separated list of file extensions to auto-trigger the pipeline on upload (for example, `".jpg,.png,.mp4"`). Leave empty to disable auto-trigger.                                                                                                                                                                                                                                                                                                                                                                                    |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world2B_v2.instanceTypes`                      | array   | `["g6e.12xlarge", "g5.12xlarge", "g5.48xlarge"]` | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires 4 GPUs with 24GB+ VRAM. 2B model runs without CPU offloading.                                                                                                                                                                                                                                                                                                                                                                                               |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world2B_v2.maxVCpus`                           | number  | `192`                                            | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world14B_v2.enabled`                           | boolean | `false`                                          | Enables Cosmos-Predict2.5-14B-Video2World for generating ~4-second videos from image/video inputs with optional text guidance using the v2.5 flow-matching architecture. Requires P-series instances.                                                                                                                                                                                                                                                                                                                                     |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world14B_v2.autoRegisterWithVAMS`              | boolean | `true`                                           | Automatically registers the Video2World 14B v2.5 pipeline during deployment.                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world14B_v2.autoTriggerOnFileExtensionsUpload` | string  | `""`                                             | Comma-separated list of file extensions to auto-trigger the pipeline on upload. Leave empty to disable auto-trigger.                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world14B_v2.instanceTypes`                     | array   | `["g6e.48xlarge", "p5.48xlarge"]`                | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). 14B models use 8-GPU context parallelism via torchrun. g6e.48xlarge (8x L40S 48GB) recommended; p5.48xlarge (8x H100 80GB) as fallback. **Note:** p4d instances are not supported due to older CUDA driver incompatibilities.                                                                                                                                                                                                                                        |
| `app.pipelines.useNvidiaCosmos.modelsPredict.video2world14B_v2.maxVCpus`                          | number  | `192`                                            | Maximum vCPUs for the AWS Batch compute environment (g6e.48xlarge and p5.48xlarge both have 192 vCPUs).                                                                                                                                                                                                                                                                                                                                                                                                                                   |

### NVIDIA Cosmos Reason (`app.pipelines.useNvidiaCosmos.modelsReason`)

NVIDIA Cosmos Reason Vision Language Models (VLMs) for analyzing video and image content to generate text-based analysis, captions, descriptions, and reasoning. **Requires VPC** and internet access for HuggingFace model downloads. Shares the same EFS model cache and HuggingFace token as Cosmos Predict pipelines.

| Field                                                                                   | Type    | Default                            | Description                                                                                                                                                          |
| --------------------------------------------------------------------------------------- | ------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason2B.enabled`                           | boolean | `false`                            | Enables Cosmos-Reason2-2B Vision Language Model for video/image analysis generating text-based output. Model size: ~5GB.                                             |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason2B.autoRegisterWithVAMS`              | boolean | `true`                             | Automatically registers the Reason 2B pipeline during deployment.                                                                                                    |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason2B.autoTriggerOnFileExtensionsUpload` | string  | `""`                               | Comma-separated file extensions to auto-trigger on upload (e.g., `".mp4,.mov,.jpg"`). Leave empty to disable.                                                        |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason2B.instanceTypes`                     | array   | `["g6e.12xlarge", "g5.12xlarge"]`  | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires 24GB+ VRAM.                                                                            |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason2B.maxVCpus`                          | number  | `192`                              | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                 |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason8B.enabled`                           | boolean | `false`                            | Enables Cosmos-Reason2-8B Vision Language Model for improved reasoning quality. Model size: ~16GB. Larger model with better spatial-temporal understanding than 2B.  |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason8B.autoRegisterWithVAMS`              | boolean | `true`                             | Automatically registers the Reason 8B pipeline during deployment.                                                                                                    |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason8B.autoTriggerOnFileExtensionsUpload` | string  | `""`                               | Comma-separated file extensions to auto-trigger on upload. Leave empty to disable.                                                                                   |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason8B.instanceTypes`                     | array   | `["g6e.12xlarge", "g6e.24xlarge"]` | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires 32GB+ VRAM per GPU. g5 instances (A10G, 24GB VRAM) are not supported for the 8B model. |
| `app.pipelines.useNvidiaCosmos.modelsReason.reason8B.maxVCpus`                          | number  | `192`                              | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                 |

### NVIDIA Cosmos Transfer (`app.pipelines.useNvidiaCosmos.modelsTransfer`)

NVIDIA Cosmos Transfer model for video transformation with control signal conditioning. Supports style transfer and content transformation using edge, depth, segmentation, or visual blur control signals. **Requires VPC** and internet access for HuggingFace model downloads. Shares the same EFS model cache and HuggingFace token as Cosmos Predict and Reason pipelines.

| Field                                                                                       | Type    | Default                           | Description                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------- | ------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useNvidiaCosmos.modelsTransfer.transfer2B.enabled`                           | boolean | `false`                           | Enables Cosmos-Transfer2.5-2B for video transformation with control signal conditioning. Model size: ~20GB. Additional dependencies: VideoDepthAnything (~2GB), SAM2 (~5GB).                                                                                                           |
| `app.pipelines.useNvidiaCosmos.modelsTransfer.transfer2B.autoRegisterWithVAMS`              | boolean | `true`                            | Automatically registers the Transfer 2B pipeline during deployment.                                                                                                                                                                                                                    |
| `app.pipelines.useNvidiaCosmos.modelsTransfer.transfer2B.autoTriggerOnFileExtensionsUpload` | string  | `""`                              | Comma-separated file extensions to auto-trigger on upload (e.g., `".mp4,.mov"`). Leave empty to disable.                                                                                                                                                                               |
| `app.pipelines.useNvidiaCosmos.modelsTransfer.transfer2B.instanceTypes`                     | array   | `["g6e.48xlarge", "p5.48xlarge"]` | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). g6e.48xlarge (8x L40S 48GB) is the recommended default. p5.48xlarge (8x H100 80GB) as fallback. **Note:** p4d.24xlarge is not supported due to older CUDA driver incompatibilities with the Transfer 2.5 runtime. |
| `app.pipelines.useNvidiaCosmos.modelsTransfer.transfer2B.maxVCpus`                          | number  | `192`                             | Maximum vCPUs for the AWS Batch compute environment (g6e.48xlarge and p5.48xlarge both have 192 vCPUs).                                                                                                                                                                                |

### NVIDIA Cosmos 3 (`app.pipelines.useNvidiaCosmos3`)

NVIDIA Cosmos 3 omnimodal world foundation models (Nano 16B, Super 64B) for generating images and videos from text prompts (text2image, text2video) or from images (image2video). **Requires VPC** and internet access for HuggingFace model downloads. Models are cached on Amazon EFS with Amazon S3 backup.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/genAi/nvidia/cosmos3/cosmos3Builder-nestedStack.ts` (`Cosmos3BuilderNestedStack`) — AWS Batch on GPU instances.
:::

| Field                                                                                             | Type    | Default                                            | Description                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------- | ------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useNvidiaCosmos3.enabled`                                                          | boolean | `false`                                            | Enables the Cosmos 3 pipeline.                                                                                                                                                                       |
| `app.pipelines.useNvidiaCosmos3.huggingFaceToken`                                                 | string  | `""`                                               | HuggingFace Read access token value (e.g., `hf_xxxx`). CDK stores this in AWS Secrets Manager during deployment. Must have access to all Cosmos3 models. **Required when enabled.**                  |
| `app.pipelines.useNvidiaCosmos3.useCodeBuild`                                                     | boolean | `false`                                            | Build container image via AWS CodeBuild + ECR instead of local Docker. Recommended for large GPU images. When `false`, uses inline CDK DockerImageAsset (requires local Docker).                     |
| `app.pipelines.useNvidiaCosmos3.useWarmInstances`                                                 | boolean | `false`                                            | Keeps GPU instances running when idle for faster pipeline starts.                                                                                                                                    |
| `app.pipelines.useNvidiaCosmos3.warmInstanceCount`                                                | number  | `1`                                                | Number of warm GPU instances to keep running when `useWarmInstances` is `true`.                                                                                                                      |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.enabled`                                       | boolean | `false`                                            | Enables Cosmos3-Nano 16B model for text2image, text2video, and image2video. Model size: ~35 GB.                                                                                                      |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.autoRegisterWithVAMS`                          | boolean | `true`                                             | Automatically registers the Nano 16B pipeline during deployment.                                                                                                                                     |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.autoTriggerOnFileExtensionsUpload`             | string  | `""`                                               | Comma-separated file extensions to auto-trigger on upload (e.g., `".jpg,.png"`). Leave empty to disable.                                                                                             |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes`                                 | array   | `["g6e.4xlarge", "g6e.12xlarge"]`                  | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires single-GPU with 48GB+ VRAM.                                                                                            |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.maxVCpus`                                      | number  | `192`                                              | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                                                 |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.super64B.enabled`                                      | boolean | `false`                                            | Enables Cosmos3-Super 64B omnimodal model for text2image, text2video, and image2video. Model size: ~133 GB.                                                                                          |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.super64B.autoRegisterWithVAMS`                         | boolean | `true`                                             | Automatically registers the Super 64B pipeline during deployment.                                                                                                                                    |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.super64B.autoTriggerOnFileExtensionsUpload`            | string  | `""`                                               | Comma-separated file extensions to auto-trigger on upload (e.g., `".jpg,.png"`). Leave empty to disable.                                                                                             |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.super64B.instanceTypes`                                | array   | `["p5.48xlarge", "p5e.48xlarge", "p4de.24xlarge"]` | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires 8x H100/H200/A100-80GB GPUs. **Note:** Super models require multi-GPU instances and will not fit on a single 80GB GPU. |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.super64B.maxVCpus`                                     | number  | `192`                                              | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                                                 |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superText2Image64B.enabled`                            | boolean | `false`                                            | Enables Cosmos3-Super-Text2Image 64B model for high-quality text-to-image generation. Model size: ~133 GB.                                                                                           |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superText2Image64B.autoRegisterWithVAMS`               | boolean | `true`                                             | Automatically registers the Super-Text2Image 64B pipeline during deployment.                                                                                                                         |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superText2Image64B.instanceTypes`                      | array   | `["p5.48xlarge", "p5e.48xlarge"]`                  | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires 8x H100/H200 GPUs.                                                                                                     |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superText2Image64B.maxVCpus`                           | number  | `192`                                              | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                                                 |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B.enabled`                           | boolean | `false`                                            | Enables Cosmos3-Super-Image2Video 64B model for high-quality image-to-video generation. Model size: ~133 GB.                                                                                         |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B.autoRegisterWithVAMS`              | boolean | `true`                                             | Automatically registers the Super-Image2Video 64B pipeline during deployment.                                                                                                                        |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B.autoTriggerOnFileExtensionsUpload` | string  | `""`                                               | Comma-separated file extensions to auto-trigger on upload (e.g., `".jpg,.png"`). Leave empty to disable.                                                                                             |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B.instanceTypes`                     | array   | `["p5.48xlarge", "p5e.48xlarge", "p4de.24xlarge"]` | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Requires 8x H100/H200/A100-80GB GPUs.                                                                                           |
| `app.pipelines.useNvidiaCosmos3.modelsOmni.superImage2Video64B.maxVCpus`                          | number  | `192`                                              | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                                                 |

### NVIDIA Gr00t Fine-Tuning (`app.pipelines.useNvidiaGr00t`)

NVIDIA Gr00t (GR00T-N1.5-3B) fine-tuning pipeline for embodied AI robot training. Uses LeRobot v2.1 datasets stored as VAMS assets. Operates at the asset level -- downloads the entire asset, looks for training data in a `dataset/` subfolder (configurable), and outputs model checkpoints. **Requires VPC** and internet access for HuggingFace model downloads.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/pipelines/genAi/nvidia/gr00t/gr00tBuilder-nestedStack.ts` (`Gr00tBuilderNestedStack`) — AWS Batch on GPU instances.
:::

| Setting                                                                         | Type    | Default                                          | Description                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------- | ------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.useNvidiaGr00t.enabled`                                          | boolean | `false`                                          | Enables the NVIDIA Gr00t fine-tuning pipeline.                                                                                                                                                                                  |
| `app.pipelines.useNvidiaGr00t.huggingFaceToken`                                 | string  | `""`                                             | HuggingFace Read access token value (e.g., `hf_xxxx`). CDK stores this in AWS Secrets Manager during deployment. Must have access to `nvidia/GR00T-N1.5-3B`. **Required when enabled.**                                         |
| `app.pipelines.useNvidiaGr00t.useCodeBuild`                                     | boolean | `false`                                          | Build container image via AWS CodeBuild + ECR instead of local Docker. Recommended for large GPU images. When `false`, uses inline CDK DockerImageAsset (requires local Docker).                                                |
| `app.pipelines.useNvidiaGr00t.useWarmInstances`                                 | boolean | `false`                                          | Keeps GPU instances running when idle for faster pipeline starts.                                                                                                                                                               |
| `app.pipelines.useNvidiaGr00t.warmInstanceCount`                                | number  | `0`                                              | Number of warm GPU instances to keep running when `useWarmInstances` is `true`.                                                                                                                                                 |
| `app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.enabled`              | boolean | `false`                                          | Enables GR00T-N1.5-3B fine-tuning.                                                                                                                                                                                              |
| `app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.autoRegisterWithVAMS` | boolean | `true`                                           | Automatically registers the fine-tuning pipeline during deployment.                                                                                                                                                             |
| `app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes`        | array   | `["g6e.4xlarge", "g6e.12xlarge", "g5.12xlarge"]` | EC2 GPU instance types for AWS Batch compute (BEST_FIT_PROGRESSIVE). Multiple types listed for regional capacity flexibility. g6e.4xlarge (1 GPU) for LoRA, g6e.12xlarge (4 GPU) for full fine-tuning, g5.12xlarge as fallback. |
| `app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.maxVCpus`             | number  | `192`                                            | Maximum vCPUs for the AWS Batch compute environment.                                                                                                                                                                            |

### Deadline Cloud Execution Type (`app.pipelines.deadlineCloudExecutionTypeEnabled`)

Support for the `DeadlineCloud` pipeline execution type: workflow task states submit OpenJD jobs to an operator-owned AWS Deadline Cloud farm/queue via `createJob`, and a job-callback Lambda resolves the workflow's task token from Deadline Cloud job status events on the account's default Amazon EventBridge bus. Deadline Cloud pipelines are asynchronous only (callback required). Not available in GovCloud. The Deadline Cloud farm must reside in the same account and Region as the VAMS deployment, and the queue's service role must have read access to the execution input locations and write access to the execution output prefixes in the asset bucket.

:::note[Implemented by]
Lambda builder: `infra/lib/lambdaBuilder/workflowFunctions.ts` (`buildDeadlineCloudJobCallbackFunction`) — deployed in the API builder stack with an EventBridge rule on the default bus.
:::

| Setting                                           | Type    | Default | Description                                                                                                          |
| ------------------------------------------------- | ------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| `app.pipelines.deadlineCloudExecutionTypeEnabled` | boolean | `false` | Deploys the Deadline Cloud job-callback Lambda + default-bus rule and grants the workflow role `deadline:CreateJob`. |

## Addons (`app.addons`)

### Garnet Framework (`app.addons.useGarnetFramework`)

Integration with the Garnet Framework external knowledge graph for NGSI-LD data synchronization.

:::note[Implemented by]
Nested stack: `infra/lib/nestedStacks/addon/addonBuilder-nestedStack.ts` (`AddonBuilderNestedStack`), which instantiates `addon/garnetFramework/garnetFrameworkBuilder-nestedStack.ts` (`GarnetFrameworkBuilderNestedStack`).
:::

| Field                                                      | Type    | Default                   | Description                                                                                                              |
| ---------------------------------------------------------- | ------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `app.addons.useGarnetFramework.enabled`                    | boolean | `false`                   | Enables Garnet Framework integration for automatic NGSI-LD indexing of all VAMS data changes.                            |
| `app.addons.useGarnetFramework.garnetApiEndpoint`          | string  | _(required when enabled)_ | Garnet Framework API endpoint URL (for example, `https://XXX.execute-api.us-east-1.amazonaws.com`). Must be a valid URL. |
| `app.addons.useGarnetFramework.garnetApiToken`             | string  | _(required when enabled)_ | API authentication token for the Garnet Framework.                                                                       |
| `app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl` | string  | _(required when enabled)_ | Amazon SQS queue URL for Garnet Framework data ingestion. Format: `https://sqs.REGION.amazonaws.com/ACCOUNT/QUEUE_NAME`. |

### Physna Sync (`app.addons.usePhysnaSync`)

One-way synchronization of supported VAMS files and metadata to a Physna tenant for geometric and semantic 3D search.

| Field                                           | Type    | Default                                                            | Description                                                                                                                                                                             |
| ----------------------------------------------- | ------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.addons.usePhysnaSync.enabled`              | boolean | `false`                                                            | Enables the Physna Sync add-on.                                                                                                                                                         |
| `app.addons.usePhysnaSync.tenantId`             | string  | _(required when enabled)_                                          | Physna tenant UUID.                                                                                                                                                                     |
| `app.addons.usePhysnaSync.apiBaseEndpoint`      | string  | `https://app-api.physna.com/v3/`                                   | Physna REST API base URL. Must end with `/`.                                                                                                                                            |
| `app.addons.usePhysnaSync.authTokenEndpoint`    | string  | `https://physna-app.auth.us-east-2.amazoncognito.com/oauth2/token` | OAuth2 token endpoint for Physna's Cognito user pool.                                                                                                                                   |
| `app.addons.usePhysnaSync.authType`             | string  | `cognito`                                                          | Authentication mode. Only `cognito` is supported in phase 1.                                                                                                                            |
| `app.addons.usePhysnaSync.clientId`             | string  | _(required when enabled)_                                          | Cognito client ID. VAMS creates the credentials secret and populates it at deploy time.                                                                                                 |
| `app.addons.usePhysnaSync.clientSecret`         | string  | _(required when enabled)_                                          | Cognito client secret. VAMS creates the credentials secret and populates it at deploy time without writing the value into the CloudFormation template.                                  |
| `app.addons.usePhysnaSync.credentialsSecretArn` | string  | `""`                                                               | Optional. ARN of an operator-managed AWS Secrets Manager secret holding `{ "clientId", "clientSecret" }`. When set, VAMS imports that secret and `clientId`/`clientSecret` are ignored. |

Provide the OAuth2 client credentials in one of two ways:

-   **Inline `clientId` + `clientSecret` (default).** Put the credentials directly in the configuration. VAMS creates the Secrets Manager secret during deployment and populates it via a custom resource whose Lambda carries the values in its code asset. Because CDK references code assets by content hash (uploaded to the CDK assets bucket), the credential value never appears in the synthesized CloudFormation template or its resource properties — and no secret has to be created ahead of deployment.

-   **`credentialsSecretArn` (operator-managed).** Create the secret yourself with a JSON value of `{ "clientId": "...", "clientSecret": "..." }` and reference it by ARN. VAMS imports the secret by ARN and ignores the inline `clientId`/`clientSecret`. Use this when secret provisioning is centralized or must be managed outside the VAMS deployment.

    ```bash
    aws secretsmanager create-secret \
        --name my-vams-physna-credentials \
        --secret-string '{"clientId":"...","clientSecret":"..."}'
    # then set app.addons.usePhysnaSync.credentialsSecretArn to the returned ARN
    ```

Enabling the Physna Sync add-on also enables the in-app Physna add-on frontend features (currently the Physna Viewer plugin; more Physna-powered UI surfaces are planned). The backend emits a `PHYSNA_ADDON` feature flag in `/api/secure-config` whenever `app.addons.usePhysnaSync.enabled` is `true`, and the frontend consumes that flag to decide whether to surface Physna add-on features for supported file types. No separate configuration is required.

:::warning[Physna Viewer tokens grant tenant-wide reach]
The Physna Viewer plugin (`GET /addon/physna/viewer`) enforces VAMS two-tier authorization on the requested asset, then returns a Physna viewer token to the browser. Physna issues this token at **tenant** scope rather than per-asset (Physna does not currently support asset-scoped viewer tokens), so a user authorized to view one synced asset holds a token whose reach spans the Physna tenant for its lifetime. Treat access to the Physna Viewer feature as granting visibility into the connected Physna tenant, and scope the `api` and `asset` permissions for the viewer route accordingly.
:::

## Example configurations

For complete configuration examples, see the template files in the repository:

-   **Commercial:** [`infra/config/config.template.commercial.json`](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/config/config.template.commercial.json)
-   **GovCloud:** [`infra/config/config.template.govcloud.json`](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/config/config.template.govcloud.json)
-   **AWS European Sovereign Cloud:** [`infra/config/config.template.eusovereign.json`](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/config/config.template.eusovereign.json)

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
            "useForAllLambdas": false,
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

:::note[Running all Lambda functions inside the VPC]
The GovCloud template sets `useGlobalVpc.useForAllLambdas` to `false`, so only the AWS Lambda functions that strictly require the VPC run inside it. Set `useGlobalVpc.useForAllLambdas` to `true` to place **all** VAMS Lambda functions inside the VPC (with the required VPC interface endpoints) when stricter network isolation is needed or the Lambda functions must reach specific VPC network components.
:::

:::warning[VPC is required for some features]
Some features require a VPC and `app.useGlobalVpc.enabled` must be `true` when they are enabled. If one is enabled while `app.useGlobalVpc.enabled` is `false`, configuration validation fails with an error that lists the offending features; set `app.useGlobalVpc.enabled` to `true` (or disable those features). VPC-requiring features are: ALB deployment (`useAlb`), OpenSearch Provisioned (`openSearch.useProvisioned`), and the container-based pipelines (Potree viewer, 3D preview thumbnail, GenAI labeling, Gaussian splatting, RapidPipeline ECS/EKS, ModelOps, Isaac Lab, NVIDIA Cosmos, NVIDIA Gr00t).
:::

### AWS European Sovereign Cloud deployment

The AWS European Sovereign Cloud (Region `eusc-de-east-1`, partition `aws-eusc`) is a separate, isolated partition. For now, deploy to it using the GovCloud guardrails: set `app.govCloud.enabled = true` so the same constraints are enforced (VPC required, no Amazon CloudFront, no Amazon Location Service). The European Sovereign Cloud Region currently exposes two Availability Zones, so a provisioned Amazon OpenSearch Service domain must set `availabilityZoneCount` to `2`.

Key differences from the commercial template (see [`config.template.eusovereign.json`](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/config/config.template.eusovereign.json)):

```json
{
    "env": { "region": "eusc-de-east-1" },
    "app": {
        "useWaf": true,
        "useKmsCmkEncryption": { "enabled": true },
        "govCloud": { "enabled": true, "il6Compliant": false },
        "useGlobalVpc": {
            "enabled": true,
            "useForAllLambdas": false,
            "addVpcEndpoints": true,
            "vpcCidrRange": "10.1.0.0/16"
        },
        "openSearch": {
            "useProvisioned": { "enabled": true, "availabilityZoneCount": 2 }
        },
        "useLocationService": { "enabled": false },
        "useAlb": {
            "enabled": true,
            "usePublicSubnet": false,
            "domainHost": "vams.example.eu",
            "certificateArn": "arn:aws-eusc:acm:REGION:ACCOUNT:certificate/ID"
        },
        "useCloudFront": { "enabled": false }
    }
}
```

:::note[Running all Lambda functions inside the VPC]
The European Sovereign Cloud template sets `useGlobalVpc.useForAllLambdas` to `false`, so only the AWS Lambda functions that strictly require the VPC run inside it. Set `useGlobalVpc.useForAllLambdas` to `true` to place **all** VAMS Lambda functions inside the VPC (with the required VPC interface endpoints) when stricter network isolation is needed or the Lambda functions must reach specific VPC network components.
:::

## Additional configuration files

Beyond `config.json`, VAMS supports several supplementary configuration files:

| File                                                         | Purpose                                                                                                                                                                                             |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `infra/config/policy/s3AdditionalBucketPolicyConfig.json`    | Additional IAM policy statements applied to all Amazon S3 buckets. Controls presigned URL and STS credential access restrictions.                                                                   |
| `infra/config/policy/iamRoleConfig.json`                     | Pre-created IAM role mappings for restricted environments. Read only when `app.iamRoleConfig.useCustomBootstrapRoles` or `app.iamRoleConfig.useCustomVamsStackRoles` is `true`.                     |
| `infra/config/csp/cspAdditionalConfig.json`                  | Additional Content Security Policy (CSP) sources for external APIs, scripts, images, media, fonts, and styles.                                                                                      |
| `infra/config/saml-config.ts`                                | SAML identity provider settings for Amazon Cognito federation. Required when `authProvider.useCognito.useSaml` is `true`. See [Security Architecture](../architecture/security.md#saml-federation). |
| `infra/config/docker/Dockerfile-customDependencyBuildConfig` | Custom Docker build configuration for Lambda layer packaging. Useful for adding custom SSL certificates for HTTPS proxy environments.                                                               |
| `infra/cdk.json` (`environments.common`)                     | Key-value pairs applied as tags on all stack resources.                                                                                                                                             |
| `infra/cdk.json` (`environments.aws`)                        | `PermissionBoundaryArn` and `IamRoleNamePrefix` for IAM role customization.                                                                                                                         |

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

| Field                   | Type   | Default | Description                                                                                                                        |
| ----------------------- | ------ | ------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `PermissionBoundaryArn` | string | `""`    | ARN of an IAM permission boundary to apply to all roles created by the VAMS core stack. Leave empty to skip permission boundaries. |
| `IamRoleNamePrefix`     | string | `""`    | Prefix string applied to all newly created IAM role names.                                                                         |

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

## Advanced IAM role customization (`app.iamRoleConfig`)

Some organizations centrally provision IAM roles and do not allow deployment processes to create them. VAMS supports two independent, opt-in mechanisms for these environments. Both are disabled by default; when both are off, VAMS manages all IAM roles itself, which is the recommended approach because grants stay automatically in sync with the resources they protect.

Each mechanism is toggled by a boolean in `config.json` under `app.iamRoleConfig`, while the actual mappings (role ARNs, construct-path-to-role-name maps) live in the separate `infra/config/policy/iamRoleConfig.json` file. This keeps the verbose values out of the main configuration and lets a central IAM team own that file.

| `app.iamRoleConfig` flag  | Mappings source (section in `iamRoleConfig.json`) | Controls                                                                                                             |
| ------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `useCustomBootstrapRoles` | `bootstrap`                                       | The CDK bootstrap roles assumed during deployment (deploy, CloudFormation execution, lookup, file/image publishing). |
| `useCustomVamsStackRoles` | `vamsStacks`                                      | The application roles VAMS constructs create inside the stacks (Lambda execution roles, and so on).                  |

:::info[How this relates to `environments.aws`]
The `environments.aws` settings in `cdk.json` (role name prefix and permission boundary) constrain roles that VAMS **creates**. `app.iamRoleConfig` instead lets VAMS **avoid creating** bootstrap and/or application roles by pointing at pre-created ones. The two are complementary: use `environments.aws` when VAMS may create roles within guardrails, and `app.iamRoleConfig` when it may not create them at all.
:::

### Bootstrap role customization (`bootstrap`)

By default, `cdk bootstrap` creates five roles per account and Region (for example, `cdk-hnb659fds-deploy-role-<account>-<region>`). When `useCustomBootstrapRoles` is `true`, VAMS configures its stack synthesizer from the `bootstrap` section. This applies to both the VAMS WAF stack and the VAMS core stack; nested stacks inherit the synthesizer from their parent automatically.

| Field in `bootstrap`             | Description                                                                                                                                                                                                            |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `useCliCredentialsSynthesizer`   | When `true`, no bootstrap IAM roles are used at all. Deployments run under the caller's own credentials and only require the staging bucket and ECR repo. Does not support cross-account deployments or CDK Pipelines. |
| `qualifier`                      | Bootstrap qualifier, if you bootstrapped with a non-default qualifier. Leave empty for the CDK default (`hnb659fds`).                                                                                                  |
| `deployRoleArn`                  | ARN of the pre-created deploy role assumed by the CDK CLI. Empty fields fall back to the CDK default name.                                                                                                             |
| `cloudFormationExecutionRoleArn` | ARN of the pre-created role AWS CloudFormation uses to execute the deployment.                                                                                                                                         |
| `lookupRoleArn`                  | ARN of the pre-created role used for environment context lookups.                                                                                                                                                      |
| `fileAssetPublishingRoleArn`     | ARN of the pre-created role used to publish file assets to the staging bucket.                                                                                                                                         |
| `imageAssetPublishingRoleArn`    | ARN of the pre-created role used to publish Docker image assets to the staging ECR repository.                                                                                                                         |
| `fileAssetsBucketName`           | Staging bucket name, only if your customized bootstrap template renamed it.                                                                                                                                            |
| `imageAssetsRepositoryName`      | Staging ECR repository name, only if your customized bootstrap template renamed it.                                                                                                                                    |

Any field left empty keeps the corresponding CDK default. ARNs may use the `${AWS::Partition}`, `${AWS::AccountId}`, and `${AWS::Region}` placeholders, which resolve at deployment time. To discover the required permissions for each role, export the default bootstrap template with `cdk bootstrap --show-template > bootstrap-template.yaml` and copy the role policies into your role-provisioning process.

:::tip[Simpler middle ground]
If your only concern is that the CloudFormation execution role gets `AdministratorAccess` by default, you do not need this feature. Instead bootstrap with `cdk bootstrap --cloudformation-execution-policies <your-managed-policy-arns>` and/or `--custom-permissions-boundary <name>`.
:::

### VAMS stack role customization (`vamsStacks`)

When `useCustomVamsStackRoles` is `true`, VAMS calls `iam.Role.customizeRoles` at the CDK app level. Because it is applied at the app level, a single IAM policy report covers the VAMS WAF stack, the core stack, and every nested stack.

| Field in `vamsStacks` | Description                                                                                                                                                                                                                                                                 |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `generateReportOnly`  | When `true` (discovery mode), VAMS still creates roles during synthesis but also writes `iam-policy-report.txt` and `iam-policy-report.json` to `cdk.out`. When `false`, role synthesis is prevented and any role not listed in `precreatedRoles` causes synthesis to fail. |
| `precreatedRoles`     | JSON object mapping each role's construct path to a pre-created IAM role name (`"<construct path>": "<role name>"`). The construct paths come from the generated report. See [`iamRoleConfig.json` format](#iamroleconfigjson-format) below for a complete example.         |

**Recommended workflow:**

1. Set `useCustomVamsStackRoles` to `true` and `generateReportOnly` to `true` in the `vamsStacks` section.
2. Run `cd infra && npx cdk synth`. VAMS writes `cdk.out/iam-policy-report.txt` (and `.json`) listing every role it would create, each with its trust policy and required permissions.
3. Hand the report to whoever pre-creates IAM roles in your organization. They create the roles outside of VAMS.
4. Populate `precreatedRoles` with a `"<construct path>": "<pre-created role name>"` entry for every role in the report, then set `generateReportOnly` to `false`.
5. Synthesize and deploy. Once every role is mapped, the synthesized templates reference the existing roles and contain no `AWS::IAM::Role` resources.

:::warning[Map every role]
With `generateReportOnly` set to `false`, synthesis fails if any role the constructs need is not present in `precreatedRoles`. Re-run the report whenever you enable a new VAMS feature or pipeline, since new features introduce new roles.
:::

#### `iamRoleConfig.json` format

The `precreatedRoles` field is a JSON object (a map), **not** an array. Each key is the **construct path** of a role exactly as it appears in the generated `iam-policy-report.txt` (the value shown in parentheses after `<missing role>`), and each value is the **name** of the IAM role you pre-created in your account. The key is a path string, not an ARN; the value is a plain role name, not an ARN.

The report lists each role like this:

```
<missing role> (vams-core-prod-us-east-1/StorageResourcesBuilder/BucketNotificationsHandler050a0587b7544547bf325f094a3db834/Role)
```

You take the text in parentheses as the key and map it to your pre-created role name. A complete `infra/config/policy/iamRoleConfig.json` with mock values looks like this:

```json
{
    "bootstrap": {
        "useCliCredentialsSynthesizer": false,
        "qualifier": "",
        "deployRoleArn": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/my-org-cdk-deploy-role",
        "cloudFormationExecutionRoleArn": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/my-org-cdk-cfn-exec-role",
        "lookupRoleArn": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/my-org-cdk-lookup-role",
        "fileAssetPublishingRoleArn": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/my-org-cdk-file-publishing-role",
        "imageAssetPublishingRoleArn": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/my-org-cdk-image-publishing-role",
        "fileAssetsBucketName": "",
        "imageAssetsRepositoryName": ""
    },
    "vamsStacks": {
        "generateReportOnly": false,
        "precreatedRoles": {
            "vams-core-prod-us-east-1/StorageResourcesBuilder/BucketNotificationsHandler050a0587b7544547bf325f094a3db834/Role": "my-org-vams-storage-bucketnotify-role",
            "vams-core-prod-us-east-1/ApiBuilder/VAMSWorkflowIAMRole/Resource": "my-org-vams-workflow-role",
            "vams-core-prod-us-east-1/AuthBuilder/Cognito/.../ServiceRole": "my-org-vams-auth-service-role",
            "vams-waf-prod-us-east-1/Wafv2CF/.../ServiceRole": "my-org-vams-waf-service-role"
        }
    }
}
```

:::note[Keys are deployment-specific]
The construct path keys begin with your full stack name (for example, `vams-core-prod-us-east-1`), which is derived from `name`, `app.baseStackName`, and the Region. If you change any of those values, the keys change and the report must be regenerated. Always copy the exact paths from your own `iam-policy-report.txt` rather than from this example. The values (`my-org-vams-*` above) are placeholders for whatever role names your IAM team assigns.
:::

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

| Category     | Description                                                                                                |
| ------------ | ---------------------------------------------------------------------------------------------------------- |
| `connectSrc` | External APIs and services the application can connect to via `XMLHttpRequest`, `WebSocket`, `EventSource` |
| `scriptSrc`  | External JavaScript libraries or CDNs that can be executed                                                 |
| `workerSrc`  | Web Worker and Service Worker sources                                                                      |
| `imgSrc`     | External image sources that can be loaded                                                                  |
| `mediaSrc`   | External media sources (audio/video) that can be loaded                                                    |
| `fontSrc`    | External font sources (for example, Google Fonts)                                                          |
| `styleSrc`   | External stylesheet sources that can be loaded                                                             |

**Behavior:**

-   **File not found** -- VAMS uses default CSP settings without failing the build.
-   **Invalid JSON** -- Logs a warning and uses default CSP settings.
-   **Empty arrays** -- Ignored; only default CSP sources are used for those categories.
-   **Duplicate prevention** -- Additional sources are merged with existing ones, avoiding duplicates.

:::warning[CSP security]
Only add trusted domains to your CSP configuration. Avoid using wildcards (`*`) as they compromise security. Regularly audit your CSP configuration and test changes in a development environment before deploying to production.
:::
