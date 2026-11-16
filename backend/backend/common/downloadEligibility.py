#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Download eligibility: the checks a file must pass before VAMS hands out its content.

One implementation of the rules `handlers/assets/downloadAsset.py` applies before it signs a
presigned GET URL, so every surface that serves asset file content applies them identically:

- the asset exists in the live table (an archived asset reads as not found), is distributable,
  and has a storage location (`get_distributable_asset_context`);
- a caller-supplied key resolves inside the asset's own prefix and within the S3 key limit
  (`resolve_asset_file_key`);
- an asset-version pin (`assetVersionId` or its alias) resolves to the per-file S3 version recorded
  in that version's snapshot (`resolve_pinned_version_id`);
- a single file exists, is not a delete marker (410), and passes the extension and content-type
  blocklist; a prefix passes only when EVERY object under it does (`check_file_eligibility`,
  `validate_prefix_content_types`);
- `is_file_archived` reads a HeadObject response as archived for `vams-status` archived/deleted or
  a GLACIER / DEEP_ARCHIVE storage class.

Signing is NOT here: the caller signs `final_key` / `version_id` itself.

This module builds no AWS client and reads no table at import. Every S3 call goes through the
client the caller passes (the Region-routing client from `common.s3` in a handler), the asset and
bucket reads go through the caller's own getters, and the asset-version lookups go through the
resolvers the caller passes (they live in `handlers/assets/assetVersions.py`, which owns the
version tables). Every refusal this module raises itself is a `VAMSGeneralErrorResponse` with a
generic message that does not echo the caller's key (status 400, or 410 for a delete-marker
version). An error raised by an injected resolver passes through unchanged, with that resolver's
own message.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Tuple

from botocore.exceptions import ClientError

from common.s3 import list_all_objects, validateUnallowedFileExtensionAndContentType
from common.s3MetadataKeys import (
    VAMS_STATUS_METADATA_KEY,
    VAMS_STATUS_ARCHIVED,
    VAMS_STATUS_DELETED,
)
from common.validators import exceeds_s3_object_key_limit
from customLogging.logger import safeLogger
from models.common import VAMSGeneralErrorResponse

logger = safeLogger()

# Default worker pool for the per-object checks of a prefix download. Callers pass their own
# (downloadAsset reads DOWNLOAD_MAX_PARALLEL_S3_WORKERS); the S3 client's connection pool
# (common.s3.S3_CLIENT_MAX_POOL_CONNECTIONS) must cover whatever is passed.
DEFAULT_MAX_PARALLEL_S3_WORKERS = 25

# Storage classes whose objects cannot be read without a restore.
ARCHIVED_STORAGE_CLASSES = ('GLACIER', 'DEEP_ARCHIVE')

# Refusal messages. Generic by design (backend/CLAUDE.md Rule 11): none carries the caller's key.
MSG_ASSET_NOT_FOUND = "Asset not found in database"
MSG_ASSET_NOT_DISTRIBUTABLE = "Asset not distributable"
MSG_ASSET_LOCATION_NOT_FOUND = "Asset location not found"
MSG_KEY_OUTSIDE_ASSET = "Requested file is not part of this asset"
MSG_KEY_TOO_LONG = "File key exceeds the maximum S3 object key length"
MSG_FILE_NOT_FOUND = "File not found in S3"
MSG_VERSION_ARCHIVED = "File version has been archived and cannot be downloaded"
MSG_FILE_CHECK_FAILED = "Error checking file in S3"
MSG_FILE_TYPE_NOT_ALLOWED = "Unallowed file extension or content type in asset file"

# Status code for a requested version that is a delete marker.
STATUS_VERSION_ARCHIVED = 410


#######################
# Archive status
#######################

def is_file_archived(head: dict) -> bool:
    """Whether a HeadObject response describes an archived object.

    Args:
        head: The HeadObject response (``Metadata`` and ``StorageClass`` are read)

    Returns:
        True for ``vams-status`` archived or deleted, or a GLACIER / DEEP_ARCHIVE storage class
    """
    vams_status = head.get('Metadata', {}).get(VAMS_STATUS_METADATA_KEY, '')
    storage_class = head.get('StorageClass', 'STANDARD')
    return (vams_status in [VAMS_STATUS_ARCHIVED, VAMS_STATUS_DELETED] or
            storage_class in ARCHIVED_STORAGE_CLASSES)


#######################
# Asset-level checks
#######################

def require_distributable_asset(asset: Optional[dict],
                                check_quarantine: Optional[Callable[[str, str], None]] = None,
                                database_id: Optional[str] = None,
                                asset_id: Optional[str] = None) -> dict:
    """Check an asset record is downloadable and return its ``assetLocation``.

    Args:
        asset: The asset record from the live asset table, or None when it was not found.
            Archived assets are not in the live table, so they arrive here as None.
        check_quarantine: The caller's compliance quarantine block
            (``common.compliance.quarantineGuard.check_quarantine_block``), called with the asset's
            database and asset id once it is known to be distributable, before its location is read.
            It raises ``QuarantineBlockedResponse`` (a ``VAMSGeneralErrorResponse``) for a quarantined
            asset and is a no-op while the deployment's block is off. This module reads no table, so
            the caller passes it; a caller that hands out file bytes always passes it.
        database_id, asset_id: the ids the quarantine block reads (default: the record's own)

    Raises:
        VAMSGeneralErrorResponse: asset not found, not distributable, quarantined, or without a
            location
    """
    if not asset:
        raise VAMSGeneralErrorResponse(MSG_ASSET_NOT_FOUND)
    if not asset.get('isDistributable', False):
        raise VAMSGeneralErrorResponse(MSG_ASSET_NOT_DISTRIBUTABLE)
    if check_quarantine is not None:
        check_quarantine(database_id or asset.get('databaseId'), asset_id or asset.get('assetId'))
    asset_location = asset.get('assetLocation')
    if not asset_location:
        raise VAMSGeneralErrorResponse(MSG_ASSET_LOCATION_NOT_FOUND)
    return asset_location


def get_distributable_asset_context(database_id: str, asset_id: str, *,
                                    get_asset: Callable[[str, str], Optional[dict]],
                                    get_bucket_details: Callable[[str], dict],
                                    check_quarantine: Optional[Callable[[str, str], None]] = None
                                    ) -> Tuple[str, str]:
    """Read the asset, check it is downloadable, and return its bucket and base key.

    Args:
        database_id: Database ID
        asset_id: Asset ID
        get_asset: ``(database_id, asset_id) -> record or None`` over the LIVE asset table
        get_bucket_details: ``bucket_id -> {'bucketName': ..., ...}``
        check_quarantine: the compliance quarantine block (see ``require_distributable_asset``)

    Returns:
        ``(bucket_name, asset_base_key)``

    Raises:
        VAMSGeneralErrorResponse: from ``require_distributable_asset`` or the bucket lookup
    """
    asset = get_asset(database_id, asset_id)
    asset_location = require_distributable_asset(asset, check_quarantine, database_id, asset_id)
    bucket_details = get_bucket_details(asset.get('bucketId'))
    return bucket_details['bucketName'], asset_location.get('Key')


#######################
# Key resolution
#######################

def normalize_s3_path(base_path: str, relative_path: str) -> str:
    """Join a base prefix and a relative path with exactly one slash between them."""
    base_path = base_path.rstrip('/')
    relative_path = relative_path.lstrip('/')
    return f"{base_path}/{relative_path}"


def asset_prefix(asset_base_key: str) -> str:
    """The asset's own prefix, always slash-terminated.

    A bucket-root key is only inside the asset when it sits under ``'<base>/'``, so the base
    ``assets/pfx`` does not cover the sibling prefix ``assets/pfxOTHER/secret.txt``.
    """
    return asset_base_key.rstrip('/') + '/'


def resolve_asset_file_key(asset_base_key: str, raw_key: Optional[str]) -> str:
    """Resolve a caller-supplied key to the full S3 key, refusing keys outside the asset.

    Accepted shapes: a leading-slash asset-relative path (CLI), the full S3 key under the asset
    prefix (web viewers pass listFiles keys), ``/`` (whole-asset prefix), or no key (the base key
    verbatim, whatever its shape).

    Raises:
        VAMSGeneralErrorResponse: the key is a bucket-root key outside the asset's prefix, or the
            resolved key exceeds the S3 object key limit
    """
    asset_base_prefix = asset_base_key.rstrip('/')
    prefix = asset_base_prefix + '/'

    if raw_key:
        if raw_key == asset_base_key or raw_key.startswith(prefix):
            # The asset's own base key, or already carries the base path: use it as-is
            final_key = raw_key
        elif not raw_key.startswith('/') and raw_key.startswith(asset_base_prefix):
            # Bucket-root key that shares the base key's characters without sitting under the
            # asset prefix - a different asset's or database's object. Asset-relative keys carry
            # a leading '/' and are never in this shape.
            raise VAMSGeneralErrorResponse(MSG_KEY_OUTSIDE_ASSET)
        else:
            # Relative key: combine with the base path
            final_key = normalize_s3_path(asset_base_key, raw_key)
    else:
        final_key = asset_base_key

    # The request model bounded the asset-relative key; the resolved key carries the asset prefix
    # as well and is what S3 measures. Refuse it here so an over-long key never reaches
    # head_object or the presigner.
    if exceeds_s3_object_key_limit(final_key):
        raise VAMSGeneralErrorResponse(MSG_KEY_TOO_LONG)
    return final_key


def asset_relative_key(final_key: str, asset_base_key: str) -> str:
    """The key relative to the asset prefix, without a leading slash.

    This is how ``fileKey`` / ``relativeKey`` is matched in asset-version snapshot records.
    """
    prefix = asset_prefix(asset_base_key)
    relative = final_key
    if relative.startswith(prefix):
        relative = relative[len(prefix):]
    return relative.lstrip('/')


#######################
# Asset-version pins
#######################

def resolve_pinned_version_id(database_id: str, asset_id: str, relative_key: str, *,
                              asset_version_id: Optional[str] = None,
                              asset_version_alias: Optional[str] = None,
                              resolve_file_version: Callable[[str, str, str, str], str] = None,
                              resolve_alias: Callable[[str, str, str], str] = None) -> str:
    """Resolve a whole-set asset-version pin to the file's S3 version ID.

    The alias wins when both are given (the request models refuse that combination anyway).

    Args:
        relative_key: from ``asset_relative_key``
        resolve_file_version: ``(db, asset, asset_version_id, relative_key) -> s3 version id``
        resolve_alias: ``(db, asset, alias) -> asset_version_id``

    Raises:
        VAMSGeneralErrorResponse: from the resolvers (version or file not in the snapshot)
        ValueError: neither a version ID nor an alias was given, or a resolver is missing
            (a caller bug)
    """
    if resolve_file_version is None or resolve_alias is None:
        raise ValueError("resolve_pinned_version_id needs both version resolvers")
    if asset_version_alias:
        resolved_asset_version_id = resolve_alias(database_id, asset_id, asset_version_alias)
        return resolve_file_version(database_id, asset_id, resolved_asset_version_id, relative_key)
    if asset_version_id:
        return resolve_file_version(database_id, asset_id, asset_version_id, relative_key)
    raise ValueError("resolve_pinned_version_id needs asset_version_id or asset_version_alias")


#######################
# S3 object checks
#######################

def s3_object_exists(s3_client, bucket: str, key: str, version_id: Optional[str] = None) -> bool:
    """Whether a HeadObject on the key (or version) succeeds.

    Returns:
        True on success, False on a 404

    Raises:
        The ClientError for any other code, so a failed check is never read as absent
    """
    try:
        params = {'Bucket': bucket, 'Key': key}
        if version_id:
            params['VersionId'] = version_id
        s3_client.head_object(**params)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        logger.warning(f"Error checking if object exists: {e}")
        raise


def validate_prefix_content_types(s3_client, bucket: str, prefix: str, *,
                                  max_workers: int = DEFAULT_MAX_PARALLEL_S3_WORKERS,
                                  list_objects: Callable = None,
                                  is_allowed: Callable[[str, str], bool] = None) -> bool:
    """Validate every object under an S3 prefix against the file blocklists.

    Args:
        s3_client: S3 client for the bucket (HeadObject per object)
        max_workers: upper bound of the HeadObject worker pool
        list_objects: ``(bucket, prefix, client=...) -> [{'Key': ...}]``; defaults to
            ``common.s3.list_all_objects`` (pages to exhaustion)
        is_allowed: ``(key, content_type) -> bool``; defaults to
            ``common.s3.validateUnallowedFileExtensionAndContentType``

    Returns:
        True when every object is allowed (or the prefix is empty), False when any object carries
        an unallowed extension or content type

    Raises:
        The underlying botocore error when a HeadObject fails, so a failed check is never read
        as a pass
    """
    list_objects = list_objects or list_all_objects
    is_allowed = is_allowed or validateUnallowedFileExtensionAndContentType

    objects = list_objects(bucket, prefix, client=s3_client)
    if not objects:
        return True

    def _check_one(key):
        head = s3_client.head_object(Bucket=bucket, Key=key)
        return is_allowed(key, head.get('ContentType', ''))

    # One HeadObject per object, fanned out over a bounded worker pool: an asset prefix holds
    # thousands of objects and serial round trips exceed the API Gateway integration timeout.
    # The first disallowed object fails the whole prefix and the remaining queued checks are
    # cancelled.
    pool_size = min(max_workers, len(objects))
    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures = [executor.submit(_check_one, obj['Key']) for obj in objects]
        try:
            for future in as_completed(futures):
                if not future.result():
                    return False
        finally:
            for future in futures:
                future.cancel()
    return True


def check_file_eligibility(database_id: str, asset_id: str, s3_client, asset_bucket: str,
                           asset_base_key: str, raw_key: Optional[str], *,
                           version_id: Optional[str] = None,
                           asset_version_id: Optional[str] = None,
                           asset_version_alias: Optional[str] = None,
                           resolve_file_version: Callable[[str, str, str, str], str] = None,
                           resolve_alias: Callable[[str, str, str], str] = None,
                           max_workers: int = DEFAULT_MAX_PARALLEL_S3_WORKERS,
                           list_objects: Callable = None,
                           is_allowed: Callable[[str, str], bool] = None,
                           object_exists: Callable[[str, str], bool] = None) -> Tuple[str, Optional[str]]:
    """Run every per-file check for one requested key and return what to sign.

    Order (each step refuses before the next runs): key containment and length, asset-version
    pin resolution, then for a prefix (key ending in ``/``) the blocklist over every object and
    the prefix's own existence, or for a single file one HeadObject of the requested version
    covering existence, delete marker and blocklist.

    Args:
        version_id: per-file S3 version (None = latest); replaced by the pin's version when an
            asset-version pin is given
        resolve_file_version / resolve_alias: required when a pin is given (see
            ``resolve_pinned_version_id``)
        max_workers / list_objects / is_allowed: see ``validate_prefix_content_types``
        object_exists: ``(bucket, key) -> bool`` for the prefix's existence check; defaults to
            ``s3_object_exists`` on ``s3_client``

    Returns:
        ``(final_key, version_id)`` - the S3 key and version to sign

    Raises:
        VAMSGeneralErrorResponse: per-file refusal (400, or 410 for a delete-marker version);
            a bulk caller treats it as a soft failure for that key alone
    """
    is_allowed = is_allowed or validateUnallowedFileExtensionAndContentType
    if object_exists is None:
        def object_exists(bucket, key):
            return s3_object_exists(s3_client, bucket, key)

    final_key = resolve_asset_file_key(asset_base_key, raw_key)

    # Resolve the version first: an asset-version pin (whole set) resolves the per-file S3
    # version from the snapshot; otherwise version_id is the per-file version passed in, or
    # None (latest).
    if asset_version_alias or asset_version_id:
        version_id = resolve_pinned_version_id(
            database_id, asset_id, asset_relative_key(final_key, asset_base_key),
            asset_version_id=asset_version_id,
            asset_version_alias=asset_version_alias,
            resolve_file_version=resolve_file_version,
            resolve_alias=resolve_alias)

    if final_key.endswith('/'):
        # Prefix download (asset base location): validate every object under it
        if not validate_prefix_content_types(s3_client, asset_bucket, final_key,
                                             max_workers=max_workers,
                                             list_objects=list_objects,
                                             is_allowed=is_allowed):
            raise VAMSGeneralErrorResponse(MSG_FILE_TYPE_NOT_ALLOWED)
        if not object_exists(asset_bucket, final_key):
            raise VAMSGeneralErrorResponse(MSG_FILE_NOT_FOUND)
    else:
        # Single file: one HeadObject covers existence and the blocklist - critical for bulk
        # requests, where per-key round trips bound the whole request's latency. A requested
        # version is headed itself, so an older version stays downloadable when the latest is a
        # delete marker.
        head_params = {'Bucket': asset_bucket, 'Key': final_key}
        if version_id:
            head_params['VersionId'] = version_id
        try:
            head = s3_client.head_object(**head_params)
        except ClientError as e:
            if e.response['Error']['Code'] in ('404', 'NoSuchKey'):
                raise VAMSGeneralErrorResponse(MSG_FILE_NOT_FOUND)
            if e.response['Error']['Code'] in ('405', 'MethodNotAllowed'):
                # This version is a delete marker
                raise VAMSGeneralErrorResponse(MSG_VERSION_ARCHIVED, status_code=STATUS_VERSION_ARCHIVED)
            logger.exception(f"Error checking file: {e}")
            raise VAMSGeneralErrorResponse(MSG_FILE_CHECK_FAILED)
        if not is_allowed(final_key, head.get('ContentType', '')):
            raise VAMSGeneralErrorResponse(MSG_FILE_TYPE_NOT_ALLOWED)

    return final_key, version_id
