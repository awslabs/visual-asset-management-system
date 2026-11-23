#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Tag the objects earlier VAMS releases synced (`reindex_utility.py --tag-synced`).

From v2.7 bucket sync writes the object tags ``VAMS:databaseId`` and ``VAMS:assetId`` on every file
version it syncs. Objects synced before the upgrade carry the old ``databaseid`` / ``assetid``
metadata instead and no tags. This walk tags the current version of each such object with the same
values, encoding and skip rules bucket sync uses (``common/vamsObjectTags.py`` in the backend source):

- the asset is the one whose stored location is the deepest that holds the key, read from the asset
  location index (``AssetLocationGSI``), one query per bucket; the object's metadata is not read; a key
  whose deepest owner is archived is not tagged (``notAttributed``), also inside a live asset's location;
- customer tags are kept and only the two ``VAMS:`` keys change; a value S3 tag values do not accept is
  written as ``b64:`` plus its base64url form; a value over 256 characters is left off;
- an object whose own tags leave no room (more than 10 with the VAMS tags) is skipped (``tagLimit``);
- a registration whose ``tagSyncedObjects`` is false is not walked;
- folder markers, VAMS system folders and keys no asset holds are left alone.

It refuses to run until the location index backfill has written its completion marker, because the
index decides which asset an object belongs to. It is never run automatically. Each object costs one
``GetObjectTagging`` and, when the tags are missing or name another asset, one ``PutObjectTagging``;
``--dry-run`` reads the tags and writes none. Every object it could not tag, and every object it
would tag in a dry run, is written to the report file (JSON Lines); the summary is logged at the end.

A tag write raises no event bucket sync subscribes to and creates no object version.
"""

import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterator, List, Optional

from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

TAG_WORKERS = 8
S3_LIST_PAGE_SIZE = 1000
_RETRY = Config(retries={"max_attempts": 8, "mode": "adaptive"})
_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException", "403"}

OUTCOME_WRITTEN = "written"
OUTCOME_WOULD_WRITE = "wouldWrite"
OUTCOME_ALREADY = "already"
OUTCOME_SKIPPED = "skipped"
OUTCOME_NOT_ATTRIBUTED = "notAttributed"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_SYSTEM = "systemKey"


def _backend_modules(backend_path: str):
    """The backend's location index and tag modules, imported from the backend source."""
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    from common import assetLocations, s3PathPatterns, vamsObjectTags  # noqa: E402
    return assetLocations, s3PathPatterns, vamsObjectTags


def _scan_all(table) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _list_keys(s3_client, bucket: str, prefix: str) -> Iterator[str]:
    kwargs = {"Bucket": bucket, "MaxKeys": S3_LIST_PAGE_SIZE}
    if prefix:
        kwargs["Prefix"] = prefix
    while True:
        response = s3_client.list_objects_v2(**kwargs)
        for entry in response.get("Contents", []):
            yield entry["Key"]
        if not response.get("IsTruncated"):
            return
        kwargs["ContinuationToken"] = response["NextContinuationToken"]


def _is_access_denied(error: ClientError) -> bool:
    status = str((error.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return (error.response or {}).get("Error", {}).get("Code") in _ACCESS_DENIED_CODES or status == "403"


class _BucketDenied(Exception):
    """Tagging is denied in this bucket; the rest of its objects are not attempted."""


def _tag_one(s3_client, tags_module, bucket: str, key: str, database_id: str, asset_id: str,
             dry_run: bool) -> Dict[str, Any]:
    try:
        existing = s3_client.get_object_tagging(Bucket=bucket, Key=key).get("TagSet", [])
        outcome, tag_set = tags_module.merged_tag_set(existing, database_id, asset_id)
        status = outcome.status
        if tag_set is not None:
            if dry_run:
                status = OUTCOME_WOULD_WRITE
            else:
                s3_client.put_object_tagging(Bucket=bucket, Key=key, Tagging={"TagSet": tag_set})
        return {"status": status, "reason": outcome.reason}
    except ClientError as e:
        if _is_access_denied(e):
            raise _BucketDenied(bucket) from e
        return {"status": OUTCOME_SKIPPED, "reason": (e.response or {}).get("Error", {}).get("Code", "error")}


def tag_synced_objects(session, resource_names_prefix: str, asset_table_name: str, buckets_table_name: str,
                       backend_path: str, dry_run: bool = False, limit: Optional[int] = None,
                       report_path: Optional[str] = None, workers: int = TAG_WORKERS) -> Dict[str, Any]:
    """Tag every attributed object of every registration with tagging on. Returns the run summary,
    with `error` set when the run could not start."""
    os.environ["VAMS_RESOURCE_PARAM_PREFIX"] = resource_names_prefix
    asset_locations, path_patterns, tags_module = _backend_modules(backend_path)
    # The marker is read with this run's own session (its profile and Region), not the backend's default
    # reader, which builds a client from the environment.
    ssm = session.client("ssm", config=_RETRY)

    def read_parameter(name: str) -> str:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]

    if not asset_locations.index_ready(asset_table_name, read_parameter=read_parameter):
        return {"error": "The asset location index is not ready: run the assetLocationIndex migration step "
                         "(it writes the completion marker) before --tag-synced."}

    dynamodb = session.resource("dynamodb", config=_RETRY)
    asset_table = dynamodb.Table(asset_table_name)
    registrations = _scan_all(dynamodb.Table(buckets_table_name))
    reserved = (path_patterns.RESERVED_S3_PREFIX_FOLDERS
                | {path_patterns.AUXILIARY_PREPARED_DOWNLOADS_PREFIX.strip("/")})
    counts: Dict[str, int] = {}
    report = open(report_path, "w") if report_path else None
    processed = 0
    locations_by_bucket: Dict[str, Any] = {}
    denied_buckets: List[str] = []

    def record(bucket: str, key: str, result: Dict[str, Any]) -> None:
        name = result["status"] + (f":{result['reason']}" if result.get("reason") else "")
        counts[name] = counts.get(name, 0) + 1
        if report and result["status"] not in (OUTCOME_WRITTEN, OUTCOME_ALREADY):
            report.write(json.dumps({"bucket": bucket, "key": key, **result}, sort_keys=True) + "\n")

    try:
        for registration in registrations:
            if limit is not None and processed >= limit:
                break
            bucket = registration.get("bucketName")
            if not bucket or not tags_module.tagging_enabled(registration):
                logger.info(f"Skipping registration {registration.get('bucketId')}: tagging is off")
                continue
            if bucket in denied_buckets:
                continue
            prefix = asset_locations.normalize_base_prefix(registration.get("baseAssetsPrefix"))
            if bucket not in locations_by_bucket:
                locations_by_bucket[bucket] = asset_locations.load_bucket_locations(asset_table, bucket)
            locations = locations_by_bucket[bucket]
            s3_client = session.client("s3", region_name=registration.get("bucketRegion") or None, config=_RETRY)
            logger.info(f"Tagging bucket {bucket} under '{prefix}' ({len(locations)} indexed locations)")
            try:
                batch = []
                for key in _list_keys(s3_client, bucket, prefix):
                    if limit is not None and processed >= limit:
                        break
                    processed += 1
                    if key.endswith("/") or path_patterns.key_has_reserved_segment(key, prefix, reserved):
                        record(bucket, key, {"status": OUTCOME_SYSTEM})
                        continue
                    # The deepest owner decides, live or archived (a live row wins at one location), so an
                    # archived asset's key is never tagged for a live asset whose location contains it.
                    owner = locations.owner(key, include_archived=True)
                    if owner.status == asset_locations.RESOLVED_AMBIGUOUS and \
                            not all(candidate.archived for candidate in owner.candidates):
                        record(bucket, key, {"status": OUTCOME_AMBIGUOUS})
                        continue
                    if owner.status != asset_locations.RESOLVED_FOUND or owner.entry.archived:
                        record(bucket, key, {"status": OUTCOME_NOT_ATTRIBUTED})
                        continue
                    batch.append((key, owner.entry.database_id, owner.entry.asset_id))
                    if len(batch) >= S3_LIST_PAGE_SIZE:
                        _run_batch(s3_client, tags_module, bucket, batch, dry_run, workers, record)
                        batch = []
                if batch:
                    _run_batch(s3_client, tags_module, bucket, batch, dry_run, workers, record)
            except _BucketDenied:
                denied_buckets.append(bucket)
                logger.warning(f"Tagging is denied in bucket {bucket}: allow s3:GetObjectTagging and "
                               "s3:PutObjectTagging to this deployment, then run again. Its other objects "
                               "were not attempted.")
                record(bucket, "", {"status": OUTCOME_SKIPPED, "reason": "accessDenied"})
    finally:
        if report:
            report.close()
    return {"dryRun": dry_run, "objectsSeen": processed, "outcomes": dict(sorted(counts.items())),
            "accessDeniedBuckets": denied_buckets, "report": report_path}


def _run_batch(s3_client, tags_module, bucket, batch, dry_run, workers, record) -> None:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [(key, pool.submit(_tag_one, s3_client, tags_module, bucket, key, database_id, asset_id, dry_run))
                   for key, database_id, asset_id in batch]
        for key, future in futures:
            record(bucket, key, future.result())
