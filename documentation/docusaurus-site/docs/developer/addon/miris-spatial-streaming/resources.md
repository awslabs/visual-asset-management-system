---
sidebar_label: Resources
---

# Miris Add-on Resources

## AWS resources

The add-on deploys `MirisBuilderNestedStack` (`infra/lib/nestedStacks/addon/miris/mirisBuilder-nestedStack.ts`) inside the add-on builder stack, and, with `upload.enabled`, `MirisUploadBuilderNestedStack` beside it. It always creates the two API Lambda functions and the custom resource that writes the GLOBAL **Miris stream** file metadata schema. The other resources depend on the viewer key mode and on the upload pipeline.

### Lambda functions

| Function                        | Created when     | Timeout    | Purpose                                                                                                                                                 |
| ------------------------------- | ---------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MirisGetAssetStatusFunction`   | Always           | 30 seconds | Serves `GET /addon/miris/asset-status`                                                                                                                  |
| `MirisMintViewerKeyFunction`    | Always           | 30 seconds | Serves `POST /addon/miris/viewer-key`                                                                                                                   |
| `MirisViewerKeyCleanupFunction` | `perView` mode   | 5 minutes  | Reads TTL deletions from the viewer key table's stream and deletes each expired key from Miris                                                          |
| The upload pipeline's functions | `upload.enabled` | 15 minutes | `vamsExecuteMirisUpload`, `mirisUploadGate`, `openPipeline`, `constructPipeline`, `pipelineEnd` (see [Architecture](./upload-pipeline.md#architecture)) |

The functions run outside the VPC; configuration validation rejects the add-on with `app.useGlobalVpc.useForAllLambdas` (see [Network](./security-and-networking.md#network)). The Lambda service creates their log groups (`/aws/lambda/<functionName>`) on first invocation.

### Amazon DynamoDB table

`MirisViewerKeyStorageTable` is created in `perView` mode only, with one row per minted viewer key and no key material. The partition key is `mirisViewerKeyId` (String); the table has no index. Each row carries `databaseId`, `assetId`, `filePath` (asset-relative), `userId`, `mirisAssetUuid`, `createdAt` and `expiresAt` (RFC 3339 UTC), and `ttl`, the key's expiry plus one hour in epoch seconds. Its `KEYS_ONLY` stream feeds the cleanup function, which deletes the key from Miris when Amazon DynamoDB TTL removes the row.

The table follows the conventions of the core tables: on-demand billing, point-in-time recovery, the deployment's AWS KMS key when one is configured, the `RETAIN` removal policy and an auto-generated name, so it is retained when the add-on is disabled or switched to `static` mode. Its name is published as the AWS Systems Manager parameter `/<name>-<baseStackName>-<region>/resourceNames/addon/dynamoTables/mirisViewerKeyStorage`.

### Amazon SQS queues and Amazon EventBridge rule

| Resource                          | Created when     | Purpose                                                                                                                                         |
| --------------------------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **MirisViewerKeyCleanupDLQ**      | `perView` mode   | Dead-letter queue for viewer key cleanup batches that fail their retries (14-day retention)                                                     |
| **MirisUploadExecutionEndedRule** | `upload.enabled` | Fails the workflow task and releases the claim when the upload's inner execution fails, times out or is aborted without its end state reporting |
| **MirisUploadExecutionEndedDLQ**  | `upload.enabled` | Dead-letter queue for the events that rule cannot deliver to `pipelineEnd` after two retries (14-day retention)                                 |

The queues use the deployment's AWS KMS key when one is configured, else SQS-managed encryption, and are destroyed with the stack.

### AWS Secrets Manager

| Secret                   | Created when  | Removal                  | Purpose                                                                                                                                                              |
| ------------------------ | ------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **MirisViewerKeySecret** | `static` mode | Destroyed with the stack | The deployment viewer key (`viewerKey`), filled at deployment time from a custom resource's code asset (see [Credentials](./security-and-networking.md#credentials)) |

The Miris Integration Key is in a secret the operator creates and names in `apiKeySecretArn`. VAMS neither creates nor deletes that secret.

### Upload pipeline resources

With `upload.enabled`, the upload builder stack creates an AWS Batch compute environment, job queue and job definition on AWS Fargate in the private subnets. The job definition is named `MirisUploadJob_<config.name>_<baseStackName>-<region>` plus a 10-character hash. Its logs are in these groups, destroyed with the stack:

| Log group                                            | Contents                           |
| ---------------------------------------------------- | ---------------------------------- |
| `/aws/vendedlogs/VAMSStateMachine-MirisUpload<hash>` | The inner state machine            |
| `/aws/vendedlogs/Pipelines/MirisUpload<hash>`        | The upload container (AWS Fargate) |

The pipeline also keeps objects of its own in the auxiliary bucket: its duplicate-upload claims under `locks/miris-upload/<databaseId>/<assetId>/<fingerprint>.claim` (see [Duplicate uploads and re-runs](./upload-pipeline.md#duplicate-uploads-and-re-runs)) and the dependency manifests of its uploads under `pipelines/miris-upload/manifests/<databaseId>/<assetId>/<sha256>.json`.

## Monitoring

The add-on creates up to three Amazon CloudWatch alarms, named `<configuration name>-<app.baseStackName>-<region>-…`, with no alarm action. Subscribe an Amazon SNS topic or another action to them.

| Alarm                        | Goes into alarm when                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `…-mirisAuthFailures`        | Miris refuses the Integration Key after a re-read, in any 5-minute period (metric `MirisAuthFailures` in namespace `VAMS/<configuration name>-<app.baseStackName>-<region>/Miris`).                                                                                                                                                                                                 |
| `…-mirisViewerKeyCleanupDLQ` | `perView` mode only: a cleanup batch is in the dead-letter queue. The queue holds only the stream's shard and sequence range; the key IDs are in the cleanup function's log ("Could not delete Miris viewer key"). The keys have already expired, so deleting them in Miris after fixing the cause is housekeeping.                                                                 |
| `…-mirisUploadEndRuleDLQ`    | `upload.enabled` only: the execution-ended rule could not deliver an event to `pipelineEnd`, so that run's workflow task stays running until its timeout and its claim stays pending until it goes stale. Fix the cause in the `pipelineEnd` function's log, then clear the claim as [Duplicate uploads and re-runs](./upload-pipeline.md#duplicate-uploads-and-re-runs) describes. |

An upload run the Integration Key fails is reported on the run itself, as a failed execution.
