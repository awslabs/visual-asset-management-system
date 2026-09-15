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
asset's folder, upload VAMS files the Physna tenant does not hold, and prune
Physna assets that no longer correspond to a VAMS file. On delete events we
remove every Physna asset under the VAMS asset folder and clean up empty
folders.

Archive and unarchive are both rewrites of the asset row across the
``#deleted`` partition-key suffix, so each emits stream records that look like
a delete. The Physna copies are removed only when the asset row is absent under
**both** the live and the archived key — a permanent delete. While the row
exists under either key the asset is still in VAMS and its Physna copies are
re-synced instead of deleted; while it is under the archived key the objects are
delete-marked, so no upload is attempted either.

Neither inventory this handler reads is complete. ``list_physna_assets_under``
narrows its scan to the asset folder, so a copy in a nested subfolder can be
missing from it, and the VAMS metadata index holds no row for a file that
carries neither metadata nor attributes. Both are therefore used as one-way
evidence: a listed Physna item is known to exist and an indexed path is known
to be in VAMS, while an absence proves nothing. A per-file delete accordingly
needs the object's S3 version state to show every version purged.
"""

import json
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key

from customLogging.logger import safeLogger

from common.dynamoDbMetadataKeys import is_excluded_metadata_record
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
    is_sync_supported_file,
    list_physna_assets_under,
    merge_metadata,
    physna_format_metadata,
)
from . import physnaFileSync

logger = safeLogger(service_name="PhysnaAssetSync")

# Safety cap on the number of DynamoDB pages one asset-wide index read will
# pull. A query page is capped at 1 MB, so this admits far more metadata and
# attribute rows than any asset carries while keeping the loop bounded.
# Reaching the cap is logged.
_ASSET_INDEX_QUERY_MAX_PAGES = 100

# File count at which the asset-wide metadata prefetch replaces per-file reads.
# A single file costs the same two queries either way; from the second file on
# the prefetch is strictly fewer round trips.
_METADATA_PREFETCH_MIN_FILES = 2

# Upper bound on the files one asset sync will upload to repair drift. Each
# upload is an S3 download plus a Physna multipart upload, so an unbounded
# repair on an asset with thousands of files would exhaust the Lambda timeout.
# The remainder is logged and picked up by the next asset-level sync.
_MAX_REPAIR_UPLOADS_PER_ASSET = 100


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


def _query_asset_index_all_pages(table, composite_key: str) -> list:
    """Return every row for ``composite_key`` from ``DatabaseIdAssetIdIndex``.

    Pages the query to exhaustion, bounded by
    :data:`_ASSET_INDEX_QUERY_MAX_PAGES`. Each call owns its own cursor, so a
    caller reading two tables pages them independently.
    """
    query_kwargs = {
        "IndexName": "DatabaseIdAssetIdIndex",
        "KeyConditionExpression": Key("databaseId:assetId").eq(composite_key),
    }
    items = []
    pages = 0
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        pages += 1
        # Presence, not value: DynamoDB omits the key entirely on the final
        # page, and a reader stubbed with a bare mock answers every ``get`` with
        # a truthy child, which a value test never terminates against.
        if "LastEvaluatedKey" not in response:
            break
        last_evaluated = response["LastEvaluatedKey"]
        if pages >= _ASSET_INDEX_QUERY_MAX_PAGES:
            logger.warning(
                f"Asset index query stopped at the {_ASSET_INDEX_QUERY_MAX_PAGES}-"
                f"page cap with pages remaining for {composite_key}"
            )
            break
        query_kwargs["ExclusiveStartKey"] = last_evaluated
    return items


def _relative_path_from_row(item: Dict[str, Any]) -> Optional[str]:
    """Return the ``filePath`` component of a row's composite sort key."""
    composite = item.get("databaseId:assetId:filePath", "")
    parts = composite.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[2]


def _prefetch_file_metadata(
    database_id: str, asset_id: str
) -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return ``{relativePath: (metadata, attributes)}`` for the whole asset.

    Two paginated ``DatabaseIdAssetIdIndex`` queries — one per table, each with
    its own cursor — return every row ``get_file_metadata`` would fetch two
    queries at a time per file, so the read cost of an asset re-sync is
    independent of the asset's file count. Asset-level rows (``filePath`` ``/``)
    are excluded; they are read separately as asset metadata. Row filters match
    ``get_file_metadata``: system records and empty values are dropped.
    """
    composite_key = f"{database_id}:{asset_id}"
    metadata_by_path: Dict[str, Dict[str, Any]] = {}
    attributes_by_path: Dict[str, Dict[str, Any]] = {}

    metadata_table = physnaCommon.asset_file_metadata_table
    if metadata_table is not None:
        for item in _query_asset_index_all_pages(metadata_table, composite_key):
            relative = _relative_path_from_row(item)
            if not relative or relative == "/":
                continue
            key = item.get("metadataKey")
            value = item.get("metadataValue")
            if not key or not value or is_excluded_metadata_record(key):
                continue
            metadata_by_path.setdefault(relative, {})[key] = {
                "value": value,
                "type": item.get("metadataValueType", "string"),
            }

    attribute_table = physnaCommon.file_attribute_table
    if attribute_table is not None:
        for item in _query_asset_index_all_pages(attribute_table, composite_key):
            relative = _relative_path_from_row(item)
            if not relative or relative == "/":
                continue
            key = item.get("attributeKey")
            value = item.get("attributeValue")
            if not key or not value:
                continue
            attributes_by_path.setdefault(relative, {})[key] = {
                "value": value,
                "type": item.get("attributeValueType", "string"),
            }

    return {
        relative: (
            metadata_by_path.get(relative, {}),
            attributes_by_path.get(relative, {}),
        )
        for relative in set(metadata_by_path) | set(attributes_by_path)
    }


def _vams_file_is_permanently_gone(
    bucket_name: Optional[str], asset_base_key: Optional[str], relative: str
) -> bool:
    """Whether S3 positively holds nothing at all for this asset-relative path.

    Absence from the VAMS metadata index is not evidence that a file was
    deleted — a file carrying neither metadata nor attributes has no row there —
    and absence from the narrowed Physna listing is not evidence either.
    Removing a Physna copy destroys indexed geometry that no re-upload can
    reconstruct from an unreadable object, so it takes the one source that can
    answer positively: the object's S3 version state. An unresolvable bucket or
    an unreadable version state answers "no".
    """
    if not bucket_name or not asset_base_key:
        return False
    return (
        physnaFileSync._vams_file_still_in_s3(
            bucket_name, [asset_base_key + relative.lstrip("/")]
        )
        is False
    )


def _upload_file_completed(
    client: PhysnaClient,
    database_id: str,
    asset_id: str,
    relative: str,
    bucket_name: str,
    s3_key: str,
) -> bool:
    """Upload one VAMS file to Physna and report whether that file's sync completed.

    ``physnaFileSync._upload_file_to_physna`` answers False when the bytes reached Physna
    but the metadata half did not: the copy then carries no ``__VAMS__FileVersion``, the
    absence every later staleness decision reads as stale. On the file-sync queue that
    answer is what reports the SQS record for redrive, and the asset-level paths reach the
    same upload, so they read the same answer — otherwise an asset-level message acks a
    half-completed upload and the only trace of it is a log line.

    A raised exception is the same outcome for this file. It is contained here so the
    asset's remaining files are still attempted, the way the per-record isolation in
    ``physnaFileSync`` contains one bad S3 record without abandoning its batch.

    Compared with ``is not False`` so a caller-supplied stand-in that answers ``None``
    reads as completed rather than as a failure.
    """
    try:
        return physnaFileSync._upload_file_to_physna(
            database_id,
            asset_id,
            relative,
            bucket_name,
            s3_key,
            client=client,
        ) is not False
    except Exception as e:
        logger.warning(
            f"Upload of {database_id}/{asset_id}{relative} to Physna failed: {e}. "
            f"Continuing with the asset's remaining files; the record is reported for "
            f"redrive."
        )
        return False


def _upload_missing_physna_files(
    client: PhysnaClient,
    database_id: str,
    asset_id: str,
    candidate_relative_paths: Set[str],
    bucket_name: Optional[str],
    asset_base_key: Optional[str],
) -> bool:
    """Upload VAMS files the Physna tenant appears not to hold. Returns whether every
    upload it attempted completed.

    The loop over the Physna listing can only patch or prune what Physna
    already holds, so this is the path that closes drift in the direction of
    Physna having less than VAMS. Only extensions in the Physna sync set are
    eligible — ``physnaFileSync`` never uploads the rest, so attempting them
    would fail on every re-sync. The work is capped per asset.

    The candidates come from subtracting a listing that is narrowed rather than
    complete, so a path Physna does hold can appear here. That costs a round
    trip, never data: ``_upload_file_to_physna`` resolves the exact path first
    and settles for a metadata refresh when the copy is already current.
    """
    eligible = sorted(p for p in candidate_relative_paths if is_sync_supported_file(p))
    if not eligible:
        return True
    if not bucket_name or not asset_base_key:
        logger.warning(
            f"{len(eligible)} VAMS file(s) appear absent from Physna for "
            f"{database_id}/{asset_id} but the asset's bucket could not be "
            f"resolved; skipping their upload."
        )
        return True

    attempted = eligible[:_MAX_REPAIR_UPLOADS_PER_ASSET]
    uploads_complete = True
    for relative in attempted:
        logger.info(
            f"Physna appears to have no copy of {database_id}/{asset_id}"
            f"{relative}; uploading it during asset re-sync."
        )
        if not _upload_file_completed(
            client,
            database_id,
            asset_id,
            relative,
            bucket_name,
            asset_base_key + relative.lstrip("/"),
        ):
            uploads_complete = False

    deferred = len(eligible) - len(attempted)
    if deferred > 0:
        # The cap defers work rather than losing it, and a redrive meets the same cap, so
        # the remainder is left to the next asset-level sync instead of being reported as
        # this record's failure.
        logger.warning(
            f"{deferred} file(s) appearing absent from Physna for {database_id}/"
            f"{asset_id} were not uploaded in this invocation (per-asset cap "
            f"{_MAX_REPAIR_UPLOADS_PER_ASSET}); the next asset-level sync "
            f"continues the repair."
        )
    return uploads_complete


def _sync_asset_metadata_to_physna(
    database_id: str, asset_id: str, is_delete: bool
) -> bool:
    """Re-sync (or remove) one VAMS asset's Physna copies. Returns whether every Physna
    write it needed landed.

    Two shapes of shortfall answer False, and the caller turns either into a reported
    batch-item failure so the SQS record is redriven rather than deleted behind an
    outcome that only half happened:

    - A file whose bytes reached Physna without their metadata half: the copy then
      carries no ``__VAMS__FileVersion``, the absence every later staleness decision
      reads as stale.
    - A file whose metadata write did not land on the route that uploads nothing at all.
      The metadata PATCH answering a bad status, the full-replace prune of stale keys
      failing, and a listing item carrying no UUID to address either to are the same
      shortfall: Physna keeps metadata VAMS no longer has, or never receives what it
      does. That route is the one an asset-metadata edit takes, so it is the one most
      invocations follow.

    Metadata-field pre-registration is the one Physna call whose failure is not counted:
    it is a best-effort smoothing layer, and an unregistered field makes the PATCH itself
    answer a bad status, which is counted.

    One file's shortfall does not abandon the asset's remaining files. The answer is
    accumulated and returned once the asset has been walked.
    """
    # The asset row answers three questions: whether the asset is still in VAMS
    # at all, whether it is archived, and what its name and bucket are. It sits
    # under the '#deleted' partition-key suffix for as long as the asset is
    # archived, so both keys are read before concluding it is gone.
    asset_row = get_asset_details(database_id, asset_id)
    is_archived = False
    if not asset_row:
        asset_row = get_asset_details(f"{database_id}#deleted", asset_id)
        is_archived = asset_row is not None

    if is_delete and asset_row:
        # Archive and unarchive are rewrites of the asset row across the
        # '#deleted' suffix, so each emits a stream record that looks like a
        # delete: the REMOVE on the key the row left, and (for an archive) an
        # INSERT into the '#deleted' partition. The asset is still in VAMS, and
        # nothing can rebuild the Physna copies once they are gone — the files
        # are S3 delete-marked while archived, so there are no bytes to upload
        # back. Re-sync instead of deleting. Only an asset present under
        # neither key is a permanent delete.
        logger.info(
            f"Stream delete for {database_id}/{asset_id} is an archive or "
            f"unarchive — the asset row is still present in VAMS. Re-syncing "
            f"rather than deleting the Physna copies."
        )
        is_delete = False

    client = PhysnaClient()
    tenant = physnaCommon.PHYSNA_TENANT_ID
    prefix = f"{database_id}/{asset_id}/"

    # What this listing guarantees: every item it yields exists in the tenant
    # and its path starts with ``prefix``. It does NOT guarantee completeness —
    # the Physna `folders` query parameter narrows the scan to the asset folder
    # itself, so a file Physna holds in a nested subfolder can be absent from
    # the listing. Every use below is therefore evidence in one direction only:
    # a listed item is known to exist, while an unlisted path proves nothing.
    physna_assets = list(list_physna_assets_under(client, tenant, prefix))

    if is_delete:
        delete_complete = True
        for item in physna_assets:
            if not _delete_by_item_completed(
                client, tenant, item, database_id, asset_id, prefix
            ):
                delete_complete = False
        logger.info(
            f"Attempted the delete of {len(physna_assets)} Physna asset(s) under "
            f"{prefix} for a VAMS asset present under neither the live nor the "
            f"archived key. The count is what the narrowed listing returned, so a "
            f"copy in a nested subfolder can remain."
        )
        # After deleting all files, attempt to delete the asset folder. The database
        # folder is not attempted: it is shared with the database's other assets.
        delete_folder_if_empty(client, tenant, prefix.rstrip("/"))
        # No upload is attempted here, but the deletes themselves can fall short: a listing
        # item carrying no addressable UUID, and a DELETE Physna answered with an unusable
        # status, both leave that copy in the tenant. The VAMS asset is gone under both keys,
        # so no later event names it — acking would orphan the copy for good, exactly as on
        # the metadata route, which reports the same condition.
        return delete_complete

    # Not a delete — re-sync metadata, upload what Physna is missing, and
    # prune orphans
    asset_details = asset_row or {}
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

    # S3 prefix every file of this asset hangs off. None when the asset's
    # bucket is unknown, which gates every upload path below.
    bucket_name = bucket_details.get("bucketName") if bucket_details else None
    asset_base_key = None
    if bucket_name:
        asset_base_key = asset_location.get(
            "Key", f"{bucket_details['baseAssetsPrefix']}{asset_id}/"
        )

    # VAMS relative paths carrying a metadata or attribute row. A file with
    # neither has no row, so this set is a lower bound on the asset's contents:
    # membership means the file is in VAMS, absence means nothing. It selects
    # which files get a metadata PATCH and seeds the upload candidates; the
    # delete decision is taken against S3 instead.
    vams_relative_paths = _list_vams_file_paths(database_id, asset_id)

    # Relative paths the (narrowed, possibly partial) Physna listing returned
    # for this asset's folder. Subtracting these yields upload *candidates*,
    # which _upload_missing_physna_files re-checks against the exact path.
    physna_relative_paths = {
        "/" + item.get("path", "")[len(prefix):]
        for item in physna_assets
        if item.get("path", "").startswith(prefix)
    }

    # Asset-wide prefetch of per-file metadata and attributes: two paginated
    # queries in place of two per file. Every DynamoDB read completes before
    # the first Physna mutation, so a read failure aborts the asset rather than
    # leaving the tenant partially rewritten.
    prefetched_file_metadata = None
    if len(vams_relative_paths) >= _METADATA_PREFETCH_MIN_FILES:
        prefetched_file_metadata = _prefetch_file_metadata(database_id, asset_id)

    # Whether every Physna write this asset sync needs lands: both halves of an
    # upload, and the metadata PATCH and stale-key prune on the route that uploads
    # nothing. One file's shortfall does not abandon the rest of the asset, so it is
    # accumulated and returned once the asset has been walked.
    sync_complete = True

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
                # Physna's metadata endpoints are addressed by UUID, so this file's
                # metadata cannot be written at all — the same shortfall as a PATCH
                # that answered a bad status, reached one step earlier.
                sync_complete = False
                logger.warning(
                    f"Physna listing item for {path} has no id field; "
                    f"keys={list(item.keys())}. Its metadata cannot be addressed; "
                    f"reporting the record for redrive."
                )
                continue

            # If Physna's copy of this file is missing __VAMS__FileVersion,
            # treat it as stale and re-upload. This catches files that were
            # uploaded before version-tracking existed on top of routing all
            # future metadata to the upload-then-patch path so the tracking
            # tag gets seeded. An archived asset's objects are delete-marked,
            # so there are no bytes to read and the existing copy is what the
            # unarchive relies on — patch its metadata instead.
            existing_physna_meta = item.get("metadata") or {}
            existing_file_version = existing_physna_meta.get(
                VAMS_RESERVED_FILE_VERSION_KEY
            )
            if not existing_file_version and asset_base_key and not is_archived:
                s3_key = asset_base_key + relative.lstrip("/")
                logger.info(
                    f"Physna asset for {path} is missing "
                    f"__VAMS__FileVersion; treating as stale and re-"
                    f"uploading during asset-metadata re-sync."
                )
                if not _upload_file_completed(
                    client, database_id, asset_id, relative, bucket_name, s3_key
                ):
                    sync_complete = False
                # Whether the re-upload completed or not, skip the PATCH
                # branch for this file — the upload path handles metadata
                # itself and also sets __VAMS__FileVersion.
                continue

            if prefetched_file_metadata is not None:
                file_meta, file_attrs = prefetched_file_metadata.get(
                    relative, ({}, {})
                )
            else:
                file_meta, file_attrs = get_file_metadata(
                    database_id, asset_id, relative
                )
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

            # Physna already holding exactly this payload leaves nothing to
            # register, prune or PATCH for this file.
            if existing_physna_meta == metadata_payload:
                continue

            try:
                ensure_metadata_fields_registered(
                    client, tenant, metadata_payload.keys()
                )
            except Exception as e:
                # Pre-registration carries no data of its own: it is a best-effort
                # smoothing layer, and an unregistered field makes the PATCH below
                # answer a bad status, which is what marks the sync incomplete.
                logger.warning(
                    f"Metadata-field pre-registration encountered an error for "
                    f"{path}: {e}"
                )

            # Full-replace semantics: prune Physna metadata keys that no
            # longer exist in VAMS. The listing item for this asset already
            # includes the current metadata dict, so we can diff without an
            # extra GET.
            to_delete = [
                k for k in existing_physna_meta.keys() if k not in metadata_payload
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
                    # Full-replace semantics did not hold: Physna keeps values VAMS no
                    # longer has. The asset's remaining files are still walked, and the
                    # record is reported so the prune is re-attempted.
                    sync_complete = False
                    logger.warning(
                        f"Failed to prune stale Physna metadata fields "
                        f"{sorted(to_delete)} from {path}: {e}. Reporting the record "
                        f"for redrive."
                    )

            response = client.request(
                "PATCH",
                f"/tenants/{tenant}/assets/{physna_asset_uuid}",
                body=json.dumps({"metadata": metadata_payload}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            if response.status not in (200, 204, 404):
                # One bad row does not abandon the asset's remaining files, but the
                # write did not land — a 404 aside, which means the Physna copy is
                # already gone and there is nothing to write. Mark the sync incomplete
                # so the record is redriven instead of acked behind an outcome it did
                # not achieve.
                sync_complete = False
                logger.warning(
                    f"Metadata PATCH failed for {path} (status "
                    f"{response.status}): {response.data!r}. Continuing with "
                    f"remaining files and reporting the record for redrive."
                )
        elif _vams_file_is_permanently_gone(bucket_name, asset_base_key, relative):
            # The VAMS object is purged, so no later event names this path: a copy the prune
            # could not remove has to report rather than ack, and one such copy does not
            # abandon the asset's remaining files.
            if not _delete_by_item_completed(
                client, tenant, item, database_id, asset_id, prefix
            ):
                sync_complete = False
        else:
            logger.info(
                f"Keeping the Physna copy of {path}: the file has no VAMS "
                f"metadata row, but S3 has not confirmed every version of the "
                f"object is gone. A file with no metadata carries no row, so "
                f"the index alone cannot show it was deleted."
            )

    if is_archived:
        # Every object of an archived asset is delete-marked, so an upload would
        # read nothing. The copies Physna already holds are exactly what the
        # unarchive needs to find.
        logger.info(
            f"Asset {database_id}/{asset_id} is archived; leaving the Physna "
            f"copies as they are rather than attempting uploads with no "
            f"readable bytes."
        )
        return sync_complete

    if not _upload_missing_physna_files(
        client,
        database_id,
        asset_id,
        vams_relative_paths - physna_relative_paths,
        bucket_name,
        asset_base_key,
    ):
        sync_complete = False

    return sync_complete


def _delete_by_item(client: PhysnaClient, tenant: str, item: Dict[str, Any]) -> bool:
    """Delete a Physna asset given its listing-item dict.

    Uses the asset's UUID (the ``id`` field on the listing item), since the
    Physna asset-scoped endpoints are keyed by UUID, not by path.

    Answers whether the copy's fate is settled. False means the listing item carried no
    addressable UUID, so no DELETE could be issued and the copy is still there — the same
    shortfall the metadata route reports when a listing item cannot be addressed, and it has
    to travel the same way rather than being swallowed here. A failed DELETE still raises,
    because that is a Physna error rather than an unusable listing item;
    :func:`_delete_by_item_completed` is what contains that raise to the one copy.
    """
    path = item.get("path", "")
    physna_asset_uuid = item.get("id") or item.get("assetId") or item.get("uuid")
    if not physna_asset_uuid:
        logger.warning(
            f"Physna listing item for {path} has no id field; "
            f"keys={list(item.keys())}. Skipping delete and reporting the record so the "
            f"copy is not left behind an acknowledgement."
        )
        return False
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
    return True


def _delete_by_item_completed(
    client: PhysnaClient,
    tenant: str,
    item: Dict[str, Any],
    database_id: str,
    asset_id: str,
    prefix: str,
) -> bool:
    """Delete one Physna copy and report whether that copy's fate is settled.

    A Physna error raised by the DELETE is that one copy's outcome, not the asset's. It is
    contained here so the asset's remaining copies are still attempted, the way
    ``_upload_file_completed`` contains one bad upload: otherwise the first status Physna
    will not retry — a rate limit part-way through a large asset — leaves every copy after
    it untouched, and a redrive that fails on the same copy never reaches them either.
    Containing it also keeps what follows the loop reachable: on the prune route that is
    the remaining files' metadata re-sync and the repair upload after them.

    The copy that survived is named in its own sync-tracking record, carrying the UUID
    Physna's asset-scoped endpoints are keyed by, since a path is not what a leftover copy
    is removed by. The asset-level record the caller writes says only that something fell
    short, and on the delete route the VAMS asset is gone under both the live and the
    archived key, so nothing else ever names the file again.
    """
    path = item.get("path", "")
    relative = "/" + path[len(prefix):].lstrip("/") if path.startswith(prefix) else path
    try:
        if _delete_by_item(client, tenant, item):
            return True
        error_message = (
            "Physna listing item carried no addressable UUID, so no delete was issued"
        )
    except Exception as e:
        logger.warning(
            f"Delete of the Physna copy of {path or relative} failed: {e}. Continuing "
            f"with the asset's remaining copies; the record is reported for redrive."
        )
        error_message = str(e)
    physnaFileSync._record_file_sync(
        database_id,
        asset_id,
        relative,
        SYNC_ACTION_DELETE,
        SYNC_STATUS_FAILED,
        physna_asset_uuid=item.get("id") or item.get("assetId") or item.get("uuid"),
        error_message=error_message,
    )
    return False


def _list_vams_file_paths(database_id: str, asset_id: str) -> Set[str]:
    """Return the set of VAMS file relative paths for this asset.

    Reads the asset's rows from the ``DatabaseIdAssetIdIndex`` GSI through
    :func:`_query_asset_index_all_pages`, so the cursor threading and the
    ``_ASSET_INDEX_QUERY_MAX_PAGES`` bound are the ones the asset-wide reads
    already use, and keeps the ``filePath`` component of each row's composite
    key. The asset-level record (path '/') is filtered out.

    The bound is safe here because callers consume the set as a lower bound: it
    selects which files get a metadata PATCH and seeds the upload candidates,
    while the delete decision is taken against the S3 version state. Stopping
    at the cap therefore leaves work for the next asset-level sync instead of
    presenting a file as absent from VAMS.
    """
    paths: Set[str] = set()
    composite_key = f"{database_id}:{asset_id}"
    for item in _query_asset_index_all_pages(
        physnaCommon.asset_file_metadata_table, composite_key
    ):
        relative = _relative_path_from_row(item)
        if not relative or relative == "/":
            continue
        paths.add(relative)
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

    The returned ``is_delete`` reflects the stream record alone, and a ``REMOVE``
    cannot be told apart from the archive/unarchive half of a rewrite across the
    ``#deleted`` partition-key suffix — both remove the row from the key it left.
    ``_sync_asset_metadata_to_physna`` resolves that against the asset row before
    deleting anything.
    """
    event_name = sns_message.get("eventName", "")
    dynamo = sns_message.get("dynamodb", {}) or {}
    keys = dynamo.get("Keys", {}) or {}
    new_image = dynamo.get("NewImage", {}) or {}

    # Shape 1: assetStorageTable
    database_id = keys.get("databaseId", {}).get("S")
    asset_id = keys.get("assetId", {}).get("S")
    if database_id and asset_id:
        # A write into the '#deleted' partition is the archive half of a
        # rewrite: the row is present, so it is a re-sync, never a delete.
        is_delete = event_name == "REMOVE"
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
    never causes the remaining records in the batch to be silently dropped,
    and the records that failed are reported back as ``batchItemFailures`` so
    the event source mapping redrives only those. Counting a failure without
    reporting it acks the message, and SQS then deletes it: the asset is never
    re-synced and nothing but a log line records the loss.

    A raised error is not the only way an asset sync falls short, and an upload is not
    the only write that can be left half done. A file whose bytes reached Physna without
    their metadata half leaves a copy carrying no ``__VAMS__FileVersion``, the state the
    next sync reads as stale. The route an asset-metadata edit takes uploads nothing at
    all, and on it a metadata PATCH answering a bad status, a failed full-replace prune,
    and a listing item with no addressable UUID are shortfalls of the same kind. The sync
    answers False for any of them, and the record is then reported and recorded exactly
    as a raised error is, so the outcome does not turn on whether the invocation uploaded
    anything.

    ``physnaFileSync`` answers its own records the same way, so it does not turn on which
    queue the message arrived on either: on that side a metadata PATCH that failed on the
    metadata-only route, a stale-key prune that failed, an upload whose Physna asset UUID
    was never obtainable, a stale copy Physna would not release, a reconcile-delete of a
    permanently deleted file, a delete whose Physna UUID lookup raised (distinct from a
    lookup that answered "nothing there", which is settled and acks), and a 409 whose
    pre-upload lookup raised — leaving the bytes Physna holds at that path of an
    undetermined version — each report the record rather than ack it. What both sides
    answer for is a Physna write the sync needed. Metadata-field pre-registration sits
    outside that by design — it carries no data of its own and its consequence surfaces as
    the PATCH status, which is counted — and so does a record with no Physna write to make:
    an unsupported extension, or an object whose version state does not show every version
    purged. A record whose S3 key cannot be resolved to a VAMS asset at all — an
    unregistered bucket, an assetId no database claims, a HeadObject that could not be
    read — is acked too, on the ground that the event may name nothing of VAMS's; the
    repair for a file whose upload was never attempted is the asset-level sync, which
    uploads what Physna does not hold on the asset's next event.

    A redrive re-attempts whatever already succeeded, which is safe because the route is
    idempotent: the target metadata is re-derived from VAMS state, a file Physna already
    holds that payload for is skipped without a write, the prune is re-diffed against the
    current listing, and an upload whose bytes already landed settles for a metadata
    refresh. It is also bounded rather than unbounded — each queue redelivers a message
    three times (``maxReceiveCount``) and then moves it to that queue's own dead-letter
    queue, so a shortfall that keeps recurring stops there instead of looping.
    """
    total = len(event.get("Records", []))
    logger.info(f"Physna asset sync invocation starting; {total} record(s) in batch")
    successful = 0
    failed = 0
    batch_item_failures: List[Dict[str, str]] = []
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

            sync_complete = _sync_asset_metadata_to_physna(
                database_id, asset_id, is_delete=is_delete)
            action = SYNC_ACTION_DELETE if is_delete else SYNC_ACTION_MODIFY
            # Identity, not truthiness: a stand-in for the sync that answers ``None``
            # reads as complete, so only an explicit False reports a shortfall.
            if sync_complete is False:
                failed += 1
                physnaFileSync._add_batch_item_failure(batch_item_failures, record)
                _record_asset_sync(
                    database_id, asset_id, action, SYNC_STATUS_FAILED,
                    error_message=(
                        "At least one Physna write this asset sync needed did not land "
                        "(an upload failed, its bytes landed without their metadata, a "
                        "metadata write on the no-upload route failed, or a Physna copy "
                        "could not be deleted); the record is reported for redrive"))
                logger.warning(
                    f"Asset sync for {database_id}/{asset_id} did not land at least one "
                    f"Physna write; reporting the record for redrive.")
                continue
            _record_asset_sync(database_id, asset_id, action, SYNC_STATUS_SUCCESS)
            successful += 1
        except Exception as e:
            failed += 1
            physnaFileSync._add_batch_item_failure(batch_item_failures, record)
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
        f"{len(batch_item_failures)} reported for redrive, out of {total} record(s)"
    )
    return physnaFileSync._with_batch_item_failures(
        {"statusCode": 200, "body": {"successful": successful, "failed": failed}},
        event,
        batch_item_failures,
    )
