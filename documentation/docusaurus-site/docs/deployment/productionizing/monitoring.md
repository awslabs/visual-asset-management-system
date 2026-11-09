---
title: "Monitoring and alarms"
sidebar_position: 10
description: "The signals that show a VAMS production deployment is healthy, that its data protection works, and that a change has gone wrong."
---

# Monitoring and alarms

This page lists the signals that indicate a VAMS production deployment is healthy, that its data
protection is working, and that a change has gone wrong. Create the alarms in your own stack or through
the VAMS custom CDK lane, so that a VAMS update does not remove them.

## Application health

| Signal                        | Source                                                                                        | Alarm on                                                  |
| ----------------------------- | --------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| API errors and latency        | Amazon API Gateway metrics (`5XXError`, `4XXError`, `Latency`)                                | Sustained 5XX rate or latency above baseline              |
| Function errors and throttles | AWS Lambda metrics (`Errors`, `Throttles`, `Duration`) for the API handlers                   | Any sustained errors or throttles                         |
| Authorizer denials            | VAMS authorization audit log group                                                            | Unusual spikes                                            |
| Indexing backlog              | Amazon SQS `ApproximateAgeOfOldestMessage` on indexing queues; messages in dead-letter queues | Age above minutes; any dead-letter message                |
| Pipeline failures             | AWS Step Functions `ExecutionsFailed`, AWS Batch job state                                    | Failures above baseline                                   |
| Web edge                      | Amazon CloudFront or Application Load Balancer error rates; AWS WAF blocked requests          | Error rate; unexpected block spikes                       |
| Search                        | OpenSearch Serverless or domain health metrics                                                | Indexing or search errors; red cluster status on a domain |

VAMS writes audit events (authentication, authorization, uploads, downloads, permission changes, actions and
errors) to dedicated log groups; see the [AWS resources inventory](../../architecture/aws-resources.md#audit-log-groups).
Use metric filters on these groups for security-relevant alarms such as repeated authorization failures or
permission changes outside a change window.

## Data protection

| Signal               | Source                                                                                | Alarm on                                                            |
| -------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Backup job failures  | AWS Backup job events in Amazon EventBridge, or AWS Backup Audit Manager              | Any failed or expired backup or copy job                            |
| PITR disabled        | AWS Config rule for DynamoDB PITR, or a scheduled `describe-continuous-backups` check | Any VAMS table without PITR                                         |
| Versioning suspended | AWS Config rule for S3 versioning                                                     | Any VAMS data bucket                                                |
| Replication lag      | S3 Replication metrics (with S3 RTC)                                                  | Latency above your file recovery point objective; operations failed |
| Key state            | AWS KMS key state changes in AWS CloudTrail                                           | `DisableKey` or `ScheduleKeyDeletion` on a VAMS key                 |
| Restore drill age    | Your drill records                                                                    | No successful drill within the planned interval                     |

## Change and drift

| Signal                             | Source                                                                   | Alarm on                                   |
| ---------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------ |
| Stack drift                        | AWS CloudFormation drift detection, run on a schedule                    | Any drifted resource in a production stack |
| Changes outside the change process | AWS CloudTrail write events by principals other than the deployment role | Any, in the production account             |
| Failed deployments                 | AWS CloudFormation stack events (`UPDATE_ROLLBACK_*`)                    | Any                                        |

## Related pages

-   [Disaster recovery](disaster-recovery.md)
-   [Change management](change-management.md)
