---
title: "Partition notes"
sidebar_position: 13
description: "Considerations for running VAMS in AWS GovCloud (US) and the AWS European Sovereign Cloud."
---

# Partition notes

VAMS provides configuration templates for the commercial AWS partition (`aws`), AWS GovCloud (US)
(`aws-us-gov`) and the AWS European Sovereign Cloud (`aws-eusc`), in
`infra/config/config.template.{commercial,govcloud,eusovereign}.json`. Service and feature availability differs
between partitions and between Regions in a partition, and it changes over time. Verify every service and
feature in this guide in your target partition and Region before relying on it.

## General guidance

-   **Build ARNs from the partition.** Scripts, policies and runbooks must use the partition of the account
    (for example `arn:aws-us-gov:` or `arn:aws-eusc:`) rather than `arn:aws:`. Read the partition from
    `aws sts get-caller-identity` in automation.
-   **Use the partition's endpoints.** AWS GovCloud (US) deployments typically use FIPS endpoints; the GovCloud
    template enables `app.useFips`. Make sure scripts and tools use the same endpoints.
-   **Check feature availability per resource and Region.** For backups, use the
    [AWS Backup feature availability](https://docs.aws.amazon.com/aws-backup/latest/devguide/backup-feature-availability.html)
    tables. For other services, use the service's own documentation for the partition.
-   **Keep copies inside the partition.** Partitions are isolated from each other. Plan backup accounts and
    recovery Regions within the same partition.

## AWS GovCloud (US)

| Topic                  | Note                                                                                                                                                                                                                                               |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Configuration template | The GovCloud template uses an Application Load Balancer for the web application, FIPS endpoints, a VPC, AWS KMS customer-managed key encryption and provisioned OpenSearch. Review the template at your VAMS release for the current values        |
| Recovery Region        | The partition has two Regions, AWS GovCloud (US-West) and AWS GovCloud (US-East). AWS Backup supports cross-Region copy between these two Regions only                                                                                             |
| AWS Backup             | At the time of writing, the feature table lists cross-account copy and management for GovCloud, and does not list restore testing; verify in the partition before you plan automated restore tests                                                 |
| Amazon Cognito         | VAMS does not use the Amazon Cognito Plus feature plan in GovCloud and uses the version of the pre-token generation trigger available there, so claims are added to ID tokens. An external identity provider is the stronger choice for production |
| Amazon EventBridge     | Event buses do not support customer-managed keys in this partition; VAMS uses the default encryption for its orchestration bus                                                                                                                     |
| A/B deployments        | Because the Application Load Balancer path names some resources after the domain host, an A/B deployment on this path needs a longer cutover window. See [Update the solution](../../deployment/update-the-solution.md)                            |

## AWS European Sovereign Cloud

| Topic                  | Note                                                                                                                                                                                                                                                                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Configuration template | The EU Sovereign template uses an Application Load Balancer for the web application. Review the template at your VAMS release for the other values                                                                                                                                                      |
| Recovery Region        | Verify which Regions are available to you in the partition. At the time of writing, the AWS Backup feature table lists one Region in the partition and does not list cross-Region backup copy for it, so plan cross-account copies within the Region and confirm options for a second location with AWS |
| AWS Backup             | At the time of writing, the feature table lists Amazon S3 support and cross-account copy and management, and does not list restore testing or AWS Backup Audit Manager; verify in the partition                                                                                                         |
| Amazon EventBridge     | Event buses do not support customer-managed keys in this partition; VAMS uses the default encryption for its orchestration bus                                                                                                                                                                          |
| Other services         | Verify Amazon S3 Replication Time Control, Amazon S3 Batch Replication, OpenSearch Serverless snapshots and Amazon Cognito features in the partition before relying on them                                                                                                                             |

## Related pages

-   [Environment strategy](environment-strategy.md)
-   [Disaster recovery](disaster-recovery.md)
