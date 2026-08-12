# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Physna Sync — asset-level event handler.

Consumes messages from the SQS queue subscribed to ``assetIndexerSnsTopic``.
The topic receives stream events from:

- ``assetStorageTable`` — asset record create / update / archive / delete.
- ``assetFileMetadataStorageTable`` — any metadata row. Rows whose composite
  sort key ends in ``:/`` represent asset-level metadata and are the ones this
  handler cares about (file-level rows are a no-op here; they are handled by
  ``physnaFileSync``).

On non-delete events, we re-sync metadata on every Physna asset under the VAMS
asset's folder and prune Physna assets that no longer correspond to a VAMS
file. On delete events we remove every Physna asset under the VAMS asset
folder and clean up empty folders.
"""

import json
import urllib.parse
from typing import Any, Dict, Optional, Set, Tuple

from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key

from customLogging.logger import safeLogger

from common.syncTracking import (
    SYNC_ACTION_DELETE,
    SYNC_ACTION_MODIFY,
    SYNC_OBJECT_TYPE_ASSET,
    SYNC_STATUS_FAILED,
    SYNC_STATUS_SUCCESS,
    write_outbound_sync_record,
)
from . import physnaCommon
from .physnaCommon import (
    PhysnaClient,
    PhysnaError,
    VAMS_RESERVED_FILE_VERSION_KEY,
    apply_vams_reserved_metadata,
    delete_folder_if_empty,
    delete_physna_metadata_fields,
    ensure_metadata_fields_registered,
    get_asset_details,
    get_asset_metadata,
    get_bucket_details,
    get_file_metadata,
    list_physna_assets_under,
    merge_metadata,
    physna_format_metadata,
)
from . import physnaFileSync

logger = safeLogger(service_name="PhysnaAssetSync")


def _record_asset_sync(database_id, asset_id, action, sync_status, error_message=None):
    """Best-effort outbound sync tracking record for an asset-level Physna sync."""
    write_outbound_sync_record(
        SYNC_OBJECT_TYPE_ASSET,
        database_id,
        physnaCommon.SYNC_SYSTEM_TYPE,
        physnaCommon.get_sync_system_unique_id(),
        action,
        sync_status,
        asset_id=asset_id,
        error_message=error_message,
    )


def _sync_asset_metadata_to_physna(
    database_id: str, asset_id: str, is_delete: bool
) -> None:
    client = PhysnaClient()
    tenant = physnaCommon.PHYSNA_TENANT_ID
    prefix = f"{database_id}/{asset_id}/"

    physna_assets = list(list_physna_assets_under(client, tenant, prefix))

    if is_delete:
        for item in physna_assets:
            _delete_by_item(client, tenant, item)
        # After deleting all files, attempt to delete asset and database folders
        delete_folder_if_empty(client, tenant, prefix.rstrip("/"))
        delete_folder_if_empty(client, tenant, database_id)
        return

    # Not a delete — re-sync metadata and prune orphans
    asset_details = get_asset_details(database_id, asset_id) or {}
    asset_meta = get_asset_metadata(database_id, asset_id)
    current_asset_name = asset_details.get("assetName")

    # Resolve bucket info once so we can reconstruct S3 keys when a Physna
    # asset turns out to be missing its __VAMS__FileVersion tag and needs a
    # re-upload instead of a plain metadata PATCH.
    bucket_details = None
    bucket_id = asset_details.get("bucketId")
    if bucket_id:
        try:
            bucket_details = get_bucket_details(bucket_id)
        except Exception as e:
            logger.warning(
                f"Could not load bucket details for {database_id}/{asset_id}: "
                f"{e}. Will skip re-upload fallback and only do metadata PATCH."
            )
    asset_location = asset_details.get("assetLocation", {}) or {}

    # Build the set of VAMS relative paths for this asset using the file metadata index
    vams_relative_paths = _list_vams_file_paths(database_id, asset_id)

    for item in physna_assets:
        path = item.get("path", "")
        if not path.startswith(prefix):
            continue
        relative = "/" + path[len(prefix) :]
        if relative in vams_relative_paths:
            # Physna metadata endpoints are scoped by the asset UUID, not the
            # path. The `id` field on each listing item IS that UUID.
            physna_asset_uuid = (
                item.get("id") or item.get("assetId") or item.get("uuid")
            )
            if not physna_asset_uuid:
                logger.warning(
                    f"Physna listing item for {path} has no id field; "
                    f"keys={list(item.keys())}. Skipping metadata update."
                )
                continue

            # If Physna's copy of this file is missing __VAMS__FileVersion,
            # treat it as stale and re-upload. This catches files that were
            # uploaded before version-tracking existed on top of routing all
            # future metadata to the upload-then-patch path so the tracking
            # tag gets seeded.
            existing_physna_meta = item.get("metadata") or {}
            existing_file_version = existing_physna_meta.get(
                VAMS_RESERVED_FILE_VERSION_KEY
            )
            if (
                not existing_file_version
                and bucket_details
                and bucket_details.get("bucketName")
            ):
                asset_base_key = asset_location.get(
                    "Key",
                    f"{bucket_details['baseAssetsPrefix']}{asset_id}/",
                )
                s3_key = asset_base_key + relative.lstrip("/")
                logger.info(
                    f"Physna asset for {path} is missing "
                    f"__VAMS__FileVersion; treating as stale and re-"
                    f"uploading during asset-metadata re-sync."
                )
                try:
                    physnaFileSync._upload_file_to_physna(
                        database_id,
                        asset_id,
                        relative,
                        bucket_details["bucketName"],
                        s3_key,
                        client=client,
                    )
                except Exception as e:
                    logger.warning(
                        f"Re-upload fallback failed for {path}: {e}. "
                        f"Continuing with remaining files."
                    )
                # Whether the re-upload succeeded or not, skip the PATCH
                # branch for this file — the upload path handles metadata
                # itself and also sets __VAMS__FileVersion.
                continue

            file_meta, file_attrs = get_file_metadata(database_id, asset_id, relative)
            merged = merge_metadata(asset_meta, file_meta, file_attrs)
            metadata_payload = physna_format_metadata(merged)

            # Overlay the VAMS-reserved asset-name tracking key. This is an
            # asset-metadata-change sync path — file bytes did not change, so
            # we preserve the existing __VAMS__FileVersion rather than
            # rewriting it.
            apply_vams_reserved_metadata(
                metadata_payload,
                current_asset_name,
                existing_file_version,
            )

            try:
                ensure_metadata_fields_registered(
                    client, tenant, metadata_payload.keys()
                )
            except Exception as e:
                logger.warning(
                    f"Metadata-field pre-registration encountered an error for "
                    f"{path}: {e}"
                )

            # Full-replace semantics: prune Physna metadata keys that no
            # longer exist in VAMS. The listing item for this asset already
            # includes the current metadata dict, so we can diff without an
            # extra GET.
            current_metadata = item.get("metadata") or {}
            to_delete = [
                k for k in current_metadata.keys() if k not in metadata_payload
            ]
            if to_delete:
                try:
                    delete_physna_metadata_fields(
                        client, tenant, physna_asset_uuid, to_delete
                    )
                    logger.info(
                        f"Pruned {len(to_delete)} stale Physna metadata "
                        f"field(s) from {path}: {sorted(to_delete)}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to prune stale Physna metadata fields "
                        f"{sorted(to_delete)} from {path}: {e}"
                    )

            response = client.request(
                "PATCH",
                f"/tenants/{tenant}/assets/{physna_asset_uuid}",
                body=json.dumps({"metadata": metadata_payload}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            if response.status not in (200, 204, 404):
                # Don't fail the whole asset re-sync on a single bad row —
                # log and continue.
                logger.warning(
                    f"Metadata PATCH failed for {path} (status "
                    f"{response.status}): {response.data!r}. Continuing with "
                    f"remaining files."
                )
        else:
            _delete_by_item(client, tenant, item)


def _delete_by_item(client: PhysnaClient, tenant: str, item: Dict[str, Any]) -> None:
    """Delete a Physna asset given its listing-item dict.

    Uses the asset's UUID (the ``id`` field on the listing item), since the
    Physna asset-scoped endpoints are keyed by UUID, not by path.
    """
    path = item.get("path", "")
    physna_asset_uuid = item.get("id") or item.get("assetId") or item.get("uuid")
    if not physna_asset_uuid:
        logger.warning(
            f"Physna listing item for {path} has no id field; "
            f"keys={list(item.keys())}. Skipping delete."
        )
        return
    response = client.request(
        "DELETE",
        f"/tenants/{tenant}/assets/{physna_asset_uuid}",
    )
    if response.status not in (200, 204, 404):
        raise PhysnaError(
            f"Delete failed for {path} ({physna_asset_uuid}) (status "
            f"{response.status}): {response.data!r}"
        )
    if "/" in path:
        folder = path.rsplit("/", 1)[0]
        delete_folder_if_empty(client, tenant, folder)


def _list_vams_file_paths(database_id: str, asset_id: str) -> Set[str]:
    """Return the set of VAMS file relative paths for this asset.

    Queries the ``DatabaseIdAssetIdIndex`` GSI for all rows belonging to the
    asset and extracts the ``filePath`` component of the composite key.
    Filters out the asset-level record (path '/'). Paginates fully.
    """
    paths: Set[str] = set()
    composite_key = f"{database_id}:{asset_id}"
    query_kwargs = {
        "IndexName": "DatabaseIdAssetIdIndex",
        "KeyConditionExpression": Key("databaseId:assetId").eq(composite_key),
    }
    while True:
        response = physnaCommon.asset_file_metadata_table.query(**query_kwargs)
        for item in response.get("Items", []):
            composite = item.get("databaseId:assetId:filePath", "")
            parts = composite.split(":", 2)
            if len(parts) != 3:
                continue
            relative = parts[2]
            if relative == "/":
                continue
            paths.add(relative)
        last_evaluated = response.get("LastEvaluatedKey")
        if not last_evaluated:
            break
        query_kwargs["ExclusiveStartKey"] = last_evaluated
    return paths


def _extract_ids_from_stream_record(
    sns_message: Dict[str, Any],
) -> Optional[Tuple[str, str, bool]]:
    """Pull (database_id, asset_id, is_delete) from an SNS-wrapped stream record.

    Returns ``None`` when the record does not represent an asset-level change
    this handler should act on.

    Two shapes are accepted:

    1. ``assetStorageTable`` streams — top-level keys ``databaseId`` + ``assetId``.
    2. ``assetFileMetadataStorageTable`` streams — composite sort key
       ``databaseId:assetId:filePath``; only rows whose ``filePath`` is ``/``
       (asset-level metadata) are relevant here.
    """
    event_name = sns_message.get("eventName", "")
    dynamo = sns_message.get("dynamodb", {}) or {}
    keys = dynamo.get("Keys", {}) or {}
    new_image = dynamo.get("NewImage", {}) or {}

    # Shape 1: assetStorageTable
    database_id = keys.get("databaseId", {}).get("S")
    asset_id = keys.get("assetId", {}).get("S")
    if database_id and asset_id:
        is_delete = event_name == "REMOVE" or database_id.endswith("#deleted")
        normalized_db = database_id.replace("#deleted", "")
        return normalized_db, asset_id, is_delete

    # Shape 2: assetFileMetadataStorageTable (composite SK)
    if event_name == "REMOVE":
        composite = keys.get("databaseId:assetId:filePath", {}).get("S")
    else:
        composite = new_image.get("databaseId:assetId:filePath", {}).get("S")
    if not composite:
        return None
    parts = composite.split(":", 2)
    if len(parts) != 3:
        return None
    comp_db, comp_asset, file_path = parts
    if file_path != "/":
        # File-level metadata is handled by physnaFileSync, not here
        return None
    # An asset-level metadata REMOVE is a metadata clear, not an asset delete —
    # treat as a re-sync (not is_delete). Asset deletes come through shape 1.
    return comp_db, comp_asset, False


def lambda_handler(event, context: LambdaContext) -> Dict[str, Any]:
    """Process every record in the SQS batch independently.

    Each record is wrapped in its own try/except so a failure on one asset
    never causes the remaining records in the batch to be silently dropped.
    """
    total = len(event.get("Records", []))
    logger.info(f"Physna asset sync invocation starting; {total} record(s) in batch")
    successful = 0
    failed = 0
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:sqs":
            continue
        database_id: Optional[str] = None
        asset_id: Optional[str] = None
        is_delete = False
        try:
            body = record.get("body", "")
            if isinstance(body, str):
                body = json.loads(body)
            if body.get("Type") != "Notification":
                continue
            sns_message = body.get("Message")
            if isinstance(sns_message, str):
                sns_message = json.loads(sns_message)

            ids = _extract_ids_from_stream_record(sns_message)
            if ids is None:
                continue
            database_id, asset_id, is_delete = ids

            _sync_asset_metadata_to_physna(database_id, asset_id, is_delete=is_delete)
            _record_asset_sync(database_id, asset_id,
                               SYNC_ACTION_DELETE if is_delete else SYNC_ACTION_MODIFY,
                               SYNC_STATUS_SUCCESS)
            successful += 1
        except Exception as e:
            failed += 1
            if database_id and asset_id:
                _record_asset_sync(database_id, asset_id,
                                   SYNC_ACTION_DELETE if is_delete else SYNC_ACTION_MODIFY,
                                   SYNC_STATUS_FAILED, error_message=str(e))
            logger.exception(
                f"Error processing asset sync record "
                f"(databaseId={database_id}, assetId={asset_id}): {e}. "
                f"Continuing with remaining records in batch."
            )
    logger.info(
        f"Physna asset sync done: {successful} succeeded, {failed} failed, "
        f"out of {total} record(s)"
    )
    return {"statusCode": 200, "body": {"successful": successful, "failed": failed}}
