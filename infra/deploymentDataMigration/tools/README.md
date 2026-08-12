# VAMS Utility Scripts

This directory contains standalone utility scripts for VAMS administration and maintenance tasks. These scripts are designed to be run from a local machine with appropriate AWS credentials.

## Available Utilities

### Reindex Utility (`reindex_utility.py`)

Re-indexes Amazon OpenSearch and any attached downstream indexers (such as the Garnet Framework addon) for assets and files. It reads all asset and file records from Amazon DynamoDB and re-publishes them to the configured indexing pipeline (Amazon SNS topics that trigger the indexer Lambda functions).

The utility supports two run modes, selected with `--mode`:

-   **`lambda` (default)** — Invokes the deployed reindexer AWS Lambda function. All reindexing runs in the cloud; no direct AWS resource access is required locally. This is the recommended mode for typical datasets. It is bound by the **15-minute Lambda maximum execution time**.
-   **`direct`** — Imports the backend reindexer handler code and runs it locally in the Python process, with **no execution-time limit**. Use this for very large asset repositories where the Lambda would otherwise exceed 15 minutes and leave records unindexed. The local process still calls AWS (DynamoDB, SSM, and OpenSearch) using your local AWS credentials.

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
-   The reindexer Lambda function deployed as part of the VAMS CDK stack (function name available in CDK stack outputs as `ReindexerFunctionNameOutput`)

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
    -   `opensearch-py` — only required when using `--clear-indexes` (the handler imports it lazily to clear the indexes)

    ```bash
    pip install boto3 botocore urllib3 opensearch-py
    ```

    The handler's other imports (`common.validators`, `common.s3MetadataKeys`, `common.s3PathPatterns`, `common.dynamoDbMetadataKeys`) use only the Python standard library, so no further packages are required.

    These libraries are only needed for direct mode. The script imports the backend handler lazily — only when `--mode direct` runs — and `opensearch-py` is imported only when `--clear-indexes` is also used. Lambda mode loads none of them, so a lambda-mode run requires only `boto3`.

-   AWS credentials with the same permissions the reindexer Lambda role has — read access to the asset, S3-asset-bucket, and asset-file-metadata DynamoDB tables; write access to the asset-file-metadata table (the touch/delete records); `ssm:GetParameter` for the index-name and endpoint parameters; and OpenSearch access (only needed for `--clear-indexes`).

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

**Clearing indexes in direct mode.** The bulk reindex (touch-and-delete) only uses Amazon DynamoDB and AWS Systems Manager, which are reachable from a local machine, so direct mode works from outside the VPC. `--clear-indexes` is different: it connects to the OpenSearch endpoint directly. A **provisioned** domain is always inside the VPC, so direct mode **rejects** `--clear-indexes` for `--opensearch-type provisioned` (the endpoint is not reachable locally). A **serverless** collection is VPC-restricted when it is private (`openSearch.useServerless.allowPublic = false`), which routes access through a VPC endpoint; direct mode allows `--clear-indexes` for serverless but warns, because it will fail against a VPC-restricted (private) collection.

To clear indexes and then reindex a provisioned (or VPC-restricted serverless) domain, clear in **lambda mode** (which runs inside the VPC) with a tiny limit so it only performs the clear, then run the bulk reindex in **direct mode** without `--clear-indexes`:

```bash
# 1. Clear the indexes via the deployed Lambda (runs in the VPC), doing minimal reindex work
python reindex_utility.py --function-name <reindexer-fn> --operation both --clear-indexes --limit 1

# 2. Bulk reindex locally with no time limit (no --clear-indexes)
python reindex_utility.py --mode direct --operation both \
    --asset-storage-table-name ... [other inputs] ... --opensearch-type provisioned
```

For full documentation, see the [Reindex Utility](https://awslabs.github.io/visual-asset-management-system/developer/utilities/reindex) page in the VAMS documentation.
