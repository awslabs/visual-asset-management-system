# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 models for the workflow-execution storage data model (Stage 1).

These document the canonical record shapes for the 11 execution storage tables.
Handlers persist dicts via common.workflows.executionRecords builders; these models are
used for validation and parsing where helpful. All use the v1 idiom
(BaseModel from aws_lambda_powertools, extra='ignore').
"""

import json
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
    """PipelineExecutionInputMetadataStorageTable row.

    scope discriminates what the row's metadata describes: 'asset' for an asset/file row, 'database'
    for a metadata-source database's own metadata (empty assetId, '/' filePath). A row stored without
    it is an asset row.

    attributes holds the file's ATTRIBUTES, separate from metadata because a pipeline's metadataInputs
    gates fileMetadata and fileAttributes independently — merging them would lose which gate delivered
    a value. Empty for asset-level and database-scope rows, and for a row whose file has none."""
    pipelineExecutionId: str
    assetId: str
    databaseId: str
    filePath: str
    scope: Optional[str] = "asset"
    metadata: Optional[Dict[str, Any]] = {}
    attributes: Optional[Dict[str, Any]] = {}
    sourceInputMetadataFileS3Key: Optional[str] = ""


class PipelineExecutionInputConfigurationRecord(BaseModel, extra='ignore'):
    """PipelineExecutionInputConfigurationStorageTable row."""
    pipelineExecutionId: str
    recordType: str = "configuration"
    inputConfiguration: Optional[str] = ""
    inputConfigurationTruncated: Optional[bool] = False
    inputConfigurationFileS3Key: Optional[str] = ""
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
    inputMetadata: Optional[str] = ""
    inputMetadataTruncated: Optional[bool] = False
    specifiedPipelinesSnapshot: Optional[List[Dict[str, Any]]] = []
    # Output target (where outputs are written).
    outputLocationType: Optional[str] = "asset"
    outputAssetId: Optional[str] = ""
    outputDatabaseId: Optional[str] = ""
    # Output base path extension (dynamic-tag-templated sub-path under the output asset); "/" = root.
    outputFileBaseExecutionPathExtension: Optional[str] = "/"
    # Input-metadata source (recording only). inputMetadataDatabaseId is the databaseMetadata source
    # database the caller NAMED; metadataSourceAssets are the [{databaseId, assetId}] entities named
    # purely as metadata sources (never input files), so a re-run reconstructs the same selection.
    # metadataSourceDatabases is every databaseId the run actually captured database metadata from,
    # which for a run with input files is derived from those files' assets rather than named.
    inputMetadataDatabaseId: Optional[str] = ""
    inputMetadataFileS3Key: Optional[str] = ""
    metadataSourceAssets: Optional[List[Dict[str, Any]]] = []
    metadataSourceDatabases: Optional[List[str]] = []


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

# Upper bound on the metadata-source asset selection per execute request. The same bound as the
# input-file selection, since it bounds the same per-request metadata-service fan-out.
MAX_METADATA_SOURCE_ASSETS_PER_EXECUTION = MAX_INPUT_FILES_PER_EXECUTION

# Upper bound on the per-pipeline parameter map. One entry per pipeline step in the workflow, so it
# mirrors the workflow's own step cap (models.workflows.MAX_SPECIFIED_PIPELINES).
MAX_PIPELINE_EXECUTION_PARAMETERS = 100

# Upper bound on the caller-supplied template tag values for one pipeline. Each is validated against
# the template's declared schema, which is itself capped at MAX_TAG_SCHEMA_FIELDS definitions;
# extra tags are ignored at resolution, so this only bounds the request.
MAX_TEMPLATE_TAGS_PER_PIPELINE = 250
MAX_TEMPLATE_TAG_KEY_LENGTH = 128
# Serialized bound on one tag value. Generous: a tag may legitimately carry a long GenAI prompt.
MAX_TEMPLATE_TAG_VALUE_LENGTH = 65536
# Aggregate serialized bound on one pipeline's tag list, and on one entry once its non-contract keys
# are counted. The per-entry bounds multiply out to far more than one DynamoDB item can hold, and the
# list is persisted verbatim on the config-snapshot record alongside the rendered config, so an
# oversized list is only discovered by put_item — after the state machine has started, which
# force-stops the run and answers a valid-looking request with a 500. Bounding it here keeps that
# rejection a 400 before launch. 128 KB admits the full entry count at a realistic value length, or a
# long GenAI prompt on several tags, while leaving the config record's other fields room under the
# 400 KB item limit; the record builder additionally holds the stored copy to its own byte budget.
MAX_TEMPLATE_TAGS_TOTAL_LENGTH = 128 * 1024

# Upper bound on a caller-supplied customTemplateOverride body, matching the absolute cap the stored
# template bodies are held to (common/workflows/templateBodyStorage.ABSOLUTE_CAP_BYTES). The rendered
# result is written as the run's config S3 object and snapshotted (truncated) on the config record.
MAX_CUSTOM_TEMPLATE_OVERRIDE_LENGTH = 5 * 1024 * 1024


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
    # An S3 VersionId is URL-safe ([A-Za-z0-9._-], plus the literal "null" on an unversioned
    # object). The value is stored on the input/metadata rows and echoed in log lines, so the
    # charset is pinned here rather than relying on S3 to reject it at read time.
    versionId: Optional[str] = Field("", max_length=256, regex=r"^[A-Za-z0-9._\-]*$")

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
            # A backslash is a legal S3 key character, so it survives into the key rather than being
            # rejected downstream; it is also how a Windows-style traversal is written. Spaces and
            # unicode stay accepted — both are legitimate in a file path.
            if "\\" in key:
                raise ValueError("relativeFileKey must not contain backslashes")
        return values


class MetadataSourceAssetModel(BaseModel, extra='ignore'):
    """One asset named purely as a metadata source for an execution. It carries no file key — a
    metadata source is an entity, never a file — and is not an input file, so it does not participate
    in arity, filters, or output-target resolution."""
    databaseId: str = Field(..., min_length=1, max_length=256)
    assetId: str = Field(..., min_length=1, max_length=256)

    @root_validator
    def validate_source_ids(cls, values):
        # Same rules as ExecuteInputFileModel: databaseId uses the ID rule (GLOBAL allowed, since an
        # asset may live in the GLOBAL database), assetId uses ASSET_ID (filename pattern).
        if values.get("databaseId"):
            _validate_id(values.get("databaseId"), allow_global=True)
        if values.get("assetId"):
            _validate_asset_id(values.get("assetId"))
        return values


class TemplateTagValue(BaseModel, extra='ignore'):
    """One caller-supplied template tag key/value pair. The value is typed Any (a string, number,
    boolean, or string list per the tag's declared type) and is bounded by its serialized length."""
    key: str = Field(..., min_length=1, max_length=MAX_TEMPLATE_TAG_KEY_LENGTH)
    value: Optional[Any] = None

    @validator("value")
    def validate_value_size(cls, v):
        if v is None:
            return v
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        if len(text) > MAX_TEMPLATE_TAG_VALUE_LENGTH:
            raise ValueError(
                f"template tag value may be at most {MAX_TEMPLATE_TAG_VALUE_LENGTH} characters "
                "when serialized")
        return v


class PipelineExecutionParameters(BaseModel, extra='ignore'):
    """Per-pipeline execution parameters: which template + its tag values, or a custom override
    config body. See the template-resolution 5-case contract."""
    templateId: Optional[str] = None
    templateTags: Optional[List[TemplateTagValue]] = Field(
        default_factory=list, max_items=MAX_TEMPLATE_TAGS_PER_PIPELINE)
    customTemplateOverride: Optional[str] = Field(
        None, max_length=MAX_CUSTOM_TEMPLATE_OVERRIDE_LENGTH)

    @root_validator
    def validate_template_id(cls, values):
        # templateId is used directly as a DynamoDB sort key at resolution.
        if values.get("templateId"):
            _validate_id(values.get("templateId"))
        return values


def _validate_pipeline_execution_parameters(pipeline_exec_params):
    """Run each per-pipeline parameter block through PipelineExecutionParameters so its field
    validation applies, and validate the map's keys as pipeline ids.

    The blocks stay raw dicts on the request: the resolution path reads them with .get() and
    persists the caller's templateTags verbatim into the config snapshot, so parsing here is for
    validation only. Raises ValueError on failure; no-op when absent."""
    if not pipeline_exec_params:
        return
    if len(pipeline_exec_params) > MAX_PIPELINE_EXECUTION_PARAMETERS:
        raise ValueError(
            f"pipelineExecutionParameters may contain at most "
            f"{MAX_PIPELINE_EXECUTION_PARAMETERS} entries")
    for pipeline_id, params in pipeline_exec_params.items():
        # The map is keyed by pipelineId and each key is matched against a resolved pipeline record.
        _validate_id(pipeline_id)
        if params is None:
            continue
        if not isinstance(params, dict):
            raise ValueError("pipelineExecutionParameters values must be objects")
        try:
            PipelineExecutionParameters(**params)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"pipelineExecutionParameters entry is invalid: {e}")


class ExecuteWorkflowRequestV2Model(BaseModel, extra='ignore'):
    """The asset-less execute request. inputFiles is 0..N (arity is validated by the cross-entity
    validator against the workflow/pipeline system config). pipelineExecutionParameters is keyed by
    pipelineId. outputAsset* are honored only when the workflow's outputTarget allows override."""
    inputFiles: Optional[List[ExecuteInputFileModel]] = Field(
        default_factory=list, max_items=MAX_INPUT_FILES_PER_EXECUTION)
    # Metadata sources: entities whose stored metadata is captured into the execution's metadata
    # payload. They are not input files (they carry no file key, are exempt from arity/filters, and do
    # not resolve an output target) and travel in their own fields so an arity-none run can name them.
    # Selection is optional at every arity; a pipeline that requires the metadata checks for it itself.
    metadataSourceDatabaseId: Optional[str] = None
    metadataSourceAssets: Optional[List[MetadataSourceAssetModel]] = Field(
        default_factory=list, max_items=MAX_METADATA_SOURCE_ASSETS_PER_EXECUTION)
    outputAssetId: Optional[str] = None
    outputDatabaseId: Optional[str] = None
    # Optional base path prefix (under the output asset) that output files are written beneath. May
    # contain dynamic tag placeholders (e.g. {{firstAssetFileFileNameNoExt}}) resolved at launch.
    # Empty/omitted -> "/" (asset root). Normalized to a single leading + trailing "/".
    outputFileBaseExecutionPathExtension: Optional[str] = Field(None, max_length=1024)
    # The entry-count bound is enforced in the root validator: max_items is a list constraint, and
    # pydantic v1 raises at class-definition time when it is set on a Dict field.
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
        # metadataSourceDatabaseId names ONE concrete database whose metadata is read. GLOBAL is
        # rejected (allow_global=False): it is the unscoped/all-databases keyword, not a database
        # whose metadata can be read, so there would be nothing to resolve it to.
        if values.get("metadataSourceDatabaseId"):
            _validate_id(values.get("metadataSourceDatabaseId"), allow_global=False)
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
        # The per-pipeline parameter blocks are validated through PipelineExecutionParameters (the
        # templateId id rule, tag key/value bounds, and override body cap) plus the pipelineId keys.
        _validate_pipeline_execution_parameters(values.get("pipelineExecutionParameters"))
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


# Metadata collections of the execution-detail view that can be read a page at a time. 'input' is the
# asset/file input metadata, 'inputDatabase' the metadata-source databases' own metadata (the
# scope=='database' subset of the same table), and 'output' the per-pipeline output metadata.
DETAIL_METADATA_COLLECTION_INPUT = "input"
DETAIL_METADATA_COLLECTION_INPUT_DATABASE = "inputDatabase"
DETAIL_METADATA_COLLECTION_OUTPUT = "output"
DETAIL_METADATA_COLLECTIONS = (
    DETAIL_METADATA_COLLECTION_INPUT,
    DETAIL_METADATA_COLLECTION_INPUT_DATABASE,
    DETAIL_METADATA_COLLECTION_OUTPUT,
)


class DetailMetadataPageRequestModel(BaseModel, extra='ignore'):
    """Query parameters of the paged execution-detail metadata read. pageSize is clamped to the
    handler's MAX_DETAIL_METADATA_PAGE_SIZE; startingToken/NextToken are the opaque base64
    continuation (either name is accepted, matching the global execution list)."""
    collection: Optional[str] = DETAIL_METADATA_COLLECTION_INPUT
    pageSize: Optional[int] = Field(None, ge=1)
    startingToken: Optional[str] = Field(None, max_length=4096)
    NextToken: Optional[str] = Field(None, max_length=4096)
    pipelineId: Optional[str] = None

    @validator("collection", pre=True)
    def _default_collection(cls, v):
        if v is None or str(v).strip() == "":
            return DETAIL_METADATA_COLLECTION_INPUT
        return str(v).strip()

    @validator("pageSize", pre=True)
    def _blank_page_size(cls, v):
        # Query parameters arrive as strings, and an omitted one is often sent as "" rather than
        # dropped; that is "unspecified", not a parse failure, so it falls back to the default.
        if v is None or str(v).strip() == "":
            return None
        return v

    @root_validator
    def validate_fields(cls, values):
        if values.get("collection") not in DETAIL_METADATA_COLLECTIONS:
            raise ValueError(f"collection must be one of {DETAIL_METADATA_COLLECTIONS}")
        # pipelineId narrows the read to one step's rows and is compared against the stored
        # pipelineId, so it is validated with the same ID rule every other pipeline id uses.
        if values.get("pipelineId"):
            _validate_id(values.get("pipelineId"))
        return values

    def resolved_starting_token(self):
        """The supplied continuation token under either accepted name ("" when none)."""
        return (self.startingToken or self.NextToken or "").strip()


class DetailMetadataPageResponseModel(BaseModel, extra='ignore'):
    """One page of a detail metadata collection. Rows carry the same scrubbed shape the details view
    returns, so a client renders a page with the columns it already has. NextToken is absent on the
    last page."""
    Items: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    collection: str
    NextToken: Optional[str] = None


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
