# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Loads the asset and reads the Miris stream recorded on one of its files, shared by the Miris
API handlers.

The stream is described by the `miris_*` keys in the asset file metadata table, keyed by exactly
one databaseId, assetId and asset-relative filePath. A request never supplies the Miris asset UUID
itself, so a caller can only reach a stream that the file references.
"""
from typing import Optional, Tuple

from common.validators import validate
from models.common import authorization_error, general_error
from models.addon.miris import (
    MIRIS_ASSET_UUID_KEY,
    MIRIS_STREAM_METADATA_KEYS,
    STATE_INVALID_UUID,
    STATE_NOT_STREAMED,
    asset_relative_file_path,
)


def load_readable_asset(asset_table, casbin_enforcer, database_id: str, asset_id: str,
                        event) -> Tuple[Optional[dict], Optional[dict]]:
    """Tier 2: load the asset and check that the caller may read it (GET).

    Returns `(asset, None)` when the read is allowed, or `(None, response)` with a 400 for an
    asset that does not exist or a 403 for a denied read.
    """
    asset = asset_table.get_item(Key={"databaseId": database_id, "assetId": asset_id}).get("Item")
    if not asset:
        return None, general_error(body={"message": "Asset not found"}, event=event)

    # Casbin policies on asset objects key off object__type='asset'; the
    # DynamoDB record doesn't carry that field, so annotate before enforce().
    asset["object__type"] = "asset"
    if not casbin_enforcer.enforce(asset, "GET"):
        return None, authorization_error()
    return asset, None


def file_path_for_asset(asset: dict, asset_id: str, request_file_path: str) -> str:
    """Normalize a validated `filePath` against the loaded asset's own root key."""
    asset_location = asset.get("assetLocation")
    asset_root_key = asset_location.get("Key") if isinstance(asset_location, dict) else None
    return asset_relative_file_path(request_file_path, asset_id, asset_root_key)


def read_stream_metadata(metadata_table, database_id: str, asset_id: str, file_path: str) -> dict:
    """Read the Miris stream keys stored on one file, as {metadataKey: string value}.

    Each key is a single-item read on (metadataKey, databaseId:assetId:filePath), so only
    rows belonging to this exact file can be returned. Absent keys are omitted.
    """
    sort_key = f"{database_id}:{asset_id}:{file_path}"
    values = {}
    for metadata_key in MIRIS_STREAM_METADATA_KEYS:
        item = metadata_table.get_item(
            Key={"metadataKey": metadata_key, "databaseId:assetId:filePath": sort_key}
        ).get("Item")
        if not item or item.get("metadataValue") is None:
            continue
        value = item["metadataValue"]
        values[metadata_key] = value if isinstance(value, str) else str(value)
    return values


def stream_uuid(stream_metadata: dict) -> str:
    """The stored `miris_asset_uuid`, trimmed, or "" when it is absent."""
    return (stream_metadata.get(MIRIS_ASSET_UUID_KEY) or "").strip()


def unusable_uuid_state(miris_asset_uuid: str) -> Optional[str]:
    """`notStreamed` for an empty UUID, `invalidUuid` for a value that is not a UUID, else None."""
    if not miris_asset_uuid:
        return STATE_NOT_STREAMED
    (valid, _message) = validate(
        {"mirisAssetUuid": {"value": miris_asset_uuid, "validator": "UUID"}}
    )
    return None if valid else STATE_INVALID_UUID
