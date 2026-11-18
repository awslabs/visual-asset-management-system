# Amazon S3 Transfer Acceleration

Amazon S3 Transfer Acceleration (TA) sends uploads and downloads of asset files through the nearest Amazon CloudFront edge location, which then carries them to the bucket over the AWS network. VAMS can use it for the asset buckets it serves to browsers, the VAMS CLI, and API clients. It is optional, off by default, and available in the commercial AWS partition only.

This page describes what the option does, when it helps, how to turn it on for a VAMS-created bucket and for an external bucket, how to confirm it works, and how it interacts with other settings. For the exact steps an external bucket owner performs first, see [Step 5b of the external bucket setup](external-s3-setup.md#step-5b-enable-s3-transfer-acceleration-optional).

## What it does and does not do

| It does                                                                                                                                                                                       | It does not                                                                                                                                       |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Signs the presigned URLs that VAMS hands to browsers, the VAMS CLI, MCP clients and API callers for asset files in a flagged bucket with the host `<bucket>.s3-accelerate.amazonaws.com`.     | Change any server-side traffic. Lambda functions, pipelines, AWS Batch, AWS Step Functions and the search indexer use the normal bucket endpoint. |
| Covers file downloads, file previews, streamed viewer files (the redirect target of the stream endpoint), multipart upload parts, asset export file links, and Download as Zip archive links. | Accelerate the artefacts bucket, the access logs bucket, or any bucket not flagged. The artefacts bucket is never accelerated.                    |
| Applies to the auxiliary bucket (previews, viewer data, staged exports, prepared zip archives) whenever the default asset bucket is accelerated.                                              | Change the VAMS API calls. Requests to Amazon API Gateway, Amazon Cognito and the web application go the same way as before.                      |
| Keeps the signature scope on the bucket's own Region, so cross-Region external buckets still work.                                                                                            | Change who can read or write a file. Authorization, presigned URL lifetime and bucket policies are the same.                                      |

## When it helps and when it does not

Transfer Acceleration helps most when the person transferring a file is far from the bucket's Region, and for large files, where a longer, steadier path to the edge location matters. It gives little or nothing to users in or near the bucket's Region, and small files see no useful change.

Amazon S3 charges an additional per-GB fee for data transferred with acceleration, on top of the usual S3 charges. See [Amazon S3 pricing](https://aws.amazon.com/s3/pricing/) (Data transfer, Transfer Acceleration). AWS has stated it does not charge the acceleration fee for an upload when acceleration is not likely to be faster; check the current AWS terms because they can change. Use the [Amazon S3 Transfer Acceleration speed comparison tool](https://s3-accelerate-speedtest.s3-accelerate.amazonaws.com/en/accelerate-speed-comparsion.html) from your users' locations to decide whether it is worth turning on.

## Amazon S3 requirements

The [Amazon S3 requirements for Transfer Acceleration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/transfer-acceleration.html#transfer-acceleration-requirements) apply to every bucket VAMS accelerates:

-   Acceleration works for general purpose buckets, with virtual-hosted style requests and a bucket name that is DNS-compliant and has no period.
-   Acceleration must be enabled on the bucket. AWS says the transfer speed can take up to 20 minutes to improve after you enable it.
-   Acceleration is supported only in a list of Regions that AWS maintains on that page, which does not include every commercial Region (for example, it did not include eu-north-1 when this page was written). Check the list for the bucket's Region before you turn the setting on. VAMS does not carry the list, so a bucket in an unsupported Region fails at deployment: the CloudFormation stack update for a VAMS-created bucket, or the acceleration check for an external bucket.
-   The accelerate endpoint is `<bucket>.s3-accelerate.amazonaws.com`. VAMS does not use the dual-stack accelerate endpoint.
-   FIPS endpoints: when the backend runs with a FIPS endpoint setting (`AWS_USE_FIPS_ENDPOINT=true`), VAMS keeps signing regional URLs for the bucket and logs the reason, because the accelerate endpoint is not used with it. Deployments that require FIPS endpoints do not get acceleration.

## Partition availability

Transfer Acceleration is available in the commercial partition (`aws`) only. The AWS GovCloud (US) and AWS European Sovereign Cloud partitions do not offer it, so configuration validation rejects the setting there instead of deploying a bucket that cannot serve accelerated URLs. The China and ISO partitions are rejected the same way. With the setting `false`, which is the default and the value in every shipped template, deployments in those partitions are unchanged.

## Configuration

There are two settings, both default `false`.

| Setting                                                             | Covers                                                                                                                          |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `app.assetBuckets.useS3TransferAcceleration`                        | The asset bucket VAMS creates (`createNewBucket: true`). VAMS enables acceleration on the bucket.                               |
| `app.assetBuckets.externalAssetBuckets[].useS3TransferAcceleration` | One external bucket entry. VAMS does not change the bucket: its owner enables acceleration first, and the deployment checks it. |

### Example: the VAMS-created bucket

```json
{
    "app": {
        "assetBuckets": {
            "createNewBucket": true,
            "defaultNewBucketSyncDatabaseId": "default",
            "useS3TransferAcceleration": true,
            "externalAssetBuckets": null
        }
    }
}
```

VAMS enables acceleration on the created asset bucket and on the auxiliary bucket.

### Example: an external bucket

```json
{
    "app": {
        "assetBuckets": {
            "createNewBucket": false,
            "useS3TransferAcceleration": false,
            "externalAssetBuckets": [
                {
                    "bucketArn": "arn:aws:s3:::my-vams-assets",
                    "baseAssetsPrefix": "/",
                    "defaultSyncDatabaseId": "default",
                    "isDefault": true,
                    "useS3TransferAcceleration": true
                }
            ]
        }
    }
}
```

With `createNewBucket: false`, leave the top-level setting `false` and set the key on the external entries. The top-level setting covers only a bucket VAMS creates.

The deployment does not create the database named in `defaultSyncDatabaseId`; create it afterwards (for example `vamscli database create --default-bucket-id <bucketId>`) before you expect assets to appear from that bucket.

:::note[How long a rejected change takes]
A flagged external bucket without acceleration (or with a denied status read) is found about 5 minutes into the deployment; CloudFormation then rolls back for 1 to 2 minutes, and the stack stays usable. Removing the entry or setting the key to `false` and deploying again took 9 to 14 minutes on a test deployment. Check the bucket with `get-bucket-accelerate-configuration` before you deploy to avoid the round trip.
:::

### The auxiliary bucket follows the default bucket

VAMS always creates the auxiliary bucket (previews, viewer data, staged exports and prepared zip archives). It follows the **default** asset bucket:

-   When the default asset bucket is the one VAMS creates, the auxiliary bucket is accelerated when `app.assetBuckets.useS3TransferAcceleration` is `true`.
-   When an external entry is marked `isDefault: true`, it overrides the created bucket as the default, and the auxiliary bucket is accelerated when that entry sets `useS3TransferAcceleration: true`, whatever the top-level setting says.

VAMS enables acceleration on the auxiliary bucket itself, because it owns it. The artefacts bucket is never accelerated.

### Validation rules

`cdk synth` and `cdk deploy` fail with a configuration error, and the [Configuration Builder](config-builder.mdx) shows the same rule, when:

-   The setting is `true` outside the commercial partition.
-   `app.assetBuckets.useS3TransferAcceleration` is `true` while `createNewBucket` is `false`. There is no VAMS-created bucket to accelerate; set the key on the external entries instead.
-   An external bucket that sets the key has a name that is not 3 to 63 characters of lowercase letters, digits and hyphens, or that contains a period. Acceleration needs a bucket name that works as a host name label, which a name with a period does not.
-   Entries for one bucket ARN (one bucket registered under several prefixes) disagree on the key. One bucket has one setting.
-   An accelerated bucket is combined with `presignedUrlNetworkRestrictions.allowedVpceIds` (only when the restriction covers an accelerated bucket). See [Presigned URL network restrictions](#presigned-url-network-restrictions).
-   A key is not a boolean, for example the string `"true"`.

## Turning it on, off and upgrading

-   The setting is off by default. Existing deployments are unchanged by the upgrade that introduces it, apart from one in-place code update of the Lambda function that fills the S3 asset buckets table: the buckets get no acceleration setting, no IAM permission is added and no table attribute is written until you turn it on.
-   Turning it on for a VAMS-created bucket takes one deployment. CloudFormation enables acceleration on the buckets and the deployment records the setting in the S3 asset buckets table. A new deployment with the key set needs no extra step: on a test deployment, uploads and downloads through the accelerate host worked from the first sign-in, because the buckets are created well before the application is usable.
-   Turning it on for an external bucket needs the bucket owner's work first. See [Step 5b](external-s3-setup.md#step-5b-enable-s3-transfer-acceleration-optional). The deployment checks the bucket and fails, naming it, if acceleration is not enabled.
-   After the deployment, backend functions pick the change up within 10 minutes, because they cache a bucket's setting for that long. On a test deployment the shareable-link call switched hosts within about 30 seconds of the deployment writing the table, in both directions (the write happens several minutes before the deployment finishes); calls that look a bucket up by name can keep the old setting for the full 10 minutes. Allow that time before verifying on a deployment with warm functions.
-   Turning it off is the reverse: set the key to `false` (or remove it) and deploy. The deployment removes the acceleration setting from the template of a VAMS-created bucket and stops writing the table attribute. For up to 10 minutes after the deployment, backend functions that are already running keep the cached setting and can still sign accelerate URLs, so a download or upload started in that window can fail only if the bucket no longer accepts the accelerate host (for example an external bucket whose owner suspended it); a VAMS-created bucket stays accelerated, so nothing fails there. Request a new link after the window. CloudFormation does not suspend acceleration on a VAMS-created bucket when the property is removed: on a test deployment both the created bucket and the auxiliary bucket still reported `Enabled` after the key was set to `false`. VAMS no longer uses it, so URLs go back to the regional host and nothing breaks, and it is harmless (only requests to the accelerate host are billed for it). To suspend it, use the bucket's **Properties > Transfer acceleration** setting. On an external bucket VAMS changes nothing, and leaving acceleration enabled there is harmless.
-   Presigned URLs already handed out keep working until they expire, in either direction, as long as the bucket still accepts that endpoint.

## How to verify it works

1. **URL host.** Request a download link and read its host. With the VAMS CLI:

    ```bash
    vamscli assets download -d <DATABASE_ID> -a <ASSET_ID> --shareable-links-only --json-output
    ```

    The `downloadUrl` values for files in a flagged bucket start with `https://<bucket>.s3-accelerate.amazonaws.com/`. A bucket that is not flagged shows `https://s3.<region>.amazonaws.com/<bucket>/...`.

    To check the stream endpoint, send a `GET` without following redirects (for example `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Authorization: Bearer <TOKEN>' <STREAM_URL>`): the endpoint answers `307` with an accelerate-host `Location`. A `curl -I` request returns `200` and does not show the redirect.

    The Download as Zip archive link is also on the accelerate host when the archive is in the accelerated auxiliary bucket. Download as Zip itself needs the global VPC (`app.useGlobalVpc.enabled`), so a deployment without it has no zip link to check.

    The bucket list also shows the setting. `GET /buckets` returns `isAccelerated` for each bucket, and the create-database bucket picker in the web app marks an accelerated bucket with `(accelerated)`. `vamscli database list-buckets` prints `Accelerated: Yes` or `Accelerated: No` for each bucket (and carries `isAccelerated` in `--json-output`), and the MCP `list_buckets` tool returns the same field, which makes them the quickest check from a terminal or an agent. The list reads the bucket table on every call, so it shows a change at once. The 10-minute cache above applies only to how backend functions sign URLs, so an accelerated entry in the list does not show that warm functions already sign accelerate URLs. Check the URL host (step 1) for that.

2. **Credential scope.** In the same URL, `X-Amz-Credential` ends with `<date>/<region>/s3/aws4_request`, and `<region>` is the **bucket's** Region, not an edge location.
3. **Browser.** Open the browser developer tools Network tab, download a file or open a viewer, and confirm the request goes to `<bucket>.s3-accelerate.amazonaws.com` and succeeds. A multipart upload shows one request per part to the same host, each response readable for its `ETag`.
4. **Speed.** A gain is visible only from a client far from the bucket Region. From inside that Region the results are the same, which is expected.

## Interactions with other settings

### Presigned URL network restrictions

`app.assetBuckets.presignedUrlNetworkRestrictions` adds a bucket policy deny for presigned requests that do not come from the allowed addresses or VPC endpoints.

-   **`allowedVpceIds` is rejected with acceleration.** An accelerated request reaches S3 from the client's public address through CloudFront edge locations, never through a VPC endpoint, so the deny statement would block every accelerated URL. The restriction is applied only to the VAMS-created asset bucket and the auxiliary bucket, so the rule applies when one of those two is accelerated. An accelerated external bucket that is neither the default bucket nor the source of the auxiliary bucket's setting carries no VAMS restriction and is not affected.
-   **`allowedIpRanges`.** Allowed together with acceleration. On a test deployment the bucket policy's `aws:SourceIp` condition matched the client's public IPv4 address for requests through the accelerate endpoint, so a request from outside the allowed ranges is denied and one from inside is served. Set the ranges to your users' public internet egress addresses, and check both an accelerated download and, if your users also read S3 directly, a direct one: a network with a private route to Amazon S3 can present a different source address on direct requests. IPv6 was not tested.

### Content Security Policy

The web application's Content Security Policy already allows the Amazon S3 hosts for the partition with a wildcard, which covers `<bucket>.s3-accelerate.amazonaws.com`. The deployment adds no extra source, so the policy size stays the same. See [Security](../architecture/security.md).

### CORS

Cross-origin rules belong to the bucket and apply to every host that serves it, including the accelerate host. The VAMS-created buckets already allow what the web application needs, and an external bucket keeps the CORS rule from [Step 2](external-s3-setup.md#step-2-configure-cors). Confirm `ETag` stays in `ExposeHeaders`: multipart uploads read it from each part response.

### Global VPC and cross-Region buckets

-   **Global VPC.** The browser or CLI user's transfer is not made from inside the VPC, so the VPC and its S3 endpoints do not affect accelerated URLs. VAMS's own server-side access to the bucket still uses the regular endpoint through the VPC.
-   **Cross-Region external bucket.** The accelerate endpoint is global. VAMS still signs for the bucket's Region, and the deployment check calls Amazon S3 in the bucket's Region, so the Lambda function that runs it needs a network path to that Region (see [Cross-Region buckets](external-s3-setup.md#cross-region-buckets)).

## Troubleshooting

| Symptom                                                                                                                                   | Cause                                                                                                         | Fix                                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The deployment fails with `S3 Transfer Acceleration is requested for bucket <name> ... but its status is not configured` (or `Suspended`) | The external bucket does not have acceleration enabled, or the status is `Suspended`.                         | Enable it on the bucket ([Step 5b](external-s3-setup.md#step-5b-enable-s3-transfer-acceleration-optional)) and deploy again, or set the key to `false`.                                                             |
| The deployment fails with `Could not read the S3 Transfer Acceleration configuration of bucket <name> in Region <region> (AccessDenied)`  | The bucket policy does not allow `s3:GetAccelerateConfiguration` to the VAMS account.                         | Add the permission to the bucket policy ([Step 1](external-s3-setup.md#step-1-configure-the-s3-bucket-policy)), and remove any explicit `Deny` for `s3:GetAccelerateConfiguration`, which an allow cannot override. |
| The deployment fails with `Could not read the S3 Transfer Acceleration configuration of bucket <name> in Region <region> (NoSuchBucket)`  | The bucket does not exist in that Region, or `bucketRegion` is wrong.                                         | Correct the entry or create the bucket.                                                                                                                                                                             |
| Configuration error naming the bucket name                                                                                                | The bucket name has a period or is not a valid host name label.                                               | Acceleration cannot serve that bucket. Set the key to `false` for it.                                                                                                                                               |
| Configuration error about the commercial partition                                                                                        | The setting is `true` in GovCloud, EU Sovereign, China or an ISO partition.                                   | Set the key to `false`.                                                                                                                                                                                             |
| Accelerated requests are slow shortly after first enabling acceleration                                                                   | AWS says the transfer speed can take up to 20 minutes to improve after you enable acceleration.               | Wait and retry. Check the status with `aws s3api get-bucket-accelerate-configuration --bucket <BUCKET_NAME>`.                                                                                                       |
| Downloads or uploads fail only on some corporate networks                                                                                 | A proxy or firewall allow-list covers the regional S3 hosts but not `*.s3-accelerate.amazonaws.com`.          | Add `*.s3-accelerate.amazonaws.com` to the allow-list, or turn acceleration off.                                                                                                                                    |
| Multipart upload fails at completion; the part response shows no `ETag`                                                                   | The bucket CORS rule does not expose `ETag`.                                                                  | Add `ETag` to `ExposeHeaders` ([Step 2](external-s3-setup.md#step-2-configure-cors)).                                                                                                                               |
| URLs still use the regional host after the deployment                                                                                     | Backend functions cache the setting for up to 10 minutes (the shareable-link call showed it within a minute). | Wait 10 minutes, then request a new link.                                                                                                                                                                           |

## Related pages

-   [Configuration reference](configuration-reference.md#asset-buckets-appassetbuckets)
-   [External Amazon S3 bucket setup](external-s3-setup.md)
-   [Security](../architecture/security.md)
-   [AWS resources](../architecture/aws-resources.md)
-   [Uninstall](uninstall.md)
