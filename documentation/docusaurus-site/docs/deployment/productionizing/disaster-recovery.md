---
title: "Disaster recovery"
sidebar_position: 11
description: "How to plan disaster recovery for VAMS: recovery objectives, strategies, the recovery runbook and restore drills."
---

# Disaster recovery

This page describes how to plan disaster recovery (DR) for VAMS: choosing recovery objectives, choosing a
strategy, the recovery runbook, and the restore drills that prove it works. The strategies follow the
patterns in [Disaster recovery of workloads on AWS](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-options-in-the-cloud.html).

## Recovery objectives

-   **Recovery point objective (RPO)**: the maximum acceptable amount of data loss, measured in time.
-   **Recovery time objective (RTO)**: the maximum acceptable time between an outage and the restored
    service.

Set both with your business owner for each kind of event. The table gives indicative values for each
mechanism; measure your own in drills, because table count, data volume and reindex time dominate.

| Event                                     | Mechanism                                                       | Indicative RPO                                                                     | Indicative RTO                           |
| ----------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------- |
| Bad write or accidental change in a table | Amazon DynamoDB PITR restore                                    | Minutes (PITR lags the present by about five minutes)                              | Hours: restore, copy back, reindex       |
| Failed upgrade or migration               | Pre-change on-demand backups, redeploy of previous version      | Time of the checkpoint                                                             | Hours                                    |
| Overwritten or deleted files              | Amazon S3 Versioning                                            | Zero for versioned objects                                                         | Minutes to hours                         |
| Loss of the production account            | AWS Backup copies in a backup account; Amazon S3 replicas       | Time of the last copy (daily plan: up to a day; files with Amazon S3 RTC: minutes) | Hours to days: new account, deploy, load |
| Loss of the Region                        | Cross-Region copies and replicas; deployment in a second Region | As above                                                                           | Hours to days, depending on strategy     |

## Strategies

| Strategy           | What runs in the recovery Region or account                                                  | RTO           | Cost            |
| ------------------ | -------------------------------------------------------------------------------------------- | ------------- | --------------- |
| Backup and restore | Nothing; backups and replicas only                                                           | Longest       | Lowest          |
| Pilot light        | A deployed VAMS stack with no users, receiving file replicas; tables loaded at recovery time | Shorter       | Low to moderate |
| Warm standby       | A deployed, scaled-down VAMS stack kept loaded with recent data                              | Shorter still | Moderate        |

For most VAMS deployments, backup and restore or pilot light meets the objectives:

-   **Backup and restore.** Keep AWS Backup copies of the tables and buckets in a backup account and,
    if Region loss is in scope, in a second Region. At recovery, deploy VAMS from your version-controlled
    configuration, load the tables from the copies, restore the files, and reindex.
-   **Pilot light.** Deploy a second VAMS stack (with a different `baseStackName`) in the recovery Region
    ahead of time, and replicate the asset, auxiliary and artefacts buckets into its buckets with Amazon S3
    Cross-Region Replication. At recovery, load the tables from the latest copy, reindex, and move DNS. This
    removes the deploy and file-copy time from the RTO.

A VAMS deployment in a second Region is a separate deployment with its own resource names. Data moved into it
must have bucket names and other resource identifiers rewritten, as in an A/B deployment (see
[Update the solution](../../deployment/update-the-solution.md)).

## Prerequisites

-   The deployment configuration and any custom code are in version control, and the exact release that
    production runs is recorded.
-   The target Region supports every service and feature your configuration uses (see
    [Partition notes](partitions.md)).
-   AWS CDK bootstrap, deployment roles and AWS KMS keys exist in the recovery account and Region.
-   Users can sign in to the recovered deployment. With an external identity provider, add the recovery
    deployment's callback URLs in advance. With Amazon Cognito only, users are recreated from an export and
    reset their passwords (see [Identity](identity.md)).
-   DNS records have a short enough time to live for the cutover you plan.

## Recovery runbook

1. **Declare and decide.** Confirm the event, choose the recovery point, and record the decision.
2. **Freeze changes.** Stop scheduled deployments and pipelines against the affected environment.
3. **Prepare the target.** For backup and restore, deploy VAMS at the same release and configuration in the
   recovery account or Region. For pilot light, confirm the standing stack is healthy.
4. **Restore tables.** Restore each table's recovery point to a temporary table (start them in parallel;
   Amazon DynamoDB limits how many tables can be created at once), stop writes (see
   [Stop writes while tables are reloaded](backups.md#stop-writes-while-tables-are-reloaded)), then copy the
   items into the deployment's table, rewriting bucket names and other resource identifiers where they
   differ, and compare item counts with the temporary tables before you allow writes again.
5. **Restore files.** For replicas, confirm replication has caught up to the recovery point. Otherwise restore
   the buckets from AWS Backup or copy them from the replicas.
6. **Reindex.** Rebuild the search indexes from the restored data (see [Search indexes](search-indexes.md)).
7. **Restore access.** Configure identity federation or recreate users; verify VAMS role assignments.
8. **Verify.** Run smoke tests: sign in, list databases and assets, open and download files, search, and run a
   pipeline that you rely on.
9. **Cut over.** Point DNS at the recovered deployment and communicate the recovery point to users.
10. **Close out.** Record actual RPO and RTO, and update this plan with what you learned.

## Restore drills

A backup that has not been restored is not known to work. Schedule drills and keep their results.

| Drill                                                                                                               | Frequency | Pass criteria                                                                                 |
| ------------------------------------------------------------------------------------------------------------------- | --------- | --------------------------------------------------------------------------------------------- |
| Restore one table from PITR to a temporary table and compare item counts                                            | Monthly   | Restore completes; counts match                                                               |
| Restore a set of files from previous versions                                                                       | Quarterly | Files open in VAMS                                                                            |
| Full restore of a non-production environment from the pre-change checkpoint, including the write freeze and reindex | Quarterly | Item and object counts match the checkpoint; smoke tests pass; RTO and freeze window recorded |
| Recovery into the backup account or second Region using the runbook                                                 | Annually  | Smoke tests pass; RPO and RTO within objectives                                               |

[AWS Backup restore testing](https://docs.aws.amazon.com/aws-backup/latest/devguide/restore-testing.html)
can automate scheduled test restores of supported resources and report the results.

## Related pages

-   [Backups](backups.md)
-   [Monitoring and alarms](monitoring.md)
