---
title: "Productionizing VAMS"
sidebar_position: 1
description: "How to operate Visual Asset Management System (VAMS) as a production service: environments, data protection, backups, disaster recovery, monitoring and change control."
---

# Productionizing VAMS

This guide describes how to operate Visual Asset Management System (VAMS) as a production service: how
to separate environments, protect and back up data, plan disaster recovery, monitor the deployment, and
control change. It applies to any VAMS deployment and any configuration template (commercial, AWS
GovCloud (US), AWS European Sovereign Cloud, or custom).

VAMS deploys with protective defaults: its Amazon DynamoDB tables have point-in-time recovery enabled,
its asset, auxiliary and artefacts Amazon Simple Storage Service (Amazon S3) buckets are versioned, and
the stores that hold data are retained when the stack is destroyed. A production deployment builds on
those defaults with backups that are independent of the deployment, copies in another account or
Region, tested restore procedures, and a controlled change process.

## Pages in this guide

| Page                                               | Topic                                                                                   |
| -------------------------------------------------- | --------------------------------------------------------------------------------------- |
| [Environment strategy](environment-strategy.md)    | Separate accounts, AWS Organizations, service control policy guardrails                 |
| [Backups](backups.md)                              | DynamoDB point-in-time recovery, on-demand checkpoints, AWS Backup plans, vault lock    |
| [Amazon S3 data protection](s3-data-protection.md) | Versioning, replication, S3 Replication Time Control, Object Lock, lifecycle, inventory |
| [Search indexes](search-indexes.md)                | Amazon OpenSearch Service snapshots and reindexing from source                          |
| [Identity](identity.md)                            | Amazon Cognito user data, export limits, external identity providers                    |
| [IAM governance](iam-governance.md)                | IAM modes, permission boundaries, IT-created roles, reviews on every upgrade            |
| [Encryption keys](encryption-keys.md)              | AWS KMS customer-managed keys and key policies for replicas and copies                  |
| [Retention](retention.md)                          | Which VAMS resources are retained on teardown and what that means for recovery          |
| [Monitoring and alarms](monitoring.md)             | Signals to watch and where they come from                                               |
| [Disaster recovery](disaster-recovery.md)          | Recovery objectives, strategies, runbook, restore drills                                |
| [Change management](change-management.md)          | Promotion, approvals, pre-change checkpoints, rollback                                  |
| [Partition notes](partitions.md)                   | AWS GovCloud (US) and AWS European Sovereign Cloud considerations                       |

## Shared responsibility

AWS is responsible for the security and resilience of the cloud; you are responsible for how VAMS is
configured, who can access it, how its data is protected and how it is recovered. The practices in this
guide are recommendations. Review them with your security, compliance and data owners, and validate each
procedure in a non-production environment before relying on it.

## Automating these practices

[Release automation](../release-automation/index.md) implements the change-management, backup and IAM
governance parts of this guide as a tool: one build promoted through environments, approvals enforced in
code, a checkpoint before every deploy, rollback and restore with verification, production-to-test data
copies, and IT role packages for environments where IT creates or bounds every role.

## Related pages

-   [Release automation](../release-automation/index.md)
-   [AWS resources inventory](../../architecture/aws-resources.md)
-   [Security architecture](../../architecture/security.md)
-   [Update the solution](../../deployment/update-the-solution.md)
-   [Uninstall the solution](../../deployment/uninstall.md)
