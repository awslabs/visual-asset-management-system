# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve the VAMS default asset bucket.

The default asset bucket is the single bucket that houses all VAMS-managed pipeline data:
the template config/webform S3 offload and every execution-time run I/O area under the
`pipelines/` prefix. CDK marks exactly one row in the S3 asset buckets table with
`isDefault = True` (the VAMS-created bucket, or a configured external bucket for all-imports
deployments).

This helper is deliberately thin and takes an injected DynamoDB table resource so it stays
unit-testable and free of module-level AWS/environment coupling: callers resolve the table with
`get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)` and hand it in.
"""

from boto3.dynamodb.conditions import Attr

from customLogging.logger import safeLogger

logger = safeLogger(service_name="DefaultBucket")


class DefaultBucketNotFoundError(Exception):
    """Raised when no bucket row is flagged as the VAMS default."""


def resolve_default_bucket(buckets_table) -> dict:
    """Return the default asset bucket row: {bucketId, bucketName, baseAssetsPrefix}.

    `buckets_table` is a boto3 DynamoDB Table resource for the S3 asset buckets table. The default
    bucket is the row with `isDefault = True`. A bucket may be registered under multiple prefixes
    (multiple rows share a bucketName); the root-prefix row is preferred so callers get the bucket's
    canonical base. Raises DefaultBucketNotFoundError when none is flagged.
    """
    items = []
    scan_kwargs = {"FilterExpression": Attr("isDefault").eq(True)}
    while True:
        response = buckets_table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    if not items:
        raise DefaultBucketNotFoundError(
            "No asset bucket is flagged as the VAMS default (isDefault=true). Verify the deployment "
            "populated the S3 asset buckets table with a default bucket."
        )

    # Prefer the bucket-root row when a default bucket is registered under multiple prefixes;
    # bucketName keeps the order total so the winner never depends on scan order.
    def _prefix_rank(item):
        prefix = (item.get("baseAssetsPrefix") or "").strip("/")
        return (0 if prefix == "" else 1, prefix, item.get("bucketName") or "")

    ordered = sorted(items, key=_prefix_rank)
    distinct_buckets = {item.get("bucketName") for item in items}
    if len(distinct_buckets) > 1:
        logger.error(
            f"More than one bucket is flagged as the VAMS default ({sorted(distinct_buckets)}); "
            f"using {ordered[0].get('bucketName')}. Clear the stale isDefault row(s) in the S3 asset "
            "buckets table."
        )

    chosen = ordered[0]
    return {
        "bucketId": chosen.get("bucketId"),
        "bucketName": chosen.get("bucketName"),
        "baseAssetsPrefix": chosen.get("baseAssetsPrefix") or "",
    }
