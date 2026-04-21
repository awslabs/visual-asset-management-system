# Utilities Overview

VAMS includes a set of standalone utility scripts for administration, maintenance, and operational tasks. These scripts are located in `infra/deploymentDataMigration/tools/` and are designed to be run from a local machine with appropriate AWS credentials.

## Available Utilities

| Utility               | Script               | Description                                                               |
| :-------------------- | :------------------- | :------------------------------------------------------------------------ |
| [Reindex](reindex.md) | `reindex_utility.py` | Re-indexes Amazon OpenSearch and downstream indexers for assets and files |

## General Prerequisites

All utility scripts require:

-   **Python 3.6+** installed locally
-   **boto3** (`pip install boto3`)
-   **AWS credentials** configured via AWS CLI profile, environment variables, or IAM role
-   **Appropriate IAM permissions** for the specific utility (documented per utility)

## Usage Context

Utility scripts can be used in two contexts:

1. **Standalone** -- Run directly from the command line for ad-hoc administration tasks
2. **Post-migration** -- Run as a follow-up step after data migration scripts (e.g., after upgrading from one VAMS version to another)

For data migration procedures, see the migration README at `infra/deploymentDataMigration/README.md`.
