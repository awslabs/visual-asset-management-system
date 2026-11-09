---
title: "Retention"
sidebar_position: 9
description: "Which VAMS resources are retained when the stack is destroyed, which are deleted, and what that means for recovery."
---

# Retention

VAMS retains the resources that hold data when its stack is destroyed, and deletes the resources that can
be recreated. This page summarizes which is which and what that means for recovery and records retention.
The authoritative, per-resource list is the [AWS resources inventory](../../architecture/aws-resources.md);
the cleanup procedure is in [Uninstall the solution](../../deployment/uninstall.md).

## Retained on teardown

| Resource                                                      | Why it is retained                                 | Recovery note                                                                                                         |
| ------------------------------------------------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Amazon DynamoDB tables                                        | System of record                                   | Survive `cdk destroy`; a redeploy creates new, auto-named tables, so retained tables are not reattached automatically |
| Asset, auxiliary, artefacts and access logs Amazon S3 buckets | Hold files and logs                                | Survive `cdk destroy`; a redeploy creates new, auto-named buckets                                                     |
| Model cache buckets                                           | Hold large downloaded model weights                | Can be refilled from the model source                                                                                 |
| VAMS-generated AWS KMS key (when VAMS creates the key)        | Keeps retained tables and buckets readable         | Delete only after every resource and backup encrypted with it is gone                                                 |
| Amazon API Gateway account CloudWatch role                    | Shared by every REST API in the account and Region | Explicitly named; can block a redeploy, see the inventory                                                             |

Because a redeploy creates new resources rather than reattaching the retained ones, retention protects data
from deletion but is not a recovery mechanism by itself. Bringing retained data back into service means
copying it into a deployment, as an A/B deployment does (see
[Update the solution](../../deployment/update-the-solution.md)).

## Deleted on teardown

| Resource                                                          | Consequence                                           |
| ----------------------------------------------------------------- | ----------------------------------------------------- |
| Amazon CloudWatch log groups, including the VAMS audit log groups | Audit and application logs are deleted with the stack |
| Web app bucket and its access logs bucket                         | Rebuilt by the next deployment                        |
| AWS Systems Manager parameters                                    | Recreated by the next deployment                      |
| Amazon EFS file systems for pipelines                             | Model caches refill; training checkpoints are lost    |
| Search collection or domain                                       | Rebuild with a reindex from source                    |

:::warning[Keep audit logs outside the stack]
The VAMS audit log groups are deleted when the stack is destroyed cleanly, and they keep events for the
retention period set on each group. If your records policy requires longer retention or retention beyond the
life of a deployment, export or subscribe the audit log groups to a log archive account, and record AWS
CloudTrail to an organization trail in that account.
:::

## Retention periods to set

| Item                       | Where to set it                               | Consider                                                           |
| -------------------------- | --------------------------------------------- | ------------------------------------------------------------------ |
| DynamoDB PITR window       | Table setting (VAMS uses the service default) | Detection time for data corruption                                 |
| AWS Backup recovery points | Backup plan rules                             | Records policy, legal holds                                        |
| S3 noncurrent versions     | Bucket lifecycle rules                        | Recovery window for overwritten files, storage cost                |
| S3 replicas                | Replica bucket lifecycle and Object Lock      | Immutability requirements                                          |
| Log groups                 | Log group retention, archive account          | Audit requirements                                                 |
| Exports and staged copies  | Staging bucket lifecycle                      | Delete promptly; staged exports are full copies of production data |

## Amazon Cognito

The VAMS administrator user is retained when it is replaced, so an administrator identity is never removed
by the same deployment that creates its successor. For the user pool itself, check the `DeletionPolicy` in
the synthesized template of your deployment, and keep a user export as described in [Identity](identity.md).

## Related pages

-   [Backups](backups.md)
-   [Uninstall the solution](../../deployment/uninstall.md)
