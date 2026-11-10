---
title: "Encryption keys"
sidebar_position: 8
description: "How VAMS uses AWS KMS customer managed keys and what their key policies need for replicas, copies and cross-account access."
---

# Encryption keys

When `app.useKmsCmkEncryption.enabled` is `true`, VAMS encrypts its Amazon DynamoDB tables, Amazon S3 data
buckets, queues, topics, log groups and OpenSearch resources with an AWS Key Management Service (AWS KMS)
customer-managed key. VAMS either creates the key, with automatic rotation enabled, or uses an external key
supplied with `useKmsCmkEncryption.optionalExternalCmkArn`. This page describes how keys affect backups,
replicas and copies. See [Security architecture](../../architecture/security.md#encryption) for the key policy
VAMS applies.

## Keys and recoverability

A backup, replica or export is only recoverable if a key that can decrypt it is available when you need it.

| Copy                                                  | Encrypted with                                | Requirement                                                               |
| ----------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------- |
| Amazon DynamoDB PITR and on-demand backups            | The table's key                               | The source key stays enabled                                              |
| AWS Backup recovery points copied to a backup account | The destination vault's key                   | The backup account owns the vault key; the source key must allow the copy |
| Amazon S3 replicas                                    | The replica key named in the replication rule | A key in the destination account and Region                               |
| Amazon DynamoDB exports                               | The export's chosen encryption                | The reader has decrypt permission on that key                             |

:::danger[Do not delete keys that protect retained data]
The VAMS-generated key is retained when the stack is destroyed, matching the retained tables and buckets it
encrypts. A key that is disabled or pending deletion makes every table, object and backup encrypted under it
unreadable. Keep keys for at least as long as the longest retention period of any data or backup they
protect.
:::

## Replica and backup keys

Use a separate key, owned by the backup or disaster recovery account, for each copy destination. That key's
policy then belongs to the team that owns the copies, and a compromise of the production account cannot
change it.

For Amazon S3 replication of SSE-KMS objects, the replication role needs `kms:Decrypt` on the source key and
`kms:Encrypt` on the replica key, granted both in IAM and in each key policy. If the destination bucket uses
Amazon S3 Bucket Keys, the replica key policy also needs `kms:Decrypt` for the replication role. See
[Replicating encrypted objects](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-config-for-kms-objects.html).

A replica key statement in the destination account:

```json
{
    "Sid": "AllowReplicationRoleToEncryptReplicas",
    "Effect": "Allow",
    "Principal": { "AWS": "arn:<partition>:iam::<SOURCE_ACCOUNT_ID>:role/<replication-role>" },
    "Action": ["kms:Encrypt", "kms:GenerateDataKey"],
    "Resource": "*"
}
```

Multi-Region AWS KMS keys share key material across Regions, but Amazon S3 treats them as single-Region keys,
so replication still needs a replica key configured explicitly.

## External keys

When you supply an external key, VAMS leaves its key policy unchanged. The key policy must grant the same
service principals the VAMS-generated key policy grants, or the encrypted resources fail to deploy or to
operate. Grant access for backup and replication roles in the same policy.

## Cross-account data copies

Copying production data into another environment requires the copying role to decrypt the source data and to
encrypt with the target's key. Grant source-key decrypt access through a key policy statement or a grant
limited to the copy role and to the duration of the copy, and remove it afterwards.

## Related pages

-   [Backups](backups.md)
-   [Amazon S3 data protection](s3-data-protection.md)
