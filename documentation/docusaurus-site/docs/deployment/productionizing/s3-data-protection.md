---
title: "Amazon S3 data protection"
sidebar_position: 4
description: "How to protect the files VAMS stores in Amazon S3: versioning, replication, Object Lock, lifecycle rules and inventory."
---

# Amazon S3 data protection

This page describes how to protect the files VAMS stores in Amazon Simple Storage Service (Amazon S3):
versioning, replication to another bucket, account or Region, optional Object Lock, lifecycle rules, and
inventory for audits and restores.

## VAMS buckets

| Bucket                 | Holds                                                          | Versioned by VAMS  | Production protection                                              |
| ---------------------- | -------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------ |
| Asset bucket(s)        | Uploaded asset files                                           | Yes                | Replication, AWS Backup, optional Object Lock                      |
| Asset auxiliary bucket | Previews, viewer files, pipeline working files, staged exports | Yes                | Replication or AWS Backup; can be regenerated in part by pipelines |
| Artefacts bucket       | Deployment artefacts, notebooks, pipeline registration bundles | Yes                | Replication or AWS Backup                                          |
| Access logs bucket     | Server access logs                                             | Yes                | Copy to a log archive account                                      |
| Web app bucket         | Built web application                                          | Yes                | Rebuilt by every deployment; no backup needed                      |
| External asset buckets | Files in buckets you registered                                | Your configuration | Your own controls                                                  |

See the [AWS resources inventory](../../architecture/aws-resources.md#amazon-s3-buckets) for the complete
list and each bucket's removal behavior.

## Versioning

VAMS enables S3 Versioning on its buckets, so an overwrite or delete keeps the previous version. Versioning
is the base for every other control on this page: AWS Backup for Amazon S3, replication and Object Lock all
require it. Do not suspend versioning on a VAMS data bucket.

Add a lifecycle rule that expires noncurrent versions after a period that suits your recovery needs, so
storage does not grow without bound:

```json
{
    "Rules": [
        {
            "ID": "expire-noncurrent-versions",
            "Status": "Enabled",
            "Filter": {},
            "NoncurrentVersionExpiration": { "NoncurrentDays": 90, "NewerNoncurrentVersions": 3 },
            "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
        }
    ]
}
```

:::warning[Manage lifecycle rules where they are defined]
Bucket settings that VAMS sets through AWS CloudFormation can be overwritten by the next deployment. Apply
extra settings such as lifecycle rules and replication through your own stack or the VAMS custom CDK
lane, and verify after each deployment that they are still present.
:::

## Replication

[S3 Replication](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication.html) copies new
objects to another bucket automatically.

| Type                           | Use                                                                                      |
| ------------------------------ | ---------------------------------------------------------------------------------------- |
| Same-Region Replication (SRR)  | Copy to a bucket in a separate backup account in the same Region                         |
| Cross-Region Replication (CRR) | Copy to a second Region for disaster recovery                                            |
| S3 Batch Replication           | Replicate objects that existed before the rule was created, or retry failed replications |

Replication rules apply to objects written after the rule exists. Use S3 Batch Replication to copy existing
objects when you first enable replication.

For a replica in another account, set the destination bucket owner as the replica owner so the backup
account owns the copies, and give the destination bucket a policy that admits only the replication role.

### Replicating objects encrypted with AWS KMS

When the deployment uses AWS KMS customer-managed key encryption, objects are encrypted with SSE-KMS and
replication needs [explicit configuration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-config-for-kms-objects.html):

-   Opt in to replicating SSE-KMS objects with `SourceSelectionCriteria.SseKmsEncryptedObjects`.
-   Name the key that encrypts replicas with `Destination.EncryptionConfiguration.ReplicaKmsKeyID`. Use a key
    in the destination account and Region.
-   Allow the replication role `kms:Decrypt` on the source key and `kms:Encrypt` on the replica key, in both
    the IAM policy and the key policies.

Amazon S3 treats multi-Region AWS KMS keys as single-Region keys, so a multi-Region key does not remove the
need to configure a replica key. See [Encryption keys](encryption-keys.md).

### S3 Replication Time Control

[S3 Replication Time Control](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-time-control.html)
(S3 RTC) replicates most objects in seconds and 99.9 percent of objects within 15 minutes, backed by a
service-level agreement, and publishes replication metrics. Enable it when your recovery point objective
for files is measured in minutes, and alarm on its replication latency and pending-operation metrics.

## Object Lock

[S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-configure.html) stores
object versions in a write-once, read-many model for a retention period or under a legal hold. It can be
enabled on an existing versioned bucket.

:::danger[Object Lock cannot be turned off]
After Object Lock is enabled on a bucket, it cannot be disabled and versioning cannot be suspended. In
compliance mode, no user can delete a locked object version before its retention date. A bucket with Object
Lock also cannot be the destination of server access logs.
:::

The recommended pattern for VAMS is to leave the source buckets without Object Lock, because VAMS deletes and
replaces objects as part of normal operation, and to enable Object Lock with a default retention period on
the **replica** bucket in the backup account. The replica then keeps an immutable history even if the
production account is compromised. Use governance mode until the retention period is agreed with your
records owner.

## Inventory

[S3 Inventory](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-inventory.html) produces a
daily or weekly list of objects and their versions, encryption status and replication status. Use it to:

-   Record which object versions existed at a backup point, so a restore can roll a bucket back to that
    moment.
-   Confirm that every object is encrypted with the expected key.
-   Find objects whose replication failed.

Deliver inventory reports to a bucket in the log archive or backup account.

## Restoring objects

| Situation                               | Restore                                                                                                 |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| One object overwritten or deleted       | Copy the previous version over the current one, or delete the delete marker                             |
| Many objects changed after a known time | Use an inventory report from before that time with S3 Batch Operations to copy the listed versions back |
| Bucket lost                             | Restore from AWS Backup, or copy from the replica bucket                                                |

After restoring files, rebuild the search indexes so search results match storage (see
[Search indexes](search-indexes.md)).

## Related pages

-   [Backups](backups.md)
-   [Encryption keys](encryption-keys.md)
