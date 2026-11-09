---
title: "Backups"
sidebar_position: 3
description: "The backup layers for VAMS data: continuous recovery on every Amazon DynamoDB table, on-demand checkpoints before every change, scheduled AWS Backup plans, and restore procedures."
---

# Backups

This page describes the backup layers for VAMS data: continuous recovery on every Amazon DynamoDB
table, on-demand checkpoints before every change, and scheduled AWS Backup plans with copies outside the
production account or Region.

## What to back up

| Data                                                           | Store                                               | Primary protection                  | Independent copy                                            |
| -------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------- |
| Databases, assets, metadata, permissions, pipelines, workflows | Amazon DynamoDB tables                              | Point-in-time recovery              | AWS Backup, on-demand backups, exports                      |
| Asset files, previews, auxiliary files                         | Asset and auxiliary Amazon S3 buckets               | S3 Versioning                       | AWS Backup for Amazon S3, S3 replication                    |
| Pipeline registration bundles, notebooks                       | Artefacts Amazon S3 bucket                          | S3 Versioning                       | AWS Backup for Amazon S3, S3 replication                    |
| Search indexes                                                 | Amazon OpenSearch Service                           | Rebuilt from DynamoDB and Amazon S3 | Service snapshots (see [Search indexes](search-indexes.md)) |
| Users and groups                                               | Amazon Cognito user pool                            | None built in                       | User export (see [Identity](identity.md))                   |
| Deployment configuration                                       | `infra/config/config.json` and custom configuration | Your version control                | Your version control                                        |

The DynamoDB tables and the Amazon S3 buckets are the system of record. Everything else is either
derived from them (search indexes) or held outside VAMS (configuration).

## DynamoDB point-in-time recovery

VAMS enables [point-in-time recovery](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/PointInTimeRecovery_Howitworks.html)
(PITR) on every table it creates. PITR keeps continuous backups for a recovery period of 1 to 35 days,
and a restore can target any second in that window up to about five minutes before the present. The
VAMS tables use the service default recovery period, which is the 35-day maximum. A restore always creates
a new table.

Check PITR status as part of every pre-change checklist:

```bash
aws dynamodb describe-continuous-backups --table-name <TABLE_NAME> \
    --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription'
```

When a table with PITR enabled is deleted, DynamoDB keeps a system backup of it for 35 days, which can be
restored to the state at deletion.

:::note[PITR is not an off-account backup]
PITR backups live in the same account and Region as the table. They protect against bad writes and
accidental changes, not against loss of the account or Region. Combine PITR with AWS Backup copies.
:::

## Checkpoints before every change

Before every deployment, update or data migration, take an on-demand backup of each VAMS table. An
on-demand backup is a full, consistent copy that is kept until you delete it, independent of the PITR
window, and gives you a named restore point that matches the change.

The resource-name prefix of a deployment is published in AWS Systems Manager Parameter Store under
`/<name>-<baseStackName>/resourceNames`, where `baseStackName` includes the Region suffix. Resolve the
table names from it and back up each one:

```bash
PREFIX="/<name>-<baseStackName>/resourceNames"
CHECKPOINT="pre-change-$(date -u +%Y%m%dT%H%M%SZ)"

for TABLE in $(aws ssm get-parameters-by-path --path "${PREFIX}/dynamoTables" --recursive \
    --query "Parameters[].Value" --output text); do
    aws dynamodb create-backup --table-name "${TABLE}" --backup-name "${CHECKPOINT}-${TABLE}"
done
```

DynamoDB backup names allow letters, digits, `_`, `-` and `.` and are limited in length; shorten the
name if a long table name exceeds it.

Record the checkpoint name with the change record. Also record the current object versions in the asset,
auxiliary and artefacts buckets (for example with an S3 Inventory report, see
[Amazon S3 data protection](s3-data-protection.md#inventory)), so objects can be restored to the same
moment.

:::tip[Verify the table list]
Table parameters are published under the `dynamoTables/` key category. Compare the output of
`get-parameters-by-path` with the tables in the stack once for your deployment, so the checkpoint covers
every table, including tables added by add-ons or custom resources.
:::

## AWS Backup plans

[AWS Backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/whatisbackup.html) adds scheduled
backups with retention rules, central monitoring, and copies to another account or Region.

| Plan rule | Suggested setting                                                                                   |
| --------- | --------------------------------------------------------------------------------------------------- |
| Resources | VAMS DynamoDB tables and the asset, auxiliary and artefacts buckets, selected by tag or ARN         |
| Frequency | Daily, plus weekly or monthly rules for longer retention                                            |
| Retention | Set to your records policy; keep at least one recovery point older than your longest detection time |
| Copy      | To a vault in a separate backup account, and optionally to a second Region                          |
| Vault     | Encrypted with an AWS KMS key owned by the backup account                                           |

Cross-account and cross-Region copy of DynamoDB backups requires the
[AWS Backup advanced features for DynamoDB](https://docs.aws.amazon.com/aws-backup/latest/devguide/advanced-ddb-backup.html)
to be enabled. Amazon S3 support in AWS Backup requires S3 Versioning, which VAMS enables on its data
buckets. Feature availability differs by resource type and Region; check the
[AWS Backup feature availability](https://docs.aws.amazon.com/aws-backup/latest/devguide/backup-feature-availability.html)
table for your Regions before you design copies.

### Protect the backups

| Control                                                                                         | Effect                                                                |
| ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Separate backup account                                                                         | A compromise of the production account cannot delete the copies       |
| [AWS Backup Vault Lock](https://docs.aws.amazon.com/aws-backup/latest/devguide/vault-lock.html) | Write-once, read-many protection for recovery points in the vault     |
| Vault access policy                                                                             | Deny deletion of recovery points to everyone except a controlled role |
| Restore testing                                                                                 | Scheduled test restores prove the backups are usable                  |

Vault Lock has a governance mode, which users with sufficient IAM permissions can remove, and a compliance
mode, which becomes immutable after a grace period of at least 72 hours. Once a compliance-mode lock is
fixed, the vault and its retention settings cannot be changed or deleted by any user or by AWS while it
holds recovery points. Start in governance mode, and move to compliance mode only after the retention
settings are confirmed.

## Exports for long-term archive

[DynamoDB export to Amazon S3](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/S3DataExport.HowItWorks.html)
writes a table's data to an S3 bucket from its PITR backup, without consuming table read capacity, and can
write to a bucket in another account or Region. Exports are useful for archives, analysis and data copies
between environments. They are not a direct restore path: loading an export back into a VAMS-managed table
requires a write process, because the table already exists.

## Restoring

| Scenario                             | Method                                                                  |
| ------------------------------------ | ----------------------------------------------------------------------- |
| Bad writes in the last 35 days       | PITR restore to a new table at a time just before the bad write         |
| Roll back a failed change            | Restore the pre-change on-demand backup to a new table                  |
| Account or Region loss               | Restore from the AWS Backup copy in the backup account or second Region |
| Single object overwritten or deleted | Restore the previous S3 object version                                  |

Because AWS CloudFormation owns the VAMS tables, a restored table is a separate table that the stack does
not manage. Either copy the items from the restored table back into the stack's table, or perform an A/B
deployment that imports the restored data, as described in
[Update the solution](../../deployment/update-the-solution.md). After restoring table data, rebuild the
search indexes (see [Search indexes](search-indexes.md)).

### Stop writes while tables are reloaded

Copying items back into a live table means clearing it first. Writes that arrive in that window are lost or
mixed into the restored data, and every delete and put also feeds the search indexing stream. Stop writes
before the reload and allow them again after it, including when the reload fails:

-   When the deployed VAMS version provides a maintenance mode that rejects API writes, use it for the reload
    window.
-   Otherwise, announce a maintenance window, pause integrations and bulk uploads, and record who confirmed
    that writes stopped and when.
-   Background work keeps running in either case: list the workflow (Step Functions) executions that are still
    running before you start, and decide whether to wait for them or stop them.
-   Restore every table to its temporary copy before you stop writes, so the window covers only the copy-back.

## Related pages

-   [Amazon S3 data protection](s3-data-protection.md)
-   [Disaster recovery](disaster-recovery.md)
