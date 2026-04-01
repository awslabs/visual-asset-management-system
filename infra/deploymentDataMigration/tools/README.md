# VAMS Utility Scripts

This directory contains standalone utility scripts for VAMS administration and maintenance tasks. These scripts are designed to be run from a local machine with appropriate AWS credentials.

## Available Utilities

### Reindex Utility (`reindex_utility.py`)

Re-indexes Amazon OpenSearch and any attached downstream indexers (such as the Garnet Framework addon) for assets and files. This utility invokes a deployed AWS Lambda function that reads all asset and file records from Amazon DynamoDB and re-publishes them to the configured indexing pipeline (Amazon SNS topics that trigger the indexer Lambda functions).

**When to use:**

-   After a major data migration or version upgrade
-   When OpenSearch indexes are out of sync with DynamoDB source data
-   After enabling or reconfiguring OpenSearch on an existing deployment
-   After enabling the Garnet Framework addon on a deployment with existing data
-   To rebuild indexes after accidental index corruption or deletion

**Prerequisites:**

-   Python 3.6+
-   `boto3` installed (`pip install boto3`)
-   AWS credentials with `lambda:InvokeFunction` permission
-   The reindexer Lambda function deployed as part of the VAMS CDK stack (function name available in CDK stack outputs as `ReindexerFunctionNameOutput`)

**Quick start:**

```bash
# Reindex both assets and files
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

For full documentation, see the [Reindex Utility](https://awslabs.github.io/visual-asset-management-system/developer/utilities/reindex) page in the VAMS documentation.
