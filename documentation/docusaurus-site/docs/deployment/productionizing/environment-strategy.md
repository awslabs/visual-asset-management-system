---
title: "Environment strategy"
sidebar_position: 2
description: "How to lay out VAMS environments across AWS accounts so that a mistake or a compromise in one environment cannot reach production data."
---

# Environment strategy

This page describes how to lay out VAMS environments across AWS accounts so that a mistake or a
compromise in one environment cannot reach production data.

## Separate accounts per environment

Deploy each VAMS environment (for example development, test and production) into its own AWS account.
An account is the strongest isolation boundary AWS provides for permissions, quotas and billing. With
separate accounts, a permission granted for development cannot be used against production, a service
quota consumed by a load test cannot throttle production, and an account-level setting such as the
Amazon API Gateway CloudWatch role is never shared between environments.

| Account                | Contents                                                | Who has write access                                             |
| ---------------------- | ------------------------------------------------------- | ---------------------------------------------------------------- |
| Development            | VAMS development stacks, short-lived test data          | Delivery team                                                    |
| Test                   | VAMS test stack, user acceptance testing data           | Delivery automation; testers through VAMS                        |
| Production             | VAMS production stack                                   | Delivery automation through an approved change; break-glass role |
| Tools (optional)       | Deployment pipelines, build artifacts, deployment state | Delivery automation                                              |
| Backup (optional)      | AWS Backup vault holding copies of production backups   | Backup administrators only                                       |
| Log archive (optional) | Centralized AWS CloudTrail and access logs              | Security team                                                    |

Running several VAMS deployments in one account is supported: give each a unique `baseStackName` in its
configuration file. Use this for development and experimentation, not to share an account between test
and production.

## AWS Organizations and guardrails

Group the accounts in [AWS Organizations](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_introduction.html)
under organizational units (OUs) that reflect their purpose, such as a workloads OU with non-production
and production children. Apply [service control policies](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)
(SCPs) at the OU level to set guardrails that no role in the account can exceed.

Guardrails that suit a VAMS production account:

| Guardrail                                                                                                                                          | Purpose                                                                                                          |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Deny actions outside the approved Regions                                                                                                          | Keeps data in the Regions you deploy to                                                                          |
| Deny `dynamodb:DeleteTable`, `dynamodb:DeleteBackup` and `s3:DeleteBucket` except for a break-glass role                                           | Protects retained data stores and their backups from accidental or malicious deletion                            |
| Deny changes to bucket versioning, Object Lock and replication configuration for every principal except the deployment role and a break-glass role | Keeps recovery features in place; the deployment role still needs these actions to create and update the buckets |
| Deny `kms:ScheduleKeyDeletion` and `kms:DisableKey` except for a break-glass role                                                                  | Keeps encrypted data readable                                                                                    |
| Deny `backup:DeleteRecoveryPoint` and vault policy changes outside the backup administrators                                                       | Protects backups                                                                                                 |
| Deny leaving the organization and disabling AWS CloudTrail                                                                                         | Keeps the audit trail                                                                                            |

SCPs do not grant permissions; they only limit them. Test every SCP in a non-production OU first, because
an over-broad deny can block VAMS deployments. AWS CloudFormation and AWS CDK bootstrap roles act inside
the account and are subject to the same SCPs.

## Deployment roles

Bootstrap each account for the AWS CDK with a trust to the account that deploys, and replace the default
`AdministratorAccess` execution policy with a scoped policy. VAMS supports custom bootstrap and stack roles
through `app.iamRoleConfig` in the configuration file, which lets a central team own every role VAMS uses.
See the [configuration reference](../../deployment/configuration-reference.md).

## Configuration parity

The test environment can only predict production behavior if it is configured the same way. Use the same
configuration template and the same values for the options that change behavior: web distribution
(Amazon CloudFront or Application Load Balancer), AWS KMS customer-managed key encryption, VPC and VPC
endpoints, FIPS endpoints, OpenSearch mode, authentication provider, and enabled pipelines and add-ons.
Differ only in sizing, domain names and account identifiers.

## Related pages

-   [Change management](change-management.md)
-   [Partition notes](partitions.md)
