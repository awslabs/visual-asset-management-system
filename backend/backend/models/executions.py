# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 models for the workflow-execution storage data model (Stage 1).

These document the canonical record shapes for the 11 execution storage tables.
Handlers persist dicts via common.workflows.executionRecords builders; these models are
used for validation and parsing where helpful. All use the v1 idiom
(BaseModel from aws_lambda_powertools, extra='ignore').
"""

from typing import Any, Dict, List, Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from customLogging.logger import safeLogger

logger = safeLogger(service_name="ExecutionModels")

TRIGGER_TYPES = ("Manual", "File-Upload")


class WorkflowExecutionRecord(BaseModel, extra='ignore'):
    """Main WorkflowExecutionsStorageTableV2 row (workflow-keyed)."""
    workflowExecutionId: str
    workflowId: str
    workflowDatabaseId: str
    workflow_arn: Optional[str] = ""
    workflow_execution_arn: Optional[str] = ""
    executionStartDate: Optional[str] = ""
    executionStopDate: Optional[str] = ""
    executionStatus: Optional[str] = "NEW"
    # SYSTEM_USER is the reserved identity for system-initiated actions (never the variant 'system').
    triggeredByUserId: Optional[str] = "SYSTEM_USER"
    triggerType: str = "Manual"
    executionLogGroupArn: Optional[str] = ""
    # ISO-8601 timestamp of the last Step Functions describe_execution poll (empty at
    # launch). executionService only re-polls when the stop date is unset and this is
    # older than the min sync interval, reducing direct SFN calls.
    lastSfnSyncCheckDate: Optional[str] = ""
    # executionError: the specific failure message (SFN error/cause) for a non-SUCCEEDED
    #   terminal status; broadly visible. executionLog: full CloudWatch log for the run,
    #   captured on every terminal completion (success or failure) for limited roles.
    executionError: Optional[str] = ""
    executionLog: Optional[str] = ""

    @validator("triggerType")
    def validate_trigger_type(cls, v):
        if v not in TRIGGER_TYPES:
            raise ValueError(f"triggerType must be one of {TRIGGER_TYPES}")
        return v


class PipelineExecutionRecord(BaseModel, extra='ignore'):
    """PipelineExecutionsStorageTable row (one per pipeline in the workflow)."""
    pipelineExecutionId: str
    workflowExecutionId: str
    pipelineId: str
    pipelineDatabaseId: str
    endStatePipeline: str = "false"
    S3AssetPipelineBucket: Optional[str] = ""
    S3AssetPipelineBucketInputMetadataFilePrefix: Optional[str] = ""
    S3AssetPipelineBucketInputConfigurationFilePrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputFilesPrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputMetadataPrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputPreviewPrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputResultsPrefix: Optional[str] = ""
    S3AssetAuxPipelineBucket: Optional[str] = ""
    S3AssetAuxPipelineBucketPrefixTemp: Optional[str] = ""
    S3AssetAuxPipelineBucketPrefixPreview: Optional[str] = ""
    executionStartDate: Optional[str] = ""
    executionStopDate: Optional[str] = ""
    executionStatus: Optional[str] = ""
    pipelineExecutionType: str = "Lambda"
    waitForCallback: Optional[str] = "Disabled"
    pipelineResourceArn: Optional[str] = ""
    vendedRoleArn: Optional[str] = ""
    s3ReadOnlyScopes: Optional[List[str]] = []
    s3ReadWriteScopes: Optional[List[str]] = []
    credentialVendingState: Optional[str] = "notVended"
    from_pipeline_execution_id: Optional[str] = ""
    # EventBridge source prefix the pipeline reports under, plus the typed lists of reported
    # sub-process resources and CloudWatch log locations. Each registeredSubExecutions entry is
    # typed by resourceType (stepFunctionsExecution today; batchJob/ecsTask/... later); each
    # registeredLogs entry is {logGroupArn, logGroupName, logStreamName, logStreamPrefix}.
    orchestrationBusEventPrefix: Optional[str] = ""
    registeredSubExecutions: Optional[List[Dict[str, Any]]] = []
    registeredLogs: Optional[List[Dict[str, Any]]] = []


class PipelineExecutionInputFileRecord(BaseModel, extra='ignore'):
    """PipelineExecutionInputFilesStorageTable row."""
    pipelineExecutionId: str
    workflowExecutionId: str
    assetId: str
    databaseId: str
    inputAssetFileKey: str


class PipelineExecutionInputMetadataRecord(BaseModel, extra='ignore'):
    """PipelineExecutionInputMetadataStorageTable row."""
    pipelineExecutionId: str
    assetId: str
    databaseId: str
    filePath: str
    metadata: Optional[Dict[str, Any]] = {}
    sourceInputMetadataFileS3Key: Optional[str] = ""


class PipelineExecutionInputConfigurationRecord(BaseModel, extra='ignore'):
    """PipelineExecutionInputConfigurationStorageTable row."""
    pipelineExecutionId: str
    recordType: str = "configuration"
    inputConfiguration: Optional[str] = ""
    inputConfigurationTruncated: Optional[bool] = False
    inputConfigurationFileS3Key: Optional[str] = ""
    inputPortMappings: Optional[Dict[str, Any]] = {}
    # Config snapshot (Phase 2): the template/tag-schema versions, resolved tags, and whether a
    # caller-supplied override body was used — so a run is traceable after templates later change.
    templateId: Optional[str] = ""
    templateSchemaVersion: Optional[str] = ""
    tagSchemaVersion: Optional[str] = ""
    templateTags: Optional[List[Dict[str, Any]]] = []
    customTemplateOverrideUsed: Optional[bool] = False


class PipelineExecutionOutputFileRecord(BaseModel, extra='ignore'):
    """PipelineExecutionOutputFilesStorageTable row."""
    pipelineExecutionId: str
    fileType: str
    relativeFilePath: str
    s3Bucket: Optional[str] = ""
    s3Key: Optional[str] = ""
    fileSize: Optional[int] = 0
    contentType: Optional[str] = ""
    s3VersionId: Optional[str] = ""


class PipelineExecutionOutputMetadataRecord(BaseModel, extra='ignore'):
    """PipelineExecutionOutputMetadataStorageTable row."""
    pipelineExecutionId: str
    targetFilePath: str
    metadataKey: str
    metadataValue: Optional[str] = ""
    sourceMetadataFileRelativePath: Optional[str] = ""


class PipelineExecutionOutputResultRecord(BaseModel, extra='ignore'):
    """PipelineExecutionOutputResultsStorageTable row."""
    pipelineExecutionId: str
    relativeFilePath: str
    resultsContent: Optional[str] = ""
    resultsContentTruncated: Optional[bool] = False
    s3Key: Optional[str] = ""


class PipelineExecutionLogRecord(BaseModel, extra='ignore'):
    """PipelineExecutionLogsStorageTable row."""
    pipelineExecutionId: str
    logType: str = "summary"
    resultLog: Optional[str] = ""
    resultLogTruncated: Optional[bool] = False
    errorLog: Optional[str] = ""
    errorLogTruncated: Optional[bool] = False
    logGroupArn: Optional[str] = ""
    logStreamName: Optional[str] = ""


class WorkflowExecutionInputRecord(BaseModel, extra='ignore'):
    """WorkflowExecutionInputsStorageTable row (asset-scoped GET source)."""
    workflowExecutionId: str
    assetId: str
    databaseId: str
    inputAssetFileKey: str
    # Concrete S3 VersionId read for this file (resolved at launch); empty for folder/whole-asset.
    versionId: Optional[str] = ""
    executionStartDate: Optional[str] = ""
    workflowId: Optional[str] = ""
    workflowDatabaseId: Optional[str] = ""


class WorkflowExecutionConfigurationRecord(BaseModel, extra='ignore'):
    """WorkflowExecutionConfigurationStorageTable row."""
    workflowExecutionId: str
    recordType: str = "configuration"
    workflowConfiguration: Optional[str] = ""
    workflowConfigurationTruncated: Optional[bool] = False
    inputMetadata: Optional[str] = ""
    inputMetadataTruncated: Optional[bool] = False
    specifiedPipelinesSnapshot: Optional[List[Dict[str, Any]]] = []
    # Output target (where outputs are written).
    outputLocationType: Optional[str] = "asset"
    outputAssetId: Optional[str] = ""
    outputDatabaseId: Optional[str] = ""
    # Output base path extension (dynamic-tag-templated sub-path under the output asset); "/" = root.
    outputFileBaseExecutionPathExtension: Optional[str] = "/"
    # Input-metadata source (recording only).
    inputMetadataAssetId: Optional[str] = ""
    inputMetadataDatabaseId: Optional[str] = ""
    inputMetadataFileS3Key: Optional[str] = ""


#######################
# Execution API request / response models
#######################

# Trigger types on the execute request. The storage layer records the canonical stored values
# "Manual"/"File-Upload" (TRIGGER_TYPES above); the request accepts the lowercase forms and the
# handler maps them (TRIGGER_TYPE_TO_STORED).
EXECUTE_TRIGGER_TYPES = ("manual", "fileUpload")
TRIGGER_TYPE_TO_STORED = {"manual": "Manual", "fileUpload": "File-Upload"}

# Upper bound on the input-file selection per execute request. Bounds the per-request S3 existence
# checks + metadata-service fan-out; matches the asset-upload request's MAX_FILES_PER_UPLOAD_REQUEST.
MAX_INPUT_FILES_PER_EXECUTION = 1000


def _validate_id(value, allow_global=False):
    from common.validators import validate
    (valid, message) = validate({
        "id": {"value": value, "validator": "ID", "allowGlobalKeyword": allow_global}
    })
    if not valid:
        raise ValueError(message)


def _validate_asset_id(value):
    """Validate an assetId with the same rule the asset handlers use (ASSET_ID / filename pattern),
    NOT the strict database-style ID pattern — asset ids may contain dots/spaces and be up to 256
    chars, so validating them as IDs would reject legitimately-named assets."""
    from common.validators import validate
    (valid, message) = validate({
        "assetId": {"value": value, "validator": "ASSET_ID"}
    })
    if not valid:
        raise ValueError(message)


class ExecuteInputFileModel(BaseModel, extra='ignore'):
    """One selected input file for an execution. relativeFileKey is asset-relative; '/' selects the
    whole asset and '/folder/' a folder. versionId is optional (latest when empty)."""
    databaseId: str = Field(..., min_length=1, max_length=256)
    assetId: str = Field(..., min_length=1, max_length=256)
    relativeFileKey: str = Field(..., min_length=1, max_length=1024)
    versionId: Optional[str] = Field("", max_length=256)

    @root_validator
    def validate_input_ids(cls, values):
        # databaseId uses the ID rule (GLOBAL allowed); assetId uses ASSET_ID (filename pattern) —
        # matching how the asset/database handlers validate them, so a legitimately-named asset is
        # not rejected here and garbage ids are caught before reaching DynamoDB.
        if values.get("databaseId"):
            _validate_id(values.get("databaseId"), allow_global=True)
        if values.get("assetId"):
            _validate_asset_id(values.get("assetId"))
        # relativeFileKey is asset-relative and must begin with "/" ("/" = whole asset,
        # "/folder/" = a folder, "/folder/file" = a file). Enforce the leading slash and reject
        # ".." traversal segments so it can't be an absolute S3 key or escape the asset prefix.
        # (The RELATIVE_FILE_PATH validator's min-length-3 rule is NOT used here because the "/"
        # whole-asset and "/x" short forms are legitimate selections.)
        key = values.get("relativeFileKey")
        if key is not None:
            if not key.startswith("/"):
                raise ValueError("relativeFileKey must be asset-relative and begin with '/'")
            if ".." in key:
                raise ValueError("relativeFileKey must not contain '..'")
        return values


class TemplateTagValue(BaseModel, extra='ignore'):
    """One caller-supplied template tag key/value pair."""
    key: str
    value: Optional[Any] = None


class PipelineExecutionParameters(BaseModel, extra='ignore'):
    """Per-pipeline execution parameters: which template + its tag values, or a custom override
    config body. See the template-resolution 5-case contract."""
    templateId: Optional[str] = None
    templateTags: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    customTemplateOverride: Optional[str] = None


class ExecuteWorkflowRequestV2Model(BaseModel, extra='ignore'):
    """The asset-less execute request. inputFiles is 0..N (arity is validated by the cross-entity
    validator against the workflow/pipeline system config). pipelineExecutionParameters is keyed by
    pipelineId. outputAsset* are honored only when the workflow's outputTarget allows override."""
    inputFiles: Optional[List[ExecuteInputFileModel]] = Field(
        default_factory=list, max_items=MAX_INPUT_FILES_PER_EXECUTION)
    outputAssetId: Optional[str] = None
    outputDatabaseId: Optional[str] = None
    # Optional base path prefix (under the output asset) that output files are written beneath. May
    # contain dynamic tag placeholders (e.g. {{firstAssetFileFileNameNoExt}}) resolved at launch.
    # Empty/omitted -> "/" (asset root). Normalized to a single leading + trailing "/".
    outputFileBaseExecutionPathExtension: Optional[str] = Field(None, max_length=1024)
    pipelineExecutionParameters: Optional[Dict[str, Dict[str, Any]]] = Field(default_factory=dict)
    executionGroupId: Optional[str] = Field(None, max_length=64)
    triggerType: Optional[str] = "manual"

    @validator("triggerType", pre=True)
    def _normalize_trigger(cls, v):
        if v is None or v == "":
            return "manual"
        return v

    @root_validator
    def validate_fields(cls, values):
        trigger = values.get("triggerType")
        if trigger not in EXECUTE_TRIGGER_TYPES:
            raise ValueError(f"triggerType must be one of {EXECUTE_TRIGGER_TYPES}")
        # Output-target ids, when supplied, must be valid. outputAssetId uses the ASSET_ID rule
        # (filename pattern, dots/spaces allowed, up to 256 chars) — the same rule the asset handlers
        # use — NOT the strict database ID pattern, which would reject a legitimately-named output
        # asset on both execute (override) and re-run. outputDatabaseId uses the ID rule (GLOBAL ok).
        if values.get("outputAssetId"):
            _validate_asset_id(values.get("outputAssetId"))
        if values.get("outputDatabaseId"):
            _validate_id(values.get("outputDatabaseId"), allow_global=True)
        # executionGroupId becomes a GSI partition key; validate its format (ID rule) when supplied so
        # a malformed/oversized value cannot reach DynamoDB and 500 after the SFN start.
        if values.get("executionGroupId"):
            _validate_id(values.get("executionGroupId"))
        # outputFileBaseExecutionPathExtension is the (dynamic-tag-templated) sub-path outputs are
        # written under, relative to the output asset root. It becomes part of an S3 key, so reject
        # ".." traversal and absolute/backslash forms. It may contain "{{tag}}" placeholders, so the
        # remaining shape is normalized (not strictly validated) in the handler. None/"" is allowed.
        ext = values.get("outputFileBaseExecutionPathExtension")
        if ext:
            if ".." in ext:
                raise ValueError("outputFileBaseExecutionPathExtension must not contain '..'")
            if "\\" in ext:
                raise ValueError("outputFileBaseExecutionPathExtension must not contain backslashes")
        # A per-pipeline templateId is used directly as a DynamoDB key at resolution; validate its
        # format here (like every other id) so a malformed value cannot reach the lookup.
        for params in (values.get("pipelineExecutionParameters") or {}).values():
            if isinstance(params, dict) and params.get("templateId"):
                _validate_id(params.get("templateId"))
        return values


class ExecuteWorkflowResponseModel(BaseModel, extra='ignore'):
    """Response to a successful execute: the new execution id (+ group id when set) and any non-fatal
    warnings surfaced by the cross-entity validator."""
    executionId: str
    executionGroupId: Optional[str] = None
    warnings: Optional[List[str]] = None


class ListExecutionsRequestModel(BaseModel, extra='ignore'):
    """Request body model for listing an asset's workflow executions.

    databaseId / assetId / (optional) workflowId arrive as path parameters and are validated in the
    handler. The body optionally carries the workflow's database to filter by a specific workflow.
    """
    workflowDatabaseId: Optional[str] = None

    @root_validator
    def validate_fields(cls, values):
        from common.validators import validate
        (valid, message) = validate({
            'workflowDatabaseId': {
                'value': values.get('workflowDatabaseId', '') or '',
                'validator': 'ID',
                'allowGlobalKeyword': True,
                'optional': True
            }
        })
        if not valid:
            raise ValueError(message)
        return values


class RerunExecutionRequestModel(BaseModel, extra='ignore'):
    """Re-run reconstructs the execute request from stored records; the caller may optionally reuse
    the original executionGroupId or supply a new one."""
    executionGroupId: Optional[str] = Field(None, max_length=64)

    @root_validator
    def validate_fields(cls, values):
        # Same GSI-partition-key format guard as the execute request.
        if values.get("executionGroupId"):
            _validate_id(values.get("executionGroupId"))
        return values


class PermanentDeleteRequestModel(BaseModel, extra='ignore'):
    """Confirmation guard for permanently deleting an execution's DynamoDB records (admin-only)."""
    confirmDelete: bool = False

    # always=True so the guard fires even when confirmDelete is omitted (a plain @validator only runs
    # on supplied values, which would let an omitted field bypass the confirmation).
    @validator("confirmDelete", always=True)
    def validate_confirmation(cls, v):
        if not v:
            raise ValueError("confirmDelete must be true to permanently delete an execution")
        return v


class ResolvedPipelineConfig(BaseModel, extra='ignore'):
    """The result of the per-pipeline template-resolution phase (WB5.1). Not persisted directly; the
    handler uses it to write the config S3 file + the config-snapshot record and to render the
    pipeline's input configuration."""
    pipelineId: str
    pipelineDatabaseId: str
    templateId: Optional[str] = ""
    renderedConfig: Optional[str] = ""
    templateTags: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    customTemplateOverrideUsed: bool = False
    configFormat: Optional[str] = "json"
