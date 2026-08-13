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

# Upper bound on the pipeline steps in one workflow. Each step becomes a Step Functions task state
# (plus its interim-tracking states) in a single state-machine definition, one pipeline-execution row
# per run, and one config + manifest S3 object per run. Generous relative to real workflows (the
# built-ins declare a handful of steps) while bounding the ASL a single request can generate.
MAX_SPECIFIED_PIPELINES = 100

# Upper bound on a trigger's defaultTemplateIds map: one entry per pipeline step the trigger names a
# default template for, so it is bounded by the same step count.
MAX_TRIGGER_DEFAULT_TEMPLATES = MAX_SPECIFIED_PIPELINES

# systemConfig.outputTarget keys whose value is a plain boolean gate. allowOverride decides whether
# an execute request may redirect output away from the input asset, so a truthy string like "false"
# stored here reads back as True and opens the override the author disabled.
_OUTPUT_TARGET_BOOLEAN_KEYS = ("allowOverride",)


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
    if not isinstance(output_target, dict):
        raise ValueError("systemConfig.outputTarget must be an object")
    # The boolean gates are checked whether or not a locationType is declared: allowOverride is read
    # at execute time regardless (the default locationType is 'asset').
    for key in _OUTPUT_TARGET_BOOLEAN_KEYS:
        if key in output_target and not isinstance(output_target[key], bool):
            raise ValueError(f"outputTarget.{key} must be a boolean")
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
    pipeline systemConfig (boolean maps with known keys, string-list filters), plus the top-level key
    set and serialized size bound. Reuses the pipeline model's shared validators so workflow and
    pipeline systemConfig are validated identically."""
    from models.pipelines import _validate_system_config_shape, validate_system_config_keys
    _validate_system_config_shape(system_config, "systemConfig")
    validate_system_config_keys(system_config)
    _validate_allow_workflow_trigger_chaining(system_config)
    _validate_default_output_path_extension(system_config)


def _validate_allow_workflow_trigger_chaining(system_config):
    """allowWorkflowTriggerChaining must be a real boolean when supplied. The shared pipeline shape
    validator does not police top-level scalars, so a truthy string like "false" would otherwise be
    stored and read as True — enabling chained triggering the author meant to disable."""
    if not isinstance(system_config, dict):
        return
    if "allowWorkflowTriggerChaining" not in system_config:
        return
    if not isinstance(system_config["allowWorkflowTriggerChaining"], bool):
        raise ValueError("systemConfig.allowWorkflowTriggerChaining must be a boolean")


def _validate_default_output_path_extension(system_config):
    """Validate systemConfig.defaultOutputFileBaseExecutionPathExtension.

    Stored UNRESOLVED, so `{{tag}}` placeholders are legal here and only the shape rules that survive
    templating are enforced: it becomes part of an S3 key, so reject '..' traversal and backslashes,
    the same two rules the execute request applies to the per-run value. The rendered result is
    re-checked at launch, when the tags have values."""
    if not isinstance(system_config, dict):
        return
    extension = system_config.get("defaultOutputFileBaseExecutionPathExtension")
    if extension is None or extension == "":
        return
    if not isinstance(extension, str):
        raise ValueError(
            "systemConfig.defaultOutputFileBaseExecutionPathExtension must be a string")
    if len(extension) > 1024:
        raise ValueError(
            "systemConfig.defaultOutputFileBaseExecutionPathExtension must be at most 1024 "
            "characters")
    if ".." in extension:
        raise ValueError(
            "systemConfig.defaultOutputFileBaseExecutionPathExtension must not contain '..'")
    if "\\" in extension:
        raise ValueError(
            "systemConfig.defaultOutputFileBaseExecutionPathExtension must not contain backslashes")


def _validate_trigger_input_file_filters(filters):
    """Validate a trigger's inputFileFilters map against the same {allow, exclude} shape the
    pipeline/workflow systemConfig filters use. No-op when absent."""
    if not filters:
        return
    from models.pipelines import _validate_input_file_filters
    _validate_input_file_filters(filters, "inputFileFilters")


def _validate_trigger_default_template_ids(default_template_ids):
    """Validate a trigger's defaultTemplateIds map: {'<pipelineDatabaseId>:<pipelineId>': templateId}.

    Both halves of the key and the value are used as DynamoDB key values to resolve the template a
    headless run launches with (triggerTemplateValidation / triggerMatching), so each is validated
    with the id rule every other id in the API uses. No-op when absent."""
    if not default_template_ids:
        return
    if not isinstance(default_template_ids, dict):
        raise ValueError("defaultTemplateIds must be an object")
    if len(default_template_ids) > MAX_TRIGGER_DEFAULT_TEMPLATES:
        raise ValueError(
            f"defaultTemplateIds may contain at most {MAX_TRIGGER_DEFAULT_TEMPLATES} entries")
    for composite, template_id in default_template_ids.items():
        pipeline_db, separator, pipeline_id = (composite or "").partition(":")
        if not separator:
            raise ValueError(
                "defaultTemplateIds keys must be '<pipelineDatabaseId>:<pipelineId>'")
        _validate_id(pipeline_db, allow_global=True)
        _validate_id(pipeline_id)
        # An empty value means "no default template for this pipeline" and is skipped downstream.
        if template_id:
            _validate_id(template_id)


def _validate_single_line_text(values):
    """Reject control characters / line breaks in the workflow's ABAC-visible name fields, using the
    shared rule the pipeline models apply to their equivalents.

    Scoped to workflowName and category because those are the ABAC CONSTRAINT fields (surfaced as
    `name` / `category` on the Tier-2 Casbin object): a `^<value>$` policy rule matches a trailing
    newline in Python, so a distinct stored name would satisfy a grant written for another.

    `description` is deliberately NOT included. It is not an ABAC constraint field, so it gains no
    security benefit, and the web offers it as a multi-line textarea — guarding it rejected exactly
    what that control produces. Log integrity is not an argument for it either: auditLogging writes
    values through json.dumps, which escapes a newline rather than emitting one."""
    from models.pipelines import validate_no_control_characters
    for field in ("workflowName", "category"):
        validate_no_control_characters(values.get(field), field)


class WorkflowSystemConfigModel(BaseModel, extra='ignore'):
    """Workflow system-config block."""
    inputFileArity: str = "one"
    assetScope: Optional[Dict[str, bool]] = {}
    metadataInputs: Optional[Dict[str, bool]] = {}
    inputFileFilters: Optional[Dict[str, List[str]]] = {}
    concurrencyRestriction: str = "none"
    outputTarget: Optional[Dict[str, Any]] = {}
    # Whether a file written by ANOTHER workflow's execution may fire this workflow's triggers.
    # A workflow never fires on output it wrote itself, whatever this is set to, so an A->A loop
    # cannot be enabled; this governs cross-workflow chaining only (e.g. generating a preview from a
    # conversion pipeline's output). Default off: chained triggering is opt-in per workflow.
    allowWorkflowTriggerChaining: bool = False
    # Default output base path extension applied when an execute request supplies none. Stored
    # UNRESOLVED — {{tag}} placeholders are substituted at launch, so one stored value gives each run
    # its own folder (e.g. "/{{jobName}}/"). Empty means no default (outputs at the asset root).
    defaultOutputFileBaseExecutionPathExtension: Optional[str] = ""


class SpecifiedPipelineRef(BaseModel, extra='ignore'):
    """One ordered pipeline reference within a workflow snapshot (see
    common.workflows.workflowRecords.build_specified_pipeline_ref; the stored row additionally
    carries the derived `pipelineDatabaseId:pipelineId` composite key)."""
    pipelineDatabaseId: str
    pipelineId: str
    jobName: Optional[str] = ""
    defaultTemplateId: Optional[str] = ""


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
    jobName: Optional[str] = Field("", max_length=64)
    defaultTemplateId: Optional[str] = Field("", max_length=64)

    @root_validator
    def validate_ids(cls, values):
        # pipelineId (and pipelineDatabaseId when present) are used as DynamoDB key values to
        # resolve the pipeline record — validate the id format like every other id in the API.
        _validate_id(values.get("pipelineId"))
        pdb = values.get("pipelineDatabaseId")
        if pdb:
            _validate_id(pdb, allow_global=True)
        # jobName becomes the ASL state name and a segment of the execution's S3 output prefix, and
        # is interpolated into a single-quoted States.Format() literal, so it carries the same id
        # character set as every other id in the API.
        job_name = values.get("jobName")
        if job_name:
            _validate_id(job_name)
        # defaultTemplateId is used directly as a DynamoDB sort key to resolve the fallback template
        # at execute time (executeWorkflow._resolve_pipeline_configs), so it carries the same id rule
        # as the per-run templateId on the execute request.
        default_template_id = values.get("defaultTemplateId")
        if default_template_id:
            _validate_id(default_template_id)
        return values


class CreateWorkflowRequestModel(BaseModel, extra='ignore'):
    """Create a workflow (V2). workflowId optional — a GUID is generated when omitted."""
    databaseId: str = Field(..., min_length=1, max_length=256)
    workflowId: Optional[str] = Field(None, min_length=1, max_length=64)
    workflowName: str = Field(..., min_length=1, max_length=256)
    category: Optional[str] = Field("", max_length=256)
    description: Optional[str] = Field("", max_length=1024)
    specifiedPipelines: List[SpecifiedPipelineInput] = Field(
        ..., min_items=1, max_items=MAX_SPECIFIED_PIPELINES)
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
        # workflowName and category are ABAC constraint fields (surfaced as `name` / `category` on the
        # Tier-2 Casbin object) and both appear in single-line log entries.
        _validate_single_line_text(values)
        return values


class UpdateWorkflowRequestModel(BaseModel, extra='ignore'):
    """Update a workflow (V2). Only supplied fields are changed."""
    workflowName: Optional[str] = Field(None, min_length=1, max_length=256)
    category: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    specifiedPipelines: Optional[List[SpecifiedPipelineInput]] = Field(
        None, max_items=MAX_SPECIFIED_PIPELINES)
    systemConfig: Optional[Dict[str, Any]] = None
    subDashboardUrl: Optional[str] = Field(None, max_length=2048)
    enabled: Optional[bool] = None
    # Clearing this is the path re-registration of a built-in takes to restore an archived row.
    archived: Optional[bool] = None

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
        _validate_single_line_text(values)
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
    # How many triggers the workflow has, and how many of those are enabled. Present on list
    # responses (one bounded triggers query per workflow on the page); None when not computed.
    # The two differ when a trigger exists but is switched off, which is why both are reported —
    # `triggerCount` alone cannot distinguish "no triggers" from "triggers, all disabled".
    triggerCount: Optional[int] = None
    triggersEnabledCount: Optional[int] = None
    # The file restriction this workflow effectively imposes, computed server-side from the workflow's
    # own inputFileFilters and (when those are open) its pipelines':
    # {allow, exclude, source: 'workflow'|'pipelines', includesTemplateOverrides: false}.
    #
    # For DISPLAY only. `includesTemplateOverrides` is always false: a template is chosen per
    # execution, so its `overrides` cannot be folded in here. A caller validating a concrete file
    # selection must resolve the chain itself (workflow -> pipeline -> chosen template's overrides)
    # rather than testing against this aggregate. None when not computed.
    aggregateWorkflowPipelineInputFileFilters: Optional[Dict[str, Any]] = None
    # The metadata inputs, input arity and output target the chain implies, for the same display
    # purpose and with the same template-override caveat.
    aggregateWorkflowPipelineMetadataInputs: Optional[Dict[str, Any]] = None


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
    # The entry-count bound is enforced in the root validator: max_items is a list constraint, and
    # pydantic v1 raises at class-definition time when it is set on a Dict field.
    defaultTemplateIds: Optional[Dict[str, str]] = Field(default_factory=dict)
    enabled: Optional[bool] = True

    @root_validator
    def validate_filters(cls, values):
        # Dispatch treats an absent `allow` list as allow-all, so a key outside {allow, exclude}
        # would turn a scoped trigger into one that fires on every uploaded file.
        _validate_trigger_input_file_filters(values.get("inputFileFilters"))
        _validate_trigger_default_template_ids(values.get("defaultTemplateIds"))
        return values


class TriggerResponseModel(BaseModel, extra='ignore'):
    """Response model for a workflow trigger.

    `triggerType` is the trigger's KEY: the bare type for a workflow's first trigger of that type, or
    `type#triggerId` for an additional one — a workflow may carry several triggers of one type, each
    with its own filters and default templates. It is the value to send back on the trigger path to
    address this specific trigger. `triggerBaseType` is the plain type for grouping and display, and
    `triggerId` is empty for the first trigger of a type.
    """
    workflowDatabaseId: str
    workflowId: str
    triggerType: str
    triggerBaseType: Optional[str] = ""
    triggerId: Optional[str] = ""
    triggerConfig: Optional[Dict[str, Any]] = {}
    enabled: bool = True
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""


class GetTriggersResponseModel(BaseModel, extra='ignore'):
    """Response model for listing a workflow's triggers."""
    Items: List[TriggerResponseModel] = []
