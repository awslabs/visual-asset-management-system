# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure helpers for the workflow-execution storage data model (Stage 1).

This module has NO AWS or environment dependencies so it can be imported and
unit-tested in isolation. It centralizes:
  - clean composite-key construction (no legacy '$' prefix)
  - ISO-8601 UTC timestamps
  - per-pipeline S3 prefix derivation (mirrors createWorkflow ASL paths)
  - record-dict builders for each execution storage table
  - text parsing/truncation for results/logs within DynamoDB item limits
"""

import uuid
from datetime import datetime, timezone

# Max bytes for a single free-form text field stored in DynamoDB (keeps each
# item comfortably under the 400 KB DynamoDB item limit).
MAX_TEXT_FIELD_BYTES = 380 * 1024
MAX_LOG_FIELD_BYTES = 390 * 1024


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
    output location, matching the ASL paths in createWorkflow.generate_workflow_asl.
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


def aux_pipeline_prefix(pipeline_name: str, pipeline_type: str, input_asset_file_key: str) -> str:
    """Auxiliary-bucket working prefix for a pipeline, mirroring the ASL
    inputOutputS3AssetAuxiliaryFilesPath layout: fileKey, then subfolder, then
    pipeline name (standardFile -> 'pipelines', previewFile -> 'preview')."""
    subfolder = _PIPELINES_PREFIX.rstrip("/")
    if pipeline_type == "previewFile":
        subfolder = _AUXILIARY_PREVIEW_PREFIX.rstrip("/")
    return f"{input_asset_file_key}/{subfolder}/{pipeline_name}/"


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
    last_sfn_sync_check_date="",
):
    """Main WorkflowExecutionsStorageTableV2 row (workflow-keyed; no asset coupling)."""
    return {
        "executionId": execution_id,  # PK
        "workflowDatabaseId:workflowId": workflow_composite_key(workflow_database_id, workflow_id),  # SK
        "workflowId": workflow_id,
        "workflowDatabaseId": workflow_database_id,
        "workflow_arn": workflow_arn,
        "workflow_execution_arn": workflow_execution_arn,
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


def build_pipeline_execution_record(
    pipeline_execution_id, workflow_execution_id, pipeline_database_id, pipeline_id,
    end_state_pipeline, s3_asset_bucket, s3_aux_bucket, output_prefixes,
    input_metadata_file_prefix, input_config_file_prefix, aux_temp_prefix,
    aux_preview_prefix, pipeline_execution_type, wait_for_callback,
    pipeline_resource_arn, from_pipeline_execution_id="",
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
        "executionStatus": "",
        "pipelineExecutionType": pipeline_execution_type,
        "waitForCallback": wait_for_callback,
        "pipelineResourceArn": pipeline_resource_arn or "",
        # STS data-model fields (Stage 1: schema only, unpopulated)
        "vendedRoleArn": "",
        "s3ReadOnlyScopes": [],
        "s3ReadWriteScopes": [],
        "credentialVendingState": "notVended",
        # optional chain / sub-process fields
        "from_pipeline_execution_id": from_pipeline_execution_id or "",
        "pipeline_execution_sub_arn": "",
        "pipeline_execution_sub_execution_arn": "",
    }
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
):
    """WorkflowExecutionInputsStorageTable row (asset-scoped GET source of truth)."""
    return {
        "workflowExecutionId": workflow_execution_id,  # PK
        "databaseId:assetId:inputAssetFileKey": input_file_composite_key(
            database_id, asset_id, input_asset_file_key),  # SK
        "databaseId:assetId": f"{database_id}:{asset_id}",  # GSI PK
        "assetId": asset_id,
        "databaseId": database_id,
        "inputAssetFileKey": normalize_file_key(input_asset_file_key),
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
):
    """PipelineExecutionInputConfigurationStorageTable row (SK='configuration')."""
    content, truncated = truncate_text(input_configuration or "")
    return {
        "pipelineExecutionId": pipeline_execution_id,  # PK
        "recordType": "configuration",  # SK
        "inputConfiguration": content,
        "inputConfigurationTruncated": truncated,
        "inputConfigurationFileS3Key": input_configuration_file_s3_key or "",
        "inputPortMappings": {},  # Stage 1: schema only
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
    """PipelineExecutionOutputResultsStorageTable row (Stage 1: schema only)."""
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
    """PipelineExecutionLogsStorageTable row (log_type='summary' in Stage 1)."""
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
    }
