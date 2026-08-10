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
    """Raised when the VAMS default asset bucket cannot be resolved to one bucket."""


class DefaultBucketAmbiguousError(DefaultBucketNotFoundError):
    """Raised when several buckets are flagged as the VAMS default.

    Subclasses DefaultBucketNotFoundError so callers that already treat an unresolvable default
    bucket as fatal need no new arm; the distinct type keeps the two causes apart in logs.
    """


def default_bucket_key(default_bucket, key: str) -> str:
    """The full bucket key for a VAMS-managed pipeline key inside the default bucket.

    The `*_s3_key` builders (template bodies, run I/O, execution inputs) produce keys relative to the
    area VAMS owns, which for an external bucket registered under a prefix is that prefix rather than
    the bucket root. Every S3 call against the default bucket goes through here so the prefix is
    applied in one place and the stored keys stay prefix-independent.

    `default_bucket` is a row from resolve_default_bucket (or the prefix string itself).
    """
    prefix = (default_bucket if isinstance(default_bucket, str)
              else (default_bucket or {}).get("baseAssetsPrefix") or "")
    prefix = prefix.strip("/")
    body = (key or "").lstrip("/")
    return f"{prefix}/{body}" if prefix else body


def resolve_default_bucket(buckets_table) -> dict:
    """Return the default asset bucket row: {bucketId, bucketName, baseAssetsPrefix}.

    `buckets_table` is a boto3 DynamoDB Table resource for the S3 asset buckets table. The default
    bucket is the row with `isDefault = True`. A bucket may be registered under multiple prefixes
    (multiple rows share a bucketName); the root-prefix row is preferred so callers get the bucket's
    canonical base. `baseAssetsPrefix` is the area VAMS owns within that bucket — join keys to it with
    default_bucket_key rather than treating them as bucket-root-relative.

    Raises DefaultBucketNotFoundError when none is flagged, DefaultBucketAmbiguousError when more
    than one bucket is.
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

    # Several flagged buckets is unresolvable, not a tie to break: the deployment only ever marks one,
    # so a second flag is a row for a bucket that left the configuration and no longer carries VAMS's
    # grants. Picking either one would send every template body and all run I/O to a bucket that may
    # reject the write, so the ambiguity surfaces instead.
    distinct_buckets = {item.get("bucketName") for item in items}
    if len(distinct_buckets) > 1:
        logger.error(
            f"More than one bucket is flagged as the VAMS default ({sorted(distinct_buckets)}). Clear "
            "the stale isDefault row(s) in the S3 asset buckets table."
        )
        raise DefaultBucketAmbiguousError(
            "More than one asset bucket is flagged as the VAMS default (isDefault=true). Clear the "
            "stale row(s) in the S3 asset buckets table so exactly one bucket is the default."
        )

    ordered = sorted(items, key=_prefix_rank)
    chosen = ordered[0]
    return {
        "bucketId": chosen.get("bucketId"),
        "bucketName": chosen.get("bucketName"),
        "baseAssetsPrefix": chosen.get("baseAssetsPrefix") or "",
    }
