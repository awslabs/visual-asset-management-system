# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The state of a row in the S3 asset buckets table (a bucket registration).

Inside VAMS a bucket registration is identified by its `bucketId`. The deploy-time custom resource
`populateS3AssetBucketsTable` writes the rows of the configuration, updating only the attributes it owns,
and it never deletes a row, so an asset or a database that names a `bucketId` always resolves it. These
attributes say what a row still stands for:

- `retired` (True): the registration left the deployment's configuration. The row stays so the assets
  and databases that name its `bucketId` still resolve, but it answers no lookup by bucket name and
  prefix, is never the default bucket and is not offered for a new database. The custom resource sets
  `isDefault` False on it too. A registration that returns to the configuration is matched on its
  bucket name and prefix, keeps its `bucketId` and loses the flag.
- `duplicateOfBucketId`: a second row for the same bucket name and prefix (an earlier release minted a
  new `bucketId` when only the prefix's spelling changed, for example `assets` to `assets/`). The row
  names the registration that answers lookups by name and prefix (its primary). Both describe the same
  S3 location, so an asset of either belongs to that location, and `same_registration` treats them
  as one.

- `source`: `static` for a row the custom resource writes from the configuration (a row without the
  field is one too), `runtime` for a registration added at run time. The custom resource never matches,
  rewrites or retires a runtime row; a runtime row's `status` (`pending`, `active`, `disabled`) is its own.

Each row also carries what serves it: `bucketRegion`, `bucketAccountId`, `defaultSyncDatabaseId` (bucket
sync's fallback database) and `bucketKmsKeyArn` when the bucket uses a customer managed key.

The helpers are pure: callers read the rows (bucketNameGSI, a scan, a get by `bucketId`) and pass them in.
"""

from typing import Any, Dict, Iterable, List, Optional

from common.assetLocations import normalize_base_prefix

REGISTRATION_RETIRED_FIELD = "retired"
REGISTRATION_DUPLICATE_OF_FIELD = "duplicateOfBucketId"
REGISTRATION_SOURCE_FIELD = "source"
REGISTRATION_STATUS_FIELD = "status"

SOURCE_STATIC = "static"
SOURCE_RUNTIME = "runtime"

STATE_ACTIVE = "active"
STATE_RETIRED = "retired"
STATE_PENDING = "pending"
STATE_DISABLED = "disabled"
_RUNTIME_STATES = (STATE_PENDING, STATE_ACTIVE, STATE_DISABLED)

# The permission object type of a registration (Tier 2). Checked on the bucket routes only: the bucket
# listing and the bucket a database is put on. Never on asset, file, sync, indexing or workflow paths.
BUCKET_OBJECT_TYPE = "bucket"

# The one answer database create and update give for a bucket id that does not exist, is not active,
# or the caller may not read, so the answer does not tell them apart. Both answer it as the same 400
# body ({"message": BUCKET_NOT_AVAILABLE_MESSAGE}).
BUCKET_NOT_AVAILABLE_MESSAGE = "Bucket ID not found or not available"


def is_retired(row: Optional[Dict[str, Any]]) -> bool:
    """Whether the registration left the configuration."""
    return (row or {}).get(REGISTRATION_RETIRED_FIELD) is True


def registration_source(row: Optional[Dict[str, Any]]) -> str:
    """`runtime` for a registration added at run time, else `static`."""
    return SOURCE_RUNTIME if (row or {}).get(REGISTRATION_SOURCE_FIELD) == SOURCE_RUNTIME else SOURCE_STATIC


def registration_state(row: Optional[Dict[str, Any]]) -> str:
    """The state of a registration: `retired` when it left the configuration; for a runtime row its
    stored `status` (`pending`, `active`, `disabled`), and `pending` when that is missing or unknown, so a
    runtime row is never active by default; `active` otherwise."""
    row = row or {}
    if is_retired(row):
        return STATE_RETIRED
    if registration_source(row) == SOURCE_RUNTIME:
        status = row.get(REGISTRATION_STATUS_FIELD)
        return status if status in _RUNTIME_STATES else STATE_PENDING
    return STATE_ACTIVE


def find_registration(bucket_name: str, object_key: str,
                      rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The registration an object of `bucket_name` belongs to: among the active rows of that bucket
    whose prefix holds the key, the one with the longest prefix, and for one location the row that
    answers for it before a row that duplicates it. None when no active row holds the key. The rows are
    those of the bucket (bucketNameGSI); rows of other buckets are ignored. Prefixes are compared in the
    shared normalized form (`assetLocations.normalize_base_prefix`, '' for the bucket root, which holds
    every key)."""
    candidates = []
    for row in rows or []:
        prefix = normalize_base_prefix(row.get("baseAssetsPrefix"))
        if (row.get("bucketName") == bucket_name and registration_state(row) == STATE_ACTIVE
                and str(object_key or "").startswith(prefix)):
            candidates.append((prefix, row))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (
        -len(candidate[0]), 1 if candidate[1].get(REGISTRATION_DUPLICATE_OF_FIELD) else 0))[1]


def bucket_authorization_object(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The Tier 2 authorization object of a registration row."""
    row = row or {}
    return {
        "object__type": BUCKET_OBJECT_TYPE,
        "bucketId": str(row.get("bucketId") or ""),
        "bucketName": str(row.get("bucketName") or ""),
        "bucketRegion": str(row.get("bucketRegion") or ""),
        "source": registration_source(row),
    }


def choosable_registration(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The row of a `bucketId` a database may be put on: the first active one, or None when the id has
    no row or every row is retired (or, for a runtime registration, not active)."""
    return next((row for row in rows or [] if registration_state(row) == STATE_ACTIVE), None)


def primary_bucket_id(row: Optional[Dict[str, Any]]) -> str:
    """The `bucketId` that answers for this row's location: the row it duplicates, else its own."""
    row = row or {}
    return str(row.get(REGISTRATION_DUPLICATE_OF_FIELD) or row.get("bucketId") or "")


def active_registrations(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows that still answer lookups by bucket name and prefix: rows that are not active
    (`registration_state`) left out, and a row that duplicates another placed after the rows that do not
    (stable, so the caller's order holds otherwise). The first row is the one a lookup that needs a
    single answer uses."""
    active = [row for row in rows or [] if registration_state(row) == STATE_ACTIVE]
    return sorted(active, key=lambda row: 1 if row.get(REGISTRATION_DUPLICATE_OF_FIELD) else 0)


def primary_registrations(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per location among the active rows, in the caller's order: the row whose own `bucketId`
    answers for the location, or the first active row of the location when that row is missing."""
    chosen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in active_registrations(rows):
        key = primary_bucket_id(row)
        if key not in chosen:
            chosen[key] = row
            order.append(key)
        elif str(row.get("bucketId") or "") == key:
            chosen[key] = row
    return [chosen[key] for key in order]


def same_registration(first: Optional[Dict[str, Any]], second: Optional[Dict[str, Any]]) -> bool:
    """Whether two registration rows stand for one location: the same `bucketId`, or one duplicates the
    other, or both duplicate the same row."""
    first_id = primary_bucket_id(first)
    return bool(first_id) and first_id == primary_bucket_id(second)
