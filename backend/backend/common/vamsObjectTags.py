# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The two VAMS object tags bucket sync writes on every file version it attributes.

``VAMS:databaseId`` and ``VAMS:assetId`` name the database and asset the version is synced to. They
are a marker for the customer's own tools (S3 Lifecycle and Replication rules match a tag key and
value), never an authority: anyone allowed ``s3:PutObjectTagging`` can set them, so attribution,
triggers, indexing and authorization never read them.

Writing a tag set creates no object version and changes no ETag, LastModified or content, and VAMS
subscribes to no ``s3:ObjectTagging:*`` notification, so the write raises no event bucket sync sees.

Rules:

- The version's tag set is read (``GetObjectTagging`` with the event's ``VersionId``) and written back
  merged (``PutObjectTagging`` with that ``VersionId``) only when a VAMS tag is missing or names another
  database or asset. Customer tags are kept; only the two ``VAMS:`` keys change.
- A value with a character S3 tag values do not accept (anything but letters, digits, space and
  ``+ - = . _ : / @``) is written as ``b64:`` plus its base64url form. A value still over 256
  characters is left off (``valueTooLong``), and a stale tag under its key is removed.
- A customer tag is never removed to make room: when the merged set would exceed 10 tags, nothing is
  written (``tagLimit``).
- ``AccessDenied`` skips the version (``accessDenied``) with a warning once per bucket per hour.

Every skipped or partial write is counted in the ``VamsTagsSkipped`` metric (namespace
``VAMS/BucketSync``, dimension ``Reason``) as an embedded-metric log line.

S3 has no conditional tagging write: a customer tag change made between the read and the write is
lost. The window is the milliseconds after the version's first event.
"""

import base64
import json
import re
import sys
import threading
import time
from typing import Any, Dict, List, NamedTuple, Optional

from botocore.exceptions import ClientError
from customLogging.logger import safeLogger

logger = safeLogger()

DATABASE_ID_TAG = "VAMS:databaseId"
ASSET_ID_TAG = "VAMS:assetId"
VAMS_TAG_KEYS = (DATABASE_ID_TAG, ASSET_ID_TAG)

ENCODED_VALUE_PREFIX = "b64:"
MAX_TAG_VALUE_LENGTH = 256
MAX_TAGS_PER_OBJECT = 10
# The characters every S3 tag value accepts, kept to ASCII.
TAG_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9 +\-=._:/@]*$")

STATUS_WRITTEN = "written"
STATUS_ALREADY = "already"
STATUS_SKIPPED = "skipped"

REASON_ACCESS_DENIED = "accessDenied"
REASON_TAG_LIMIT = "tagLimit"
REASON_VALUE_TOO_LONG = "valueTooLong"
REASON_ERROR = "error"

# The registration field that turns tagging off for one bucket registration (absent means on).
TAG_SYNCED_OBJECTS_FIELD = "tagSyncedObjects"

METRIC_NAMESPACE = "VAMS/BucketSync"
METRIC_SKIPPED = "VamsTagsSkipped"
METRIC_DIMENSION = "Reason"

ACCESS_DENIED_WARNING_INTERVAL_SECONDS = 3600
_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException", "403"}

_warned_lock = threading.Lock()
_access_denied_warned_at: Dict[str, float] = {}


class TagOutcome(NamedTuple):
    """`status` is written, already or skipped; `reason` names why a tag was skipped or left off."""
    status: str
    reason: str = ""


def tagging_enabled(registration: Optional[Dict[str, Any]]) -> bool:
    """Whether a bucket registration has tagging on; a registration without the field has it on."""
    value = (registration or {}).get(TAG_SYNCED_OBJECTS_FIELD)
    return value is not False and str(value).lower() != "false"


def encode_tag_value(value: str) -> Optional[str]:
    """The tag value for an id: the id itself when S3 accepts it, else ``b64:`` and its base64url
    form. None when the value is over 256 characters either way."""
    value = value or ""
    if TAG_VALUE_PATTERN.match(value) and not value.startswith(ENCODED_VALUE_PREFIX):
        encoded = value
    else:
        encoded = ENCODED_VALUE_PREFIX + base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
    return encoded if len(encoded) <= MAX_TAG_VALUE_LENGTH else None


def decode_tag_value(value: str) -> str:
    """The id a tag value names (the inverse of `encode_tag_value`)."""
    if value and value.startswith(ENCODED_VALUE_PREFIX):
        return base64.urlsafe_b64decode(value[len(ENCODED_VALUE_PREFIX):].encode("ascii")).decode("utf-8")
    return value


def merged_tag_set(existing: List[Dict[str, str]], database_id: str, asset_id: str):
    """`(TagOutcome, tag set to write or None)` for a version whose tag set is `existing`.

    None means nothing is written: the VAMS tags already say this (`already`), or the customer tags
    leave no room (`skipped`, `tagLimit`). A too-long value is left off and its outcome carries
    `valueTooLong`."""
    wanted = {DATABASE_ID_TAG: encode_tag_value(database_id), ASSET_ID_TAG: encode_tag_value(asset_id)}
    too_long = any(value is None for value in wanted.values())
    current = {tag.get("Key"): tag.get("Value") for tag in existing or []}
    customer = [tag for tag in existing or [] if tag.get("Key") not in VAMS_TAG_KEYS]
    vams = [{"Key": key, "Value": value} for key, value in wanted.items() if value is not None]
    reason = REASON_VALUE_TOO_LONG if too_long else ""

    if all(current.get(key) == value for key, value in wanted.items() if value is not None) and \
            all(key not in current for key, value in wanted.items() if value is None):
        return TagOutcome(STATUS_ALREADY, reason), None
    if len(customer) + len(vams) > MAX_TAGS_PER_OBJECT:
        return TagOutcome(STATUS_SKIPPED, REASON_TAG_LIMIT), None
    return TagOutcome(STATUS_WRITTEN, reason), customer + vams


def _version_kwargs(bucket: str, key: str, version_id: Optional[str]) -> Dict[str, str]:
    kwargs = {"Bucket": bucket, "Key": key}
    if version_id and version_id != "null":
        kwargs["VersionId"] = version_id
    return kwargs


def _is_access_denied(error: ClientError) -> bool:
    error_info = (error.response or {}).get("Error", {})
    status = str((error.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return error_info.get("Code") in _ACCESS_DENIED_CODES or status == "403"


def _warn_access_denied(bucket: str) -> None:
    now = time.monotonic()
    with _warned_lock:
        last = _access_denied_warned_at.get(bucket)
        if last is not None and now - last < ACCESS_DENIED_WARNING_INTERVAL_SECONDS:
            return
        _access_denied_warned_at[bucket] = now
    logger.warning(f"VAMS tags cannot be written in bucket {bucket} (access denied). The files are still "
                   "attributed and indexed; allow s3:GetObjectTagging, s3:GetObjectVersionTagging, "
                   "s3:PutObjectTagging and s3:PutObjectVersionTagging to tag them")


def reset_access_denied_warnings() -> None:
    """Forget when each bucket's access-denied warning was logged (tests)."""
    with _warned_lock:
        _access_denied_warned_at.clear()


def emit_bucket_sync_count(metric_name: str, reason: str) -> None:
    """One embedded-metric log line adding 1 to a bucket-sync count (namespace `VAMS/BucketSync`,
    dimension `Reason`)."""
    try:
        sys.stdout.write(json.dumps({
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": METRIC_NAMESPACE,
                    "Dimensions": [[METRIC_DIMENSION]],
                    "Metrics": [{"Name": metric_name, "Unit": "Count"}],
                }],
            },
            METRIC_DIMENSION: reason,
            metric_name: 1,
        }) + "\n")
    except Exception:  # nosec B110 - a metric line is best-effort and must never fail the sync
        pass


def emit_skipped_metric(reason: str) -> None:
    """One embedded-metric log line counting a skipped or partial VAMS tag write."""
    emit_bucket_sync_count(METRIC_SKIPPED, reason)


def tag_synced_version(s3_client, bucket: str, key: str, version_id: Optional[str], database_id: str,
                       asset_id: str) -> TagOutcome:
    """Read the version's tags and write the VAMS tags when they are missing or name another asset.
    Never raises: a failed read or write is reported in the outcome."""
    kwargs = _version_kwargs(bucket, key, version_id)
    try:
        existing = s3_client.get_object_tagging(**kwargs).get("TagSet", [])
        outcome, tag_set = merged_tag_set(existing, database_id, asset_id)
        if tag_set is not None:
            s3_client.put_object_tagging(**kwargs, Tagging={"TagSet": tag_set})
    except ClientError as e:
        if _is_access_denied(e):
            _warn_access_denied(bucket)
            outcome = TagOutcome(STATUS_SKIPPED, REASON_ACCESS_DENIED)
        else:
            logger.warning(f"VAMS tags not written for {key} in bucket {bucket}: "
                           f"{(e.response or {}).get('Error', {}).get('Code')}")
            outcome = TagOutcome(STATUS_SKIPPED, REASON_ERROR)
    except Exception as e:
        logger.warning(f"VAMS tags not written for {key} in bucket {bucket}: {type(e).__name__}")
        outcome = TagOutcome(STATUS_SKIPPED, REASON_ERROR)
    if outcome.reason:
        if outcome.reason == REASON_VALUE_TOO_LONG:
            logger.warning(f"The VAMS tag value for asset {asset_id} is over {MAX_TAG_VALUE_LENGTH} "
                           f"characters; {ASSET_ID_TAG} is left off {key}")
        emit_skipped_metric(outcome.reason)
    return outcome
