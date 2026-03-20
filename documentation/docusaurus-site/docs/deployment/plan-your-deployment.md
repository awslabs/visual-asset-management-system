# Plan your deployment

Before deploying Visual Asset Management System (VAMS), review the decisions on this page to determine the architecture, authentication method, and optional features that best fit your environment.

## Deployment modes

VAMS supports three deployment modes. Your choice depends on the AWS partition, network isolation requirements, and organizational security policies.

| Mode | Web distribution | Partition | VPC required | Description |
|---|---|---|---|---|
| **Commercial** | Amazon CloudFront + Amazon S3 | `aws` | Optional | Default mode. Uses Amazon CloudFront for global edge caching and static website hosting. |
| **GovCloud** | Application Load Balancer + Amazon S3 | `aws-us-gov` | Yes | For AWS GovCloud (US) Regions. Amazon CloudFront is not available; an Application Load Balancer (ALB) serves the web application. Supports full VPC isolation with VPC endpoints for restricted environments. |

:::info[GovCloud constraints]
When deploying to AWS GovCloud, the following services are unavailable or restricted:

- Amazon CloudFront is not supported. Use the ALB deployment mode.
- Amazon Location Service is not supported. Disable `app.useLocationService.enabled`.
- AWS WAF `AdvancedSecurityMode` for Amazon Cognito is not available (automatically suppressed).
:::


## Key decisions

### Authentication provider

VAMS supports three authentication approaches. You must choose exactly one.

| Option | Configuration | Description |
|---|---|---|
| **Amazon Cognito** (default) | `authProvider.useCognito.enabled: true` | VAMS creates and manages an Amazon Cognito user pool. Users receive a temporary password by email. Supports optional SAML federation. |
| **Amazon Cognito with SAML** | `authProvider.useCognito.enabled: true` and `useSaml: true` | Amazon Cognito with federated SAML from an external identity provider (IdP). Requires additional SAML configuration. |
| **External OAuth IdP** | `authProvider.useExternalOAuthIdp.enabled: true` | Bring your own OAuth 2.0 / OpenID Connect identity provider (for example, PingFederate, Okta). Requires configuring multiple IdP endpoint URLs and client credentials. |


### Web distribution

| Option | When to use | Configuration |
|---|---|---|
| **Amazon CloudFront** | Commercial AWS. Provides global edge caching, AWS-managed TLS, and a generated domain URL. | `useCloudFront.enabled: true` |
| **Amazon CloudFront with custom domain** | Commercial AWS with organizational branding requirements. Requires an AWS Certificate Manager (ACM) certificate in `us-east-1`. | `useCloudFront.customDomain.enabled: true` |
| **Application Load Balancer** | AWS GovCloud or when CloudFront is not permitted. Requires a registered domain name and an ACM certificate in the deployment Region. | `useAlb.enabled: true` |
| **API only (no web UI)** | Headless deployments driven entirely through API or CLI. | Both `useCloudFront.enabled: false` and `useAlb.enabled: false` |

:::danger[Mutual exclusion]
You cannot enable both Amazon CloudFront and ALB simultaneously. The deployment will fail validation if both are set to `true`.
:::


### Search capability

Amazon OpenSearch Service provides full-text search, filtering, and map-view functionality in the VAMS web interface.

| Option | Configuration | Notes |
|---|---|---|
| **OpenSearch Serverless** | `openSearch.useServerless.enabled: true` | Fully managed, pay-per-use. No VPC required. Default for commercial deployments. |
| **OpenSearch Provisioned** | `openSearch.useProvisioned.enabled: true` | Dedicated cluster with configurable instance types. Requires VPC with a minimum of 3 Availability Zones. |
| **No OpenSearch** | Both set to `false` | Search is disabled. The assets page returns all authorized assets without filtering. |

:::note[Choose only one]
You cannot enable both OpenSearch Serverless and OpenSearch Provisioned at the same time.
:::


### VPC configuration

| Option | Configuration | Notes |
|---|---|---|
| **No VPC** | `useGlobalVpc.enabled: false` | Simplest deployment. Not compatible with ALB, OpenSearch Provisioned, or container-based pipelines. |
| **VAMS-managed VPC** | `useGlobalVpc.enabled: true` with `vpcCidrRange` | VAMS creates a new VPC with isolated, private, and public subnets. Specify a CIDR range (for example, `10.1.0.0/16`). |
| **Import existing VPC** | `useGlobalVpc.enabled: true` with `optionalExternalVpcId` | Import an existing VPC by ID. Requires providing isolated subnet IDs and optionally private and public subnet IDs. See [Deploy the solution](deploy-the-solution.md) for the two-phase deployment process. |

:::tip[Automatic VPC enablement]
The VPC is automatically enabled when any of the following features are turned on: ALB deployment, OpenSearch Provisioned, or any container-based pipeline (Potree viewer, Gaussian splatting, GenAI labeling, RapidPipeline, ModelOps, Isaac Lab, 3D preview thumbnail).
:::


**Subnet sizing guidance:**

| Feature | IPs per subnet |
|---|---|
| ALB | Up to 8 (scales during runtime) |
| Container-based pipelines | ~2 per active workflow execution |
| Lambda functions in VPC | 1 per deployed function per subnet (~66 in v2.5) |
| VPC interface endpoints | 1 per endpoint per subnet |

A minimum of 128 IPv4 addresses per subnet is recommended.

### Encryption

| Option | Configuration | Notes |
|---|---|---|
| **AWS-managed keys** (default) | `useKmsCmkEncryption.enabled: false` | Uses default or AWS-managed encryption for Amazon S3, Amazon DynamoDB, Amazon SQS, and Amazon SNS. |
| **Customer-managed KMS key** | `useKmsCmkEncryption.enabled: true` | VAMS creates a customer-managed AWS KMS key and applies it to all storage resources. Optionally import an existing key with `optionalExternalCmkArn`. |

