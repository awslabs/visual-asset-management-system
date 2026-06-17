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

from typing import FrozenSet, Tuple

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
# PIPELINES_PREFIX: asset-bucket prefix where workflow pipeline outputs are
#   staged (``pipelines/{pipelineName}/{jobName}/output/{executionId}/...``).
#   The ASSET_PATH_PIPELINE validator requires paths under this prefix.
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
# PIPELINE_OUTPUT_RESULTS_PREFIX: reserved for structured pipeline result
#   data. Not yet used by workflow generation; defined ahead of a future
#   feature.
# ---------------------------------------------------------------------------
PIPELINE_OUTPUT_FILES_PREFIX = "/files/"
PIPELINE_OUTPUT_PREVIEWS_PREFIX = "/previews/"
PIPELINE_OUTPUT_METADATA_PREFIX = "/metadata/"
PIPELINE_OUTPUT_RESULTS_PREFIX = "/results/"

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

ALLOWED_PREVIEW_FILE_EXTENSIONS: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".svg", ".gif")
