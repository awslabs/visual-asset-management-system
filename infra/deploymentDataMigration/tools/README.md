# VAMS Utility Scripts

This directory contains standalone utility scripts for VAMS administration and maintenance tasks. These scripts are designed to be run from a local machine with appropriate AWS credentials.

## Available Utilities

### Reindex Utility (`reindex_utility.py`)

Re-indexes Amazon OpenSearch and any attached downstream indexers (such as the Garnet Framework addon) for assets and files. It reads the asset records from Amazon DynamoDB and the file objects in the registered Amazon S3 asset buckets, and re-publishes them to the configured indexing pipeline (Amazon SNS topics that trigger the indexer Lambda functions).

The utility supports two run modes, selected with `--mode`:

-   **`lambda` (default)** — Invokes the deployed reindexer AWS Lambda function. All reindexing runs in the cloud; no direct AWS resource access is required locally. This is the recommended mode for typical datasets. It is bound by the **15-minute Lambda maximum execution time**.
-   **`direct`** — Imports the backend reindexer handler code and runs it locally in the Python process, with **no execution-time limit**. Use this for very large asset repositories where the Lambda would otherwise exceed 15 minutes and leave records unindexed. The local process still calls AWS (DynamoDB, S3, SSM, and OpenSearch) using your local AWS credentials.

**When to use:**

-   After a major data migration or version upgrade
-   When OpenSearch indexes are out of sync with DynamoDB source data
-   After enabling or reconfiguring OpenSearch on an existing deployment
-   After enabling the Garnet Framework addon on a deployment with existing data
-   To rebuild indexes after accidental index corruption or deletion

**When to use `direct` mode specifically:** the deployed Lambda cannot run longer than 15 minutes. For very large repositories (millions of assets or files), a Lambda-mode reindex can hit that limit and stop before every record is reindexed. Direct mode runs the same handler locally with no time limit, so it can process arbitrarily large repositories in a single run.

#### Lambda mode

**Prerequisites:**

-   Python 3.6+
-   `boto3` installed (`pip install boto3`)
-   AWS credentials with `lambda:InvokeFunction` permission
-   The reindexer Lambda function deployed as part of the VAMS CDK stack (function name available in CDK stack outputs as `OpenSearchReindexerFunctionNameOutput`)

**Quick start:**

```bash
# Reindex both assets and files (lambda mode is the default)
python reindex_utility.py --function-name vams-prod-reindexer --operation both

# Dry run (no changes)
python reindex_utility.py --function-name vams-prod-reindexer --operation both --dry-run

# Reindex assets only
python reindex_utility.py --function-name vams-prod-reindexer --operation assets

# Reindex files only with a limit (for testing)
python reindex_utility.py --function-name vams-prod-reindexer --operation files --limit 100

# Clear indexes before reindexing
python reindex_utility.py --function-name vams-prod-reindexer --operation both --clear-indexes

# Asynchronous invocation (for large datasets)
python reindex_utility.py --function-name vams-prod-reindexer --operation both --async

# Use a specific AWS profile and region
python reindex_utility.py --function-name vams-prod-reindexer --operation both --profile my-profile --region us-west-2
```

#### Direct mode (local run, no 15-minute limit)

Direct mode runs the backend reindexer handler locally. Because it executes the handler code instead of invoking the Lambda, you must provide all of the handler's configuration inputs (the same values the Lambda receives as environment variables) and the handler's Python libraries. The backend source location defaults to the `backend/backend` directory resolved relative to the script, so `--backend-path` is only needed to override a non-standard checkout layout.

**Prerequisites:**

-   Python 3.6+ (use the same Python version as the Lambda runtime, 3.12, where possible)
-   The VAMS backend source available locally (the `backend/backend` directory that contains the `handlers` and `common` packages)
-   The backend reindexer handler's Python libraries installed locally:

    -   `boto3`
    -   `botocore`
    -   `urllib3`
    -   `aws-lambda-powertools` — used by the backend `customLogging` logger that the handler imports
    -   `opensearch-py` — only required when using `--clear-indexes` (the handler imports it lazily to clear the indexes)

    ```bash
    pip install boto3 botocore urllib3 aws-lambda-powertools opensearch-py
    ```

    The handler's other imports (`common.validators`, `common.s3MetadataKeys`, `common.s3PathPatterns`, `common.dynamoDbMetadataKeys`) use only the Python standard library, so no further packages are required.

    These libraries are only needed for direct mode. The script imports the backend handler lazily — only when `--mode direct` runs — and `opensearch-py` is imported only when `--clear-indexes` is also used. Lambda mode loads none of them, so a lambda-mode run requires only `boto3`.

-   AWS credentials with the same permissions the reindexer Lambda role has — read access to the asset, S3-asset-bucket, and asset-file-metadata DynamoDB tables; write access to the asset-file-metadata table (the touch/delete records); `s3:ListBucket` and `s3:GetObject` on the asset buckets (the file reindex lists each bucket and reads object metadata); `ssm:GetParameter` for the index-name and endpoint parameters; and OpenSearch access (only needed for `--clear-indexes`).

**Inputs:** the table names and SSM parameter names mirror the environment variables configured on the reindexer Lambda and are available in the CDK stack outputs / SSM and on the Lambda's environment configuration. All inputs below are required **except** `--backend-path`, `--opensearch-type`, and `--region`, which default as noted.

| Argument                                   | Maps to env var                          | Description                                                                                                                                                       |
| :----------------------------------------- | :--------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--backend-path`                           | (sys.path)                               | Path to the `backend/backend` source directory. Defaults to the backend source resolved relative to the script; override only for a non-standard checkout layout. |
| `--asset-storage-table-name`               | `ASSET_STORAGE_TABLE_NAME`               | DynamoDB asset storage table                                                                                                                                      |
| `--s3-asset-buckets-storage-table-name`    | `S3_ASSET_BUCKETS_STORAGE_TABLE_NAME`    | DynamoDB S3 asset buckets storage table                                                                                                                           |
| `--asset-file-metadata-storage-table-name` | `ASSET_FILE_METADATA_STORAGE_TABLE_NAME` | DynamoDB asset/file metadata table (touch/delete target)                                                                                                          |
| `--opensearch-asset-index-ssm-param`       | `OPENSEARCH_ASSET_INDEX_SSM_PARAM`       | SSM parameter holding the asset index name                                                                                                                        |
| `--opensearch-file-index-ssm-param`        | `OPENSEARCH_FILE_INDEX_SSM_PARAM`        | SSM parameter holding the file index name                                                                                                                         |
| `--opensearch-endpoint-ssm-param`          | `OPENSEARCH_ENDPOINT_SSM_PARAM`          | SSM parameter holding the OpenSearch endpoint                                                                                                                     |
| `--opensearch-type`                        | `OPENSEARCH_TYPE`                        | `serverless` or `provisioned` (default `provisioned`)                                                                                                             |
| `--region`                                 | `AWS_REGION`                             | AWS region for the local AWS SDK calls and OpenSearch signing                                                                                                     |

**Quick start** (run from `infra/deploymentDataMigration/tools/`; `--backend-path` is omitted so it uses the default location):

```bash
python reindex_utility.py --mode direct --operation both \
    --asset-storage-table-name vams-prod-assetStorage \
    --s3-asset-buckets-storage-table-name vams-prod-s3AssetBuckets \
    --asset-file-metadata-storage-table-name vams-prod-assetFileMetadata \
    --opensearch-asset-index-ssm-param /vams-prod/aos/assetIndexName \
    --opensearch-file-index-ssm-param /vams-prod/aos/fileIndexName \
    --opensearch-endpoint-ssm-param /vams-prod/aos/endPoint \
    --opensearch-type provisioned \
    --region us-east-1
```

`--operation`, `--dry-run`, `--limit`, `--profile`, and `--region` work the same in both modes.

**Clearing indexes in direct mode.** The bulk reindex (touch-and-delete) only uses Amazon DynamoDB, Amazon S3, and AWS Systems Manager, which are reachable from a local machine, so direct mode works from outside the VPC. `--clear-indexes` is different: it connects to the OpenSearch endpoint directly. A **provisioned** domain is always inside the VPC, so direct mode **rejects** `--clear-indexes` for `--opensearch-type provisioned` (the endpoint is not reachable locally). A **serverless** collection is VPC-restricted when it is private (`openSearch.useServerless.allowPublic = false`), which routes access through a VPC endpoint; direct mode allows `--clear-indexes` for serverless but warns, because it will fail against a VPC-restricted (private) collection.

To clear indexes and then reindex a provisioned (or VPC-restricted serverless) domain, clear in **lambda mode** (which runs inside the VPC) with a tiny limit so it only performs the clear, then run the bulk reindex in **direct mode** without `--clear-indexes`:

```bash
# 1. Clear the indexes via the deployed Lambda (runs in the VPC), doing minimal reindex work
python reindex_utility.py --function-name <reindexer-fn> --operation both --clear-indexes --limit 1

# 2. Bulk reindex locally with no time limit (no --clear-indexes)
python reindex_utility.py --mode direct --operation both \
    --asset-storage-table-name ... [other inputs] ... --opensearch-type provisioned
```

#### Tag objects synced by earlier releases (`--tag-synced`)

From v2.7, bucket sync writes two object tags on every file version it syncs, `VAMS:databaseId` and `VAMS:assetId`, instead of copying the object onto itself with `databaseid` / `assetid` metadata. Objects synced before the upgrade keep that metadata and have no tags. `--tag-synced` tags the current version of each of them, with the values, encoding and skip rules bucket sync uses. It is never run automatically and replaces the reindex for that run.

```bash
# See what would be tagged (reads each object's tags, writes none)
python reindex_utility.py --tag-synced --dry-run \
    --resource-names-ssm-param-prefix /vams-prod-us-east-1/resourceNames --region us-east-1

# Tag them
python reindex_utility.py --tag-synced \
    --resource-names-ssm-param-prefix /vams-prod-us-east-1/resourceNames --region us-east-1
```

-   **Prerequisite:** the `assetLocationIndex` migration step has completed (it writes the index's completion marker). The run is refused until then, because the asset location index decides which asset holds each object; the object's own metadata is not read.
-   **Inputs:** `--resource-names-ssm-param-prefix` is the core stack output `ResourceNamesSSMParamPrefixOutput`; the asset and bucket table names are read under it (`--asset-storage-table-name` and `--s3-asset-buckets-storage-table-name` override them). It runs locally with the backend source (`--backend-path`) and its Python libraries, like direct mode, using your AWS credentials, which need `dynamodb:Scan` on the bucket table, `dynamodb:Query` on the asset table's `AssetLocationGSI`, `ssm:GetParameter(s)ByPath` under the prefix, and `s3:ListBucket`, `s3:GetObjectTagging` and `s3:PutObjectTagging` on each asset bucket.
-   **What it tags:** every object of every bucket registration whose `tagSyncedObjects` is not `false`, held by a live asset (the asset whose stored location is the deepest one holding the object; an object whose deepest owner is archived is `notAttributed`, also inside a live asset's location), except folder markers and VAMS system folders. Customer tags are kept and only the two `VAMS:` keys change; an object whose own tags leave no room for both (more than 8 customer tags) is skipped (`tagLimit`). Each object costs one `GetObjectTagging` and, when the VAMS tags are missing or name another asset, one `PutObjectTagging`. A tag write creates no object version, changes no ETag or LastModified, and raises no event bucket sync processes; a bucket with Amazon EventBridge notifications on sends "Object Tags Added" events to its own rules.
-   **Report:** every object it did not tag (or, in a dry run, would tag) is a line in `tagSynced-<dryRun|run>-report.jsonl` (`--report-file` to change it), with the reason: `notAttributed`, `ambiguous` (two assets claim the location), `systemKey`, `tagLimit`, `accessDenied`. An access-denied bucket is reported once and its remaining objects are not attempted; the exit code is then `1`. A second run writes nothing for objects already tagged. `--limit` bounds the number of objects read.

For full documentation, see the [Reindex Utility](https://awslabs.github.io/visual-asset-management-system/developer/utilities/reindex) page in the VAMS documentation.
