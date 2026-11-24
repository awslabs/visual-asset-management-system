# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Enumeration of the files that currently live in the registered asset buckets.

Walks every registration of the S3 asset buckets table with ``list_objects_v2`` (current versions only,
so a file behind a delete marker is never listed): a retired registration is not walked, and two rows for
one location (``common/bucketRegistrations.py``) are walked once. It skips folder markers, reserved system
folders and
``.previewFile.`` objects, and attributes each key to the live asset whose stored location contains it.
Once the location index is ready (``assetLocations.index_ready``), each bucket's index partition is read
once and every key is matched in memory (longest match), so files in any stored location are found. The
deepest stored location that holds a key owns it, live or archived, and the key is kept only when that
owner is live: a file of an archived asset nested in a live asset's location is enumerated for nobody,
never for the containing asset. Before that, the asset id is the first key segment beneath the bucket's
base prefix and the live database id comes from the asset table's ``BucketIdGSI``. Either way a row in a
``{databaseId}#deleted`` partition does not count, so files of archived assets are excluded.

The OpenSearch reindexer and the vector reindexer both walk the buckets this way; these helpers are
the single definition of "what is an asset file" for both.
"""

import json
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Key

from common.assetLocations import (
    RESOLVED_FOUND,
    BucketLocations,
    index_ready,
    load_bucket_locations,
    relative_path_in,
)
from common.assetLocations import normalize_base_prefix as _normalize_registration_prefix
from common.bucketRegistrations import active_registrations, primary_bucket_id, primary_registrations
from common.s3PathPatterns import PREVIEW_FILE_PATTERN, RESERVED_S3_PREFIX_FOLDERS
from common.validators import validate
from customLogging.logger import safeLogger

logger = safeLogger()

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
    # The bucket registration the file was listed under; one bucket can hold several registrations
    # (different baseAssetsPrefix values), so a continuation names the registration, not the bucket.
    bucket_id: str = ""


def normalize_base_prefix(base_prefix: Optional[str]) -> str:
    """The bucket's base prefix in canonical form (``assetLocations.normalize_base_prefix``): a
    trailing slash, no leading slash, ``''`` for the bucket root."""
    return _normalize_registration_prefix(base_prefix)


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


def _resolve_in_location(asset_table, bucket_ids: List[str], asset_id: str) -> Optional[Tuple[str, str]]:
    """(bucketId, live databaseId) of the first of `bucket_ids` (the rows of one location, the walked row
    first) that holds a live row for `asset_id`, or None."""
    for bucket_id in bucket_ids:
        database_id = resolve_live_database_id(asset_table, bucket_id, asset_id)
        if database_id:
            return bucket_id, database_id
    return None


def scan_bucket_registrations(buckets_table) -> List[Dict]:
    """Every row of the S3 asset buckets table."""
    response = buckets_table.scan()
    buckets = list(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = buckets_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        buckets.extend(response.get("Items", []))
    return buckets


def encode_continuation(bucket_name: str, start_after: str, bucket_id: Optional[str]) -> str:
    """An opaque resumption token: the bucket registration being walked (bucket name and bucketId) and
    the last key handled in it."""
    return json.dumps({"bucket": bucket_name, "bucketId": bucket_id or "", "startAfter": start_after})


def decode_continuation(token: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(bucket name, last key handled, bucketId). The bucketId is None for a token written before tokens
    named the registration; such a token resumes by bucket name alone."""
    if not token:
        return None, None, None
    decoded = json.loads(token)
    return decoded.get("bucket"), decoded.get("startAfter"), decoded.get("bucketId")


def _registration_order(bucket: Dict) -> Tuple[str, str, str]:
    """The walk order of bucket registrations: bucket name, then bucketId (of the row that answers for
    the location, which is the row's own for every row that duplicates none), then base prefix."""
    return (bucket["bucketName"], primary_bucket_id(bucket), bucket.get("baseAssetsPrefix") or "")


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

    Bucket registrations are walked in (``bucketName``, ``bucketId``) order. ``start_after`` is a token
    from a previous call; the walk skips the registrations ordered before the one it names and resumes
    that one after the key it names, so a registration already finished is never listed again (two
    registrations of one bucket with different prefixes are two entries of the walk). When
    ``time_remaining_fn`` reports fewer than ``min_remaining_ms`` milliseconds at a page boundary, the
    files gathered so far are returned with a token; a completed walk returns ``None`` for the token.
    ``database_id`` keeps only files whose asset resolves to that database.
    """
    token_bucket, token_key, token_bucket_id = decode_continuation(start_after)
    registrations = [bucket for bucket in scan_bucket_registrations(buckets_table) if bucket.get("bucketName")]
    # A retired registration (it left the configuration) is not walked, and two rows for one location
    # are walked once, as the row that answers for it; an asset of either row is listed.
    active = active_registrations(registrations)
    buckets = sorted(primary_registrations(active), key=_registration_order)
    location_ids: Dict[str, List[str]] = {}
    for bucket in active:
        location_ids.setdefault(primary_bucket_id(bucket), []).append(bucket.get("bucketId") or "")
    # Every row's location, retired rows included, so an owner's registration can be compared.
    location_of = {str(bucket.get("bucketId") or ""): primary_bucket_id(bucket) for bucket in registrations}
    if token_bucket_id:
        # A token names the registration it stopped in; a row that duplicates another resumes there.
        token_bucket_id = location_of.get(token_bucket_id, token_bucket_id)
    resolved: Dict[Tuple[str, str], Optional[Tuple[str, str]]] = {}
    refs: List[FileRef] = []
    by_index = index_ready(getattr(asset_table, "name", "") or "")
    locations_by_bucket: Dict[str, BucketLocations] = {}

    def out_of_time() -> bool:
        return time_remaining_fn is not None and time_remaining_fn() < min_remaining_ms

    for bucket in buckets:
        bucket_name = bucket["bucketName"]
        bucket_id = bucket.get("bucketId")
        walked_location = primary_bucket_id(bucket)
        if token_bucket is not None and token_bucket_id is not None:
            position, token_position = (bucket_name, walked_location), (token_bucket, token_bucket_id)
            if position < token_position:
                continue
            resume_key = token_key if position == token_position else None
        elif token_bucket is not None:
            # A token that names no registration resumes by bucket name, as it was written.
            if bucket_name < token_bucket:
                continue
            resume_key = token_key if bucket_name == token_bucket else None
        else:
            resume_key = None
        base_prefix = normalize_base_prefix(bucket.get("baseAssetsPrefix"))
        # The walked row first, then the other rows for its location.
        member_ids = [bucket_id or ""] + [other for other in location_ids.get(walked_location, [])
                                          if other != (bucket_id or "")]
        last_key = resume_key
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(**list_objects_kwargs(bucket_name, base_prefix, resume_key)):
            for obj in page.get("Contents", []):
                s3_key = obj["Key"]
                last_key = s3_key
                if is_folder_marker(s3_key) or is_excluded_s3_key(s3_key):
                    continue
                if by_index:
                    if bucket_name not in locations_by_bucket:
                        locations_by_bucket[bucket_name] = load_bucket_locations(asset_table, bucket_name)
                    # The deepest owner decides, live or archived; an archived one keeps the key from
                    # every asset, so it is not handed to a live asset whose location contains it.
                    resolution = locations_by_bucket[bucket_name].owner(s3_key, include_archived=True)
                    if resolution.status != RESOLVED_FOUND or resolution.entry.archived:
                        continue
                    entry = resolution.entry
                    if database_id is not None and entry.database_id != database_id:
                        continue
                    # The owner is found by bucket name; it must be in the registration being walked,
                    # or the reference would pair one registration's asset with another's prefix.
                    if entry.bucket_id and location_of.get(entry.bucket_id, entry.bucket_id) != walked_location:
                        logger.warning(f"Key {s3_key} in bucket {bucket_name} is held by asset {entry.asset_id} "
                                       f"of registration {entry.bucket_id}, not of {bucket_id}; not listed "
                                       "for this registration")
                        continue
                    refs.append(FileRef(
                        database_id=entry.database_id,
                        asset_id=entry.asset_id,
                        relative_file_key=relative_path_in(entry.location, s3_key),
                        bucket_name=bucket_name,
                        base_assets_prefix=base_prefix,
                        s3_key=s3_key,
                        bucket_id=entry.bucket_id or bucket_id or "",
                    ))
                    continue
                asset_id = extract_asset_id_from_key(s3_key, base_prefix)
                if not asset_id or not is_valid_asset_id(asset_id):
                    continue
                cache_key = (bucket_id or "", asset_id)
                if cache_key not in resolved:
                    resolved[cache_key] = _resolve_in_location(asset_table, member_ids, asset_id)
                if not resolved[cache_key]:
                    continue
                owner_bucket_id, resolved_database_id = resolved[cache_key]
                if database_id is not None and resolved_database_id != database_id:
                    continue
                refs.append(FileRef(
                    database_id=resolved_database_id,
                    asset_id=asset_id,
                    relative_file_key=relative_file_key(s3_key, base_prefix, asset_id),
                    bucket_name=bucket_name,
                    base_assets_prefix=base_prefix,
                    s3_key=s3_key,
                    bucket_id=owner_bucket_id or "",
                ))
            if last_key is not None and out_of_time():
                return refs, encode_continuation(bucket_name, last_key, walked_location or bucket_id)
    return refs, None
