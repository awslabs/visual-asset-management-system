# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Template-tag replacement for per-pipeline input configuration + selected execution fields.

A pipeline's input configuration (today the ``inputParameters`` JSON string; later the upgraded
pipeline input-configuration field) may contain ``{{tagName}}`` template tags that are replaced,
per pipeline task, with values drawn from that task's resolved manifest + execution context. This
lets a pipeline ship a ready-made configuration file with placeholders instead of reconstructing it
field-by-field in its ``vamsExecute`` lambda.

Rendering runs on the execution side (``executeWorkflow`` at launch for pipeline 1, the interim
tracking lambda for pipelines 2+), so each task's tags reflect ITS manifest (with shadowed inputs).
The pipeline receives an already-rendered configuration file; it never renders itself.

Design:
  - **Format-agnostic text substitution.** The renderer operates on the raw configuration TEXT, so
    it works no matter the configuration format (JSON today; YAML / OpenJD later).
  - **Two substitution kinds.** A ``scalar`` tag substitutes a JSON-string-escaped bare value meant
    to sit inside existing quotes (``"databaseId": "{{firstAssetFileDatabaseId}}"``); a ``json`` tag
    substitutes a JSON literal (object / array / number) meant to sit WITHOUT surrounding quotes
    (``"files": {{assetFileKeyArray}}``). Each tag's kind is fixed and documented.
  - **Strict.** An unknown ``{{tag}}`` (one not in the catalog below) raises
    ``MissingTemplateTagError`` rather than being left in place or blanked. This surfaces typos and
    reserves the space for the future dynamic tags (``{{metadata_<key>}}`` and user-defined
    per-pipeline tags) — which are NOT implemented yet and therefore error today.
  - **Empty-not-error for absent sources.** A defined tag whose underlying value is absent (e.g.
    ``{{firstAssetFileAssetId}}`` when the manifest carries no input files) resolves to an empty
    string / ``[]`` / ``0`` rather than raising, so no-input-files executions render cleanly.
  - **Metadata content is loaded lazily.** Metadata-content tags (``{{inputMetadataObject}}`` etc.)
    trigger a single metadata read only when such a tag is actually present in the text.
"""

import json
import os
import re
from datetime import datetime, timezone

from common.workflows import templateTags as tags

# A template tag: {{ tagName }} — the name is alphanumeric + underscore. Whitespace inside the
# braces is tolerated. This is intentionally strict so it does not accidentally match JSON/Jinja
# constructs that are not VAMS tags.
_TAG_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class MissingTemplateTagError(Exception):
    """Raised when the configuration text uses a ``{{tag}}`` that is not in the catalog.

    Carries the sorted list of unknown tag names so the caller can surface exactly which tags are
    undefined."""

    def __init__(self, unknown_tags):
        self.unknown_tags = sorted(set(unknown_tags))
        super().__init__(
            "Unknown template tag(s) in input configuration: "
            + ", ".join("{{" + t + "}}" for t in self.unknown_tags)
            + ". Only the documented VAMS template tags are supported "
            "(dynamic {{metadata_<key>}} and user-defined per-pipeline tags are not yet available)."
        )


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def _s(value):
    """A scalar tag value: coerce to string, empty string for None."""
    return "" if value is None else str(value)


def _join_s3(bucket, key):
    """Reconstruct ``s3://bucket/key`` from a bucket + bucket-relative key; ``""`` when no bucket."""
    if not bucket:
        return ""
    return f"s3://{bucket}/{key or ''}"


def _file_name(key):
    """Basename of an S3 key (``xid/test/pump.e57`` -> ``pump.e57``); ``""`` when empty."""
    return (key or "").rstrip("/").split("/")[-1]


def _split_ext(key):
    """(stem, ext) of a key's basename: ``pump.e57`` -> (``pump``, ``.e57``)."""
    name = _file_name(key)
    stem, ext = os.path.splitext(name)
    return stem, ext


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------

# Metadata-content tag names -> path into the metadata payload (VAMS envelope). A None path means
# the whole payload. These are the only tags that require a metadata read.
_METADATA_TAGS = {
    tags.INPUT_METADATA_OBJECT: None,
    tags.ASSET_METADATA_OBJECT: ("VAMS", "assetMetadata"),
    tags.FILE_METADATA_OBJECT: ("VAMS", "fileMetadata"),
    tags.FILE_ATTRIBUTES_OBJECT: ("VAMS", "fileAttributes"),
    tags.ASSET_DATA_OBJECT: ("VAMS", "assetData"),
}


def render_timestamps(now=None):
    """The job-start timestamp trio, computed at render time. Exposed so callers can build a
    consistent context; ``now`` may be injected for testing."""
    now = now or datetime.now(timezone.utc)
    return {
        "jobStartTimestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jobStartTimestampUnix": str(int(now.timestamp())),
        "jobStartDate": now.strftime("%Y-%m-%d"),
    }


def build_template_context(manifest, execution, now=None):
    """Build the base (non-metadata) template context: ``{tagName: (kind, value)}`` where kind is
    ``"scalar"`` or ``"json"``.

    ``manifest`` is the per-pipeline manifest envelope for THIS task (see executionRecords.
    build_manifest_envelope). ``execution`` is a dict of the pure-execution scalars the manifest
    does not carry: executionId, workflowId, workflowDatabaseId, pipelineExecutionId, pipelineId,
    pipelineDatabaseId, jobName, triggerType, executingUserName, executionStartTimestamp. Any
    missing key resolves to an empty string (no-input / partial-context safe)."""
    manifest = manifest or {}
    execution = execution or {}

    input_files = manifest.get("inputFiles") or []
    first = input_files[0] if input_files else {}

    outputs = manifest.get("outputs") or {}
    output_bucket = outputs.get("bucket", "")
    output_target = manifest.get("outputTarget") or {}
    aux_bucket = manifest.get("auxBucket", "")
    aux_temp_prefix = manifest.get("auxTempPrefix", "")
    aux_preview_suffix = manifest.get("auxPreviewPipelineSuffix", "") or ""
    system_config = manifest.get("systemConfig") or {}

    # First-input-file reconstructed locations (empty string throughout when there are no inputs).
    first_key = first.get("key", "")
    first_bucket = first.get("bucket", "")
    first_aux_preview_prefix = first.get("auxPreviewPrefix", "")
    first_aux_preview_key = first_aux_preview_prefix.rstrip("/") if first_aux_preview_prefix else ""
    if first_aux_preview_key and aux_preview_suffix:
        first_aux_preview_key = f"{first_aux_preview_key}/{aux_preview_suffix.strip('/')}"
    first_stem, first_ext = _split_ext(first_key)

    # Input-file collections.
    key_array = [f.get("key", "") for f in input_files]
    rel_array = [f.get("relativePath", "") for f in input_files]
    s3uri_array = [_join_s3(f.get("bucket", ""), f.get("key", "")) for f in input_files]
    version_array = [f.get("versionId", "") for f in input_files]
    asset_id_array = [f.get("assetId", "") for f in input_files]
    database_id_array = [f.get("databaseId", "") for f in input_files]

    def _unique(seq):
        seen, out = set(), []
        for v in seq:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    context = {}

    def scalar(name, value):
        context[name] = ("scalar", _s(value))

    def as_json(name, value):
        context[name] = ("json", value)

    # --- A. Execution & workflow identity ---
    scalar(tags.EXECUTION_ID, execution.get("executionId", ""))
    scalar(tags.WORKFLOW_ID, execution.get("workflowId", ""))
    scalar(tags.WORKFLOW_DATABASE_ID, execution.get("workflowDatabaseId", ""))
    scalar(tags.TRIGGER_TYPE, execution.get("triggerType", ""))
    scalar(tags.EXECUTING_USER_NAME, execution.get("executingUserName", ""))

    # --- B. Pipeline-task identity ---
    scalar(tags.PIPELINE_EXECUTION_ID, execution.get("pipelineExecutionId", ""))
    scalar(tags.PIPELINE_ID, execution.get("pipelineId", ""))
    scalar(tags.PIPELINE_NAME, execution.get("pipelineId", ""))
    scalar(tags.PIPELINE_DATABASE_ID, execution.get("pipelineDatabaseId", ""))
    scalar(tags.JOB_NAME, execution.get("jobName", ""))

    # --- C. Timestamps ---
    ts = render_timestamps(now)
    scalar(tags.JOB_START_TIMESTAMP, ts["jobStartTimestamp"])
    scalar(tags.JOB_START_TIMESTAMP_UNIX, ts["jobStartTimestampUnix"])
    scalar(tags.JOB_START_DATE, ts["jobStartDate"])
    scalar(tags.EXECUTION_START_TIMESTAMP, execution.get("executionStartTimestamp", ""))

    # --- D. First input file (empty strings when no inputs) ---
    scalar(tags.FIRST_ASSET_FILE_DATABASE_ID, first.get("databaseId", ""))
    scalar(tags.FIRST_ASSET_FILE_ASSET_ID, first.get("assetId", ""))
    scalar(tags.FIRST_ASSET_FILE_ASSET_BUCKET, first_bucket)
    scalar(tags.FIRST_ASSET_FILE_ASSET_ROOT_S3_KEY, first.get("assetRootS3Key", ""))
    scalar(tags.FIRST_ASSET_FILE_RELATIVE_PATH, first.get("relativePath", ""))
    scalar(tags.FIRST_ASSET_FILE_KEY, first_key)
    scalar(tags.FIRST_ASSET_FILE_VERSION_ID, first.get("versionId", ""))
    scalar(tags.FIRST_ASSET_FILE_AUX_PREVIEW_PREFIX, first_aux_preview_prefix)
    scalar(tags.FIRST_ASSET_FILE_S3_URI, _join_s3(first_bucket, first_key))
    scalar(tags.FIRST_ASSET_FILE_AUX_PREVIEW_S3_URI, _join_s3(aux_bucket, first_aux_preview_key))
    scalar(tags.FIRST_ASSET_FILE_FILE_NAME, _file_name(first_key))
    scalar(tags.FIRST_ASSET_FILE_FILE_NAME_NO_EXT, first_stem)
    scalar(tags.FIRST_ASSET_FILE_FILE_EXTENSION, first_ext)

    # --- E. Input-file collections (JSON literals; count is a bare number) ---
    as_json(tags.ASSET_FILE_KEY_ARRAY, key_array)
    as_json(tags.ASSET_FILE_RELATIVE_PATH_ARRAY, rel_array)
    as_json(tags.ASSET_FILE_S3_URI_ARRAY, s3uri_array)
    as_json(tags.ASSET_FILE_VERSION_ID_ARRAY, version_array)
    as_json(tags.ASSET_FILE_OBJECT_ARRAY, input_files)
    as_json(tags.ASSET_FILE_ASSET_ID_ARRAY, asset_id_array)
    as_json(tags.ASSET_FILE_UNIQUE_ASSET_ID_ARRAY, _unique(asset_id_array))
    as_json(tags.ASSET_FILE_DATABASE_ID_ARRAY, database_id_array)
    as_json(tags.ASSET_FILE_UNIQUE_DATABASE_ID_ARRAY, _unique(database_id_array))
    as_json(tags.ASSET_FILE_COUNT, len(input_files))

    # --- F. Output locations ---
    scalar(tags.OUTPUT_BUCKET, output_bucket)
    scalar(tags.OUTPUT_FILES_PREFIX, outputs.get("files", ""))
    scalar(tags.OUTPUT_FILES_S3_URI, _join_s3(output_bucket, outputs.get("files", "")))
    scalar(tags.OUTPUT_PREVIEWS_PREFIX, outputs.get("previews", ""))
    scalar(tags.OUTPUT_PREVIEWS_S3_URI, _join_s3(output_bucket, outputs.get("previews", "")))
    scalar(tags.OUTPUT_METADATA_PREFIX, outputs.get("metadata", ""))
    scalar(tags.OUTPUT_METADATA_S3_URI, _join_s3(output_bucket, outputs.get("metadata", "")))
    scalar(tags.OUTPUT_RESULTS_PREFIX, outputs.get("results", ""))
    scalar(tags.OUTPUT_RESULTS_S3_URI, _join_s3(output_bucket, outputs.get("results", "")))
    scalar(tags.OUTPUT_TARGET_ASSET_ID, output_target.get("assetId", ""))
    scalar(tags.OUTPUT_TARGET_DATABASE_ID, output_target.get("databaseId", ""))
    scalar(tags.OUTPUT_TARGET_LOCATION_TYPE, output_target.get("locationType", ""))
    scalar(tags.OUTPUT_TARGET_ASSET_ROOT_S3_KEY, output_target.get("assetRootS3Key", ""))
    scalar(tags.OUTPUT_FILE_BASE_EXECUTION_PATH_EXTENSION,
           output_target.get("fileBaseExecutionPathExtension", "/"))

    # --- G. Auxiliary locations ---
    scalar(tags.AUX_BUCKET, aux_bucket)
    scalar(tags.AUX_TEMP_PREFIX, aux_temp_prefix)
    scalar(tags.AUX_TEMP_S3_URI, _join_s3(aux_bucket, aux_temp_prefix))
    scalar(tags.AUX_PREVIEW_PIPELINE_SUFFIX, aux_preview_suffix)

    # --- H. Metadata / configuration locations ---
    scalar(tags.INPUT_METADATA_S3_LOCATION, manifest.get("inputMetadataS3Location", ""))
    scalar(tags.INPUT_CONFIGURATION_S3_LOCATION, execution.get("inputConfigurationS3Location", ""))

    # --- I. System / orchestration ---
    scalar(tags.ORCHESTRATION_BUS_ARN, system_config.get("orchestrationBusArn", ""))
    scalar(tags.ORCHESTRATION_EVENT_PREFIX, system_config.get("orchestrationEventPrefix", ""))

    # --- K. Deadline Cloud (empty until the pipeline configuration supplies them) ---
    # Sourced from the execution context when present (a future pipeline-configuration overhaul
    # will populate them); default to empty so a Deadline OpenJD template referencing them renders
    # today without tripping the strict unknown-tag check.
    for deadline_tag in tags.DEADLINE_TAGS:
        scalar(deadline_tag, execution.get(deadline_tag, ""))

    return context


def _metadata_context(metadata_payload):
    """Build the metadata-content tag context from a loaded metadata payload dict. Each tag is a
    ``json`` (object) substitution; a missing path resolves to an empty object."""
    payload = metadata_payload if isinstance(metadata_payload, dict) else {}
    context = {}
    for tag, path in _METADATA_TAGS.items():
        if path is None:
            value = payload
        else:
            value = payload
            for seg in path:
                value = value.get(seg, {}) if isinstance(value, dict) else {}
        context[tag] = ("json", value if value is not None else {})
    return context


def _substitute(text, context):
    """Replace every ``{{tag}}`` in ``text`` using ``context`` ({tag: (kind, value)}). Raises
    MissingTemplateTagError listing any tags not in the context. Scalars are JSON-string-escaped
    (surrounding quotes stripped, so they sit inside the template's own quotes); json values are
    emitted as JSON literals."""
    found = set(_TAG_PATTERN.findall(text))
    unknown = found - set(context.keys())
    if unknown:
        raise MissingTemplateTagError(unknown)

    def _replace(match):
        name = match.group(1)
        kind, value = context[name]
        if kind == "json":
            return json.dumps(value)
        # scalar: JSON-escape the string body without the surrounding quotes so it is safe to sit
        # inside the template's existing quotes (handles embedded quotes/backslashes/control chars).
        return json.dumps(_s(value))[1:-1]

    return _TAG_PATTERN.sub(_replace, text)


def uses_template_tags(text):
    """True if the text contains at least one ``{{tag}}``."""
    return bool(text) and bool(_TAG_PATTERN.search(text))


def render_config(text, manifest, execution, metadata_loader=None, now=None):
    """Render an input-configuration text (or any templated field) against a task's manifest +
    execution context. Returns the rendered text unchanged when it contains no tags.

    ``metadata_loader`` is an optional zero-arg callable returning the metadata payload dict; it is
    invoked at most once, and only when a metadata-content tag is actually present (lazy read).
    Raises MissingTemplateTagError on an unknown tag (strict)."""
    if not uses_template_tags(text):
        return text

    context = build_template_context(manifest, execution, now=now)

    # Only load metadata when a metadata-content tag is present.
    if any(tag in text for tag in _METADATA_TAGS):
        payload = metadata_loader() if callable(metadata_loader) else {}
        context.update(_metadata_context(payload))
    else:
        # Metadata tags are still DEFINED (empty) so their absence-from-text is the only reason
        # they are not populated; they never trip the strict unknown-tag check.
        context.update(_metadata_context({}))

    return _substitute(text, context)
