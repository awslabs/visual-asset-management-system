# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The state of a row in the S3 asset buckets table (a bucket registration).

Inside VAMS a bucket registration is identified by its `bucketId`. The deploy-time custom resource
`populateS3AssetBucketsTable` is the only writer, and it never deletes a row, so an asset or a database
that names a `bucketId` always resolves it. Two attributes say what a row still stands for:

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

The helpers are pure: callers read the rows (bucketNameGSI, a scan, a get by `bucketId`) and pass them in.
"""

from typing import Any, Dict, Iterable, List, Optional

REGISTRATION_RETIRED_FIELD = "retired"
REGISTRATION_DUPLICATE_OF_FIELD = "duplicateOfBucketId"


def is_retired(row: Optional[Dict[str, Any]]) -> bool:
    """Whether the registration left the configuration."""
    return (row or {}).get(REGISTRATION_RETIRED_FIELD) is True


def primary_bucket_id(row: Optional[Dict[str, Any]]) -> str:
    """The `bucketId` that answers for this row's location: the row it duplicates, else its own."""
    row = row or {}
    return str(row.get(REGISTRATION_DUPLICATE_OF_FIELD) or row.get("bucketId") or "")


def active_registrations(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows that still answer lookups by bucket name and prefix: retired rows left out, and a row
    that duplicates another placed after the rows that do not (stable, so the caller's order holds
    otherwise). The first row is the one a lookup that needs a single answer uses."""
    active = [row for row in rows or [] if not is_retired(row)]
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
