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
#   "none"``) terminal paths. No shipped pipeline writes here yet, so the
#   channel is read-ready but has no producer.
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
