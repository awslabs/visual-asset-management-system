# Service Quotas and Limits

This page documents the service quotas, default limits, and configurable thresholds for the Visual Asset Management System (VAMS). Some limits are inherent to AWS services, while others are configurable through the VAMS deployment configuration.

---

## API Limits

### API Gateway Throttling

VAMS uses Amazon API Gateway V2 (HTTP API) with configurable rate limiting.

| Parameter | Default | Configurable | Configuration Key |
|---|---|---|---|
| Global rate limit | 50 requests/second | Yes | `app.api.globalRateLimit` |
| Global burst limit | 100 requests | Yes | `app.api.globalBurstLimit` |
| Request timeout | 29 seconds | No | Amazon API Gateway hard limit |
| Authorizer cache TTL | 30 seconds | No | Set in CDK authorizer construct |

:::tip
The burst limit must be greater than or equal to the rate limit. Adjust both values in `infra/config/config.json` and redeploy to apply changes.
:::


### AWS Lambda Function Limits

All VAMS Lambda functions share the same configuration:

| Parameter | Value | Configurable |
|---|---|---|
| Timeout | 15 minutes | No (CDK constant) |
| Memory | 5,308 MB (4 vCPU) | No (CDK constant) |
| Runtime | Python 3.12 | No (CDK constant) |
| Concurrent executions | AWS account default (1,000) | Via AWS Service Quotas |

### Authentication Limits

| Parameter | Default | Configurable | Configuration Key |
|---|---|---|---|
| Credential/token timeout | 3,600 seconds (1 hour) | Yes | `app.authProvider.useCognito.credTokenTimeoutSeconds` |
| Presigned URL timeout | 86,400 seconds (24 hours) | Yes | `app.authProvider.presignedUrlTimeoutSeconds` |
| Upload initializations | 10 per user per minute | No | Hardcoded rate limit |

---

## Storage Limits

### Amazon DynamoDB

All VAMS DynamoDB tables use on-demand (pay-per-request) billing mode, which automatically scales to handle workload demands.

| Parameter | Value |
|---|---|
| Billing mode | On-demand (PAY_PER_REQUEST) |
| Maximum item size | 400 KB (DynamoDB service limit) |
| Metadata records per entity | 500 |
| Table count | 25+ tables |

:::info
On-demand mode has no provisioned throughput to configure. Amazon DynamoDB automatically allocates capacity based on traffic patterns. For sustained high-throughput workloads, monitor your account-level DynamoDB service quotas.
:::


### Amazon S3

| Parameter | Value |
|---|---|
| Maximum object size | 5 TB (Amazon S3 service limit) |
| Multipart upload threshold | 5 GB (parts required above this size) |
| Maximum parts per upload | 10,000 (Amazon S3 service limit) |
| Part size range | 5 MB to 5 GB |
| Bucket encryption | AWS KMS (when CMK enabled) or Amazon S3-managed |

### Amazon OpenSearch

| Parameter | Serverless | Provisioned |
|---|---|---|
| Index OCUs (default) | 2 index + 2 search | N/A |
| Data node instance type | N/A | Configurable (default: `r6g.large.search`) |
| Master node instance type | N/A | Configurable (default: `r6g.large.search`) |
| EBS volume size | N/A | Configurable (default: 240 GB per node) |
| Data nodes | N/A | 3 (requires 3-AZ VPC) |
| Master nodes | N/A | 3 |
| Engine version | OpenSearch 2.7 | OpenSearch 2.7 |

---

## Pipeline Limits

### General Pipeline Limits

| Parameter | Value |
|---|---|
| AWS Step Functions state transitions | Based on workflow complexity |
| Pipeline execution types | Lambda, Amazon SQS, Amazon EventBridge |
| Concurrent workflow executions per asset | Multiple (with different input files) |

### Pipeline-Specific Limits

| Pipeline | Parameter | Limit |
|---|---|---|
| 3D Preview Thumbnail | Maximum input file size | 100 GB |
| All ECS pipelines | Metadata JSON input | 8,000 characters |
| Gaussian Splat Toolbox | GPU instance required | `g6e.2xlarge` or `g5.xlarge` |
| Isaac Lab Training | GPU instance required | `g6e.2xlarge` or `g5.xlarge` |
| RapidPipeline (EKS) | Node instance type | Configurable |
| RapidPipeline (EKS) | Job timeout | Configurable |
| RapidPipeline (EKS) | Job backoff limit | Configurable |

---

## Upload Limits

### File Upload Restrictions

| Parameter | Value |
|---|---|
| Blocked file extensions | `.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat` |
| Upload stage 1 rate limit | 10 initializations per user per minute |
| File validation | Extension and MIME type checks on API upload only |

### Blocked MIME Types

The following MIME types are rejected during file upload validation:

| MIME Type | Description |
|---|---|
| `application/java-archive` | Java archive files |
| `application/x-msdownload` | Windows executables |
| `application/x-sh` | Shell scripts |
| `application/x-php` | PHP scripts |
| `application/javascript` | JavaScript files |
| `application/x-powershell` | PowerShell scripts |
| `application/vbscript` | VBScript files |
| `application/x-ms-dos-executable` | DOS executables |
| `application/x-bat-script` | Batch scripts |
| `application/vnd.ms-word.document.macroEnabled.12` | Macro-enabled Word documents |

---

## Amazon Cognito Limits

When using Amazon Cognito as the authentication provider:

| Parameter | Default | Notes |
|---|---|---|
| User pool users | 40,000,000 | Amazon Cognito service default |
| Custom attributes per user | 50 | Amazon Cognito service limit |
| Groups per user pool | 10,000 | Amazon Cognito service limit |
| Invitation email delivery | Via Amazon SES or Cognito default | Rate limits apply |

:::note
Amazon Cognito service quotas can be increased through the AWS Service Quotas console if your deployment requires higher limits.
:::


---

## Network and VPC Limits

| Parameter | Value |
|---|---|
| VPC endpoints per configuration | 1-11 per Availability Zone |
| Availability Zones required (ALB) | 2 minimum |
| Availability Zones required (OpenSearch Provisioned) | 3 minimum |
| Availability Zones required (Lambda in VPC) | 1 minimum |

For detailed cost implications of VPC endpoint configurations, see the [cost estimates](../overview/costs.md).
