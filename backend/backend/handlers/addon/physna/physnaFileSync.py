# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Physna Sync — file-level event handler.

Consumes messages from the SQS queue subscribed to ``fileIndexerSnsTopic``.
Handles three event shapes:

1. S3 ObjectCreated / PUT / Copy events — uploads the file to Physna.
2. S3 ObjectRemoved events (including archive delete-markers) — deletes the
   file from Physna.
3. DynamoDB streams on asset file metadata / attribute tables — updates (or
   uploads) the file's metadata in Physna.
"""

import json
import os
import tempfile
import urllib.parse
from typing import Any, Dict, Optional

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.exceptions import ClientError

from customLogging.logger import safeLogger
from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
)
from common.s3PathPatterns import RESERVED_S3_PREFIX_FOLDERS, EXCLUDED_FILE_PATH_PATTERNS
from common.syncTracking import (
    SYNC_ACTION_CREATE,
    SYNC_ACTION_DELETE,
    SYNC_ACTION_MODIFY,
    SYNC_OBJECT_TYPE_ASSET_FILE,
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
    build_physna_folder_path,
    build_physna_path,
    delete_folder_if_empty,
    delete_physna_metadata_fields,
    ensure_metadata_fields_registered,
    get_asset_details,
    get_asset_metadata,
    get_bucket_details,
    get_bucket_details_by_name,
    get_database_id_for_asset_id,
    get_file_metadata,
    get_physna_asset,
    is_sync_supported_file,
    lookup_physna_asset_id,
    merge_metadata,
    physna_format_metadata,
)

logger = safeLogger(service_name="PhysnaFileSync")

# Reuse the same skip set as Garnet
_EXCLUDED_PREFIXES = RESERVED_S3_PREFIX_FOLDERS
_EXCLUDED_PATTERNS = EXCLUDED_FILE_PATH_PATTERNS

_s3 = boto3.client("s3", config=physnaCommon._retry_config)


def _should_skip_s3_key(s3_key: str) -> bool:
    if s3_key.endswith("/"):
        return True
    if any(p in s3_key for p in _EXCLUDED_PATTERNS):
        return True
    for part in s3_key.split("/"):
        if part in _EXCLUDED_PREFIXES:
            return True
    return False


def _head_object_with_encoding_fallback(
    bucket_name: str, s3_key: str
) -> Optional[Dict[str, Any]]:
    """head_object that tolerates both decoded and literal S3 event keys.

    S3 event notifications deliver keys URL-encoded (form encoding: spaces
    → '+', specials → %XX), but some event sources deliver literal text.
    Filenames that legitimately contain '+' (e.g., "BACC66K41F158AM+---.CATPart")
    round-trip differently depending on which shape arrives. If a first
    head_object returns 404, we retry with the alternative encoding before
    giving up. Returns the HeadObject response along with the effective
    key that worked, or None if neither shape exists.
    """
    try:
        response = _s3.head_object(Bucket=bucket_name, Key=s3_key)
        return {"response": response, "key": s3_key}
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey"):
            logger.warning(f"head_object failed for {bucket_name}/{s3_key}: {e}")
            return None
        alt_key = urllib.parse.unquote_plus(s3_key)
        if alt_key == s3_key:
            alt_key = urllib.parse.quote(s3_key, safe="/+")
        if alt_key == s3_key:
            logger.warning(
                f"head_object 404 for {bucket_name}/{s3_key}, no alternative "
                f"encoding to try"
            )
            return None
        try:
            response = _s3.head_object(Bucket=bucket_name, Key=alt_key)
            logger.info(
                f"head_object succeeded with alternative encoding: {alt_key!r} "
                f"(originally {s3_key!r})"
            )
            return {"response": response, "key": alt_key}
        except ClientError as alt_e:
            logger.warning(
                f"head_object failed with both encodings for {bucket_name} "
                f"(tried {s3_key!r} and {alt_key!r}): {alt_e}"
            )
            return None


def _resolve_asset_from_s3_key_without_metadata(
    bucket_name: str, s3_key: str
) -> Optional[Dict[str, Any]]:
    """Derive databaseId / assetId / relative path from an S3 key alone.

    Used for ``ObjectRemoved`` events (including archive delete markers):
    by the time we handle them, ``head_object`` can no longer retrieve the
    VAMS-injected user-metadata (``assetid``, ``databaseid``). Instead we
    reverse-look up the bucket registry (``bucketNameGSI``) to get
    ``baseAssetsPrefix``, strip that prefix, treat the first path segment
    as the assetId, and reverse-look up the assetId (``assetIdGSI``) with
    bucket disambiguation to obtain the databaseId. Disambiguation mirrors
    ``fileIndexer.lookup_database_id_for_permanent_delete`` so both sync
    paths resolve identically on the same event.

    Returns ``None`` when any step fails — callers must not fabricate
    identifiers from partial data.
    """
    bucket_details = get_bucket_details_by_name(bucket_name)
    if not bucket_details:
        logger.warning(
            f"Cannot resolve S3 key without metadata: bucket not registered "
            f"in s3_asset_buckets: {bucket_name}"
        )
        return None

    base_prefix = bucket_details.get("baseAssetsPrefix") or ""
    key_after_prefix = (
        s3_key[len(base_prefix):] if base_prefix and s3_key.startswith(base_prefix)
        else s3_key
    )
    key_after_prefix = key_after_prefix.lstrip("/")
    if "/" not in key_after_prefix:
        logger.warning(
            f"S3 key does not contain an asset-path separator after stripping "
            f"base prefix (expected `{{assetId}}/...`): "
            f"bucket={bucket_name}, key={s3_key}, stripped={key_after_prefix}"
        )
        return None
    asset_id, remainder = key_after_prefix.split("/", 1)
    if not asset_id or not remainder:
        logger.warning(
            f"S3 key does not match VAMS asset layout: bucket={bucket_name}, "
            f"key={s3_key}"
        )
        return None

    # Pass bucket + prefix so the GSI lookup can disambiguate when an
    # assetId happens to appear in more than one database.
    database_id = get_database_id_for_asset_id(
        asset_id,
        bucket_name=bucket_name,
        base_assets_prefix=base_prefix,
    )
    if not database_id:
        logger.warning(
            f"Could not resolve databaseId for assetId={asset_id} (bucket="
            f"{bucket_name}, key={s3_key}); treating as non-VAMS and skipping."
        )
        return None

    relative = "/" + remainder
    return {
        "databaseId": database_id,
        "assetId": asset_id,
        "relativePath": relative,
        "bucketName": bucket_name,
        "s3Key": s3_key,
    }


def _resolve_asset_from_s3_event(bucket_name: str, s3_key: str) -> Optional[Dict[str, Any]]:
    """Look up asset+db IDs and relative path for an S3 object.

    Returns None when the event should be silently skipped.
    """
    head_result = _head_object_with_encoding_fallback(bucket_name, s3_key)
    if not head_result:
        return None
    head = head_result["response"]
    # Use whichever key form actually existed in S3 — this becomes the
    # canonical key for all downstream calls (download, Physna path, etc.).
    s3_key = head_result["key"]
    s3_metadata = head.get("Metadata", {}) or {}
    asset_id = s3_metadata.get(ASSET_ID_METADATA_KEY)
    database_id = s3_metadata.get(DATABASE_ID_METADATA_KEY)
    if not asset_id or not database_id:
        logger.warning(f"Missing assetid/databaseid in S3 metadata for {s3_key}")
        return None

    asset_details = get_asset_details(database_id, asset_id)
    if not asset_details:
        logger.warning(f"Asset not found: {database_id}/{asset_id}")
        return None

    bucket_details = get_bucket_details(asset_details.get("bucketId"))
    if not bucket_details:
        logger.warning(f"Bucket not registered for asset {asset_id}")
        return None

    asset_location = asset_details.get("assetLocation", {}) or {}
    asset_base_key = asset_location.get(
        "Key", f"{bucket_details['baseAssetsPrefix']}{asset_id}/"
    )
    if s3_key.startswith(asset_base_key):
        relative = s3_key[len(asset_base_key):]
    else:
        relative = s3_key
    if not relative.startswith("/"):
        relative = "/" + relative

    return {
        "databaseId": database_id,
        "assetId": asset_id,
        "relativePath": relative,
        "bucketName": bucket_name,
        "s3Key": s3_key,
        "assetDetails": asset_details,
    }


def _get_s3_version_id(bucket_name: str, s3_key: str) -> Optional[str]:
    """Return the current S3 VersionId for the object, or None on any error.

    VAMS buckets are versioned; the VersionId is the authoritative identifier
    for "this specific bytes" and is what we write into Physna's
    ``__VAMS__FileVersion`` metadata key so we can later detect when Physna's
    copy no longer matches VAMS.

    Uses the encoding-fallback helper so filenames with '+' or other specials
    resolve regardless of whether the caller passed a decoded or raw S3 key.
    """
    head_result = _head_object_with_encoding_fallback(bucket_name, s3_key)
    if not head_result:
        return None
    version_id = head_result["response"].get("VersionId")
    return str(version_id) if version_id else None


def _build_metadata_payload(
    database_id: str,
    asset_id: str,
    relative_path: str,
    file_version: Optional[str],
    asset_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge VAMS asset + file metadata + file attributes and overlay the
    VAMS-reserved tracking keys (assetName, fileVersion).

    Reserved keys always win over same-named user metadata — the whole point
    of these keys is to reflect VAMS truth. Passing ``file_version=None``
    omits ``__VAMS__FileVersion`` (used on metadata-only updates where we
    preserve Physna's existing value rather than overwriting it).
    """
    asset_meta = get_asset_metadata(database_id, asset_id)
    file_meta, file_attrs = get_file_metadata(database_id, asset_id, relative_path)
    merged = merge_metadata(asset_meta, file_meta, file_attrs)
    payload = physna_format_metadata(merged)

    if asset_details is None:
        asset_details = get_asset_details(database_id, asset_id) or {}
    asset_name = asset_details.get("assetName")

    return apply_vams_reserved_metadata(payload, asset_name, file_version)


def _delete_physna_asset_by_uuid(
    client: PhysnaClient, physna_asset_uuid: str, full_path: str
) -> None:
    """Best-effort DELETE on a Physna asset by UUID, for reupload flows.

    Unlike ``_delete_physna_asset``, this does NOT trigger empty-folder
    cleanup — we're about to repopulate the folder with a fresh upload, so
    deleting the folder would be counterproductive.
    """
    response = client.request(
        "DELETE",
        f"/tenants/{physnaCommon.PHYSNA_TENANT_ID}/assets/{physna_asset_uuid}",
    )
    if response.status not in (200, 204, 404):
        raise PhysnaError(
            f"Delete-for-reupload failed for {full_path} ({physna_asset_uuid}) "
            f"with status {response.status}: {response.data!r}"
        )
    logger.info(
        f"Deleted stale Physna asset {full_path} ({physna_asset_uuid}) "
        f"ahead of re-upload"
    )


def _record_file_sync(database_id, asset_id, relative_path, action, sync_status,
                      s3_version_id=None, physna_asset_uuid=None, error_message=None):
    """Best-effort outbound sync tracking record for a file-level Physna sync."""
    write_outbound_sync_record(
        SYNC_OBJECT_TYPE_ASSET_FILE,
        database_id,
        physnaCommon.SYNC_SYSTEM_TYPE,
        physnaCommon.get_sync_system_unique_id(),
        action,
        sync_status,
        asset_id=asset_id,
        file_path=relative_path,
        s3_version_id=s3_version_id,
        error_message=error_message,
        sync_system_entity_id=physna_asset_uuid,
    )


def _upload_file_to_physna(
    database_id: str,
    asset_id: str,
    relative_path: str,
    bucket_name: str,
    s3_key: str,
    client: Optional[PhysnaClient] = None,
) -> bool:
    """Sync a VAMS file to Physna, recording the outcome in sync tracking."""
    sync_ctx: Dict[str, Any] = {}
    try:
        return _upload_file_to_physna_impl(
            database_id, asset_id, relative_path, bucket_name, s3_key,
            client=client, sync_ctx=sync_ctx,
        )
    except Exception as e:
        _record_file_sync(
            database_id, asset_id, relative_path,
            sync_ctx.get("action", SYNC_ACTION_MODIFY), SYNC_STATUS_FAILED,
            s3_version_id=sync_ctx.get("s3VersionId"),
            error_message=str(e),
        )
        raise


def _upload_file_to_physna_impl(
    database_id: str,
    asset_id: str,
    relative_path: str,
    bucket_name: str,
    s3_key: str,
    client: Optional[PhysnaClient] = None,
    sync_ctx: Optional[Dict[str, Any]] = None,
) -> bool:
    """Sync a VAMS file to Physna, respecting ``__VAMS__FileVersion`` tracking.

    Flow:
      1. Read the current S3 VersionId of the object (authoritative identity
         of the bytes in VAMS).
      2. Check whether Physna already has an asset at this path.
         a. If yes and its ``__VAMS__FileVersion`` matches the current S3
            VersionId → Physna's copy is already up to date; skip the upload
            and only refresh metadata.
         b. If yes and the version does NOT match → DELETE the stale Physna
            asset and fall through to a fresh upload.
         c. If no → just upload.
      3. Upload (POST with no metadata to decouple from schema issues).
      4. PATCH metadata including the VAMS-reserved tracking keys.
    """
    client = client or PhysnaClient()

    full_path = build_physna_path(database_id, asset_id, relative_path)
    filename = os.path.basename(relative_path)

    # Step 1: current S3 version of the object we are about to sync
    current_s3_version = _get_s3_version_id(bucket_name, s3_key)

    # Step 2: see what Physna already has at this path
    existing_uuid: Optional[str] = None
    existing_file_version: Optional[str] = None
    try:
        existing_uuid = lookup_physna_asset_id(
            client, physnaCommon.PHYSNA_TENANT_ID, full_path
        )
        if existing_uuid:
            existing_asset = get_physna_asset(
                client, physnaCommon.PHYSNA_TENANT_ID, existing_uuid
            )
            if existing_asset:
                existing_metadata = existing_asset.get("metadata") or {}
                ev = existing_metadata.get(VAMS_RESERVED_FILE_VERSION_KEY)
                existing_file_version = str(ev) if ev else None
    except Exception as e:
        # If we can't determine the current state, fall through to upload —
        # Physna will 409 if the path is taken, and we handle that below.
        logger.warning(
            f"Could not determine current Physna state for {full_path}; "
            f"proceeding with upload: {e}"
        )

    # Sync tracking: a pre-existing Physna asset means this is a modify.
    sync_action = SYNC_ACTION_MODIFY if existing_uuid else SYNC_ACTION_CREATE
    if sync_ctx is not None:
        sync_ctx["action"] = sync_action
        sync_ctx["s3VersionId"] = current_s3_version

    # Step 2a: identical version already in Physna — skip the upload
    if (
        existing_uuid
        and current_s3_version
        and existing_file_version == current_s3_version
    ):
        logger.info(
            f"Physna already has {full_path} at S3 version "
            f"{current_s3_version}; skipping re-upload, refreshing metadata "
            f"only."
        )
        metadata_payload = _build_metadata_payload(
            database_id,
            asset_id,
            relative_path,
            file_version=None,  # preserve existing __VAMS__FileVersion
        )
        try:
            _update_physna_metadata(
                client, full_path, existing_uuid, metadata_payload
            )
        except PhysnaError as e:
            logger.warning(
                f"Metadata refresh failed for {full_path}: {e}. File remains "
                f"in Physna; will retry on next VAMS metadata change."
            )
        return True

    # Step 2b: stale version — delete before re-uploading so we don't collide
    if existing_uuid and (
        existing_file_version is None
        or (current_s3_version and existing_file_version != current_s3_version)
    ):
        logger.info(
            f"Physna copy of {full_path} is stale "
            f"(__VAMS__FileVersion={existing_file_version!r}, "
            f"current S3 VersionId={current_s3_version!r}); deleting "
            f"ahead of re-upload"
        )
        try:
            _delete_physna_asset_by_uuid(client, existing_uuid, full_path)
            # Clear the uuid so the post-upload code doesn't assume it still
            # applies.
            existing_uuid = None
        except PhysnaError as e:
            logger.warning(
                f"Failed to delete stale Physna asset {full_path}; will still "
                f"attempt upload (Physna may 409): {e}"
            )

    # Step 3: upload
    tmp_dir = tempfile.mkdtemp(prefix="physna-")
    local_path = os.path.join(tmp_dir, filename)
    physna_asset_uuid: Optional[str] = None
    try:
        try:
            _s3.download_file(bucket_name, s3_key, local_path)
        except ClientError as e:
            # The S3 object is gone (deleted / never uploaded / ObjectRemoved
            # event lost). If Physna still has a copy, reconcile by deleting
            # it so the two sides converge. Returning True here marks the
            # batch record as handled; the SQS event won't be retried, which
            # is what we want — retrying will hit the same NoSuchKey.
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                logger.info(
                    f"S3 object missing for Physna sync "
                    f"(bucket={bucket_name}, key={s3_key}); reconciling by "
                    f"deleting the Physna copy if present."
                )
                if existing_uuid:
                    try:
                        _delete_physna_asset_by_uuid(
                            client, existing_uuid, full_path
                        )
                    except PhysnaError as de:
                        logger.warning(
                            f"Reconcile-delete failed for {full_path}: {de}"
                        )
                return True
            raise
        with open(local_path, "rb") as f:
            file_bytes = f.read()

        # Physna expects the `path` field to be the FULL asset path, including
        # the filename. Passing only the folder triggers:
        #   "Asset path does not match file extension: '' '.stp'"
        # because Physna parses the path's extension and compares to the
        # uploaded file's extension.
        fields = {
            "file": (filename, file_bytes),
            "path": full_path,
            "createMissingFolders": "true",
        }
        response = client.request(
            "POST",
            f"/tenants/{physnaCommon.PHYSNA_TENANT_ID}/assets",
            fields=fields,
        )
        if response.status in (200, 201):
            logger.info(
                f"Uploaded {full_path} to Physna (S3 VersionId="
                f"{current_s3_version!r})"
            )
            physna_asset_uuid = _extract_physna_asset_id(response)
        elif response.status == 409:
            logger.info(
                f"Physna POST returned 409 (already exists) for {full_path}; "
                f"proceeding to metadata update"
            )
            physna_asset_uuid = existing_uuid
        else:
            raise PhysnaError(
                f"Upload failed with status {response.status}: {response.data!r}"
            )
    finally:
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
            os.rmdir(tmp_dir)
        except OSError as cleanup_err:
            logger.warning(f"Cleanup failed for {tmp_dir}: {cleanup_err}")

    # Step 4: set metadata including the reserved tracking keys. Failure to
    # set metadata does NOT fail the upload — the file is in Physna and the
    # next VAMS metadata change will retry the PATCH.
    metadata_payload = _build_metadata_payload(
        database_id,
        asset_id,
        relative_path,
        file_version=current_s3_version,
    )

    # If we still don't have a UUID (e.g., 409 with failed existence lookup),
    # try once more now that the asset is definitely there.
    if not physna_asset_uuid:
        try:
            physna_asset_uuid = lookup_physna_asset_id(
                client, physnaCommon.PHYSNA_TENANT_ID, full_path
            )
        except Exception as e:
            logger.warning(
                f"Physna asset UUID lookup failed after upload for "
                f"{full_path}: {e}"
            )

    if not physna_asset_uuid:
        logger.warning(
            f"File uploaded to Physna at {full_path} but no asset UUID was "
            f"obtainable; skipping metadata set. Will retry on next VAMS "
            f"metadata change."
        )
        _record_file_sync(database_id, asset_id, relative_path, sync_action,
                          SYNC_STATUS_SUCCESS, s3_version_id=current_s3_version)
        return True

    try:
        _update_physna_metadata(
            client, full_path, physna_asset_uuid, metadata_payload
        )
    except PhysnaError as e:
        logger.warning(
            f"File uploaded but metadata set failed for {full_path}: {e}. "
            f"The file is in Physna; metadata will be retried on the next "
            f"VAMS metadata change."
        )
    _record_file_sync(database_id, asset_id, relative_path, sync_action,
                      SYNC_STATUS_SUCCESS, s3_version_id=current_s3_version,
                      physna_asset_uuid=physna_asset_uuid)
    return True


def _extract_physna_asset_id(response) -> Optional[str]:
    """Best-effort extraction of the Physna asset UUID from an upload response.

    Physna's upload response has varied across API versions. We try the most
    common shapes: top-level ``id``, ``assetId``, ``uuid``; nested under a
    single-key ``asset`` wrapper; or a list with a single item.
    """
    try:
        body = json.loads(response.data.decode("utf-8")) if response.data else {}
    except (ValueError, AttributeError):
        return None

    def _pick(obj):
        if not isinstance(obj, dict):
            return None
        for key in ("id", "assetId", "uuid"):
            val = obj.get(key)
            if val:
                return str(val)
        return None

    direct = _pick(body)
    if direct:
        return direct
    if isinstance(body, dict):
        nested = body.get("asset") or body.get("data")
        if isinstance(nested, dict):
            return _pick(nested)
        if isinstance(nested, list) and nested:
            return _pick(nested[0])
    if isinstance(body, list) and body:
        return _pick(body[0])
    return None


def _update_physna_metadata(
    client: PhysnaClient,
    full_path: str,
    physna_asset_uuid: str,
    metadata_payload: Dict[str, Any],
) -> bool:
    """Mirror VAMS metadata onto a Physna asset — full replace, not merge.

    Physna's ``PATCH /assets/{uuid}`` only updates the keys you send; keys
    already set on the asset that you omit are left in place. To keep Physna
    in sync with VAMS (where a removed VAMS metadata key should disappear
    from Physna too), this function:

    1. Auto-registers any missing tenant-level metadata fields.
    2. GETs the current metadata on the Physna asset.
    3. Computes which keys exist in Physna but not in the new payload and
       DELETEs them via ``DELETE /assets/{uuid}/metadata`` with
       ``{"metadataFieldNames": [...]}``.
    4. PATCHes the asset with the new ``{"metadata": {...}}`` payload so the
       remaining keys take their current VAMS values.

    Returns True on success, False when the asset doesn't exist (404).
    Raises ``PhysnaError`` on other non-success responses from the PATCH.
    Delete-stage failures are logged and do not fail the overall sync.
    """
    # Step 1: ensure tenant schema has every key we are about to set
    desired_keys = (
        set(metadata_payload.keys())
        if isinstance(metadata_payload, dict)
        else set()
    )
    try:
        ensure_metadata_fields_registered(
            client, physnaCommon.PHYSNA_TENANT_ID, desired_keys
        )
    except Exception as e:
        logger.warning(
            f"Metadata-field pre-registration encountered an error for "
            f"{full_path}: {e}"
        )

    # Step 2: read the current metadata on the asset so we can diff
    current_metadata: Dict[str, Any] = {}
    try:
        current_asset = get_physna_asset(
            client, physnaCommon.PHYSNA_TENANT_ID, physna_asset_uuid
        )
        if current_asset is None:
            logger.info(
                f"Asset not present in Physna for metadata update: {full_path}"
            )
            return False
        current_metadata = current_asset.get("metadata") or {}
    except Exception as e:
        # Can't read current metadata — skip the prune step, still push updates.
        logger.warning(
            f"Failed to read current Physna metadata for {full_path}; "
            f"skipping prune of removed keys: {e}"
        )

    # Step 3: delete keys that no longer exist in VAMS
    to_delete = [k for k in current_metadata.keys() if k not in desired_keys]
    if to_delete:
        try:
            delete_physna_metadata_fields(
                client,
                physnaCommon.PHYSNA_TENANT_ID,
                physna_asset_uuid,
                to_delete,
            )
            logger.info(
                f"Pruned {len(to_delete)} stale Physna metadata field(s) "
                f"from {full_path}: {sorted(to_delete)}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to prune stale Physna metadata fields "
                f"{sorted(to_delete)} from {full_path}: {e}"
            )

    # Step 4: PATCH to set/update the remaining desired keys
    if not metadata_payload:
        # Nothing left to set. If we got here the prune already ran.
        return True

    response = client.request(
        "PATCH",
        f"/tenants/{physnaCommon.PHYSNA_TENANT_ID}/assets/{physna_asset_uuid}",
        body=json.dumps({"metadata": metadata_payload}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    if response.status in (200, 204):
        logger.info(f"Updated metadata on Physna for {full_path}")
        return True
    if response.status == 404:
        logger.info(f"Asset not present in Physna for metadata update: {full_path}")
        return False
    raise PhysnaError(
        f"Metadata update failed for {full_path} with status {response.status}: "
        f"{response.data!r}"
    )


def _s3_object_still_exists(
    database_id: str, asset_id: str, relative_path: str
) -> Optional[bool]:
    """Return True if the VAMS S3 object for this file is still present,
    False if definitively absent, and None when we cannot determine it
    (in which case callers must NOT proceed with a destructive action).

    The bucket+key are resolved via the VAMS asset record so event source
    mismatches (e.g. a metadata stream event with stale inputs) can still
    be cross-checked against the real source of truth in S3.
    """
    asset_details = get_asset_details(database_id, asset_id)
    if not asset_details:
        # Asset itself is gone in VAMS. Treat as "no S3 object" — the
        # corresponding Physna copy is genuinely orphaned and safe to delete.
        return False
    bucket_details = get_bucket_details(asset_details.get("bucketId"))
    if not bucket_details or not bucket_details.get("bucketName"):
        # Can't resolve the bucket — don't take destructive action on a guess.
        return None
    asset_location = asset_details.get("assetLocation", {}) or {}
    asset_base_key = asset_location.get(
        "Key", f"{bucket_details['baseAssetsPrefix']}{asset_id}/"
    )
    s3_key = asset_base_key + relative_path.lstrip("/")
    bucket_name = bucket_details["bucketName"]
    head_result = _head_object_with_encoding_fallback(bucket_name, s3_key)
    # head_object returning None can be either "genuine 404" or "other
    # error" — the helper already falls back through both URL encodings,
    # so a None here almost always means the object is actually gone. Treat
    # it as False; the upstream delete caller still has path-level safety
    # (Physna asset UUID lookup) before any API call.
    return head_result is not None


def _delete_physna_asset(
    client: PhysnaClient,
    database_id: str,
    asset_id: str,
    relative_path: str,
    *,
    skip_s3_existence_check: bool = False,
) -> None:
    path = build_physna_path(database_id, asset_id, relative_path)

    # SAFETY: before we ever issue a Physna DELETE, confirm the VAMS S3
    # object for this file really is gone. Event sources outside the S3
    # ``ObjectRemoved`` notifier (DynamoDB streams on metadata/attribute
    # tables, manual reconcile calls, etc.) can misroute a "something
    # changed" event into this function even when the file is still
    # present. Deleting from Physna in that case permanently loses the
    # indexed geometry and forces a full reindex on the next upload.
    #
    # Callers that have their own stronger signal that the S3 object is
    # definitively gone (namely ``_handle_s3_record`` responding to an
    # ``ObjectRemoved`` notification with matching eventName + bucket/key)
    # may pass ``skip_s3_existence_check=True`` to bypass this guard —
    # the event itself is authoritative in that case.
    if not skip_s3_existence_check:
        s3_present = _s3_object_still_exists(database_id, asset_id, relative_path)
        if s3_present is True:
            logger.info(
                f"Skipping Physna delete for {path}: the VAMS S3 object is "
                f"still present. The event that triggered this delete is "
                f"likely a metadata/row change rather than a file removal."
            )
            return
        if s3_present is None:
            logger.warning(
                f"Skipping Physna delete for {path}: could not verify S3 "
                f"state. Erring on the side of preserving the Physna copy."
            )
            return

    # Look up the Physna asset UUID — Physna's asset-scoped endpoints are keyed
    # by UUID, not by path.
    try:
        physna_asset_uuid = lookup_physna_asset_id(
            client, physnaCommon.PHYSNA_TENANT_ID, path
        )
    except Exception as e:
        logger.warning(f"Physna asset UUID lookup failed for {path}: {e}")
        physna_asset_uuid = None

    if not physna_asset_uuid:
        logger.info(f"Physna asset not found for delete (already gone): {path}")
        return

    response = client.request(
        "DELETE",
        f"/tenants/{physnaCommon.PHYSNA_TENANT_ID}/assets/{physna_asset_uuid}",
    )
    if response.status not in (200, 204, 404):
        _record_file_sync(database_id, asset_id, relative_path, SYNC_ACTION_DELETE,
                          SYNC_STATUS_FAILED, physna_asset_uuid=physna_asset_uuid,
                          error_message=f"Delete failed with status {response.status}")
        raise PhysnaError(
            f"Delete failed for {path} ({physna_asset_uuid}) with status "
            f"{response.status}: {response.data!r}"
        )
    logger.info(f"Deleted Physna asset (or already gone): {path}")
    _record_file_sync(database_id, asset_id, relative_path, SYNC_ACTION_DELETE,
                      SYNC_STATUS_SUCCESS, physna_asset_uuid=physna_asset_uuid)

    folder = build_physna_folder_path(database_id, asset_id, relative_path)
    if folder:
        delete_folder_if_empty(client, physnaCommon.PHYSNA_TENANT_ID, folder)


def _handle_s3_record(record: Dict[str, Any]) -> bool:
    event_name = record.get("eventName", "")
    bucket = record.get("s3", {}).get("bucket", {}).get("name")
    raw_key = record.get("s3", {}).get("object", {}).get("key")
    if not bucket or not raw_key:
        return False
    s3_key = urllib.parse.unquote_plus(raw_key)
    if _should_skip_s3_key(s3_key):
        logger.info(
            f"Skipping S3 event for excluded path (pipeline/preview/workspace/"
            f"temp-upload prefix or .previewFile.* pattern): bucket={bucket}, "
            f"s3Key={s3_key}, eventName={event_name}"
        )
        return True

    # ObjectRemoved:* events arrive AFTER the object is already gone,
    # so head_object 404s and the standard metadata-backed resolver bails
    # out. This includes BOTH:
    #   * ``ObjectRemoved:Delete`` — permanent delete of a specific version.
    #   * ``ObjectRemoved:DeleteMarkerCreated`` — soft delete / archive on
    #     a versioned bucket (a new delete marker hides prior versions).
    # For Physna, either shape means the user no longer has the file in
    # VAMS, so both should trigger a Physna DELETE. We fall back to a
    # metadata-free resolver that derives identifiers from the S3 key
    # layout + bucket registry + assetIdGSI. For all other event types
    # (ObjectCreated / ObjectRestore / ...), the head_object resolver
    # remains authoritative because the file is present.
    if event_name.startswith("ObjectRemoved"):
        resolved = _resolve_asset_from_s3_key_without_metadata(bucket, s3_key)
    else:
        resolved = _resolve_asset_from_s3_event(bucket, s3_key)
    if not resolved:
        return True

    relative = resolved["relativePath"]
    if not is_sync_supported_file(relative):
        logger.info(
            f"Skipping S3 event for unsupported file type (Physna only accepts "
            f"specific 3D/CAD, document, and image formats): bucket={bucket}, "
            f"s3Key={s3_key}, relativePath={relative}, eventName={event_name}"
        )
        return True

    client = PhysnaClient()

    if event_name.startswith("ObjectRemoved"):
        # S3 itself told us the object is gone — that's the authoritative
        # signal. Skip the extra head_object round-trip.
        _delete_physna_asset(
            client,
            resolved["databaseId"],
            resolved["assetId"],
            relative,
            skip_s3_existence_check=True,
        )
        return True

    # Default: treat as upload/create/update
    return _upload_file_to_physna(
        resolved["databaseId"],
        resolved["assetId"],
        relative,
        resolved["bucketName"],
        resolved["s3Key"],
        client=client,
    )


def _handle_file_metadata_stream(record: Dict[str, Any]) -> bool:
    """Handle a DynamoDB stream record for file metadata/attribute tables."""
    event_name = record.get("eventName", "")
    dynamo = record.get("dynamodb", {}) or {}
    if event_name == "REMOVE":
        composite = (dynamo.get("Keys", {}) or {}).get("databaseId:assetId:filePath", {}).get("S")
    else:
        composite = (dynamo.get("NewImage", {}) or {}).get("databaseId:assetId:filePath", {}).get("S")
    if not composite:
        return True
    parts = composite.split(":", 2)
    if len(parts) != 3:
        return True
    database_id, asset_id, relative = parts
    if relative == "/":
        return True  # asset-level change is handled by physnaAssetSync
    if not is_sync_supported_file(relative):
        logger.info(
            f"Skipping VAMS metadata stream event for unsupported file type "
            f"(Physna only accepts specific 3D/CAD, document, and image "
            f"formats): databaseId={database_id}, assetId={asset_id}, "
            f"relativePath={relative}, eventName={event_name}"
        )
        return True

    # NOTE: A DynamoDB REMOVE on the file-metadata or file-attribute tables
    # only means the user cleared custom metadata / attributes on the file —
    # it does NOT imply the file itself was deleted. File deletion is
    # signaled via S3 ``ObjectRemoved`` events, which go through
    # ``_handle_s3_record`` above. We therefore fall through to the normal
    # metadata-update path on REMOVE; ``_build_metadata_payload`` will omit
    # the now-missing keys, and the full-replace PATCH prunes them from
    # Physna too.

    asset_details = get_asset_details(database_id, asset_id)
    if not asset_details:
        return True
    bucket_details = get_bucket_details(asset_details.get("bucketId"))
    if not bucket_details or not bucket_details.get("bucketName"):
        return True

    asset_location = asset_details.get("assetLocation", {}) or {}
    asset_base_key = asset_location.get(
        "Key", f"{bucket_details['baseAssetsPrefix']}{asset_id}/"
    )
    s3_key = asset_base_key + relative.lstrip("/")
    bucket_name = bucket_details["bucketName"]

    client = PhysnaClient()

    # Per requirements: check if the file already exists in Physna FIRST, then
    # route to upload-with-metadata or metadata-only update accordingly. Some
    # Physna metadata endpoints return 400 (not 404) for missing assets, so
    # relying on the PATCH response code alone is not reliable.
    full_path = build_physna_path(database_id, asset_id, relative)
    try:
        physna_asset_uuid = lookup_physna_asset_id(
            client, physnaCommon.PHYSNA_TENANT_ID, full_path
        )
    except Exception as e:
        logger.exception(
            f"Failed to look up Physna asset for {full_path}; falling back to upload: {e}"
        )
        physna_asset_uuid = None

    if not physna_asset_uuid:
        logger.info(
            f"Physna asset not found for metadata change; uploading: {full_path}"
        )
        return _upload_file_to_physna(
            database_id, asset_id, relative, bucket_name, s3_key, client=client
        )

    # Read the existing Physna asset so we can:
    #   1. Check whether it carries the __VAMS__FileVersion tracking key.
    #      Absence means Physna's copy was uploaded before VAMS started
    #      tracking versions — we treat that as stale and re-upload rather
    #      than just patching metadata. This matches the upload-path rule
    #      that a missing version tag implies an out-of-date file.
    #   2. Preserve the existing __VAMS__FileVersion in the payload when it
    #      IS present, so the full-replace diff in _update_physna_metadata
    #      does not strip it.
    existing_file_version: Optional[str] = None
    try:
        existing_asset = get_physna_asset(
            client, physnaCommon.PHYSNA_TENANT_ID, physna_asset_uuid
        )
        if existing_asset:
            existing_meta = existing_asset.get("metadata") or {}
            ev = existing_meta.get(VAMS_RESERVED_FILE_VERSION_KEY)
            existing_file_version = str(ev) if ev else None
    except Exception as e:
        logger.warning(
            f"Could not read existing Physna metadata for {full_path}: {e}"
        )

    if not existing_file_version:
        logger.info(
            f"Physna asset for {full_path} is missing "
            f"__VAMS__FileVersion; treating as stale and re-uploading "
            f"before applying metadata change."
        )
        return _upload_file_to_physna(
            database_id, asset_id, relative, bucket_name, s3_key, client=client
        )

    # Build the metadata payload. This is a metadata-only change — we MUST
    # NOT bump __VAMS__FileVersion here (that key only advances on a new
    # upload). Passing file_version=None keeps the reserved key out of the
    # payload so we can explicitly carry forward the existing Physna value
    # below.
    metadata_payload = _build_metadata_payload(
        database_id,
        asset_id,
        relative,
        file_version=None,
        asset_details=asset_details,
    )
    metadata_payload[VAMS_RESERVED_FILE_VERSION_KEY] = existing_file_version

    try:
        updated = _update_physna_metadata(
            client, full_path, physna_asset_uuid, metadata_payload
        )
    except PhysnaError as e:
        logger.warning(
            f"Metadata update failed for {full_path}: {e}. File stays in "
            f"Physna; will retry on next VAMS metadata change."
        )
        _record_file_sync(database_id, asset_id, relative, SYNC_ACTION_MODIFY,
                          SYNC_STATUS_FAILED, physna_asset_uuid=physna_asset_uuid,
                          error_message=str(e))
        return True

    if updated:
        _record_file_sync(database_id, asset_id, relative, SYNC_ACTION_MODIFY,
                          SYNC_STATUS_SUCCESS, physna_asset_uuid=physna_asset_uuid)
        return True

    # Lookup said asset exists but PATCH returned 404 (race / just-deleted).
    # Fall back to upload so the eventual state matches VAMS.
    logger.info(
        f"Physna PATCH returned not-found after lookup succeeded; "
        f"falling back to upload for {full_path}"
    )
    return _upload_file_to_physna(
        database_id, asset_id, relative, bucket_name, s3_key, client=client
    )


def _safe_handle_s3_record(record: Dict[str, Any]) -> bool:
    """Wrap ``_handle_s3_record`` so a single record failure never aborts
    the batch. Any exception is logged with the triggering bucket/key so
    the failure is diagnosable, and processing continues with the next
    record."""
    try:
        return _handle_s3_record(record)
    except Exception as e:
        s3_info = record.get("s3", {}) or {}
        bucket = (s3_info.get("bucket") or {}).get("name")
        key = (s3_info.get("object") or {}).get("key")
        logger.exception(
            f"Error processing S3 record (bucket={bucket}, key={key}, "
            f"eventName={record.get('eventName')}): {e}"
        )
        return False


def _safe_handle_file_metadata_stream(record: Dict[str, Any]) -> bool:
    """Wrap ``_handle_file_metadata_stream`` for per-record isolation."""
    try:
        return _handle_file_metadata_stream(record)
    except Exception as e:
        dynamo = record.get("dynamodb", {}) or {}
        keys = dynamo.get("Keys", {}) or {}
        composite = keys.get("databaseId:assetId:filePath", {}).get("S")
        logger.exception(
            f"Error processing DynamoDB stream record "
            f"(compositeKey={composite}, eventName={record.get('eventName')}): {e}"
        )
        return False


def _walk_records(event: Dict[str, Any]) -> int:
    """Walk every record in the SQS batch, isolating failures per record.

    Each individual handler invocation is wrapped in its own try/except so
    one bad record (e.g., a single upload that Physna rejects) does not
    cause the rest of the batch to be silently dropped. SQS batches that
    contain multiple uploads (e.g., the user uploads three files at once)
    must ALL be attempted even when an earlier one fails.
    """
    successful = 0
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:sqs":
            continue

        # SQS body → SNS envelope parsing is per-record; wrap it so a
        # malformed body on one record doesn't abort the batch.
        try:
            body = record.get("body", "")
            if isinstance(body, str):
                body = json.loads(body)
            if body.get("Type") != "Notification" or not body.get("Message"):
                continue
            sns_message = body["Message"]
            if isinstance(sns_message, str):
                sns_message = json.loads(sns_message)
        except Exception as e:
            logger.exception(f"Failed to parse SQS→SNS envelope: {e}")
            continue

        # Direct DynamoDB stream record
        if sns_message.get("eventSource") == "aws:dynamodb" or sns_message.get(
            "eventName"
        ) in ("INSERT", "MODIFY", "REMOVE"):
            if _safe_handle_file_metadata_stream(sns_message):
                successful += 1
            continue

        # Nested Records (S3 or nested SQS from sqsBucketSync)
        for inner in sns_message.get("Records", []):
            src = inner.get("eventSource", "")
            if src == "aws:s3":
                if _safe_handle_s3_record(inner):
                    successful += 1
            elif src == "aws:sqs":
                try:
                    inner_body = inner.get("body", "")
                    if isinstance(inner_body, str):
                        inner_body = json.loads(inner_body)
                    if (
                        inner_body.get("Type") == "Notification"
                        and inner_body.get("Message")
                    ):
                        nested = inner_body["Message"]
                        if isinstance(nested, str):
                            nested = json.loads(nested)
                    else:
                        continue
                except Exception as e:
                    logger.exception(f"Failed to parse nested SQS→SNS envelope: {e}")
                    continue

                for s3_rec in nested.get("Records", []):
                    if s3_rec.get("eventSource") == "aws:s3":
                        if _safe_handle_s3_record(s3_rec):
                            successful += 1
    return successful


def lambda_handler(event, context: LambdaContext) -> Dict[str, Any]:
    total = len(event.get("Records", []))
    logger.info(
        f"Physna file sync invocation starting; {total} SQS record(s) in batch"
    )
    successful = _walk_records(event)
    logger.info(
        f"Physna file sync done: {successful} inner record(s) processed "
        f"successfully across {total} SQS record(s) in batch"
    )
    return {"statusCode": 200, "body": {"successful": successful}}
