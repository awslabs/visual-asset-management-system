# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Canonical definitions of the reserved S3 path prefixes and special file-name
patterns VAMS uses inside asset buckets.

VAMS reserves a set of folder names inside asset buckets for system use
(temporary uploads, pipeline workspaces, preview data). Objects under these
folders are not regular asset files: the indexers (OpenSearch/Garnet), the
bucket-sync reconciler, workflow auto-trigger, and the Physna sync add-on all
skip them. VAMS also marks file-level preview images with a special file-name
pattern (``{baseFile}.previewFile.{ext}``) that those same consumers must
recognize.

This module is the single source of truth for these values. Do not redefine
the literal folder names, the ``.previewFile.`` marker, or the allowed preview
image extensions at call sites -- import them from here so all usages can be
found and changed in one place.

The frontend mirrors the preview-file values in
``web/src/common/constants/fileFormats.ts`` (``PREVIEW_FILE_PATTERN``,
``previewFileFormats``); keep the two in sync when changing them.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, FrozenSet, Iterable, Optional, Tuple

# ---------------------------------------------------------------------------
# Reserved S3 prefix folders.
#
# Folder (path-segment) names reserved for VAMS system use inside asset
# buckets. Any S3 key with one of these as a path segment is system data, not
# an asset file: indexers, bucket sync, workflow auto-trigger, and add-on
# syncs (Garnet, Physna) all exclude such keys. Membership is checked per
# path segment (``part in RESERVED_S3_PREFIX_FOLDERS``), so both singular and
# plural spellings are listed.
# ---------------------------------------------------------------------------
RESERVED_S3_PREFIX_FOLDERS: FrozenSet[str] = frozenset(
    {
        "pipeline",
        "pipelines",
        "preview",
        "previews",
        "temp-upload",
        "temp-uploads",
        "workspace",
        "workspaces",
    }
)

# ---------------------------------------------------------------------------
# Reserved write prefixes.
#
# Concrete key prefixes (with trailing slash) that handlers use when writing
# into the reserved folders above.
#
# TEMPORARY_UPLOAD_PREFIX / PREVIEW_PREFIX: asset-bucket prefixes used by the
#   upload handlers for in-flight multipart uploads and asset-level preview
#   images.
# PIPELINES_PREFIX: prefix where workflow run I/O is staged inside the VAMS
#   default asset bucket -- pipeline outputs under
#   ``pipelines/{pipelineName}/{jobName}/output/{executionId}/...`` and execution
#   input definitions under ``pipelines/workflowExecutionInputs/{executionId}/``.
#   Relative to the area VAMS owns within that bucket (its ``baseAssetsPrefix``,
#   empty for a bucket registered at the root), which
#   ``executionRecords.run_bucket_key`` joins on at each S3 call. The
#   ASSET_PATH_PIPELINE validator requires paths under this prefix, and runs on
#   the relative form.
# AUXILIARY_PREVIEW_PREFIX: auxiliary-bucket subfolder (singular ``preview/``,
#   unlike the asset bucket's plural ``previews/``) where previewFile-type
#   pipelines write non-versioned viewer data under
#   ``{assetFileKey}/preview/{pipelineName}/``.
# ---------------------------------------------------------------------------
TEMPORARY_UPLOAD_PREFIX = "temp-uploads/"
PREVIEW_PREFIX = "previews/"
PIPELINES_PREFIX = "pipelines/"
AUXILIARY_PREVIEW_PREFIX = "preview/"

# ---------------------------------------------------------------------------
# Upload staging keys.
#
# An upload's files are staged under TEMPORARY_UPLOAD_PREFIX and copied to their
# final keys when the upload is completed. Each upload stages in its own folder,
#
#   ``{baseAssetsPrefix}temp-uploads/{upload tag}/{final key}``
#
# so concurrent uploads of one file path never share a staged object, and a
# completion that refuses its staged object deletes only its own upload's file.
# The upload tag is UPLOAD_STAGING_TAG_LENGTH hex characters of the SHA-256 of
# the upload id: a fixed length, and shorter than the upload id itself, because
# the whole staging key must fit the 1,024-byte S3 key limit.
# The folder costs a file key UPLOAD_STAGING_TAG_LENGTH + 1 bytes against that
# limit; together with the prefix, an uploaded file's final key can be at most
# 1024 - len(baseAssetsPrefix) - 26 bytes (998 at the bucket root). The tag only
# has to tell apart uploads of the same path that are completed at the same
# time; with 48 bits, a clash between 100 such uploads is about a 1 in 57
# billion chance. Every staging key is built here; do not assemble one at a
# call site.
# ---------------------------------------------------------------------------
UPLOAD_STAGING_TAG_LENGTH = 12


def upload_staging_tag(upload_id: str) -> str:
    """The folder name an upload's files are staged under: the first UPLOAD_STAGING_TAG_LENGTH hex
    characters of the SHA-256 of ``upload_id``."""
    return hashlib.sha256(upload_id.encode("utf-8")).hexdigest()[:UPLOAD_STAGING_TAG_LENGTH]


def staged_upload_key(base_assets_prefix: str, upload_id: str, final_s3_key: str) -> str:
    """The key upload ``upload_id`` stages the file bound for ``final_s3_key`` under, in the bucket
    whose normalized ``baseAssetsPrefix`` is ``base_assets_prefix``."""
    return f"{base_assets_prefix}{TEMPORARY_UPLOAD_PREFIX}{upload_staging_tag(upload_id)}/{final_s3_key}"


def legacy_staged_upload_key(base_assets_prefix: str, final_s3_key: str) -> str:
    """LEGACY STAGING KEY (#1928): the per-path key every upload staged under before per-upload
    staging. Read only to complete or abort an upload initialized before the upgrade; nothing is
    created under it any more.

    Remove it, and the fallbacks in ``handlers/assets/uploadFile.py`` that use it, once no supported
    upgrade starts from a release that staged per path (2.6.7 and earlier). Until then an upload
    initialized before the upgrade can still be completed through it for as long as its upload
    record is completable: 7 days after initialization (UPLOAD_EXPIRATION_DAYS in uploadFile.py,
    the same age the VAMS asset bucket aborts incomplete multipart uploads at)."""
    return f"{base_assets_prefix}{TEMPORARY_UPLOAD_PREFIX}{final_s3_key}"


# ---------------------------------------------------------------------------
# Auxiliary-bucket top-level prefixes.
#
# AUXILIARY_PREPARED_DOWNLOADS_PREFIX: top-level auxiliary-bucket prefix holding
#   prepared (zip download) archives under
#   ``vams.preparedDownloads/{databaseId}/{assetId}/{userHash}/{requestId}/``. A
#   lifecycle rule expires everything under it after the configured retention,
#   whether or not the feature is enabled. Mirrored as PREPARED_DOWNLOADS_PREFIX in
#   infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts and in the
#   zipArchive pipeline (backendPipelines/utility/zipArchive).
#
# Every other auxiliary object lives under ``{databaseId}/...`` (see
# ``executionRecords.aux_preview_file_prefix``). The dot in the first segment
# keeps the two apart: a database id matches ``^[-_a-zA-Z0-9]{3,63}$``
# (validators.id_pattern), which has no dot, so no database's auxiliary data can
# start with this prefix, and no database id needs to be reserved for it.
# ---------------------------------------------------------------------------
AUXILIARY_PREPARED_DOWNLOADS_PREFIX = "vams.preparedDownloads/"

# ---------------------------------------------------------------------------
# Pipeline staging path segments.
#
# Unlike the prefixes above, these are mid-path segments (leading AND trailing
# slash) within the pipeline staging structure under PIPELINES_PREFIX:
# ``pipelines/{pipelineName}/{jobName}/output/{executionId}/{outputType}/``.
#
# PIPELINE_OUTPUT_PREFIX: separates the pipeline/job identification from the
#   per-execution output folders. The ASSET_PATH_PIPELINE validator requires
#   it exactly once in a pipeline output path.
# PIPELINE_INPUT_PREFIX: reserved for staging per-execution pipeline inputs.
#   Not yet used by workflow generation; defined ahead of a future feature.
# ---------------------------------------------------------------------------
PIPELINE_OUTPUT_PREFIX = "/output/"
PIPELINE_INPUT_PREFIX = "/input/"

# ---------------------------------------------------------------------------
# Pipeline output type segments.
#
# Per-execution output subfolders under
# ``...{PIPELINE_OUTPUT_PREFIX}{executionId}/``, one per output type. The
# workflow ASL points each pipeline's output path variables here, and the
# process-output step reads each location for its output type:
#
# PIPELINE_OUTPUT_FILES_PREFIX: file-level outputs (new asset files and
#   ``.previewFile.X`` thumbnails) -- outputS3AssetFilesPath.
# PIPELINE_OUTPUT_PREVIEWS_PREFIX: asset-level preview images --
#   outputS3AssetPreviewPath.
# PIPELINE_OUTPUT_METADATA_PREFIX: metadata files produced by the pipeline --
#   outputS3AssetMetadataPath.
# PIPELINE_OUTPUT_RESULTS_PREFIX: structured pipeline result data (text a
#   pipeline returns instead of, or alongside, asset files). The workflow ASL
#   threads it as ``outputResultsPrefixRelative`` (next pipeline's manifest) and
#   ``resultsPathKey`` (process-output step), and processWorkflowExecutionOutput
#   reads it for both the normal and the results-only (``outputLocationType:
#   "none"``) terminal paths. Pipelines receive it as ``outputS3AssetResultsPath``
#   from ``manifestHelper.resolve_inputs``.
# ---------------------------------------------------------------------------
PIPELINE_OUTPUT_FILES_PREFIX = "/files/"
PIPELINE_OUTPUT_PREVIEWS_PREFIX = "/previews/"
PIPELINE_OUTPUT_METADATA_PREFIX = "/metadata/"
PIPELINE_OUTPUT_RESULTS_PREFIX = "/results/"

# ---------------------------------------------------------------------------
# Reserved results object.
#
# EXECUTION_STATUS_RESULTS_FILENAME: object name directly under the results
#   prefix through which a pipeline reports a terminal outcome it records rather
#   than raises -- a failure met AFTER its outputs were written, so those outputs
#   still reach the asset. Body:
#   ``{"status": "FAILED", "error": "<code>", "cause": "<text>"}``.
#   The process-output step reads it after write-back on both terminal paths
#   and, when ``status`` is FAILED, records the execution and its end-state
#   pipeline row FAILED with ``executionError = "<error>: <cause>"``. Any other
#   status, or no object, leaves the outcome to the write-back result. The
#   object is additionally recorded as an ordinary results row.
# ---------------------------------------------------------------------------
EXECUTION_STATUS_RESULTS_FILENAME = "execution.status.json"

# ---------------------------------------------------------------------------
# File-level preview files.
#
# PREVIEW_FILE_PATTERN: marker substring identifying a file-level preview
#   file. Preview files are stored next to their base file as
#   ``{baseFile}.previewFile.{ext}`` (e.g. ``model.gltf.previewFile.png``).
#   A key is a preview file iff it CONTAINS this substring.
#
# EXCLUDED_FILE_PATH_PATTERNS: file-name patterns excluded from generic file
#   processing (indexing, sync, workflow auto-trigger). Currently only the
#   preview-file marker. Note: the fileIndexer intentionally does NOT use this
#   list verbatim -- it handles preview files specially (re-indexing the base
#   file instead of skipping outright).
#
# ALLOWED_PREVIEW_FILE_EXTENSIONS: image extensions (lowercase) accepted for
#   preview files, both for the ``.previewFile.{ext}`` suffix and for direct
#   asset-preview uploads. Mirrored in the frontend as ``previewFileFormats``.
# ---------------------------------------------------------------------------
PREVIEW_FILE_PATTERN = ".previewFile."

EXCLUDED_FILE_PATH_PATTERNS: Tuple[str, ...] = (PREVIEW_FILE_PATTERN,)

ALLOWED_PREVIEW_FILE_EXTENSIONS: Tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".webp",
    ".bmp",
)

# The size limit of a preview file, in bytes (5 MiB). Three places use it and must agree:
#   - the upload handlers reject a preview file LARGER than this;
#   - the web shows a file as its own preview only when its size is STRICTLY UNDER this
#     (``isSelfPreviewable`` in web/src/common/constants/fileFormats.ts);
#   - the media preview pipeline writes only previews STRICTLY UNDER this and writes none for an image
#     the web already shows as its own preview (backendPipelines/preview/3dThumbnail).
# A drift test reads the web constant, and another reads the container's.
PREVIEW_FILE_SIZE_LIMIT_BYTES: int = 5 * 1024 * 1024


def is_self_previewable(file_name: str, size: Optional[int]) -> bool:
    """True when the web shows ``file_name`` as its own preview: its extension is an allowed
    preview extension and its size is known and strictly under PREVIEW_FILE_SIZE_LIMIT_BYTES.

    Mirrors ``isSelfPreviewable`` in web/src/common/constants/fileFormats.ts. A file with an unknown
    size is not self-previewable.
    """
    if size is None or isinstance(size, bool) or not isinstance(size, (int, float)):
        return False
    dot = file_name.rfind(".")
    if dot < 0:
        return False
    return file_name[dot:].lower() in ALLOWED_PREVIEW_FILE_EXTENSIONS and size < PREVIEW_FILE_SIZE_LIMIT_BYTES


# ---------------------------------------------------------------------------
# Choosing a base file's preview.
#
# A file can hold several ``.previewFile.<ext>`` objects: a pipeline run with
# another output format writes ``<file>.previewFile.png`` beside an existing
# ``<file>.previewFile.gif``, and neither the run nor process-output removes the
# other format. Every consumer that attaches one preview to a base file (the
# file listings and the asset export) picks it with select_newest_preview_file,
# so they agree on which preview is current.
# ---------------------------------------------------------------------------
def preview_file_has_allowed_extension(file_key: str) -> bool:
    """True when the text after ``.previewFile.`` is an allowed preview image extension."""
    if PREVIEW_FILE_PATTERN not in file_key:
        return False
    extension = "." + file_key.split(PREVIEW_FILE_PATTERN)[1]
    return extension.lower() in ALLOWED_PREVIEW_FILE_EXTENSIONS


def preview_modified_timestamp(value: Any) -> float:
    """Sort key for a preview candidate's last-modified value: a datetime or ISO 8601 text, read
    as UTC when it carries no offset. A missing or unreadable value sorts as the oldest."""
    try:
        if isinstance(value, str):
            text = value.strip()
            value = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()
    except (ValueError, OverflowError, OSError):
        pass
    return float("-inf")


def select_newest_preview_file(candidates: Iterable[Tuple[str, Any, bool]]) -> Optional[str]:
    """The preview to attach to one base file, from its ``(key, lastModified, isArchived)`` candidates.

    A live preview beats an archived one and, among those, the latest ``lastModified`` wins; equal
    times keep the first in key order. Candidates without an allowed extension are ignored.
    """
    best_key, best_rank = None, None
    for key, modified, archived in sorted(candidates, key=lambda candidate: candidate[0]):
        if not preview_file_has_allowed_extension(key):
            continue
        rank = (not archived, preview_modified_timestamp(modified))
        if best_rank is None or rank > best_rank:
            best_key, best_rank = key, rank
    return best_key


# ---------------------------------------------------------------------------
# Reserved-segment test.
#
# The single implementation of "is this key VAMS system data". Consumers used to
# spell this three different ways and one of them was wrong in a way no test
# caught, so it lives here instead:
#
#   fileIndexer / workflowTriggerDispatch  every path segment  (correct)
#   sqsBucketSync                          the FIRST segment after the
#                                          configured bucket prefix  (too narrow)
#
# Two ways the narrow form misses system data, both reachable once a bucket is
# registered with a non-root ``baseAssetsPrefix``:
#
#   1. A reserved segment DEEPER than the first. Pipeline run I/O is written
#      under the bucket's own VAMS area, so with a prefix of ``myprefix/`` the
#      key is ``myprefix/pipelines/...`` -- fine -- but any shape that puts a
#      reserved folder below an asset id (``myprefix/{assetId}/preview/...``)
#      reads as an ordinary asset file to a first-segment test.
#   2. A key that does not START with the configured prefix. The first-segment
#      test resolves nothing at all there, and bucket sync then treats the
#      object as "asset id unresolvable" rather than as system data -- which
#      still writes file metadata and history and, critically, re-stamps the
#      object's S3 metadata by copying it onto itself.
#
# That last point is why this is not cosmetic: the self-copy re-enters the same
# ``s3:ObjectCreated:*`` notification that triggered the handler, so treating
# system data as an asset file adds an S3 write, another event, and another hop
# of the Lambda lineage depth that AWS's recursive-loop detection counts.
#
# Both forms are checked -- the raw key AND the prefix-stripped remainder -- so
# the answer does not depend on whether the caller's prefix happens to line up
# with the key. Prefix-relative keys (already stripped by the caller) and
# absolute bucket keys therefore give the same answer.
# ---------------------------------------------------------------------------
def key_has_reserved_segment(object_key: str, prefix: str = "", reserved=None) -> bool:
    """True when any path segment of the key names a reserved VAMS system folder.

    Args:
        object_key: The S3 object key, with or without the bucket's VAMS prefix.
        prefix: The bucket's ``baseAssetsPrefix``, if any. Optional: the raw key
            is always checked too, so omitting it never makes the test weaker.
        reserved: The reserved folder names to test against. Defaults to
            ``RESERVED_S3_PREFIX_FOLDERS``. Injectable because callers hold their
            own module-level reference that their tests substitute -- reading the
            module global unconditionally would silently make those patches inert
            and leave the tests asserting nothing.

    Returns:
        bool: True if the key is VAMS system data and must not be treated as an
        asset file.
    """
    if not object_key:
        return False
    reserved_names = RESERVED_S3_PREFIX_FOLDERS if reserved is None else reserved

    candidates = [object_key]
    normalized_prefix = (prefix or "").strip("/")
    if normalized_prefix:
        prefix_with_slash = normalized_prefix + "/"
        stripped = object_key.lstrip("/")
        if stripped.startswith(prefix_with_slash):
            candidates.append(stripped[len(prefix_with_slash):])

    for candidate in candidates:
        for part in candidate.split("/"):
            if part in reserved_names:
                return True
    return False


# ---------------------------------------------------------------------------
# Asset location key + asset-relative path.
#
# An asset's stored ``assetLocation.Key`` names its folder. Derived and migrated
# locations end with ``/``; a location supplied as ``bucketExistingKey`` may
# not. Plain concatenation then runs the folder name into the file path
# (``projects/building-a`` + ``sub/m.glb`` -> ``projects/building-asub/m.glb``),
# so every key built from a stored location goes through this join.
# ---------------------------------------------------------------------------
def join_asset_location_key(location_key: str, relative_path: str) -> str:
    """The S3 key of an asset-relative file path under an asset's stored location key.

    Args:
        location_key: The asset's ``assetLocation.Key``, with or without its
            trailing ``/``.
        relative_path: The asset-relative file path, with or without its leading ``/``.

    Returns:
        str: The key ``common.assetLocations.object_key_for`` names: in a folder location,
        the folder and the path joined by exactly one ``/``; in a single-file location, the
        object itself for its own file name, an own preview, or an entry of its companion
        folder ``{file key}/`` for any other path. ``/`` in a single-file location names the
        asset as a whole and yields the object's key. An empty location key yields the path
        without its leading ``/``.
    """
    # Imported here: assetLocations imports this module at load.
    from common.assetLocations import normalize_location, object_key_for

    if not normalize_location(location_key):
        return (relative_path or "").lstrip("/")
    key = object_key_for(location_key, relative_path)
    return key if key is not None else normalize_location(location_key)
