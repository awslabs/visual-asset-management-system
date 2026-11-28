# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The old versions a v2.6 to v2.7 ``move`` conversion left outside the asset, and their permanent delete.

The upgrade step ``assetLocationFileToFolder`` (``infra/deploymentDataMigration/v2.6_to_v2.7/upgrade``)
converts an asset created on a file key ``K`` into a folder asset and records the conversion on the asset
row as ``assetLocationConversion`` (``from`` = ``K``, ``to`` = the new location, ``mode``, ``state``
``pending`` or ``complete``). The record travels with the row when the asset is archived or unarchived.

A ``move`` copies every version of ``K`` and of each of its own previews ``K.previewFile.{ext}`` into the
companion folder, to ``K/{name}`` and ``K/{name}.previewFile.{ext}``, oldest first. Each copy is a relocated
version (``vams-changesource: fileRelocate``) that names its asset (``vams-changeassetidfrom``) and its
source version (``vams-changeassetfileversionfrom``). The step then puts a delete marker on each source key.
The sources lie outside the new location ``K/``, so a delete of the location never reaches them.

``delete_moved_source_versions`` deletes, at the exact source keys, the versions a copy names, and then the
delete markers of a source key left with no version. A version at a source key that no copy names was
written there after the move: it is not the asset's, so it stays (and its key keeps its delete markers), and
the delete logs it. The moved versions are read from the copies' own metadata rather than from a list in the
conversion record: the copies name every moved version whatever their number (a list on the row is bounded
by the 400 KB item limit), and rows converted before this delete existed carry no such list. A delete of the
asset or of the moved file runs this first, while the copies still name their sources, so a retry after a
refused S3 call finds what is left.

Legacy: this module goes with the single-file location code in the next feature release.
"""

from typing import Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from common.assetLocations import contains, normalize_location
from common.s3MetadataKeys import (
    VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_SOURCE_FILE_RELOCATE,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
)
from customLogging.logger import safeLogger

logger = safeLogger()

CONVERSION_ATTRIBUTE = "assetLocationConversion"
CONVERSION_MODE_MOVE = "move"
CONVERSION_STATE_PENDING = "pending"
CONVERSION_STATE_COMPLETE = "complete"

# The answer to a delete of an asset, or of its moved file, while the move is still pending: the copies may be
# incomplete, and the step that finishes them reads the sources the delete would remove.
MOVE_PENDING_REFUSAL = (
    "This asset's file is still being moved into its folder by the v2.6 to v2.7 upgrade step "
    "assetLocationFileToFolder. Run that step again to finish the move, then try again.")

# A head of a copy version that is gone (another delete removed it meanwhile) names no source.
_GONE_ERROR_CODES = frozenset({"404", "NoSuchKey", "NoSuchVersion"})


class MovedVersionsDeleteIncomplete(Exception):
    """An S3 call refused while the old moved versions were read or deleted; what is left stays for a retry."""


def _move_record(asset: Optional[Dict]) -> Optional[Dict]:
    record = (asset or {}).get(CONVERSION_ATTRIBUTE)
    if isinstance(record, dict) and record.get("mode") == CONVERSION_MODE_MOVE:
        return record
    return None


def is_pending_move(asset: Optional[Dict]) -> bool:
    """Whether the asset's ``move`` conversion has started and not finished."""
    record = _move_record(asset)
    return record is not None and record.get("state") == CONVERSION_STATE_PENDING


def completed_move(asset: Optional[Dict]) -> Optional[Tuple[str, str]]:
    """``(file key, location)`` of the asset's finished ``move`` conversion, else None.

    The record must name the file key and its companion folder, and the asset must still be stored there;
    a record that does not is logged and read as no move, so the delete touches nothing outside the asset.
    """
    record = _move_record(asset)
    if record is None or record.get("state") != CONVERSION_STATE_COMPLETE:
        return None
    file_key = normalize_location(record.get("from"))
    target = normalize_location(record.get("to"))
    location = normalize_location(((asset or {}).get("assetLocation") or {}).get("Key"))
    if not file_key or file_key.endswith("/") or target != file_key + "/" or location != target:
        logger.error(f"Asset {(asset or {}).get('assetId')}: its move conversion record does not match its "
                     f"location, so no old version outside the location is deleted")
        return None
    return file_key, target


def moved_file_key(asset: Optional[Dict]) -> Optional[str]:
    """The key of the moved file in its companion folder (``K/{name}``), for a pending or finished move."""
    record = _move_record(asset)
    file_key = normalize_location((record or {}).get("from"))
    if not file_key or file_key.endswith("/"):
        return None
    return f"{file_key}/{file_key.rsplit('/', 1)[-1]}"


def _direct_entries(s3_client, bucket: str, base_key: str) -> Dict[str, Dict[str, List[str]]]:
    """``{key: {'versions': [...], 'markers': [...]}}`` of ``base_key`` and each key that adds no ``/`` to it.

    Listed with ``base_key`` as the prefix and ``/`` as the delimiter, so the keys under ``base_key/`` (the
    asset's whole folder, for a source key) come back as one common prefix and are never read.
    """
    entries: Dict[str, Dict[str, List[str]]] = {}
    kwargs = {"Bucket": bucket, "Prefix": base_key, "Delimiter": "/"}
    while True:
        page = s3_client.list_object_versions(**kwargs)
        for kind, name in (("Versions", "versions"), ("DeleteMarkers", "markers")):
            for entry in page.get(kind, []):
                entries.setdefault(entry["Key"], {"versions": [], "markers": []})[name].append(entry["VersionId"])
        if not page.get("IsTruncated"):
            return entries
        # A page that ends at a common prefix carries a key marker and no version marker
        kwargs["KeyMarker"] = page.get("NextKeyMarker")
        kwargs.pop("VersionIdMarker", None)
        if page.get("NextVersionIdMarker"):
            kwargs["VersionIdMarker"] = page["NextVersionIdMarker"]


def _named_sources(s3_client, bucket: str, copy_key: str, versions: List[str], asset_id: str) -> set:
    """The source version ids the step's copies at ``copy_key`` name (relocated copies of this asset only)."""
    named = set()
    for version_id in versions:
        try:
            head = s3_client.head_object(Bucket=bucket, Key=copy_key, VersionId=version_id)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in _GONE_ERROR_CODES:
                continue
            raise
        metadata = head.get("Metadata") or {}
        if (metadata.get(VAMS_CHANGE_SOURCE_METADATA_KEY) == VAMS_CHANGE_SOURCE_FILE_RELOCATE
                and metadata.get(VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY) == asset_id
                and metadata.get(VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY)):
            named.add(metadata[VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY])
    return named


def delete_moved_source_versions(s3_client, bucket: str, asset: Dict, copy_key: Optional[str] = None) -> List[str]:
    """Delete the versions a finished ``move`` left at the asset's old keys; return the old keys now empty.

    For each source key, ``K`` and each ``K.previewFile.{ext}`` (exact keys: ``contains(K, key)`` without the
    keys under ``K/``, so ``K.bak``, ``Kx/...`` or ``K.previewFile.png/x`` are never read), the versions its
    copy names are deleted, then its delete markers when no version is left. ``copy_key`` limits the delete to
    the moved file's own keys: it is the key a file delete removes, and anything but the moved file
    (``K/{name}``) deletes nothing. An asset with no finished move deletes nothing.

    Raises:
        MovedVersionsDeleteIncomplete: S3 refused a listing, head or delete. Nothing outside the source keys
            was touched, and a retry deletes what is left.
    """
    move = completed_move(asset)
    if move is None:
        return []
    file_key, target = move
    name = file_key.rsplit("/", 1)[-1]
    moved_file = target + name
    if copy_key is not None and copy_key != moved_file:
        return []
    asset_id = asset.get("assetId", "")
    emptied: List[str] = []
    try:
        sources = {key: entry for key, entry in _direct_entries(s3_client, bucket, file_key).items()
                   if contains(file_key, key) and not key.startswith(file_key + "/")}
        if not sources:
            return []
        copies = _direct_entries(s3_client, bucket, moved_file)
        for source, entry in sorted(sources.items()):
            copy = moved_file + source[len(file_key):]
            named = _named_sources(s3_client, bucket, copy, copies.get(copy, {}).get("versions", []), asset_id)
            moved = [version for version in entry["versions"] if version in named]
            for version in moved:
                s3_client.delete_object(Bucket=bucket, Key=source, VersionId=version)
            foreign = len(entry["versions"]) - len(moved)
            if foreign:
                logger.warning(f"Asset {asset_id}: {foreign} version(s) at {source} were not moved by its "
                               f"conversion and stay, with the key's delete markers")
                continue
            for marker in entry["markers"]:
                s3_client.delete_object(Bucket=bucket, Key=source, VersionId=marker)
            emptied.append(source)
            logger.info(f"Asset {asset_id}: deleted {len(moved)} moved version(s) and {len(entry['markers'])} "
                        f"delete marker(s) at {source}")
    except Exception as e:
        code = e.response.get("Error", {}).get("Code") if isinstance(e, ClientError) else type(e).__name__
        logger.error(f"Asset {asset_id}: the old versions its move left outside its location could not all be "
                     f"deleted ({code}); {len(emptied)} old key(s) emptied")
        raise MovedVersionsDeleteIncomplete(str(e)) from e
    return emptied
