# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 models for the workflow V2 storage data model (Phase 2).

These document the canonical record shapes for the workflow, trigger, and execution-output-index
tables. Handlers persist dicts via common.workflows.workflowRecords builders; these models are used
for validation and parsing where helpful. All use the v1 idiom (BaseModel from
aws_lambda_powertools, extra='ignore').
"""

from typing import Any, Dict, List, Optional

from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from customLogging.logger import safeLogger

logger = safeLogger(service_name="WorkflowV2Models")

TRIGGER_TYPES = ("fileUpload",)
CONCURRENCY_RESTRICTIONS = ("none", "perAsset", "perInputFile")
# outputTarget.locationType values. "asset" writes outputs onto an asset; "none" is results-only
# (no asset files/metadata — only results text + logs against the execution), which requires an
# input-file arity of "none".
OUTPUT_LOCATION_TYPES = ("asset", "none")
# systemConfig.inputFileArity values: "none" (results-only, no input file), "one" (single input
# file), "multi" (multiple input files). Shared by pipeline + workflow system-config validation.
INPUT_FILE_ARITIES = ("none", "one", "multi")
# Schemes permitted for the workflow subDashboardUrl. Restricting to http(s) prevents a stored
# XSS vector (e.g. a javascript:/data: URL rendered as a link in the web UI).
_ALLOWED_URL_SCHEMES = ("http://", "https://")


def _validate_subdashboard_url(url):
    """Validate an optional subDashboardUrl: must be an absolute http(s) URL. Empty/None is allowed
    (no dashboard link). Blocks javascript:/data:/other schemes to prevent stored XSS in the UI."""
    if not url:
        return
    if not any(url.lower().startswith(scheme) for scheme in _ALLOWED_URL_SCHEMES):
        raise ValueError("subDashboardUrl must be an absolute http:// or https:// URL")


def _validate_id(value, allow_global=False):
    """Validate an id via the common validator framework; raises ValueError on failure."""
    from common.validators import validate
    (valid, message) = validate({
        "id": {"value": value, "validator": "ID", "allowGlobalKeyword": allow_global}
    })
    if not valid:
        raise ValueError(message)


def _validate_output_target(system_config):
    """Validate systemConfig.outputTarget.locationType and its coupling to inputFileArity.

    - locationType 'none' (results-only) writes no asset outputs but MAY still take input files
      (e.g. a metadata-analysis workflow that reads files and emits only results text + logs), so
      it is allowed with any inputFileArity.
    - locationType 'asset' with inputFileArity 'none' has no input asset to lock the output to, so
      an output asset must be selectable at execute time: allowOverride must be true (otherwise
      every execution would fail for want of an output target). No-op when systemConfig/outputTarget
      is absent."""
    if not system_config:
        return
    output_target = system_config.get("outputTarget") or {}
    location_type = output_target.get("locationType")
    if location_type is None:
        return
    if location_type not in OUTPUT_LOCATION_TYPES:
        raise ValueError(f"outputTarget.locationType must be one of {OUTPUT_LOCATION_TYPES}")
    if (location_type == "asset"
            and system_config.get("inputFileArity", "one") == "none"
            and not output_target.get("allowOverride", False)):
        raise ValueError(
            "A workflow with no input files (inputFileArity 'none') that writes to an asset must "
            "allow output override (outputTarget.allowOverride true) so an output asset can be "
            "chosen at execution time.")


def _validate_input_file_arity(system_config):
    """Reject an inputFileArity outside the allowed set. No-op when systemConfig is absent or the
    key is omitted (the default 'one' applies downstream)."""
    if not system_config:
        return
    arity = system_config.get("inputFileArity")
    if arity is not None and arity not in INPUT_FILE_ARITIES:
        raise ValueError(f"inputFileArity must be one of {INPUT_FILE_ARITIES}")


def _validate_system_config_shapes(system_config):
    """Validate the assetScope / metadataInputs / inputFileFilters value shapes shared with the
    pipeline systemConfig (boolean maps with known keys, string-list filters). Reuses the pipeline
    model's shared validator so workflow and pipeline systemConfig are validated identically."""
    from models.pipelines import _validate_system_config_shape
    _validate_system_config_shape(system_config, "systemConfig")


class WorkflowSystemConfigModel(BaseModel, extra='ignore'):
    """Workflow system-config block."""
    inputFileArity: str = "one"
    assetScope: Optional[Dict[str, bool]] = {}
    metadataInputs: Optional[Dict[str, bool]] = {}
    inputFileFilters: Optional[Dict[str, List[str]]] = {}
    concurrencyRestriction: str = "none"
    outputTarget: Optional[Dict[str, Any]] = {}


class SpecifiedPipelineRef(BaseModel, extra='ignore'):
    """One ordered pipeline reference within a workflow snapshot."""
    pipelineDatabaseId: str
    pipelineId: str
    jobName: Optional[str] = ""


class WorkflowRecordV2(BaseModel, extra='ignore'):
    """WorkflowStorageTableV2 row (PK databaseId, SK workflowId)."""
    databaseId: str
    workflowId: str
    workflowName: Optional[str] = ""
    category: Optional[str] = ""
    description: Optional[str] = ""
    workflow_arn: Optional[str] = ""
    aslSchemaVersion: Optional[str] = ""
    specifiedPipelines: Optional[List[Dict[str, Any]]] = []
    systemConfig: Optional[Dict[str, Any]] = {}
    subDashboardUrl: Optional[str] = ""
    enabled: bool = True
    archived: bool = False
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
    createdBy: Optional[str] = ""
    modifiedBy: Optional[str] = ""
    schemaVersion: Optional[int] = 1


class WorkflowTriggerRecord(BaseModel, extra='ignore'):
    """WorkflowTriggersStorageTable row (PK workflowDatabaseId:workflowId, SK triggerType)."""
    workflowDatabaseId: str
    workflowId: str
    triggerType: str
    triggerConfig: Optional[Dict[str, Any]] = {}
    enabled: bool = True
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""


class WorkflowExecutionOutputIndexRecord(BaseModel, extra='ignore'):
    """WorkflowExecutionOutputsIndexStorageTable row (PK databaseId:assetId, SK workflowExecutionId)."""
    databaseId: str
    assetId: str
    workflowExecutionId: str


#######################
# Workflow API request / response models
#######################

class SpecifiedPipelineInput(BaseModel, extra='ignore'):
    """A pipeline reference in a create/update request. pipelineDatabaseId defaults to the workflow's
    database when omitted (the common same-database case); jobName is optional (generated if absent).
    defaultTemplateId is the fallback template the execution uses for this pipeline when the run supplies
    no per-pipeline templateId (used e.g. by the v2.5->v2.6 migration for consolidated built-in refs)."""
    pipelineId: str = Field(..., min_length=1, max_length=64)
    pipelineDatabaseId: Optional[str] = None
    jobName: Optional[str] = ""
    defaultTemplateId: Optional[str] = Field("", max_length=64)

    @root_validator
    def validate_ids(cls, values):
        # pipelineId (and pipelineDatabaseId when present) are used as DynamoDB key values to
        # resolve the pipeline record — validate the id format like every other id in the API.
        _validate_id(values.get("pipelineId"))
        pdb = values.get("pipelineDatabaseId")
        if pdb:
            _validate_id(pdb, allow_global=True)
        return values


class CreateWorkflowRequestModel(BaseModel, extra='ignore'):
    """Create a workflow (V2). workflowId optional — a GUID is generated when omitted."""
    databaseId: str = Field(..., min_length=1, max_length=256)
    workflowId: Optional[str] = Field(None, min_length=1, max_length=64)
    workflowName: str = Field(..., min_length=1, max_length=256)
    category: Optional[str] = Field("", max_length=256)
    description: Optional[str] = Field("", max_length=1024)
    specifiedPipelines: List[SpecifiedPipelineInput] = Field(..., min_items=1)
    systemConfig: Optional[Dict[str, Any]] = Field(default_factory=dict)
    subDashboardUrl: Optional[str] = Field("", max_length=2048)
    enabled: Optional[bool] = True

    @root_validator
    def validate_fields(cls, values):
        _validate_id(values.get("databaseId"), allow_global=True)
        if values.get("workflowId"):
            _validate_id(values.get("workflowId"))
        pipelines = values.get("specifiedPipelines") or []
        if len(pipelines) < 1:
            raise ValueError("At least one pipeline is required in specifiedPipelines")
        cr = values.get("systemConfig", {}).get("concurrencyRestriction") if values.get("systemConfig") else None
        if cr is not None and cr not in CONCURRENCY_RESTRICTIONS:
            raise ValueError(f"concurrencyRestriction must be one of {CONCURRENCY_RESTRICTIONS}")
        _validate_input_file_arity(values.get("systemConfig"))
        _validate_output_target(values.get("systemConfig"))
        _validate_system_config_shapes(values.get("systemConfig"))
        _validate_subdashboard_url(values.get("subDashboardUrl"))
        return values


class UpdateWorkflowRequestModel(BaseModel, extra='ignore'):
    """Update a workflow (V2). Only supplied fields are changed."""
    workflowName: Optional[str] = Field(None, min_length=1, max_length=256)
    category: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    specifiedPipelines: Optional[List[SpecifiedPipelineInput]] = None
    systemConfig: Optional[Dict[str, Any]] = None
    subDashboardUrl: Optional[str] = Field(None, max_length=2048)
    enabled: Optional[bool] = None

    @root_validator
    def validate_fields(cls, values):
        if not any(v is not None for v in values.values()):
            raise ValueError("At least one field must be provided for update")
        pipelines = values.get("specifiedPipelines")
        if pipelines is not None and len(pipelines) < 1:
            raise ValueError("specifiedPipelines must contain at least one pipeline when provided")
        cfg = values.get("systemConfig")
        if cfg is not None:
            cr = cfg.get("concurrencyRestriction")
            if cr is not None and cr not in CONCURRENCY_RESTRICTIONS:
                raise ValueError(f"concurrencyRestriction must be one of {CONCURRENCY_RESTRICTIONS}")
            _validate_input_file_arity(cfg)
            _validate_output_target(cfg)
            _validate_system_config_shapes(cfg)
        _validate_subdashboard_url(values.get("subDashboardUrl"))
        return values


class WorkflowResponseModel(BaseModel, extra='ignore'):
    """Response model for a workflow (V2). Mirrors the stored record; save responses carry warnings."""
    databaseId: str
    workflowId: str
    workflowName: Optional[str] = ""
    category: Optional[str] = ""
    description: Optional[str] = ""
    workflow_arn: Optional[str] = ""
    aslSchemaVersion: Optional[str] = ""
    specifiedPipelines: Optional[List[Dict[str, Any]]] = []
    systemConfig: Optional[Dict[str, Any]] = {}
    subDashboardUrl: Optional[str] = ""
    enabled: bool = True
    archived: bool = False
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
    createdBy: Optional[str] = ""
    modifiedBy: Optional[str] = ""
    schemaVersion: Optional[int] = 1
    # Non-fatal save-time consistency warnings (workflow<->pipeline). Present on create/update.
    warnings: Optional[List[str]] = None
    # A workflow's triggers, present on the single-workflow details response.
    triggers: Optional[List[Dict[str, Any]]] = None
    # Total number of executions for this workflow. Present on list responses (computed per page
    # via a bounded COUNT query on the WorkflowExecutionsByWorkflowGSI); None when not computed.
    executionCount: Optional[int] = None


class GetWorkflowsResponseModel(BaseModel, extra='ignore'):
    """Response model for listing workflows."""
    Items: List[WorkflowResponseModel] = []
    NextToken: Optional[str] = None


#######################
# Trigger API request / response models
#######################

class InputFileFiltersModel(BaseModel, extra='ignore'):
    """allow/exclude filter lists (ext/path/name/wildcard)."""
    allow: Optional[List[str]] = []
    exclude: Optional[List[str]] = []


class SetTriggerRequestModel(BaseModel, extra='ignore'):
    """Set (create/replace) a workflow trigger. fileUpload today: inputFileFilters +
    defaultTemplateIds ({'<pipelineDatabaseId>:<pipelineId>': templateId})."""
    inputFileFilters: Optional[Dict[str, List[str]]] = Field(default_factory=dict)
    defaultTemplateIds: Optional[Dict[str, str]] = Field(default_factory=dict)
    enabled: Optional[bool] = True


class TriggerResponseModel(BaseModel, extra='ignore'):
    """Response model for a workflow trigger."""
    workflowDatabaseId: str
    workflowId: str
    triggerType: str
    triggerConfig: Optional[Dict[str, Any]] = {}
    enabled: bool = True
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""


class GetTriggersResponseModel(BaseModel, extra='ignore'):
    """Response model for listing a workflow's triggers."""
    Items: List[TriggerResponseModel] = []
