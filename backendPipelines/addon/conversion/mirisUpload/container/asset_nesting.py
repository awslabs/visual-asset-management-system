#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Which keys under the asset root belong to another asset stored inside it.

A key belongs to the deepest stored location that contains it (the backend's
`common/assetLocations.py`), so the run leaves out every key a nested location holds; the pipeline
looks the nested locations up and passes them on the definition (`nestedLocations`).
`normalize_location`, `_is_preview_of` and `contains` repeat the backend's rule;
`tests/test_miris_upload_location_rule_matches_core.py` holds them to it.
"""

from typing import Optional

# The preview file marker, `{file}.previewFile.{ext}` (the backend's `s3PathPatterns`).
PREVIEW_FILE_PATTERN = ".previewFile."


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
