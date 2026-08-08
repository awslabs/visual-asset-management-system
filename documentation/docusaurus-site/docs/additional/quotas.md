# Service Quotas and Limits

This page documents the service quotas, default limits, and configurable thresholds for the Visual Asset Management System (VAMS). Some limits are inherent to AWS services, while others are configurable through the VAMS deployment configuration.

---

## API Limits

### API Gateway Throttling

VAMS uses Amazon API Gateway REST API with configurable rate limiting.

| Parameter            | Default            | Configurable | Configuration Key                         |
| -------------------- | ------------------ | ------------ | ----------------------------------------- |
| Global rate limit    | 50 requests/second | Yes          | `app.api.apiGatewayRest.globalRateLimit`  |
| Global burst limit   | 100 requests       | Yes          | `app.api.apiGatewayRest.globalBurstLimit` |
| Request timeout      | 29 seconds         | No           | Amazon API Gateway hard limit             |
| Authorizer cache TTL | 30 seconds         | No           | Set in CDK authorizer construct           |

:::tip
The burst limit must be greater than or equal to the rate limit. Adjust both values in `infra/config/config.json` and redeploy to apply changes.
:::

### AWS Lambda Function Limits

All VAMS Lambda functions share the same configuration:

| Parameter             | Value                       | Configurable           |
| --------------------- | --------------------------- | ---------------------- |
| Timeout               | 15 minutes                  | No (CDK constant)      |
| Memory                | 5,308 MB (4 vCPU)           | No (CDK constant)      |
| Runtime               | Python 3.12                 | No (CDK constant)      |
| Concurrent executions | AWS account default (1,000) | Via AWS Service Quotas |

### Authentication Limits

| Parameter                | Default                   | Configurable | Configuration Key                                     |
| ------------------------ | ------------------------- | ------------ | ----------------------------------------------------- |
| Credential/token timeout | 3,600 seconds (1 hour)    | Yes          | `app.authProvider.useCognito.credTokenTimeoutSeconds` |
| Presigned URL timeout    | 86,400 seconds (24 hours) | Yes          | `app.authProvider.presignedUrlTimeoutSeconds`         |
| Upload initializations   | 10 per user per minute    | No           | Hardcoded rate limit                                  |

---

## Storage Limits

### Amazon DynamoDB

All VAMS DynamoDB tables use on-demand (pay-per-request) billing mode, which automatically scales to handle workload demands.

| Parameter                   | Value                           |
| --------------------------- | ------------------------------- |
| Billing mode                | On-demand (PAY_PER_REQUEST)     |
| Maximum item size           | 400 KB (DynamoDB service limit) |
| Metadata records per entity | 500                             |
| Table count                 | 25+ tables                      |

:::info
On-demand mode has no provisioned throughput to configure. Amazon DynamoDB automatically allocates capacity based on traffic patterns. For sustained high-throughput workloads, monitor your account-level DynamoDB service quotas.
:::

### Amazon S3

| Parameter                  | Value                                           |
| -------------------------- | ----------------------------------------------- |
| Maximum object size        | 5 TB (Amazon S3 service limit)                  |
| Multipart upload threshold | 5 GB (parts required above this size)           |
| Maximum parts per upload   | 10,000 (Amazon S3 service limit)                |
| Part size range            | 5 MB to 5 GB                                    |
| Bucket encryption          | AWS KMS (when CMK enabled) or Amazon S3-managed |

### Amazon OpenSearch

| Parameter                 | Serverless         | Provisioned                                                                                         |
| ------------------------- | ------------------ | --------------------------------------------------------------------------------------------------- |
| Index OCUs (default)      | 2 index + 2 search | N/A                                                                                                 |
| Data node instance type   | N/A                | Configurable (default: `r7g.large.search`)                                                          |
| Master node instance type | N/A                | Configurable (default: `r7g.large.search`)                                                          |
| EBS volume size           | N/A                | Configurable (default: 120 GB per node)                                                             |
| Data nodes                | N/A                | One per Availability Zone (2 or 3)                                                                  |
| Master nodes              | N/A                | 3                                                                                                   |
| Engine version            | Managed by AWS     | OpenSearch 3.x (OpenSearch 2.x in the AWS European Sovereign Cloud, which does not yet support 3.x) |

---

## Pipeline Limits

### General Pipeline Limits

| Parameter                                | Value                                  |
| ---------------------------------------- | -------------------------------------- |
| AWS Step Functions state transitions     | Based on workflow complexity           |
| Pipeline execution types                 | Lambda, Amazon SQS, Amazon EventBridge |
| Concurrent workflow executions per asset | Multiple (with different input files)  |

### Pipeline-Specific Limits

| Pipeline               | Parameter               | Limit                        |
| ---------------------- | ----------------------- | ---------------------------- |
| 3D Preview Thumbnail   | Maximum input file size | 100 GB                       |
| All ECS pipelines      | Metadata JSON input     | 8,000 characters             |
| Gaussian Splat Toolbox | GPU instance required   | `g6e.2xlarge` or `g5.xlarge` |
| Isaac Lab Training     | GPU instance required   | `g6e.2xlarge` or `g5.xlarge` |
| RapidPipeline (EKS)    | Node instance type      | Configurable                 |
| RapidPipeline (EKS)    | Job timeout             | Configurable                 |
| RapidPipeline (EKS)    | Job backoff limit       | Configurable                 |

### Pipeline Template and Tag-Schema Limits

Bounds on a pipeline configuration template and the typed tag schema that supplies its `{{tagName}}`
placeholders. Every limit in this table **rejects the create or update request** with a `400` response,
so an authoring mistake is reported immediately rather than surfacing at run time.

| Parameter                              | Value                            |
| -------------------------------------- | -------------------------------- |
| Tag definitions per tag schema         | 250                              |
| `tagKey` length                        | 128 characters                   |
| `label` / `description` length         | 1,024 characters each            |
| `enumValues` entries per `enum` tag    | 250                              |
| `enumValues` entry length              | 256 characters                   |
| `default` value length                 | 4,096 characters (serialized)    |
| `inputInstructions` length             | 4,096 characters                 |
| Input-file filter patterns per list    | 250 (`allow` and `exclude` each) |
| Input-file filter pattern length       | 512 characters                   |
| Auxiliary preview suffix length        | 256 characters                   |
| AWS Deadline Cloud job template length | 256 KB                           |
| Pipeline task timeout                  | 604,800 seconds (7 days)         |

:::note
A template's `configBody` has no character limit of its own. A body too large to store inline is
offloaded to Amazon S3 automatically and the record keeps a pointer to it, so template size is bounded
by the absolute storage cap rather than by a request-validation rule.
:::

### Workflow Execution Limits

Bounds that apply when an execution is launched. These divide into two kinds, and the difference
matters when reading a result:

-   **Rejected** — the request fails with a `400` and nothing runs.
-   **Truncated and reported** — the execution proceeds, and the response's `warnings` array names what
    was dropped. A run that hits one of these succeeds with less input than was available, so treat a
    warning as a signal to narrow the run rather than as noise.

| Parameter                                    | Value                          | Exceeding it           |
| -------------------------------------------- | ------------------------------ | ---------------------- |
| Input files per execution                    | 1,000                          | Rejected               |
| Metadata-source assets per execution         | 1,000                          | Rejected               |
| Per-pipeline parameter entries per execution | 100                            | Rejected               |
| Template tag values per pipeline             | 250                            | Rejected               |
| Template tag key length                      | 128 characters                 | Rejected               |
| Template tag value length                    | 65,536 characters (serialized) | Rejected               |
| `customTemplateOverride` length per pipeline | 5 MB                           | Rejected               |
| Output base-path extension length            | 1,024 characters               | Rejected               |
| Metadata entries captured per entity         | 1,000                          | Truncated and reported |
| Metadata bytes captured per entity           | 300 KB                         | Truncated and reported |
| Metadata bytes captured per execution        | 128 MB                         | Truncated and reported |

:::info
**The input-file limit counts individually specified files only.** It bounds the number of file
selections an execute request enumerates. A whole-asset selection, a folder selection, and any files a
pipeline reads for itself from the asset directory once it is running are **not** counted and are not
limited — a whole-asset run over an asset holding tens of thousands of files is a single selection.
Reach the limit only by naming more than 1,000 individual files in one request; select the folder or the
whole asset instead.
:::

:::info
The per-entity metadata bounds are applied to each database, each asset, and each file's metadata and
attributes **independently**, and keys are kept in sorted order until the bound is reached. The
per-execution bound is applied across the whole run: rows are considered broadest-first, and a row that
does not fit is emptied rather than partially kept, so a reader never sees a silently half-populated
entity. A tag value is generous at 64 KB because a tag may legitimately carry a long generative-AI
prompt.
:::

### Execution Detail and Log Limits

Bounds on what a single API response returns for one execution. These are response-shaping limits, not
data limits — the full data remains stored and reachable.

| Parameter                                      | Value             | Exceeding it                                      |
| ---------------------------------------------- | ----------------- | ------------------------------------------------- |
| Rows read per detail collection                | 2,000             | Truncated, flagged                                |
| Input rows returned per detail collection      | 1,000             | Truncated, flagged                                |
| Bytes returned per detail collection           | 4 MB              | Truncated, flagged                                |
| Total detail response size                     | 5 MB              | Truncated, flagged                                |
| Metadata bytes guaranteed in a detail response | 256 KB minimum    | Reserved so a file-heavy run still shows metadata |
| Metadata rows per detail-metadata page         | 500 (default 100) | Rejected above the maximum                        |
| Executions per global list page                | 100               | Rejected above the maximum                        |
| Free-form text bytes per execution record      | 380 KB            | Truncated, flagged                                |
| Log text bytes per execution record            | 390 KB            | Truncated                                         |

:::note
A truncated detail collection is named in the response's `truncatedCollections` array, and a truncated
configuration or result body sets its own flag (`renderedConfigTruncated`, `resultsContentTruncated`)
alongside a pointer to the complete object in Amazon S3. Retrieve a large collection through its paged
endpoint rather than the detail response.
:::

---

## Upload Limits

### File Upload Restrictions

| Parameter                 | Value                                                                                                                    |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Blocked file extensions   | `.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat` |
| Upload stage 1 rate limit | 10 initializations per user per minute                                                                                   |
| File validation           | Extension and MIME type checks on API upload only                                                                        |

### Blocked MIME Types

The following MIME types are rejected during file upload validation:

| MIME Type                                          | Description                  |
| -------------------------------------------------- | ---------------------------- |
| `application/java-archive`                         | Java archive files           |
| `application/x-msdownload`                         | Windows executables          |
| `application/x-sh`                                 | Shell scripts                |
| `application/x-php`                                | PHP scripts                  |
| `application/javascript`                           | JavaScript files             |
| `application/x-powershell`                         | PowerShell scripts           |
| `application/vbscript`                             | VBScript files               |
| `application/x-ms-dos-executable`                  | DOS executables              |
| `application/x-bat-script`                         | Batch scripts                |
| `application/vnd.ms-word.document.macroEnabled.12` | Macro-enabled Word documents |

---

## Amazon Cognito Limits

When using Amazon Cognito as the authentication provider:

| Parameter                  | Default                           | Notes                          |
| -------------------------- | --------------------------------- | ------------------------------ |
| User pool users            | 40,000,000                        | Amazon Cognito service default |
| Custom attributes per user | 50                                | Amazon Cognito service limit   |
| Groups per user pool       | 10,000                            | Amazon Cognito service limit   |
| Invitation email delivery  | Via Amazon SES or Cognito default | Rate limits apply              |

:::note
Amazon Cognito service quotas can be increased through the AWS Service Quotas console if your deployment requires higher limits.
:::

---

## Network and VPC Limits

| Parameter                                            | Value                      |
| ---------------------------------------------------- | -------------------------- |
| VPC endpoints per configuration                      | 1-11 per Availability Zone |
| Availability Zones required (ALB)                    | 2 minimum                  |
| Availability Zones required (OpenSearch Provisioned) | 3 minimum                  |
| Availability Zones required (Lambda in VPC)          | 1 minimum                  |

For detailed cost implications of VPC endpoint configurations, see the [cost estimates](../overview/costs.md).
