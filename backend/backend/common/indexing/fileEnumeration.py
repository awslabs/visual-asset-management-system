# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Enumeration of the files that currently live in the registered asset buckets.

Walks every row of the S3 asset buckets table with ``list_objects_v2`` (current versions only, so a
file behind a delete marker is never listed), skips folder markers, reserved system folders and
``.previewFile.`` objects, derives the asset id from the first key segment beneath the bucket's base
prefix, and resolves the live database id through the asset table's ``BucketIdGSI`` -- a row in a
``{databaseId}#deleted`` partition does not resolve, so files of archived assets are excluded.

The OpenSearch reindexer and the vector reindexer both walk the buckets this way; these helpers are
the single definition of "what is an asset file" for both.
"""

import json
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Key

from common.s3PathPatterns import PREVIEW_FILE_PATTERN, RESERVED_S3_PREFIX_FOLDERS
from common.validators import validate
from customLogging.logger import safeLogger

logger = safeLogger(service_name="FileEnumeration")

# Key substrings that mark an object as a derived artefact rather than an asset file.
EXCLUDED_KEY_PATTERNS: Tuple[str, ...] = (PREVIEW_FILE_PATTERN,)

ASSET_BUCKET_ID_INDEX = "BucketIdGSI"


@dataclass(frozen=True)
class FileRef:
    """One current asset file: where it lives and which asset it belongs to."""

    database_id: str
    asset_id: str
    relative_file_key: str
    bucket_name: str
    base_assets_prefix: str
    s3_key: str


def normalize_base_prefix(base_prefix: Optional[str]) -> str:
    """The bucket's base prefix without a leading slash (``''`` when unset)."""
    prefix = base_prefix or ""
    return prefix[1:] if prefix.startswith("/") else prefix


def is_folder_marker(s3_key: str) -> bool:
    return s3_key.endswith("/")


def is_excluded_s3_key(s3_key: str) -> bool:
    """True for preview-file objects and for keys with a reserved system folder as a path segment."""
    if any(pattern in s3_key for pattern in EXCLUDED_KEY_PATTERNS):
        return True
    return any(part in RESERVED_S3_PREFIX_FOLDERS for part in s3_key.split("/"))


def extract_asset_id_from_key(object_key: str, base_prefix: str) -> Optional[str]:
    """The asset id: the first path segment beneath ``base_prefix`` (the first segment of the key when
    the bucket has no base prefix); None when the key is not beneath the prefix."""
    if not base_prefix or base_prefix == "/":
        parts = object_key.split("/")
        return parts[0] if parts and parts[0] else None
    prefix = base_prefix if base_prefix.endswith("/") else base_prefix + "/"
    if object_key.startswith(prefix):
        parts = object_key[len(prefix):].split("/")
        return parts[0] if parts and parts[0] else None
    return None


def is_valid_asset_id(asset_id: str) -> bool:
    """The common ASSET_ID validator applied to a key-derived candidate."""
    try:
        (valid, _) = validate({"assetId": {"value": asset_id, "validator": "ASSET_ID"}})
        return bool(valid)
    except Exception as e:
        logger.warning(f"Error validating asset ID {asset_id}: {e}")
        return False


def relative_file_key(s3_key: str, base_prefix: str, asset_id: str) -> str:
    """The asset-relative file path with a leading slash: the key minus the base prefix and ``{assetId}/``."""
    relative_path = s3_key
    if base_prefix and relative_path.startswith(base_prefix):
        relative_path = relative_path[len(base_prefix):].lstrip("/")
    if relative_path.startswith(f"{asset_id}/"):
        relative_path = relative_path[len(asset_id) + 1:]
    return f"/{relative_path}"


def list_objects_kwargs(bucket_name: str, base_prefix: str, start_after: Optional[str] = None) -> Dict[str, str]:
    """``list_objects_v2`` paginate arguments for a bucket walk; ``Prefix`` only when the bucket has one."""
    kwargs = {"Bucket": bucket_name}
    if base_prefix and base_prefix != "/":
        kwargs["Prefix"] = base_prefix
    if start_after:
        kwargs["StartAfter"] = start_after
    return kwargs


def resolve_live_database_id(asset_table, bucket_id: Optional[str], asset_id: Optional[str]) -> Optional[str]:
    """The databaseId of the live (non-archived) asset row for (bucketId, assetId), via BucketIdGSI."""
    if not bucket_id or not asset_id:
        return None
    response = asset_table.query(
        IndexName=ASSET_BUCKET_ID_INDEX,
        KeyConditionExpression=Key("bucketId").eq(bucket_id) & Key("assetId").eq(asset_id),
    )
    for item in response.get("Items", []):
        candidate = item.get("databaseId", "")
        if candidate and not candidate.endswith("#deleted"):
            return candidate
    return None


def scan_bucket_registrations(buckets_table) -> List[Dict]:
    """Every row of the S3 asset buckets table."""
    response = buckets_table.scan()
    buckets = list(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = buckets_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        buckets.extend(response.get("Items", []))
    return buckets


def encode_continuation(bucket_name: str, start_after: str) -> str:
    """An opaque resumption token: the bucket being walked and the last key handled in it."""
    return json.dumps({"bucket": bucket_name, "startAfter": start_after})


def decode_continuation(token: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not token:
        return None, None
    decoded = json.loads(token)
    return decoded.get("bucket"), decoded.get("startAfter")


def enumerate_latest_live_files(
    *,
    s3_client,
    buckets_table,
    asset_table,
    database_id: Optional[str] = None,
    start_after: Optional[str] = None,
    time_remaining_fn: Optional[Callable[[], int]] = None,
    min_remaining_ms: int = 90000,
) -> Tuple[List[FileRef], Optional[str]]:
    """The current files of every live asset, and a continuation token when time ran out.

    Buckets are walked in ``bucketName`` order. ``start_after`` is a token from a previous call; the
    walk resumes after the key it names and skips the buckets ordered before its bucket. When
    ``time_remaining_fn`` reports fewer than ``min_remaining_ms`` milliseconds at a page boundary, the
    files gathered so far are returned with a token; a completed walk returns ``None`` for the token.
    ``database_id`` keeps only files whose asset resolves to that database.
    """
    token_bucket, token_key = decode_continuation(start_after)
    buckets = sorted(
        (bucket for bucket in scan_bucket_registrations(buckets_table) if bucket.get("bucketName")),
        key=lambda bucket: bucket["bucketName"],
    )
    resolved: Dict[Tuple[str, str], Optional[str]] = {}
    refs: List[FileRef] = []

    def out_of_time() -> bool:
        return time_remaining_fn is not None and time_remaining_fn() < min_remaining_ms

    for bucket in buckets:
        bucket_name = bucket["bucketName"]
        if token_bucket is not None and bucket_name < token_bucket:
            continue
        base_prefix = normalize_base_prefix(bucket.get("baseAssetsPrefix"))
        bucket_id = bucket.get("bucketId")
        resume_key = token_key if bucket_name == token_bucket else None
        last_key = resume_key
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(**list_objects_kwargs(bucket_name, base_prefix, resume_key)):
            for obj in page.get("Contents", []):
                s3_key = obj["Key"]
                last_key = s3_key
                if is_folder_marker(s3_key) or is_excluded_s3_key(s3_key):
                    continue
                asset_id = extract_asset_id_from_key(s3_key, base_prefix)
                if not asset_id or not is_valid_asset_id(asset_id):
                    continue
                cache_key = (bucket_id or "", asset_id)
                if cache_key not in resolved:
                    resolved[cache_key] = resolve_live_database_id(asset_table, bucket_id, asset_id)
                resolved_database_id = resolved[cache_key]
                if not resolved_database_id:
                    continue
                if database_id is not None and resolved_database_id != database_id:
                    continue
                refs.append(FileRef(
                    database_id=resolved_database_id,
                    asset_id=asset_id,
                    relative_file_key=relative_file_key(s3_key, base_prefix, asset_id),
                    bucket_name=bucket_name,
                    base_assets_prefix=base_prefix,
                    s3_key=s3_key,
                ))
            if last_key is not None and out_of_time():
                return refs, encode_continuation(bucket_name, last_key)
    return refs, None
