#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The locations of other assets stored inside an asset's location, whose files are not the asset's.

A key belongs to the deepest stored location that contains it (the backend's
`common/assetLocations.py`), so the upload leaves out every key a nested location holds: the gate's
fingerprint and the container's listing, download and root choice. `normalize_location`,
`_is_preview_of` and `contains` repeat the backend's rule, because a pipeline function cannot
import `backend/`; `tests/test_miris_upload_location_rule_matches_core.py` holds them to it.
"""

import json
from typing import Optional

# The asset table's location index: partition `locationBucket`, sort `locationKey`.
LOCATION_INDEX_NAME = "AssetLocationGSI"
# The preview file marker, `{file}.previewFile.{ext}` (the backend's `s3PathPatterns`).
PREVIEW_FILE_PATTERN = ".previewFile."
# The nested locations travel to the container on the state machine input, which Step Functions
# limits to 256 KB; past this size the run fails instead.
MAX_NESTED_LOCATIONS_BYTES = 64 * 1024


def normalize_location(key: Optional[str]) -> str:
    """The location key in its stored S3 form: no leading ``/``; empty for no location."""
    return (key or "").lstrip("/")


def _is_preview_of(base_key: str, object_key: str) -> bool:
    """Whether ``object_key`` is ``{base_key}.previewFile.{ext}``, a preview directly beside the base key."""
    stem = base_key + PREVIEW_FILE_PATTERN
    if not object_key.startswith(stem):
        return False
    extension = object_key[len(stem):]
    return bool(extension) and "/" not in extension


def contains(location: Optional[str], object_key: Optional[str]) -> bool:
    """Whether an S3 object key belongs to an asset location.

    A folder location contains every key that starts with it, the folder marker
    itself included. A single-file location contains its own key, every key under
    ``{file key}/`` and its own previews. An empty location contains nothing.
    """
    loc = normalize_location(location)
    if not loc or not object_key:
        return False
    if loc.endswith("/"):
        return object_key.startswith(loc)
    return object_key == loc or object_key.startswith(loc + "/") or _is_preview_of(loc, object_key)


def held_by_nested(nested_locations, object_key) -> bool:
    """Whether a location nested in the asset's own holds `object_key`."""
    return any(contains(location, object_key) for location in nested_locations or ())


def nested_locations(asset_table, bucket, root_prefix, database_id, asset_id):
    """The sorted locations of the other assets, live or archived, stored inside `root_prefix` (a
    folder location ending in '/') in `bucket`, read from the location index.

    Every location longer than the root that starts with it is nested in it. An asset stored at
    the root itself shares the location and is not nested. Until the index backfill completes, an
    asset the index does not list yet is not found, as with the backend's reads before then.
    """
    root = normalize_location(root_prefix)
    if not bucket or not root:
        return []
    found = set()
    request = {
        "IndexName": LOCATION_INDEX_NAME,
        "KeyConditionExpression": "locationBucket = :bucket AND begins_with(locationKey, :root)",
        "ExpressionAttributeValues": {":bucket": bucket, ":root": root},
    }
    while True:
        page = asset_table.query(**request)
        for row in page.get("Items", []):
            location = normalize_location(row.get("locationKey"))
            own = row.get("databaseId") == database_id and row.get("assetId") == asset_id
            if not own and len(location) > len(root) and location.startswith(root):
                found.add(location)
        if not page.get("LastEvaluatedKey"):
            return sorted(found)
        request["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def check_size(locations) -> None:
    """Raise when the nested locations are too large to hand to the container."""
    size = len(json.dumps(locations).encode("utf-8"))
    if size > MAX_NESTED_LOCATIONS_BYTES:
        raise Exception(
            f"The asset holds {len(locations)} nested assets, whose locations ({size} bytes) are more "
            f"than the Miris upload can leave out ({MAX_NESTED_LOCATIONS_BYTES} bytes), so its files "
            "are not sent to Miris.")
