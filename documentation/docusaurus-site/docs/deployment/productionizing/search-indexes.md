---
title: "Search indexes"
sidebar_position: 5
description: "How VAMS search indexes on Amazon OpenSearch Service are protected: snapshots, and reindexing from the source data."
---

# Search indexes

VAMS search runs on Amazon OpenSearch Service, either as an OpenSearch Serverless collection or as a
provisioned domain. The asset and file indexes are derived data: VAMS builds them from the asset records in
Amazon DynamoDB and the objects in Amazon S3. This page describes how to recover search after data loss, a
restore or a migration.

## Recovery approach

Treat the Amazon DynamoDB tables and Amazon S3 buckets as the source of truth and rebuild the indexes from them.
A rebuild always produces indexes that match the restored data, which a snapshot taken at a different
moment does not. Use service snapshots for faster recovery of the search layer alone, when the tables and
buckets were not affected.

| Situation                                      | Recovery                                                              |
| ---------------------------------------------- | --------------------------------------------------------------------- |
| Tables or buckets restored                     | Reindex from source                                                   |
| Data copied into another deployment            | Reindex from source in the target                                     |
| After a data migration or version upgrade      | Reindex from source, as the version's migration instructions describe |
| Index corrupted or deleted; source data intact | Restore a snapshot, or reindex from source                            |
| Collection or domain replaced                  | Reindex from source                                                   |

## Reindex from source

VAMS includes a reindex utility in `infra/deploymentDataMigration/tools/reindex_utility.py`. It reads asset
records from Amazon DynamoDB and file objects from Amazon S3 and rebuilds the asset and file indexes.

| Mode               | How it runs                                                  | When to use                                                                      |
| ------------------ | ------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| `lambda` (default) | Invokes the deployed reindexer AWS Lambda function           | Most deployments                                                                 |
| `direct`           | Runs the reindexer code locally with no execution-time limit | Very large repositories that would exceed the Lambda function's maximum run time |

The reindexer function name is published as the stack output `OpenSearchReindexerFunctionNameOutput`. See
the utility's README in the same directory for options and prerequisites.

```bash
python infra/deploymentDataMigration/tools/reindex_utility.py \
    --function-name <REINDEXER_FUNCTION_NAME> --operation both
```

Include a reindex in every restore procedure and in every restore drill, and record how long it takes for
your data volume. Search results are incomplete until the reindex finishes, so the reindex duration is part
of your recovery time.

## OpenSearch Serverless snapshots

OpenSearch Serverless [creates snapshots of each collection automatically every hour](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-snapshots.html),
with no configuration and no extra storage charge. A snapshot can be restored only to its original
collection, not to a new one, and restored indexes receive new identifiers. Snapshots therefore protect
against index-level mistakes in a running collection, but not against loss of the collection itself; for
that case, reindex from source.

## OpenSearch Service domain snapshots

A provisioned domain [takes hourly automated snapshots and keeps up to 336 of them for 14 days](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-snapshots.html).
Automated snapshots are for recovering the same domain. To move indexes to another domain or keep them
longer, register an Amazon S3 snapshot repository and take manual snapshots, which are stored in your
bucket and billed at standard Amazon S3 rates.

## Related pages

-   [Backups](backups.md)
-   [Disaster recovery](disaster-recovery.md)
