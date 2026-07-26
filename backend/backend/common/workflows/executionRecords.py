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

import uuid
from datetime import datetime, timezone

# Max bytes for a single free-form text field stored in DynamoDB (keeps each
# item comfortably under the 400 KB DynamoDB item limit).
MAX_TEXT_FIELD_BYTES = 380 * 1024
MAX_LOG_FIELD_BYTES = 390 * 1024

# Schema versions stamped on the VAMS-authored manifest and metadata files.
MANIFEST_SCHEMA_VERSION = 1
# v1: flat {schemaVersion, metadata} envelope (build_metadata_envelope). v2: grouped-by-asset
# envelope (build_grouped_metadata_envelope) for multi-file execution.
METADATA_SCHEMA_VERSION = 1
METADATA_SCHEMA_VERSION_GROUPED = 2

# Constant PK for the by-date global-list GSI (newest-first query, not a table scan).
ALL_EXECUTIONS_LIST_PARTITION = "execution"


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


def build_grouped_metadata_envelope(assets):
    """The v2 grouped-by-asset input-metadata file envelope (schemaVersion-stamped). 'assets' is a
    list of build_metadata_asset_group(...) dicts — one per involved asset. Asset-level metadata is
    the fileKey '/' record within an asset group; file metadata/attributes are per-file records."""
    return {
        "schemaVersion": METADATA_SCHEMA_VERSION_GROUPED,
        "assets": assets or [],
    }


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
    fileAttributes}}`` view the config-template renderer's metadata-content tags read.

    - A v2 grouped body (``{"schemaVersion": 2, "assets": [...]}``) is projected for the given
      (databaseId, assetId, fileKey): assetData + assetMetadata come from the asset's '/' record,
      fileMetadata/fileAttributes from the fileKey record. A fileKey of '/' (whole-asset selection)
      resolves to the asset-level record only, leaving the file scopes empty — mirroring the writer,
      which emits no per-file record for a whole-asset selection.
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
        }}
    return body


def truncate_text(text: str, limit: int = MAX_TEXT_FIELD_BYTES):
    """Trim text to <= limit bytes (UTF-8). Returns (text, was_truncated)."""
    if text is None:
        return "", False
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


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


def build_input_metadata_record(
    pipeline_execution_id, database_id, asset_id, file_path, metadata,
    source_input_metadata_file_s3_key,
):
    """PipelineExecutionInputMetadataStorageTable row ('/' filePath = asset-level)."""
    fp = normalize_file_key(file_path)
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "databaseId:assetId:filePath": f"{database_id}:{asset_id}:{fp}",  # SK
        "assetId": asset_id,
        "databaseId": database_id,
        "filePath": fp,
        "metadata": metadata or {},
        "sourceInputMetadataFileS3Key": source_input_metadata_file_s3_key or "",
    }


def build_input_configuration_record(
    pipeline_execution_id, input_configuration, input_configuration_file_s3_key,
    template_id="", template_schema_version="", tag_schema_version="",
    template_tags=None, custom_template_override_used=False, custom_template_override="",
    config_format="",
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
    """
    content, truncated = truncate_text(input_configuration or "")
    override_content, override_truncated = truncate_text(custom_template_override or "")
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "recordType": "configuration",  # SK
        "inputConfiguration": content,
        "inputConfigurationTruncated": truncated,
        "inputConfigurationFileS3Key": input_configuration_file_s3_key or "",
        "inputPortMappings": {},
        # Config snapshot: what the run was built from.
        "templateId": template_id or "",
        "templateSchemaVersion": template_schema_version or "",
        "tagSchemaVersion": tag_schema_version or "",
        "templateTags": template_tags or [],
        "customTemplateOverrideUsed": bool(custom_template_override_used),
        "customTemplateOverride": override_content,
        "customTemplateOverrideTruncated": override_truncated,
        # Format of the rendered config body, so the detail view highlights it correctly.
        "configFormat": config_format or "",
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
    result_content, result_truncated = truncate_text(result_log or "", limit=MAX_LOG_FIELD_BYTES)
    error_content, error_truncated = truncate_text(error_log or "", limit=MAX_LOG_FIELD_BYTES)
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


def build_workflow_configuration_record(
    workflow_execution_id, workflow_configuration, input_metadata, specified_pipelines_snapshot,
    output_location_type="asset", output_asset_id="", output_database_id="",
    output_file_base_execution_path_extension="/",
    input_metadata_asset_id="", input_metadata_database_id="",
    input_metadata_file_s3_key="",
):
    """WorkflowExecutionConfigurationStorageTable row (SK='configuration')."""
    config_content, config_truncated = truncate_text(workflow_configuration or "")
    metadata_content, metadata_truncated = truncate_text(input_metadata or "")
    return {
        "workflowExecutionId": workflow_execution_id,  # PK
        "recordType": "configuration",  # SK
        "workflowConfiguration": config_content,
        "workflowConfigurationTruncated": config_truncated,
        "inputMetadata": metadata_content,
        "inputMetadataTruncated": metadata_truncated,
        "specifiedPipelinesSnapshot": specified_pipelines_snapshot or [],
        # Output target (where the execution's outputs are written).
        "outputLocationType": output_location_type or "asset",
        "outputAssetId": output_asset_id or "",
        "outputDatabaseId": output_database_id or "",
        # Path segment inserted between the output asset location key and each output file's
        # relative path ('/' = none).
        "outputFileBaseExecutionPathExtension": output_file_base_execution_path_extension or "/",
        # Input-metadata source (recording only).
        "inputMetadataAssetId": input_metadata_asset_id or "",
        "inputMetadataDatabaseId": input_metadata_database_id or "",
        "inputMetadataFileS3Key": input_metadata_file_s3_key or "",
    }
