# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 models for the pipeline V2 storage data model (Phase 2).

These document the canonical record shapes for the pipeline, template, and template-tag-schema
tables. Handlers persist dicts via common.workflows.pipelineRecords builders; these models are used
for validation and parsing where helpful. All use the v1 idiom (BaseModel from
aws_lambda_powertools, extra='ignore').

The tag-field type set is the shared primitive subset (string / integer / number / boolean /
string-list / enum) — deliberately NOT the specialized metadata types (XYZ, matrix, geo). The
tag-field model mirrors the shape of models.metadataSchema.MetadataSchemaFieldModel so the two stay
one paradigm across VAMS; the shared validation surface lands in common/templateTagSchema.py (WB2).
"""

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from customLogging.logger import safeLogger

logger = safeLogger(service_name="PipelineV2Models")

# A bare Lambda function name or name:alias/version (AWS allows [a-zA-Z0-9-_], up to 140 chars,
# optionally suffixed with a ":alias" or ":version"). Full ARNs are validated separately via the
# partition-aware ARN validator.
_LAMBDA_FUNCTION_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9-_]{1,140}(:[a-zA-Z0-9-_$]{1,128})?$")

PIPELINE_EXECUTION_TYPES = ("Lambda", "SQS", "EventBridge", "DeadlineCloud")
TEMPLATE_CONFIG_FORMATS = ("json", "yaml", "openjd", "xml", "raw")
BODY_STORAGE_VALUES = ("inline", "s3")
# systemConfig.inputFileArity values: "none"/"one"/"multi" (mirrors workflows.INPUT_FILE_ARITIES).
INPUT_FILE_ARITIES = ("none", "one", "multi")
# Keys a template's `overrides` may set (must mirror executionValidation.TEMPLATE_OVERRIDABLE_KEYS).
# Any other key is rejected at save so a typo/unknown key is not silently ignored at execute time.
TEMPLATE_OVERRIDE_KEYS = ("inputFileArity", "metadataInputs", "assetScope", "inputFileFilters")
# `wholeAsset` is the shorthand emitted by the CDK pipeline registration schemas
# (vamsSchema/pipeline.json); the four *Allowed keys are the canonical UI/record vocabulary. Both
# are accepted so a registered pipeline's assetScope is not rejected.
_ASSET_SCOPE_KEYS = (
    "crossAssetAllowed", "singleAssetOnly", "wholeAssetAllowed", "folderAllowed", "wholeAsset")
_METADATA_INPUT_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes")


def _validate_system_config_shape(cfg, context):
    """Validate the shared systemConfig value shapes used by both pipeline systemConfig and a
    template's `overrides`: inputFileArity enum, assetScope/metadataInputs boolean maps with known
    keys, and inputFileFilters {allow,exclude} string lists. `context` labels errors. Only validates
    keys that are present (partial configs / overrides are expected). Raises ValueError on failure."""
    if not cfg:
        return
    if not isinstance(cfg, dict):
        raise ValueError(f"{context} must be an object")

    arity = cfg.get("inputFileArity")
    if arity is not None and arity not in INPUT_FILE_ARITIES:
        raise ValueError(f"{context}.inputFileArity must be one of {INPUT_FILE_ARITIES}")

    scope = cfg.get("assetScope")
    if scope is not None:
        if not isinstance(scope, dict):
            raise ValueError(f"{context}.assetScope must be an object")
        for k, v in scope.items():
            if k not in _ASSET_SCOPE_KEYS:
                raise ValueError(f"{context}.assetScope has unknown key '{k}'; allowed: {_ASSET_SCOPE_KEYS}")
            if not isinstance(v, bool):
                raise ValueError(f"{context}.assetScope.{k} must be a boolean")

    metadata = cfg.get("metadataInputs")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ValueError(f"{context}.metadataInputs must be an object")
        for k, v in metadata.items():
            if k not in _METADATA_INPUT_KEYS:
                raise ValueError(f"{context}.metadataInputs has unknown key '{k}'; allowed: {_METADATA_INPUT_KEYS}")
            if not isinstance(v, bool):
                raise ValueError(f"{context}.metadataInputs.{k} must be a boolean")

    filters = cfg.get("inputFileFilters")
    if filters is not None:
        if not isinstance(filters, dict):
            raise ValueError(f"{context}.inputFileFilters must be an object")
        for list_key in ("allow", "exclude"):
            if list_key in filters:
                lst = filters[list_key]
                if not isinstance(lst, list) or not all(isinstance(x, str) for x in lst):
                    raise ValueError(f"{context}.inputFileFilters.{list_key} must be a list of strings")


def _validate_template_overrides(overrides):
    """Validate a template's `overrides` object at save time: only the overridable keys are allowed,
    and each present value is validated against the shared systemConfig shape. No-op when absent."""
    if overrides is None:
        return
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be an object")
    for key in overrides:
        if key not in TEMPLATE_OVERRIDE_KEYS:
            raise ValueError(
                f"overrides may only contain {TEMPLATE_OVERRIDE_KEYS}; got unknown key '{key}'")
    _validate_system_config_shape(overrides, "overrides")


def _validate_template_bodies(config_format, config_body, web_form_json):
    """Best-effort structural validation of the freeform template bodies. The config body is only
    parse-checked when its declared format is JSON (yaml/openjd/xml/raw are passed through as text).
    webFormJson, when present, must be valid JSON (it is a serialized form definition)."""
    import json as _json
    if config_format == "json" and config_body:
        try:
            _json.loads(config_body)
        except (ValueError, TypeError):
            raise ValueError("configBody is not valid JSON (configFormat is 'json')")
    if web_form_json:
        try:
            _json.loads(web_form_json)
        except (ValueError, TypeError):
            raise ValueError("webFormJson must be valid JSON")


def _validate_pipeline_system_config(system_config):
    """Validate a pipeline systemConfig block (inputFileArity enum + assetScope/metadataInputs/
    inputFileFilters value shapes). No-op when absent."""
    _validate_system_config_shape(system_config, "systemConfig")


class TemplateTagType(str, Enum):
    """Primitive tag-value types a template tag may declare (shared primitive subset)."""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string-list"
    ENUM = "enum"


class TemplateTagFieldModel(BaseModel, extra='ignore'):
    """Single tag definition within a template tag schema (mirrors MetadataSchemaFieldModel)."""
    tagKey: str
    type: TemplateTagType = TemplateTagType.STRING
    required: bool = False
    default: Optional[Any] = None
    label: Optional[str] = ""
    description: Optional[str] = ""
    # Allowed values for the enum type; item type for string-list is implicitly string.
    enumValues: Optional[List[str]] = None

    @validator("type", pre=True)
    def normalize_type(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v


class PipelineExecutionConfigModel(BaseModel, extra='ignore'):
    """Typed execution-config block (replaces the loose userProvidedResource JSON).

    The stored DynamoDB shape (see pipelineRecords.build_pipeline_execution_config) keeps a
    per-execution-type sub-block under each of `lambda`, `sqs`, `eventBridge`, `deadlineCloud`.
    `lambda` is a Python keyword and cannot be a pydantic field name, so it is not modeled as a
    named field here; this model (extra='ignore') validates the common scalar fields and the
    non-keyword sub-blocks, and the `lambda` sub-block passes through untouched on the raw dict.
    """
    executionType: str = "Lambda"
    waitForCallback: Optional[str] = "Disabled"
    taskTimeout: Optional[str] = ""
    taskHeartbeatTimeout: Optional[str] = ""
    sqs: Optional[Dict[str, Any]] = {}
    eventBridge: Optional[Dict[str, Any]] = {}
    deadlineCloud: Optional[Dict[str, Any]] = {}


class PipelineSystemConfigModel(BaseModel, extra='ignore'):
    """Pipeline system-config block (admin-only)."""
    inputFileArity: str = "one"
    assetScope: Optional[Dict[str, bool]] = {}
    metadataInputs: Optional[Dict[str, bool]] = {}
    requireTemplate: bool = False
    allowCustomTemplateOverride: bool = False
    auxPreviewPipelineSuffix: Optional[str] = ""
    inputFileFilters: Optional[Dict[str, List[str]]] = {}


class PipelineRecordV2(BaseModel, extra='ignore'):
    """PipelineStorageTableV2 row (PK databaseId, SK pipelineId)."""
    databaseId: str
    pipelineId: str
    pipelineName: Optional[str] = ""
    category: Optional[str] = ""
    description: Optional[str] = ""
    executionConfig: Optional[Dict[str, Any]] = {}
    systemConfig: Optional[Dict[str, Any]] = {}
    enabled: bool = True
    archived: bool = False
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
    createdBy: Optional[str] = ""
    modifiedBy: Optional[str] = ""
    schemaVersion: Optional[int] = 1


class PipelineTemplateRecord(BaseModel, extra='ignore'):
    """PipelineTemplatesStorageTable row (PK pipelineDatabaseId:pipelineId, SK templateId)."""
    pipelineDatabaseId: str
    pipelineId: str
    templateId: str
    templateName: Optional[str] = ""
    description: Optional[str] = ""
    configFormat: str = "json"
    allowCustomEdit: bool = False
    inputInstructions: Optional[str] = ""
    bodyStorage: str = "inline"
    configBody: Optional[str] = ""
    webFormJson: Optional[str] = ""
    configBodyS3Key: Optional[str] = ""
    configBodyHash: Optional[str] = ""
    webFormS3Key: Optional[str] = ""
    webFormHash: Optional[str] = ""
    overrides: Optional[Dict[str, Any]] = {}
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
    createdBy: Optional[str] = ""
    modifiedBy: Optional[str] = ""
    schemaVersion: Optional[int] = 1


class PipelineTemplateTagSchemaRecord(BaseModel, extra='ignore'):
    """PipelineTemplateTagSchemaStorageTable row (PK tagSchemaId, SK owner key).

    `fields` is stored as a JSON string inline (mirrors MetadataSchemaStorageTableV2) or offloaded to
    S3 when bodyStorage='s3'.
    """
    tagSchemaId: str
    pipelineDatabaseId: str
    pipelineId: str
    templateId: str
    bodyStorage: str = "inline"
    fields: Optional[str] = ""
    fieldsS3Key: Optional[str] = ""
    fieldsHash: Optional[str] = ""
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
    createdBy: Optional[str] = ""
    modifiedBy: Optional[str] = ""
    schemaVersion: Optional[int] = 1


#######################
# Pipeline API request / response models
#######################

def _validate_id(value, allow_global=False):
    """Validate an id via the common validator framework; raises ValueError on failure."""
    from common.validators import validate
    (valid, message) = validate({
        "id": {"value": value, "validator": "ID", "allowGlobalKeyword": allow_global}
    })
    if not valid:
        raise ValueError(message)


# Maximum task timeout / heartbeat (1 week), mirroring the V1 pipeline model.
MAX_TASK_TIMEOUT_SECONDS = 604800
WAIT_FOR_CALLBACK_VALUES = ("Enabled", "Disabled")


def _validate_execution_config(execution_config):
    """Validate the executionConfig block beyond executionType: the per-type resource sub-fields
    (which are baked into the deployed Step Functions definition) and the callback/timeout scalars.

    Mirrors the V1 pipeline model's validation (models/pipelines.py) using the shared validators, so
    a malformed SQS url / EventBridge ARN/source/detailType, an out-of-bounds taskTimeout, or an
    invalid waitForCallback is rejected at parse time rather than emitted into a broken state machine.
    Raises ValueError on failure."""
    from common.validators import validate
    config = execution_config or {}
    exec_type = config.get("executionType", "Lambda")

    checks = {}
    if exec_type == "Lambda":
        # resourceId is the Lambda invoke target baked into the state machine. Accept either a
        # Lambda function ARN (partition-aware) or a bare function name/alias. Reject anything
        # else so a malformed target is caught at authoring time, not at execute time.
        resource_id = (config.get("lambda") or {}).get("resourceId")
        if resource_id:
            if resource_id.startswith("arn:"):
                checks["lambda.resourceId"] = {"value": resource_id, "validator": "ARN"}
            elif not _LAMBDA_FUNCTION_NAME_PATTERN.match(resource_id):
                raise ValueError(
                    "lambda.resourceId must be a Lambda function ARN or a valid function name")
    elif exec_type == "SQS":
        queue_url = (config.get("sqs") or {}).get("queueUrl")
        if queue_url:
            checks["sqs.queueUrl"] = {"value": queue_url, "validator": "SQS_QUEUE_URL"}
    elif exec_type == "EventBridge":
        eb = config.get("eventBridge") or {}
        if eb.get("busArn"):
            checks["eventBridge.busArn"] = {"value": eb["busArn"], "validator": "EVENTBRIDGE_BUS_ARN"}
        if eb.get("source"):
            checks["eventBridge.source"] = {"value": eb["source"], "validator": "EVENTBRIDGE_SOURCE"}
        if eb.get("detailType"):
            checks["eventBridge.detailType"] = {
                "value": eb["detailType"], "validator": "EVENTBRIDGE_DETAIL_TYPE"}
    if checks:
        (valid, message) = validate(checks)
        if not valid:
            raise ValueError(message)

    wait_for_callback = config.get("waitForCallback")
    if wait_for_callback not in (None, "") and wait_for_callback not in WAIT_FOR_CALLBACK_VALUES:
        raise ValueError(f"waitForCallback must be one of {WAIT_FOR_CALLBACK_VALUES}")

    for field in ("taskTimeout", "taskHeartbeatTimeout"):
        raw = config.get(field)
        if raw in (None, ""):
            continue
        try:
            seconds = int(raw)
        except (ValueError, TypeError):
            raise ValueError(f"{field} must be a positive integer (seconds)")
        if seconds <= 0:
            raise ValueError(f"{field} must be a positive non-zero value (seconds)")
        if seconds > MAX_TASK_TIMEOUT_SECONDS:
            raise ValueError(
                f"{field} cannot exceed {MAX_TASK_TIMEOUT_SECONDS} seconds (1 week)")


class CreatePipelineRequestModel(BaseModel, extra='ignore'):
    """Create a pipeline (V2). pipelineId optional — a GUID is generated when omitted."""
    databaseId: str = Field(..., min_length=1, max_length=256)
    pipelineId: Optional[str] = Field(None, min_length=1, max_length=64)
    pipelineName: str = Field(..., min_length=1, max_length=256)
    category: Optional[str] = Field("", max_length=256)
    description: Optional[str] = Field("", max_length=1024)
    executionConfig: Dict[str, Any] = Field(default_factory=dict)
    systemConfig: Optional[Dict[str, Any]] = Field(default_factory=dict)
    enabled: Optional[bool] = True

    @root_validator
    def validate_fields(cls, values):
        _validate_id(values.get("databaseId"), allow_global=True)
        if values.get("pipelineId"):
            _validate_id(values.get("pipelineId"))
        exec_type = (values.get("executionConfig") or {}).get("executionType", "Lambda")
        if exec_type not in PIPELINE_EXECUTION_TYPES:
            raise ValueError(f"executionType must be one of {PIPELINE_EXECUTION_TYPES}")
        _validate_execution_config(values.get("executionConfig"))
        _validate_pipeline_system_config(values.get("systemConfig"))
        return values


class UpdatePipelineRequestModel(BaseModel, extra='ignore'):
    """Update a pipeline (V2). Only supplied fields are changed."""
    pipelineName: Optional[str] = Field(None, min_length=1, max_length=256)
    category: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    executionConfig: Optional[Dict[str, Any]] = None
    systemConfig: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None

    @root_validator
    def validate_fields(cls, values):
        if not any(v is not None for v in values.values()):
            raise ValueError("At least one field must be provided for update")
        exec_config = values.get("executionConfig")
        if exec_config is not None:
            exec_type = exec_config.get("executionType", "Lambda")
            if exec_type not in PIPELINE_EXECUTION_TYPES:
                raise ValueError(f"executionType must be one of {PIPELINE_EXECUTION_TYPES}")
            _validate_execution_config(exec_config)
        _validate_pipeline_system_config(values.get("systemConfig"))
        return values


class PipelineResponseModel(BaseModel, extra='ignore'):
    """Response model for a pipeline (V2). Mirrors the stored record plus its templates on details."""
    databaseId: str
    pipelineId: str
    pipelineName: Optional[str] = ""
    category: Optional[str] = ""
    description: Optional[str] = ""
    executionConfig: Optional[Dict[str, Any]] = {}
    systemConfig: Optional[Dict[str, Any]] = {}
    enabled: bool = True
    archived: bool = False
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
    createdBy: Optional[str] = ""
    modifiedBy: Optional[str] = ""
    schemaVersion: Optional[int] = 1
    # Count of saved templates for this pipeline. Present on both the list and details responses.
    templateCount: Optional[int] = None
    # Present on the single-pipeline details response.
    templates: Optional[List[Dict[str, Any]]] = None


class GetPipelinesResponseModel(BaseModel, extra='ignore'):
    """Response model for listing pipelines."""
    Items: List[PipelineResponseModel] = []
    NextToken: Optional[str] = None


#######################
# Template API request / response models
#######################

class CreateTemplateRequestModel(BaseModel, extra='ignore'):
    """Create a pipeline template (V2). Clients always send configBody/webFormJson inline; the
    handler decides inline-vs-S3 storage. templateId optional — a GUID is generated when omitted."""
    templateId: Optional[str] = Field(None, min_length=1, max_length=64)
    templateName: str = Field(..., min_length=1, max_length=256)
    description: Optional[str] = Field("", max_length=1024)
    configFormat: str = "json"
    configBody: Optional[str] = ""
    webFormJson: Optional[str] = ""
    allowCustomEdit: Optional[bool] = False
    inputInstructions: Optional[str] = Field("", max_length=4096)
    overrides: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # When true, this template is the pipeline's default (auto-selected at execute time when the
    # pipeline requires a template and none is supplied). At most one default per pipeline — the
    # handler clears any prior default when a new one is set.
    isDefault: Optional[bool] = False
    # Tag schema may be created inline with the template (its fields are stored in the tag-schema
    # table); when omitted the template has no user-defined tags.
    tagSchema: Optional[List[Dict[str, Any]]] = None

    @root_validator
    def validate_fields(cls, values):
        if values.get("templateId"):
            _validate_id(values.get("templateId"))
        if values.get("configFormat") not in TEMPLATE_CONFIG_FORMATS:
            raise ValueError(f"configFormat must be one of {TEMPLATE_CONFIG_FORMATS}")
        _validate_template_overrides(values.get("overrides"))
        _validate_template_bodies(values.get("configFormat"), values.get("configBody"),
                                  values.get("webFormJson"))
        return values


class UpdateTemplateRequestModel(BaseModel, extra='ignore'):
    """Update a pipeline template (V2)."""
    templateName: Optional[str] = Field(None, min_length=1, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    configFormat: Optional[str] = None
    configBody: Optional[str] = None
    webFormJson: Optional[str] = None
    allowCustomEdit: Optional[bool] = None
    inputInstructions: Optional[str] = Field(None, max_length=4096)
    overrides: Optional[Dict[str, Any]] = None
    isDefault: Optional[bool] = None
    tagSchema: Optional[List[Dict[str, Any]]] = None

    @root_validator
    def validate_fields(cls, values):
        if not any(v is not None for v in values.values()):
            raise ValueError("At least one field must be provided for update")
        if values.get("configFormat") is not None and values.get("configFormat") not in TEMPLATE_CONFIG_FORMATS:
            raise ValueError(f"configFormat must be one of {TEMPLATE_CONFIG_FORMATS}")
        _validate_template_overrides(values.get("overrides"))
        _validate_template_bodies(values.get("configFormat"), values.get("configBody"),
                                  values.get("webFormJson"))
        return values


class TemplateResponseModel(BaseModel, extra='ignore'):
    """Response model for a template (V2). configBody/webFormJson are always returned inline — the
    handler rehydrates from S3 when the row was offloaded, transparent to the client."""
    pipelineDatabaseId: str
    pipelineId: str
    templateId: str
    templateName: Optional[str] = ""
    description: Optional[str] = ""
    configFormat: str = "json"
    configBody: Optional[str] = ""
    webFormJson: Optional[str] = ""
    allowCustomEdit: bool = False
    inputInstructions: Optional[str] = ""
    overrides: Optional[Dict[str, Any]] = {}
    isDefault: bool = False
    tagSchema: Optional[List[Dict[str, Any]]] = None
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
    createdBy: Optional[str] = ""
    modifiedBy: Optional[str] = ""
    schemaVersion: Optional[int] = 1


class GetTemplatesResponseModel(BaseModel, extra='ignore'):
    """Response model for listing a pipeline's templates."""
    Items: List[TemplateResponseModel] = []
    NextToken: Optional[str] = None


#######################
# Tag-schema API request / response models
#######################

class SetTagSchemaRequestModel(BaseModel, extra='ignore'):
    """Set (replace) a template's tag schema. `fields` is the list of tag definitions; each is
    validated against the shared primitive type set + reserved-key rules by the handler."""
    fields: List[TemplateTagFieldModel] = Field(default_factory=list)


class TagSchemaResponseModel(BaseModel, extra='ignore'):
    """Response model for a template's tag schema (fields returned as a parsed list, not the stored
    JSON string)."""
    pipelineDatabaseId: str
    pipelineId: str
    templateId: str
    tagSchemaId: Optional[str] = ""
    fields: List[Dict[str, Any]] = []
    dateCreated: Optional[str] = ""
    dateModified: Optional[str] = ""
