# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure helpers for the workflow-execution storage data model.

This module has NO AWS or environment dependencies so it can be imported and
unit-tested in isolation. It centralizes:
  - clean composite-key construction (no legacy '$' prefix)
  - ISO-8601 UTC timestamps
  - per-pipeline S3 prefix derivation (matching the workflow ASL output paths)
  - record-dict builders for each execution storage table
  - text parsing/truncation for results/logs within DynamoDB item limits
"""

import json
import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal

# DynamoDB rejects an item over 400 KB, so every variable-size field of one item — free-form text
# bodies, tag lists, config snapshots, entity lists — shares ONE byte budget, with a reserve left
# for the item's keys and fixed-size attributes.
MAX_ITEM_BYTES = 400 * 1024
ITEM_FIXED_FIELD_RESERVE_BYTES = 20 * 1024
MAX_TEXT_FIELD_BYTES = MAX_ITEM_BYTES - ITEM_FIXED_FIELD_RESERVE_BYTES
MAX_LOG_FIELD_BYTES = 390 * 1024
# Ceiling on the variable-size COLLECTIONS of one item (tag lists, source-entity lists, step
# snapshots), taken before the text bodies so a large collection shortens the bodies — which carry a
# truncation flag and a full copy in S3 — instead of overflowing the item. Sized to the sum of the
# request-side caps that feed the largest such group, one pipeline's tag list plus its two config
# blocks (models.executions.MAX_TEMPLATE_TAGS_TOTAL_LENGTH + 2 x
# models.pipelines.MAX_CONFIG_BLOCK_BYTES), so a request those models accepted is stored whole.
MAX_ITEM_COLLECTION_BYTES = 256 * 1024

# Schema versions stamped on the VAMS-authored manifest and metadata files.
MANIFEST_SCHEMA_VERSION = 1
# v1: flat {schemaVersion, metadata} envelope (build_metadata_envelope). v2: grouped-by-asset
# envelope (build_grouped_metadata_envelope) for multi-file execution.
METADATA_SCHEMA_VERSION = 1
METADATA_SCHEMA_VERSION_GROUPED = 2

# Constant PK for the by-date global-list GSI (newest-first query, not a table scan).
ALL_EXECUTIONS_LIST_PARTITION = "execution"

# The value a systemConfig's metadataInputs map carries for a key it OMITS. A stored map may be
# partial two ways: a record written before a key existed cannot carry it, and the API stores
# systemConfig wholesale, so a client that sends only the keys it cares about persists exactly those.
# Every reader resolves an omitted key through this table rather than through plain truthiness, which
# would read a partial map as opting OUT of everything it does not mention.
#
# All four default ON, matching build_pipeline_system_config / build_workflow_system_config: a
# configuration that says nothing about a metadata type gets it. Add a new key here in the same change
# that adds it to the builders, and the readers stay correct for every record written before it.
METADATA_INPUT_DEFAULTS = {
    "assetMetadata": True,
    "fileMetadata": True,
    "fileAttributes": True,
    "databaseMetadata": True,
}


def metadata_input_enabled(metadata_inputs, key) -> bool:
    """One metadataInputs toggle, resolving a key the map omits to its builder default."""
    return bool((metadata_inputs or {}).get(key, METADATA_INPUT_DEFAULTS.get(key, True)))


def new_guid() -> str:
    """Generate a VAMS execution/pipeline-execution GUID (32 hex chars)."""
    return uuid.uuid4().hex


def iso_now() -> str:
    """Current UTC time as ISO-8601 with a trailing Z and no microseconds."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_seconds_since(iso_timestamp: str) -> float:
    """Seconds elapsed between an ISO-8601 'YYYY-MM-DDTHH:MM:SSZ' timestamp and now.

    Returns a very large number for an empty/unparseable timestamp so callers treat
    it as 'stale enough to refresh'. Used to throttle Step Functions polling.
    """
    if not iso_timestamp:
        return float("inf")
    try:
        dt = datetime.strptime(iso_timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def normalize_file_key(file_key: str) -> str:
    """Return an asset-relative key with exactly one leading slash."""
    if not file_key:
        return "/"
    return "/" + file_key.lstrip("/")


def workflow_composite_key(workflow_database_id: str, workflow_id: str) -> str:
    """Clean 'workflowDatabaseId:workflowId' (no legacy '$' prefix)."""
    return f"{workflow_database_id}:{workflow_id}"


def pipeline_composite_key(pipeline_database_id: str, pipeline_id: str) -> str:
    """Clean 'pipelineDatabaseId:pipelineId'."""
    return f"{pipeline_database_id}:{pipeline_id}"


def input_file_composite_key(database_id: str, asset_id: str, file_key: str) -> str:
    """Clean 'databaseId:assetId:/normalizedFileKey'."""
    return f"{database_id}:{asset_id}:{normalize_file_key(file_key)}"


def orchestration_event_prefix(event_source_prefix: str, execution_id: str,
                               pipeline_execution_id: str) -> str:
    """Per-execution+pipeline EventBridge source prefix a pipeline reports sub-process ARNs
    under: '<eventSourcePrefix>.execution.<executionId>.pipeline.<pipelineExecutionId>'."""
    return f"{event_source_prefix}.execution.{execution_id}.pipeline.{pipeline_execution_id}"


# Reserved S3 prefix literals (mirror common/s3PathPatterns.py; duplicated here
# as plain strings to keep this module dependency-free).
_PIPELINES_PREFIX = "pipelines/"
_AUXILIARY_PREVIEW_PREFIX = "preview/"
_PIPELINE_OUTPUT_SEGMENT = "output"
_PIPELINE_OUTPUT_FILES_SEGMENT = "files"
_PIPELINE_OUTPUT_PREVIEWS_SEGMENT = "previews"
_PIPELINE_OUTPUT_METADATA_SEGMENT = "metadata"
_PIPELINE_OUTPUT_RESULTS_SEGMENT = "results"


def pipeline_output_prefixes(first_pipeline_name: str, first_job_name: str, execution_id: str) -> dict:
    """Concrete per-execution output prefixes for the first pipeline's global
    output location, matching the workflow ASL output paths.
    Returns a dict with keys: files, previews, metadata, results.
    """
    base = (f"{_PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}/"
            f"{_PIPELINE_OUTPUT_SEGMENT}/{execution_id}/")
    return {
        "files": base + _PIPELINE_OUTPUT_FILES_SEGMENT + "/",
        "previews": base + _PIPELINE_OUTPUT_PREVIEWS_SEGMENT + "/",
        "metadata": base + _PIPELINE_OUTPUT_METADATA_SEGMENT + "/",
        "results": base + _PIPELINE_OUTPUT_RESULTS_SEGMENT + "/",
    }


def aux_pipeline_prefix(pipeline_name: str, execution_id: str) -> str:
    """Auxiliary-bucket temporary working prefix for a pipeline execution, relative to the aux
    bucket (no scheme, no bucket): 'pipelines/{pipelineName}/{executionId}/'. Scoped to the
    execution so concurrent runs of the same pipeline cannot collide on working files."""
    return f"{_PIPELINES_PREFIX}{pipeline_name}/{execution_id}/"


def aux_preview_file_prefix(database_id: str, asset_file_key: str) -> str:
    """Per-input-file auxiliary-bucket preview prefix, relative to the aux bucket (no scheme, no
    bucket, no trailing slash): '{databaseId}/{assetFileKey}/preview'.

    The asset file key is the FULL asset-bucket key (asset location key + relative file path), so
    any custom asset base prefix carried by the asset location key is preserved rather than assuming
    the key is prefixed by the asset id. Every input file gets its own unique aux preview location
    regardless of pipeline type. A pipeline that writes preview/viewer data appends the manifest's
    auxPreviewPipelineSuffix (e.g. '/PotreeViewer') to target a viewer-specific subfolder here."""
    fk = (asset_file_key or "").strip("/")
    base = database_id or ""
    preview_segment = _AUXILIARY_PREVIEW_PREFIX.rstrip("/")
    if fk:
        return f"{base}/{fk}/{preview_segment}"
    return f"{base}/{preview_segment}"


# Per-execution input-definition folder (asset bucket): the shared input metadata file plus
# each pipeline's config + resolved manifest. Keyed only on the execution id so executeWorkflow
# and the ASL compute identical keys (both independently draw job-name uuids).
_EXECUTION_INPUTS_SEGMENT = "workflowExecutionInputs"


def execution_input_prefix(execution_id: str) -> str:
    """Per-execution input-definition folder (asset-bucket relative). Trailing slash."""
    return f"{_PIPELINES_PREFIX}{_EXECUTION_INPUTS_SEGMENT}/{execution_id}/"


def execution_input_metadata_key(execution_id: str) -> str:
    """Asset-bucket key of the shared input-metadata file for an execution."""
    return execution_input_prefix(execution_id) + "metadata.json"


def pipeline_input_metadata_key(execution_id: str, pipeline_index: int) -> str:
    """Asset-bucket key of a pipeline's OWN narrowed input-metadata file.

    Written only when a step's effective metadataInputs is narrower than the workflow's, so the
    common case (every step wanting what the workflow gathered) stays a single shared object. The
    step's manifest points at whichever file applies, and manifestHelper.resolve_inputs takes the
    metadata location from the manifest — so the pipeline reads its own file with no reader change."""
    return f"{execution_input_prefix(execution_id)}pipeline{pipeline_index}/metadata.json"


def pipeline_input_config_key(execution_id: str, pipeline_index: int) -> str:
    """Asset-bucket key of a pipeline's input configuration file."""
    return f"{execution_input_prefix(execution_id)}pipeline{pipeline_index}/config.json"


def pipeline_input_manifest_key(execution_id: str, pipeline_index: int) -> str:
    """Asset-bucket key of a pipeline's resolved input manifest file."""
    return f"{execution_input_prefix(execution_id)}pipeline{pipeline_index}/manifest.json"


def build_manifest_entry(relative_path: str, bucket: str, key: str, version_id: str = "",
                         database_id: str = "", asset_id: str = "",
                         asset_root_s3_key: str = "", aux_preview_prefix: str = "") -> dict:
    """One self-locating input-manifest entry: an asset-relative path mapped to the S3
    location (bucket/key/versionId) and asset identity a pipeline reads for that path.

    Locations are carried as relative keys plus the file's own bucket (never a pre-built
    s3:// URI): `assetRootS3Key` is this file's asset-root prefix within `bucket`, and
    `auxPreviewPrefix` is this file's unique auxiliary-bucket preview prefix. Downstream
    consumers reconstruct s3:// as needed from `bucket` + the relevant relative key."""
    return {
        "relativePath": normalize_file_key(relative_path),
        "databaseId": database_id or "",
        "assetId": asset_id or "",
        "assetRootS3Key": asset_root_s3_key or "",
        "auxPreviewPrefix": aux_preview_prefix or "",
        "bucket": bucket,
        "key": key,
        "versionId": version_id or "",
    }


def build_manifest_output_target(location_type="asset", asset_id="", database_id="",
                                 file_base_execution_path_extension="/"):
    """outputTarget block for the manifest envelope: where the execution's outputs are written.
    location_type is 'asset' today (outputs go onto an asset); asset_id/database_id identify
    that asset. The end-state process-output lambda uses this rather than assuming the output
    target equals the input asset.

    fileBaseExecutionPathExtension is inserted between the output asset's location key and each
    output file's relative path (final key = assetLocationKey + extension + relativePath). It
    defaults to '/' (no extra path segment); a value like '/exec-2026/' writes all outputs under
    that sub-folder of the asset."""
    return {
        "locationType": location_type or "asset",
        "assetId": asset_id or "",
        "databaseId": database_id or "",
        "fileBaseExecutionPathExtension": file_base_execution_path_extension or "/",
    }


def build_manifest_outputs(bucket="", files="", previews="", metadata="", results=""):
    """outputs block for the manifest envelope: a single output `bucket` plus bucket-relative
    prefixes for each output kind (no pre-built s3:// URIs). Downstream consumers reconstruct
    s3://{bucket}/{prefix} as needed."""
    return {
        "bucket": bucket or "",
        "files": files or "",
        "previews": previews or "",
        "metadata": metadata or "",
        "results": results or "",
    }


def build_manifest_envelope(input_files, input_metadata_s3_location, outputs,
                            aux_bucket, aux_temp_prefix,
                            system_config=None, output_target=None,
                            aux_preview_pipeline_suffix=""):
    """The per-pipeline manifest envelope (schemaVersion-stamped): resolved input files plus
    the metadata, output, and auxiliary-bucket locations, the output-target identity, and the
    systemConfig block.

    Locations avoid pre-built s3:// URIs: `outputs` carries a bucket + bucket-relative prefixes,
    `auxBucket` is the auxiliary bucket NAME only, and `auxTempPrefix` is a bucket-relative
    temporary working prefix. Per-input-file aux preview locations live on each input file entry
    (`auxPreviewPrefix`); `auxPreviewPipelineSuffix` is a per-pipeline viewer subfolder (e.g.
    '/PotreeViewer', empty by default) a pipeline appends to its input file's preview prefix."""
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "inputFiles": input_files or [],
        "inputMetadataS3Location": input_metadata_s3_location or "",
        "outputs": build_manifest_outputs(
            bucket=(outputs or {}).get("bucket", ""),
            files=(outputs or {}).get("files", ""),
            previews=(outputs or {}).get("previews", ""),
            metadata=(outputs or {}).get("metadata", ""),
            results=(outputs or {}).get("results", ""),
        ),
        "outputTarget": output_target or build_manifest_output_target(),
        "auxBucket": aux_bucket or "",
        "auxTempPrefix": aux_temp_prefix or "",
        "auxPreviewPipelineSuffix": aux_preview_pipeline_suffix or "",
        "systemConfig": system_config or {},
    }


def build_manifest_system_config(orchestration_bus_arn="", orchestration_event_prefix=""):
    """systemConfig block for the manifest envelope: the orchestration bus ARN and event
    prefix a pipeline reports sub-process ARNs/logs under. Empty when not configured."""
    return {
        "orchestrationBusArn": orchestration_bus_arn or "",
        "orchestrationEventPrefix": orchestration_event_prefix or "",
    }


def build_metadata_envelope(metadata):
    """The v1 shared input-metadata file envelope (schemaVersion-stamped); the metadata payload is
    preserved verbatim under 'metadata'. Used by the current single-file execute path; the
    multi-file overhaul moves to build_grouped_metadata_envelope."""
    return {
        "schemaVersion": METADATA_SCHEMA_VERSION,
        "metadata": metadata if metadata is not None else {},
    }


def build_metadata_file_record(file_key, metadata=None, attributes=None):
    """One uniform file/asset record for the v2 grouped metadata envelope.

    file_key '/' is the asset-level record; '/name.ext' a file; '/folder/' a folder (folders carry
    metadata=None). 'attributes' (file attributes) is omitted when None so asset/folder records stay
    minimal. metadata is preserved verbatim (the VAMS-scoped dict) or None."""
    record = {
        "fileKey": normalize_file_key(file_key),
        "metadata": metadata if metadata is not None else None,
    }
    if attributes is not None:
        record["attributes"] = attributes
    return record


def build_metadata_asset_group(database_id, asset_id, asset_data=None, files=None):
    """One assets[] entry for the v2 grouped metadata envelope: asset identity + assetData + its
    ordered file/asset records (each from build_metadata_file_record)."""
    return {
        "databaseId": database_id or "",
        "assetId": asset_id or "",
        "assetData": asset_data or {},
        "files": files or [],
    }


def build_metadata_database_group(database_id, metadata=None):
    """One databases[] entry for the v2 grouped metadata envelope: a metadata-source database's
    identity + its database-level metadata (read-only input; databases are never a metadata output
    target)."""
    return {
        "databaseId": database_id or "",
        "metadata": metadata or {},
    }


def build_grouped_metadata_envelope(assets, databases=None):
    """The v2 grouped-by-asset input-metadata file envelope (schemaVersion-stamped). 'assets' is a
    list of build_metadata_asset_group(...) dicts — one per involved asset. Asset-level metadata is
    the fileKey '/' record within an asset group; file metadata/attributes are per-file records.

    'databases' is a list of build_metadata_database_group(...) dicts carried as a top-level sibling
    of 'assets' (database metadata belongs to no asset) — one per database the run captured metadata
    from. The key is present only when the list is non-empty, exactly as it would be for an empty
    'assets', so its absence — rather than an empty list — means the run captured no database
    metadata."""
    envelope = {
        "schemaVersion": METADATA_SCHEMA_VERSION_GROUPED,
        "assets": assets or [],
    }
    if databases:
        envelope["databases"] = list(databases)
    return envelope


def narrow_metadata_envelope(envelope, metadata_inputs):
    """A copy of a v2 grouped envelope carrying only the metadata types `metadata_inputs` enables.

    This is the SECOND of two independent applications of the same four metadataInputs keys, and it
    is deliberate — do not collapse it into the other one:

    - INTAKE (the workflow's gate, applied in executeWorkflow._build_grouped_metadata) decides what
      the execution gathers into the shared per-execution envelope at all.
    - DELIVERY (a step's own effective gate, applied HERE) decides what that one step receives.

    A step therefore receives a type only when the workflow gathered it AND the step asks for it.
    Removing either application silently widens delivery back to the workflow gate, which is the bug
    this narrowing exists to fix.

    Narrowing is subtractive only: assetMetadata clears each asset's fileKey '/' record metadata,
    fileMetadata/fileAttributes clear the per-file records, and databaseMetadata drops the top-level
    'databases' key entirely (its absence is what a run with no database metadata already looks
    like). Asset identity, assetData, and the file-record skeleton are preserved so a reader still
    resolves the same subjects. Returns the envelope unchanged when every type is enabled."""
    body = envelope or {}
    if not isinstance(body, dict):
        return body
    want_asset = metadata_input_enabled(metadata_inputs, "assetMetadata")
    want_file = metadata_input_enabled(metadata_inputs, "fileMetadata")
    want_attr = metadata_input_enabled(metadata_inputs, "fileAttributes")
    want_database = metadata_input_enabled(metadata_inputs, "databaseMetadata")
    if want_asset and want_file and want_attr and want_database:
        return body

    narrowed = dict(body)
    assets = []
    for group in body.get("assets", []) or []:
        group_copy = dict(group or {})
        files = []
        for record in group_copy.get("files", []) or []:
            record_copy = dict(record or {})
            is_asset_level = normalize_file_key(record_copy.get("fileKey")) == "/"
            if is_asset_level:
                if not want_asset:
                    record_copy["metadata"] = None
            else:
                if not want_file:
                    record_copy["metadata"] = None
                if not want_attr and "attributes" in record_copy:
                    record_copy.pop("attributes")
            files.append(record_copy)
        group_copy["files"] = files
        assets.append(group_copy)
    narrowed["assets"] = assets
    if not want_database:
        narrowed.pop("databases", None)
    return narrowed


def get_database_metadata(envelope, database_id):
    """Return the metadata map recorded for one databaseId in a v2 envelope's 'databases' list, or
    {} when the envelope carries no entry for it. Keeps the per-database lookup a single call for
    readers projecting one (databaseId, assetId, fileKey) at a time.

    A run that captured exactly ONE database resolves to that database whatever the requested id. A
    file-less run has no asset to project through, so its subject databaseId is empty and an id match
    would find nothing — yet the one database it named is unambiguously the database its metadata
    describes. With several databases the requested id is the only thing that can disambiguate them,
    so the match is required."""
    groups = (envelope or {}).get("databases", []) or []
    if len(groups) == 1:
        return (groups[0] or {}).get("metadata") or {}
    for group in groups:
        if (group or {}).get("databaseId") == database_id:
            return (group or {}).get("metadata") or {}
    return {}


def get_asset_file_record(envelope, database_id, asset_id, file_key):
    """Return the {fileKey, metadata, attributes?} record for a (databaseId, assetId, fileKey) from a
    v2 envelope, or None when absent. Keeps pipeline read code a single call; file_key is normalized
    before comparison so callers can pass either 'a.glb' or '/a.glb'."""
    fk = normalize_file_key(file_key)
    for asset in (envelope or {}).get("assets", []) or []:
        if asset.get("databaseId") == database_id and asset.get("assetId") == asset_id:
            for file_record in asset.get("files", []) or []:
                if file_record.get("fileKey") == fk:
                    return file_record
    return None


def to_legacy_vams_view(metadata_body, database_id="", asset_id="", file_key=""):
    """Project a metadata payload onto the legacy ``{"VAMS": {assetData, assetMetadata, fileMetadata,
    fileAttributes, databaseMetadata}}`` view the config-template renderer's metadata-content tags read.

    - A v2 grouped body (``{"schemaVersion": 2, "assets": [...]}``) is projected for the given
      (databaseId, assetId, fileKey): assetData + assetMetadata come from the asset's '/' record,
      fileMetadata/fileAttributes from the fileKey record. A fileKey of '/' (whole-asset selection)
      resolves to the asset-level record only, leaving the file scopes empty — mirroring the writer,
      which emits no per-file record for a whole-asset selection. databaseMetadata is the entry the
      envelope's top-level 'databases' list holds for THE databaseId being projected, so the five
      scopes describe one coherent (database, asset, file) subject; it is empty when the list carries
      no entry for that database.
    - A body already in the ``{"VAMS": {...}}`` shape (or any non-grouped dict) passes through
      unchanged; ``{}`` when it is not a usable dict.

    Mirrors the pipeline-side manifestHelper.to_legacy_vams_view so the backend render path and the
    pipeline read path resolve metadata identically."""
    body = metadata_body or {}
    if not isinstance(body, dict):
        return {}
    if body.get("schemaVersion") == METADATA_SCHEMA_VERSION_GROUPED and "assets" in body:
        asset_group = {}
        for asset in body.get("assets", []) or []:
            if asset.get("databaseId") == database_id and asset.get("assetId") == asset_id:
                asset_group = asset
                break
        asset_record = get_asset_file_record(body, database_id, asset_id, "/") or {}
        is_asset_level = normalize_file_key(file_key) == "/"
        file_record = {} if is_asset_level else (
            get_asset_file_record(body, database_id, asset_id, file_key) or {})
        return {"VAMS": {
            "assetData": asset_group.get("assetData") or {},
            "assetMetadata": asset_record.get("metadata") or {},
            "fileMetadata": file_record.get("metadata") or {},
            "fileAttributes": file_record.get("attributes") or {},
            "databaseMetadata": get_database_metadata(body, database_id),
        }}
    return body


def to_dynamodb_numerics(value):
    """A copy of value with every float replaced by an equivalent Decimal, at any nesting depth.

    DynamoDB has no float type: boto3 raises TypeError on one, so a single fractional value anywhere
    inside a caller-supplied structure fails the whole put_item. A non-finite float and any type
    outside the JSON set fall back to their string form, which DynamoDB can store."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int, Decimal)):
        return value
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {k: to_dynamodb_numerics(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_dynamodb_numerics(v) for v in value]
    return str(value)


def serialized_bytes(value) -> int:
    """Serialized UTF-8 size of a structured value, for item-size accounting."""
    return len(json.dumps(value, default=str).encode("utf-8"))


def _budget_shares(sizes, total_limit):
    """Per-field byte limits for fields sharing one item budget: a field smaller than its equal share
    of the budget keeps its whole size and donates the remainder to the oversized ones. Returns None
    when the fields already fit."""
    if sum(sizes) <= total_limit:
        return None
    limits = [0] * len(sizes)
    remaining = total_limit
    pending = list(range(len(sizes)))
    while pending:
        share = remaining // len(pending)
        under = [i for i in pending if sizes[i] <= share]
        if not under:
            for i in pending:
                limits[i] = share
            # Integer-division leftover goes to the first oversized field.
            limits[pending[0]] += remaining - share * len(pending)
            break
        for i in under:
            limits[i] = sizes[i]
            remaining -= sizes[i]
            pending.remove(i)
    return limits


def truncate_text(text: str, limit: int = MAX_TEXT_FIELD_BYTES):
    """Trim text to <= limit bytes (UTF-8). Returns (text, was_truncated)."""
    if text is None:
        return "", False
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def truncate_text_budget(texts, total_limit: int = MAX_TEXT_FIELD_BYTES):
    """Trim a group of free-form text fields that share ONE DynamoDB item so their combined UTF-8
    size stays within total_limit. A field smaller than its equal share of the budget is kept whole
    and its unused bytes are redistributed to the oversized fields. Returns a list of
    (text, was_truncated) in input order."""
    values = [t or "" for t in texts]
    sizes = [len(v.encode("utf-8")) for v in values]
    limits = _budget_shares(sizes, total_limit)
    if limits is None:
        return [(v, False) for v in values]
    return [truncate_text(values[i], limit=limits[i]) for i in range(len(values))]


def truncate_map(mapping, limit: int):
    """Trim a map to <= limit serialized bytes, dropping entries in sorted-key order so the kept set
    is the same for the same input. Returns (map, was_truncated)."""
    body = mapping or {}
    if serialized_bytes(body) <= limit:
        return body, False
    kept = {}
    used = 2  # the enclosing braces
    for key in sorted(body):
        entry = serialized_bytes(key) + 2 + serialized_bytes(body[key]) + (2 if kept else 0)
        if used + entry > limit:
            break
        kept[key] = body[key]
        used += entry
    return kept, True


def truncate_list(items, limit: int):
    """Trim a list to <= limit serialized bytes, keeping a leading run so entries whose order carries
    meaning (selection order, step order) stay contiguous. Returns (list, was_truncated)."""
    values = list(items or [])
    if serialized_bytes(values) <= limit:
        return values, False
    kept = []
    used = 2  # the enclosing brackets
    for item in values:
        entry = serialized_bytes(item) + (2 if kept else 0)
        if used + entry > limit:
            break
        kept.append(item)
        used += entry
    return kept, True


def truncate_collection_budget(collections, total_limit: int = MAX_ITEM_COLLECTION_BYTES):
    """Trim a group of maps/lists that share ONE DynamoDB item so their combined serialized size
    stays within total_limit, on the same donate-the-remainder shares truncate_text_budget uses.
    Returns a list of (collection, was_truncated) in input order."""
    values = [[] if c is None else c for c in collections]
    limits = _budget_shares([serialized_bytes(v) for v in values], total_limit)
    if limits is None:
        return [(v, False) for v in values]
    return [truncate_map(v, limits[i]) if isinstance(v, dict) else truncate_list(v, limits[i])
            for i, v in enumerate(values)]


def remaining_budget(claimed, total_limit: int = MAX_TEXT_FIELD_BYTES) -> int:
    """The share of an item's budget left once the already-sized fields in `claimed` are counted, so a
    large collection shortens the fields taken after it rather than pushing the item past the DynamoDB
    limit."""
    return max(total_limit - sum(serialized_bytes(c) for c in claimed), 0)


def build_workflow_execution_record(
    execution_id, workflow_database_id, workflow_id, workflow_arn,
    workflow_execution_arn, execution_start_date, execution_status,
    triggered_by_user_id, trigger_type, execution_log_group_arn,
    last_sfn_sync_check_date="", execution_group_id="",
):
    """Main WorkflowExecutionsStorageTableV2 row (workflow-keyed; no asset coupling).

    execution_group_id groups executions launched together (bulk / re-run). When set it is the PK of
    the WorkflowExecutionsByGroupGSI (SK executionStartDate), so abort-by-group can enumerate a group's
    executions. Empty when the execution is not part of a group (the attribute is omitted so it stays
    out of the sparse GSI).
    """
    record = {
        "workflowExecutionId": execution_id,  # PK
        "workflowDatabaseId:workflowId": workflow_composite_key(workflow_database_id, workflow_id),  # SK
        "workflowId": workflow_id,
        "workflowDatabaseId": workflow_database_id,
        "workflow_arn": workflow_arn,
        "workflow_execution_arn": workflow_execution_arn,
        "allListPartition": ALL_EXECUTIONS_LIST_PARTITION,  # by-date GSI PK (global newest-first list)
        "executionStartDate": execution_start_date,  # GSI SK, always set at launch
        "executionStopDate": "",
        "executionStatus": execution_status,
        "triggeredByUserId": triggered_by_user_id or "system",
        "triggerType": trigger_type,
        "executionLogGroupArn": execution_log_group_arn or "",
        # Timestamp of the last Step Functions describe_execution poll for this
        # execution. Empty at launch. executionService throttles SFN polling against
        # this (only re-polls when the stop date is unset AND this is older than the
        # min sync interval), reducing describe_execution calls.
        "lastSfnSyncCheckDate": last_sfn_sync_check_date or "",
        # executionError: the specific failure message (SFN error/cause), populated only
        #   for a non-SUCCEEDED terminal status; this is the broadly-visible message.
        # executionLog: full CloudWatch log data for the run, captured on EVERY terminal
        #   completion (success or failure) for debugging; intended for more limited roles.
        # Both empty at launch.
        "executionError": "",
        "executionLog": "",
    }
    # executionGroupId is a sparse GSI PK: set it only when the execution belongs to a group, so
    # ungrouped executions do not populate the WorkflowExecutionsByGroupGSI.
    if execution_group_id:
        record["executionGroupId"] = execution_group_id
    return record


def build_pipeline_execution_record(
    pipeline_execution_id, workflow_execution_id, pipeline_database_id, pipeline_id,
    end_state_pipeline, s3_asset_bucket, s3_aux_bucket, output_prefixes,
    input_metadata_file_prefix, input_config_file_prefix, aux_temp_prefix,
    aux_preview_prefix, pipeline_execution_type, wait_for_callback,
    pipeline_resource_arn, from_pipeline_execution_id="",
    orchestration_bus_event_prefix="",
):
    """PipelineExecutionsStorageTable row (one per pipeline in the workflow)."""
    rec = {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "workflowExecutionId": workflow_execution_id,  # SK + GSI1/2/3 PK
        "pipelineId": pipeline_id,
        "pipelineDatabaseId": pipeline_database_id,
        "pipelineDatabaseId:pipelineId": pipeline_composite_key(pipeline_database_id, pipeline_id),  # GSI1 SK
        "endStatePipeline": "true" if end_state_pipeline else "false",  # GSI3 SK (string)
        "S3AssetPipelineBucket": s3_asset_bucket,
        "S3AssetPipelineBucketInputMetadataFilePrefix": input_metadata_file_prefix,
        "S3AssetPipelineBucketInputConfigurationFilePrefix": input_config_file_prefix,
        "S3AssetPipelineBucketOutputFilesPrefix": output_prefixes.get("files", ""),
        "S3AssetPipelineBucketOutputMetadataPrefix": output_prefixes.get("metadata", ""),
        "S3AssetPipelineBucketOutputPreviewPrefix": output_prefixes.get("previews", ""),
        "S3AssetPipelineBucketOutputResultsPrefix": output_prefixes.get("results", ""),
        "S3AssetAuxPipelineBucket": s3_aux_bucket,
        "S3AssetAuxPipelineBucketPrefixTemp": aux_temp_prefix,
        "S3AssetAuxPipelineBucketPrefixPreview": aux_preview_prefix,
        "executionStartDate": "",
        "executionStopDate": "",
        # NEW (queued) until the pipeline's task state starts; flipped to RUNNING then terminal.
        "executionStatus": "NEW",
        "pipelineExecutionType": pipeline_execution_type,
        "waitForCallback": wait_for_callback,
        "pipelineResourceArn": pipeline_resource_arn or "",
        # STS data-model fields
        "vendedRoleArn": "",
        "s3ReadOnlyScopes": [],
        "s3ReadWriteScopes": [],
        "credentialVendingState": "notVended",
        # EventBridge source prefix the pipeline reports sub-process ARNs/logs under, plus the
        # typed lists it registers. Each registeredSubExecutions entry is typed by resourceType
        # ('stepFunctionsExecution' today; 'batchJob'/'ecsTask'/... later) so the abort path knows
        # how to stop it; each registeredLogs entry is {logGroupArn, logGroupName, logStreamName,
        # logStreamPrefix} so full-mode logs can pull from the right CloudWatch location.
        "orchestrationBusEventPrefix": orchestration_bus_event_prefix or "",
        "registeredSubExecutions": [],
        "registeredLogs": [],
    }
    # from_pipeline_execution_id is the PipelineExecChainGSI sort key; DynamoDB rejects an empty
    # string for an indexed key attribute. Set it only when this pipeline chains from a prior one
    # (a sparse GSI — first/unchained pipelines are simply absent from the chain index).
    if from_pipeline_execution_id:
        rec["from_pipeline_execution_id"] = from_pipeline_execution_id
    return rec


def build_pipeline_input_file_record(
    pipeline_execution_id, workflow_execution_id, database_id, asset_id, input_asset_file_key,
):
    """PipelineExecutionInputFilesStorageTable row."""
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "databaseId:assetId:inputAssetFileKey": input_file_composite_key(
            database_id, asset_id, input_asset_file_key),  # SK
        "databaseId:assetId": f"{database_id}:{asset_id}",  # GSI PK
        "assetId": asset_id,
        "databaseId": database_id,
        "inputAssetFileKey": normalize_file_key(input_asset_file_key),
        "workflowExecutionId": workflow_execution_id,
    }


def build_workflow_execution_input_record(
    workflow_execution_id, database_id, asset_id, input_asset_file_key,
    execution_start_date, workflow_id, workflow_database_id,
    s3_bucket="", asset_root_s3_key="", version_id="",
):
    """WorkflowExecutionInputsStorageTable row (asset-scoped GET source of truth).

    s3Bucket + assetRootS3Key locate this input file's own asset root: the bucket name plus the
    bucket-relative asset-root prefix (no s3:// URI). Each input file may belong to a different
    asset (different bucket and base location key), so its root is stored per file rather than
    assumed shared; the interim lambda uses it to compute the asset-relative path for the rebuilt
    manifest.

    versionId is the concrete S3 VersionId the run read for this file (resolved at launch), captured
    so the execution's history shows the exact version used, not the time-relative "latest". Empty
    for folder/whole-asset selections (no single version)."""
    return {
        "workflowExecutionId": workflow_execution_id,  # PK
        "databaseId:assetId:inputAssetFileKey": input_file_composite_key(
            database_id, asset_id, input_asset_file_key),  # SK
        "databaseId:assetId": f"{database_id}:{asset_id}",  # GSI PK
        "assetId": asset_id,
        "databaseId": database_id,
        "inputAssetFileKey": normalize_file_key(input_asset_file_key),
        "s3Bucket": s3_bucket,
        "assetRootS3Key": asset_root_s3_key,
        "versionId": version_id or "",
        "executionStartDate": execution_start_date,  # GSI SK
        "workflowId": workflow_id,
        "workflowDatabaseId": workflow_database_id,
    }


def metadata_envelope_rows(envelope):
    """Flatten a grouped metadata envelope into one row per (asset, filePath) that carries metadata
    or file attributes, plus one row per database that carries metadata.

    Yields dicts of {databaseId, assetId, filePath, metadata, attributes, scope} ready for
    build_input_metadata_record. 'scope' is 'asset' for an asset/file row and 'database' for an entry
    of the envelope's 'databases' list, which belongs to no asset and so flattens to an empty assetId
    and a '/' filePath — each database becoming its own row, so a run spanning several databases keeps
    their metadata separately readable. Asset-level metadata lives on the fileKey '/' record, so it
    flattens to a '/' filePath row — a reader that walks only per-FILE records misses asset metadata
    entirely.

    'attributes' (file attributes) rides on the SAME row as that file's metadata rather than becoming
    its own row: attributes describe the very file the row already identifies, so they are a second
    property of one fact, not a second fact. (Contrast 'database', which earns its own scope because a
    database is a different entity.) Keeping them on one row also leaves the row identity — and so the
    details view's dedupe key of (pipelineId, scope, databaseId, assetId, filePath) — unchanged.

    A record is skipped only when it has NEITHER metadata NOR attributes: the envelope always emits a
    '/' record per asset (so the file list is uniform) even when the asset carries nothing, and
    persisting those would fill the details response with empty rows. Attributes alone are enough to
    keep a row, because the four metadataInputs gates are independent — 'fileMetadata: false' with
    'fileAttributes: true' is a valid configuration whose files carry attributes and no metadata, and
    skipping on empty metadata alone would make those attributes unreadable through the API.

    Accepts the legacy flat shape too, so a caller holding either envelope version works.
    """
    payload = envelope or {}
    if not isinstance(payload, dict):
        return
    if payload.get("schemaVersion") == METADATA_SCHEMA_VERSION_GROUPED and "assets" in payload:
        for group in payload.get("databases") or []:
            database_metadata = (group or {}).get("metadata")
            if not database_metadata:
                continue
            yield {
                "databaseId": (group or {}).get("databaseId", ""),
                "assetId": "",
                "filePath": "/",
                "metadata": database_metadata,
                "attributes": {},
                "scope": "database",
            }
        for group in payload.get("assets") or []:
            database_id = (group or {}).get("databaseId", "")
            asset_id = (group or {}).get("assetId", "")
            for record in (group or {}).get("files") or []:
                metadata = (record or {}).get("metadata")
                attributes = (record or {}).get("attributes")
                if not metadata and not attributes:
                    continue
                yield {
                    "databaseId": database_id,
                    "assetId": asset_id,
                    "filePath": (record or {}).get("fileKey", "/"),
                    "metadata": metadata or {},
                    "attributes": attributes or {},
                    "scope": "asset",
                }
        return
    # Legacy flat shape: {"VAMS": {"assetMetadata": {...}}} carries asset metadata only.
    legacy = (payload.get("VAMS") or {}).get("assetMetadata") or {}
    if legacy:
        yield {"databaseId": "", "assetId": "", "filePath": "/", "metadata": legacy,
               "attributes": {}, "scope": "asset"}


def pipeline_metadata_envelope_rows(envelope, pipeline_inputs, metadata_source_assets=None):
    """The metadata_envelope_rows of one PIPELINE: the subset of the run's envelope rows describing
    entities that pipeline reads from.

    The envelope is assembled once from ALL of the run's selected inputs and metadata sources, but a
    workflow's pipelines do not receive the same inputs — each receives the subset passing its own
    effective inputFileFilters, and an arity-'none' pipeline receives none. pipeline_inputs is that
    pipeline's received subset ([{databaseId, assetId, relativeFileKey}, ...]); metadata_source_assets
    the run's named source assets, which every pipeline reads regardless of arity or filters.

    Which rows belong to a pipeline, by row kind:
      - A per-FILE row belongs to the pipeline that received THAT file. This is the whole question the
        rows answer — "which metadata went into which pipeline" — and a pipeline that never saw the
        file read none of its metadata.
      - An asset-level ('/') row belongs to a pipeline receiving at least one file from that asset, or
        to every pipeline when the asset is a named metadata source. A reader looking at one pipeline's
        rows is asking what that step read; an asset it got no file from is not in its picture at all,
        so its asset-level metadata is not something the step read. A named source asset is the
        exception because the run reads it as an entity in its own right, not through a file.
      - A DATABASE row belongs to every pipeline. Database metadata describes an entity, not a file
        selection, and the shared per-execution envelope every task is handed carries the whole
        'databases' list — so every pipeline can read it whatever files it received. Narrowing these to
        the databases of a pipeline's own files would under-report what the step can read.
      - An asset row naming no asset (the legacy flat envelope's single row) belongs to every pipeline:
        it is attributable to no asset, so no per-pipeline input set can include or exclude it.
    """
    received_files = set()
    received_assets = set()
    for item in pipeline_inputs or []:
        database_id = (item or {}).get("databaseId", "")
        asset_id = (item or {}).get("assetId", "")
        received_assets.add((database_id, asset_id))
        received_files.add(
            (database_id, asset_id, normalize_file_key((item or {}).get("relativeFileKey", "/"))))
    source_assets = {((s or {}).get("databaseId", ""), (s or {}).get("assetId", ""))
                     for s in metadata_source_assets or []}

    # A pipeline receiving no files still reads asset metadata: with no input file to project through,
    # the pipeline-side helper takes the envelope's FIRST asset group as its subject (run_vams_view),
    # so that group's asset-level metadata is what the step actually reads. Recording it keeps the rows
    # and the step's own read describing the same entity; omitting it under-reports the step.
    if not received_assets:
        groups = (envelope or {}).get("assets") or []
        if groups:
            subject = groups[0] or {}
            source_assets = source_assets | {
                (subject.get("databaseId", "") or "", subject.get("assetId", "") or "")}

    for row in metadata_envelope_rows(envelope):
        if row["scope"] == "database" or not row["assetId"]:
            yield row
            continue
        entity = (row["databaseId"], row["assetId"])
        if row["filePath"] == "/":
            if entity in received_assets or entity in source_assets:
                yield row
            continue
        if (row["databaseId"], row["assetId"], row["filePath"]) in received_files:
            yield row


def build_input_metadata_record(
    pipeline_execution_id, database_id, asset_id, file_path, metadata,
    source_input_metadata_file_s3_key, scope="asset", attributes=None,
):
    """PipelineExecutionInputMetadataStorageTable row ('/' filePath = asset-level).

    scope discriminates what the metadata describes: 'asset' for an asset/file row, 'database' for the
    metadata-source database's own metadata (empty assetId, '/' filePath, so its SK is
    '{databaseId}::/'). Readers group by it to present database metadata separately from asset
    metadata; a row written without it is an asset row.

    'attributes' holds that file's ATTRIBUTES, kept in their own key rather than merged into
    'metadata': the two are distinct entities in the four-key metadataInputs model (fileMetadata and
    fileAttributes are separately gated), so merging them would make the delivered set unattributable
    to the gate that allowed it. A row for an asset-level or database scope carries an empty map.

    Both maps land on the same item, and the metadata service bounds each entity's set on its own, so
    they share ONE byte budget here: whichever is oversized drops entries in sorted-key order and
    carries its own truncation flag. Truncating is the alternative to failing put_item after the state
    machine has already started, which loses the whole run and reports nothing about the cause."""
    fp = normalize_file_key(file_path)
    ((metadata_body, metadata_truncated),
     (attributes_body, attributes_truncated)) = truncate_collection_budget(
        [to_dynamodb_numerics(metadata or {}), to_dynamodb_numerics(attributes or {})],
        total_limit=MAX_TEXT_FIELD_BYTES)
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "databaseId:assetId:filePath": f"{database_id}:{asset_id}:{fp}",  # SK
        "assetId": asset_id,
        "databaseId": database_id,
        "filePath": fp,
        "scope": scope or "asset",
        "metadata": metadata_body,
        "metadataTruncated": metadata_truncated,
        "attributes": attributes_body,
        "attributesTruncated": attributes_truncated,
        "sourceInputMetadataFileS3Key": source_input_metadata_file_s3_key or "",
    }


def build_input_configuration_record(
    pipeline_execution_id, input_configuration, input_configuration_file_s3_key,
    template_id="", template_schema_version="", tag_schema_version="",
    template_tags=None, custom_template_override_used=False, custom_template_override="",
    config_format="", effective_system_config=None, template_overrides=None,
):
    """PipelineExecutionInputConfigurationStorageTable row (SK='configuration').

    Snapshots exactly what went into the run so it stays traceable and re-runnable even after the
    source template + tag schema later change or are archived:
      - inputConfiguration: the final rendered config actually sent to the pipeline (truncated
        inline; the full body is the per-execution S3 file at inputConfigurationFileS3Key).
      - templateId + templateSchemaVersion + tagSchemaVersion: the template/tag-schema versions
        resolved at run time.
      - templateTags: the resolved tag values passed.
      - customTemplateOverrideUsed: whether a caller-supplied override body was rendered.
      - customTemplateOverride: the RAW override body (pre-render, tags un-substituted) when one was
        supplied, so a re-run can faithfully reconstruct a template-less override execution (there is
        no templateId to re-resolve). Truncated inline; empty when no override was used.
      - effectiveSystemConfig: the systemConfig this step actually ran under — the pipeline's own
        systemConfig merged with the chosen template's `overrides`
        (executionValidation.resolve_effective_pipeline_config). Only knowable at execute time, because
        the template is chosen per run; without it a finished execution cannot say which
        inputFileArity / assetScope / metadataInputs / inputFileFilters were in force.
      - templateOverrides: just the keys the template overrode, so a reader can see WHY the effective
        config differs from the pipeline's own (e.g. a template raising inputFileArity from 'none').

    Every variable-size field lands on the same item, so all of them are accounted against the one
    400 KB limit: the tag list and the two config maps take a bounded share first (each flagged when
    trimmed), and the text bodies then share whatever is left. Tag values are caller-supplied, so they
    are also normalized to DynamoDB numerics — a fractional value anywhere inside them would otherwise
    fail put_item after the state machine has started.
    """
    ((tags, tags_truncated),
     (effective_config, effective_config_truncated),
     (overrides, overrides_truncated)) = truncate_collection_budget([
         to_dynamodb_numerics(template_tags or []),
         to_dynamodb_numerics(effective_system_config or {}),
         to_dynamodb_numerics(template_overrides or {}),
     ])
    ((content, truncated),
     (override_content, override_truncated)) = truncate_text_budget(
        [input_configuration or "", custom_template_override or ""],
        total_limit=remaining_budget([tags, effective_config, overrides]))
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "recordType": "configuration",  # SK
        "inputConfiguration": content,
        "inputConfigurationTruncated": truncated,
        "inputConfigurationFileS3Key": input_configuration_file_s3_key or "",
        # Config snapshot: what the run was built from.
        "templateId": template_id or "",
        "templateSchemaVersion": template_schema_version or "",
        "tagSchemaVersion": tag_schema_version or "",
        "templateTags": tags,
        "templateTagsTruncated": tags_truncated,
        "customTemplateOverrideUsed": bool(custom_template_override_used),
        "customTemplateOverride": override_content,
        "customTemplateOverrideTruncated": override_truncated,
        # Format of the rendered config body, so the detail view highlights it correctly.
        "configFormat": config_format or "",
        # The settings this step ran under, and the template overrides that shaped them.
        "effectiveSystemConfig": effective_config,
        "effectiveSystemConfigTruncated": effective_config_truncated,
        "templateOverrides": overrides,
        "templateOverridesTruncated": overrides_truncated,
    }


def build_output_file_record(
    pipeline_execution_id, file_type, relative_file_path, s3_bucket, s3_key,
    file_size, content_type, s3_version_id,
):
    """PipelineExecutionOutputFilesStorageTable row (file_type in {file, preview})."""
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "fileType:relativeFilePath": f"{file_type}:{relative_file_path}",  # SK
        "fileType": file_type,
        "relativeFilePath": relative_file_path,
        "s3Bucket": s3_bucket,
        "s3Key": s3_key,
        "fileSize": file_size,
        "contentType": content_type or "",
        "s3VersionId": s3_version_id or "",
    }


def build_output_metadata_record(
    pipeline_execution_id, target_file_path, metadata_key, metadata_value,
    source_metadata_file_relative_path,
):
    """PipelineExecutionOutputMetadataStorageTable row ('/' target = asset-level)."""
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "targetFilePath:metadataKey": f"{target_file_path}:{metadata_key}",  # SK
        "targetFilePath": target_file_path,
        "metadataKey": metadata_key,
        "metadataValue": metadata_value,
        "sourceMetadataFileRelativePath": source_metadata_file_relative_path or "",
    }


def build_output_result_record(
    pipeline_execution_id, relative_file_path, results_content, s3_key,
):
    """PipelineExecutionOutputResultsStorageTable row."""
    content, truncated = truncate_text(results_content or "")
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "relativeFilePath": relative_file_path,  # SK
        "resultsContent": content,
        "resultsContentTruncated": truncated,
        "s3Key": s3_key or "",
    }


def build_log_record(
    pipeline_execution_id, log_type, result_log, error_log, log_group_arn, log_stream_name,
):
    """PipelineExecutionLogsStorageTable row (log_type='summary')."""
    ((result_content, result_truncated),
     (error_content, error_truncated)) = truncate_text_budget(
        [result_log or "", error_log or ""], total_limit=MAX_LOG_FIELD_BYTES)
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "logType": log_type,  # SK
        "resultLog": result_content,
        "resultLogTruncated": result_truncated,
        "errorLog": error_content,
        "errorLogTruncated": error_truncated,
        "logGroupArn": log_group_arn or "",
        "logStreamName": log_stream_name or "",
    }


def output_asset_partition_key(output_database_id, output_asset_id):
    """Partition value for WorkflowExecConfigByOutputAssetGSI: '{outputDatabaseId}:{outputAssetId}'.

    Mirrors the 'databaseId:assetId' shape the by-input-asset GSI uses, so a caller listing an asset's
    executions builds the same key for both directions."""
    return f"{output_database_id}:{output_asset_id}"


def build_workflow_configuration_record(
    workflow_execution_id, input_metadata, specified_pipelines_snapshot,
    output_location_type="asset", output_asset_id="", output_database_id="",
    output_file_base_execution_path_extension="/",
    input_metadata_database_id="",
    input_metadata_file_s3_key="",
    execution_start_date="",
    metadata_source_assets=None, metadata_source_databases=None,
):
    """WorkflowExecutionConfigurationStorageTable row (SK='configuration').

    metadata_source_assets is the run's resolved [{databaseId, assetId}] metadata-source selection and
    input_metadata_database_id the source database the CALLER named — the selection a re-run replays.
    metadata_source_databases is every databaseId the run actually captured database metadata from
    (derived from the input files' assets when the run has input files, the caller's one choice when it
    has none), so the read paths authorize and report the set that is really in the envelope rather than
    re-deriving it. These are recorded here (and nowhere else) because they are not input FILES:
    re-emitting them as inputFiles on a re-run would violate an arity-'none' workflow's own
    no-input-files rule.

    The step snapshot and the two source lists are variable-size fields on the same item as the
    inputMetadata body, so they take a bounded share of the one 400 KB budget first (each flagged when
    trimmed) and the body takes what is left — inputMetadata is also written to S3 in full, while
    overflowing the item would lose the whole run after the state machine has started.

    The source lists have first claim on that share, and the step snapshot only what they leave: the
    read paths gate an execution on the entities it names here, so an entry dropped from a source list
    is an entity the gate stops asking for, while a dropped snapshot entry costs only detail."""
    ((source_assets, source_assets_truncated),
     (source_databases, source_databases_truncated)) = truncate_collection_budget([
         [{"databaseId": s.get("databaseId", ""), "assetId": s.get("assetId", "")}
          for s in (metadata_source_assets or [])],
         [d for d in (metadata_source_databases or []) if d],
     ])
    pipelines_snapshot, pipelines_truncated = truncate_list(
        to_dynamodb_numerics(specified_pipelines_snapshot or []),
        remaining_budget([source_assets, source_databases],
                         total_limit=MAX_ITEM_COLLECTION_BYTES))
    ((metadata_content, metadata_truncated),) = truncate_text_budget(
        [input_metadata or ""],
        total_limit=remaining_budget([pipelines_snapshot, source_assets, source_databases]))
    record = {
        "workflowExecutionId": workflow_execution_id,  # PK
        "recordType": "configuration",  # SK
        "inputMetadata": metadata_content,
        "inputMetadataTruncated": metadata_truncated,
        "specifiedPipelinesSnapshot": pipelines_snapshot,
        "specifiedPipelinesSnapshotTruncated": pipelines_truncated,
        # Output target (where the execution's outputs are written).
        "outputLocationType": output_location_type or "asset",
        "outputAssetId": output_asset_id or "",
        "outputDatabaseId": output_database_id or "",
        # Path segment inserted between the output asset location key and each output file's
        # relative path ('/' = none).
        "outputFileBaseExecutionPathExtension": output_file_base_execution_path_extension or "/",
        # Sort key for WorkflowExecConfigByOutputAssetGSI, so an output-asset listing can be bounded
        # and ordered by recency exactly like the by-input-asset one.
        "executionStartDate": execution_start_date or iso_now(),
        # Input-metadata source (recording only).
        "inputMetadataDatabaseId": input_metadata_database_id or "",
        "inputMetadataFileS3Key": input_metadata_file_s3_key or "",
        # Metadata-source assets, in selection order. A re-run rebuilds the same sources from these.
        "metadataSourceAssets": source_assets,
        "metadataSourceAssetsTruncated": source_assets_truncated,
        # Every database whose metadata the run captured, in capture order. This is the set the read
        # paths gate on, so a run deriving its databases from the input files does not have to re-derive
        # them (the input rows carry no database-metadata provenance).
        "metadataSourceDatabases": source_databases,
        "metadataSourceDatabasesTruncated": source_databases_truncated,
    }
    # GSI partition for WorkflowExecConfigByOutputAssetGSI, written ONLY for an asset-targeted run
    # with a resolved destination. Omitting the attribute keeps the row out of the index entirely
    # (DynamoDB indexes sparsely), so results-only executions never appear in an asset's history and
    # an empty-string partition can never collect unrelated rows.
    if (output_location_type or "asset") == "asset" and output_asset_id and output_database_id:
        record["outputDatabaseId:outputAssetId"] = output_asset_partition_key(
            output_database_id, output_asset_id)
    return record
