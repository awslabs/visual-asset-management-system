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

import json
import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from models.common import validation_error_message
from common.workflows import executionValidation as ev
from common.workflows import templateRender as tr
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
_METADATA_INPUT_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes", "databaseMetadata")
# The only keys an inputFileFilters map may carry. An absent `allow` list means allow-all, so a
# mistyped key would silently widen the filter instead of narrowing it.
_INPUT_FILE_FILTER_KEYS = ("allow", "exclude")
# Rejected in an exclude list (they would exclude every file); allowed in an allow list, where they
# simply mean allow-all. Defined with the filter semantics in common/workflows/executionValidation.
_MATCH_EVERYTHING_PATTERNS = ev.MATCH_EVERYTHING_PATTERNS

# C0/C1 control characters. A name or category carries a single line of display text, and two
# properties depend on that:
#   - `name` and `category` are ABAC constraint fields, matched by the Casbin `equals` operator as
#     regexMatch(value, '^<constraint>$'). Python's '$' also matches immediately before a trailing
#     newline, so a trailing newline on a stored value still satisfies a constraint written
#     without one, while being a distinct stored value.
#   - Audit and application log entries are one line each, so an embedded newline splits one record
#     into two forgeable-looking lines.
# Everything a real name needs — unicode letters, spaces, and punctuation — is untouched.
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# The two job-template dialects Deadline Cloud createJob accepts.
DEADLINE_TEMPLATE_TYPES = ("JSON", "YAML")
# Upper bound on the inline OpenJD job template. Step Functions caps a state-machine definition at
# 1 MB and the template is embedded in it verbatim, so this leaves ample room for the rest of the ASL
# while accommodating any realistic job template.
MAX_DEADLINE_TEMPLATE_LENGTH = 256 * 1024

# Bounds on the authored collections that reach DynamoDB, S3, or a per-file match loop. Generous
# relative to real authoring — the built-in pipelines declare at most 3 tag fields and a handful of
# filter patterns — so they bound a runaway request without rejecting any legitimate schema.
# Every filter pattern is matched against every candidate input file, so the list length multiplies
# the per-execution match work.
MAX_INPUT_FILE_FILTER_PATTERNS = 250
MAX_INPUT_FILE_FILTER_PATTERN_LENGTH = 512
# Tag-schema bounds. The schema is stored as one JSON body (inline on the row, or offloaded to S3
# above the inline threshold), so an unbounded field count or free-text length is an unbounded row.
MAX_TAG_SCHEMA_FIELDS = 250
MAX_TAG_KEY_LENGTH = 128
MAX_TAG_TEXT_LENGTH = 1024
MAX_TAG_ENUM_VALUES = 250
MAX_TAG_ENUM_VALUE_LENGTH = 256
MAX_TAG_DEFAULT_LENGTH = 4096
# Upper bound on the per-pipeline viewer subfolder appended to an input file's preview prefix.
MAX_AUX_PREVIEW_SUFFIX_LENGTH = 256

# Upper bound on a serialized systemConfig / executionConfig block. Both are free-form JSON objects
# stored whole on their row, and systemConfig is snapshotted again per pipeline step on the execution's
# config record, where it shares that item's text budget. 64 KB is ~100x the largest shipped block
# (607 bytes), so it bounds a runaway request at parse time — a 400 — instead of an unpersistable item
# discovered by put_item after the state machine has launched. It also bounds the filter lists as a
# whole: the per-pattern caps multiply out to far more than one item can hold, while 64 KB still admits
# the full pattern count at any realistic pattern length.
MAX_CONFIG_BLOCK_BYTES = 64 * 1024

# executionConfig gets its own ceiling because a DeadlineCloud block legitimately carries an OpenJD
# template up to MAX_DEADLINE_TEMPLATE_LENGTH, which alone exceeds the systemConfig budget. The
# allowance is that template plus room for the sibling execution-type blocks around it.
MAX_EXECUTION_CONFIG_BYTES = MAX_DEADLINE_TEMPLATE_LENGTH + (64 * 1024)

# systemConfig keys whose value is a plain boolean gate. The shared shape validator policies the
# nested boolean MAPS (assetScope / metadataInputs); these are the top-level scalars, where a truthy
# string like "false" is stored and read back as True — inverting the gate the author set. For
# allowCustomTemplateOverride that means accepting caller-supplied template bodies on a pipeline
# configured to refuse them.
_SYSTEM_CONFIG_BOOLEAN_KEYS = ("requireTemplate", "allowCustomTemplateOverride")

# Every key a systemConfig block may carry, across the pipeline and workflow vocabularies (the first
# four are shared; auxPreviewPipelineSuffix / requireTemplate / allowCustomTemplateOverride are
# pipeline-only, the remainder workflow-only). Both records store the block WHOLESALE and every reader
# resolves a named key, so an unknown key is neither read nor reported — it is stored, snapshotted per
# execution, and returned. Rejecting it turns an author's typo into a 400 instead of a setting that
# silently does nothing.
_SYSTEM_CONFIG_KEYS = (
    "inputFileArity", "assetScope", "metadataInputs", "inputFileFilters",
    "auxPreviewPipelineSuffix", "requireTemplate", "allowCustomTemplateOverride",
    "concurrencyRestriction", "outputTarget", "allowWorkflowTriggerChaining",
    "defaultOutputFileBaseExecutionPathExtension",
)

# Every key an executionConfig block may carry: the common scalars plus the four per-execution-type
# resource sub-blocks (see pipelineRecords.build_pipeline_execution_config).
_EXECUTION_CONFIG_KEYS = (
    "executionType", "waitForCallback", "taskTimeout", "taskHeartbeatTimeout",
    "lambda", "sqs", "eventBridge", "deadlineCloud",
)


def _validate_config_block_size(cfg, context, max_bytes=MAX_CONFIG_BLOCK_BYTES):
    """Reject a systemConfig / executionConfig block whose serialized size exceeds `max_bytes`.
    No-op for a non-dict (the caller reports the type) or an unserializable value (the field's own
    type validation reports that)."""
    if not isinstance(cfg, dict):
        return
    try:
        size = len(json.dumps(cfg, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return
    if size > max_bytes:
        raise ValueError(f"{context} must be at most {max_bytes} bytes when serialized")


def validate_no_control_characters(value, field_name):
    """Reject C0/C1 control characters in a single-line free-text field. No-op for empty/non-string."""
    if not value or not isinstance(value, str):
        return
    if _CONTROL_CHAR_PATTERN.search(value):
        raise ValueError(f"{field_name} must not contain control characters or line breaks")


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
        _validate_input_file_filters(filters, f"{context}.inputFileFilters")


def _validate_input_file_filters(filters, context):
    """Validate an {allow, exclude} input-file-filter map: only those two keys, each a list of
    strings. Raises ValueError on failure.

    A match-everything pattern in `exclude` is rejected. Exclude is applied after allow, so a bare
    '*' there excludes every file and makes the pipeline or workflow permanently unrunnable — always
    an authoring mistake rather than an intent. An empty or absent exclude list is the way to express
    'exclude nothing'. The same pattern in `allow` is fine: it means allow-all, which is also what an
    absent allow list means."""
    if not isinstance(filters, dict):
        raise ValueError(f"{context} must be an object")
    for key in filters:
        if key not in _INPUT_FILE_FILTER_KEYS:
            raise ValueError(
                f"{context} has unknown key '{key}'; allowed: {_INPUT_FILE_FILTER_KEYS}")
    for list_key in _INPUT_FILE_FILTER_KEYS:
        if list_key in filters:
            lst = filters[list_key]
            if not isinstance(lst, list) or not all(isinstance(x, str) for x in lst):
                raise ValueError(f"{context}.{list_key} must be a list of strings")
            if len(lst) > MAX_INPUT_FILE_FILTER_PATTERNS:
                raise ValueError(
                    f"{context}.{list_key} may contain at most "
                    f"{MAX_INPUT_FILE_FILTER_PATTERNS} patterns")
            for pattern in lst:
                if len(pattern) > MAX_INPUT_FILE_FILTER_PATTERN_LENGTH:
                    raise ValueError(
                        f"{context}.{list_key} patterns may be at most "
                        f"{MAX_INPUT_FILE_FILTER_PATTERN_LENGTH} characters")
    for pattern in filters.get("exclude") or []:
        if pattern.strip() in _MATCH_EVERYTHING_PATTERNS:
            raise ValueError(
                f"{context}.exclude may not contain '{pattern.strip()}': it would exclude every "
                "file. Use an empty exclude list to exclude nothing.")


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


def _validate_template_bodies(config_format, config_body, web_form_json, tag_schema=None):
    """Best-effort structural validation of the freeform template bodies. The config body is only
    parse-checked when its declared format is JSON (yaml/openjd/xml/raw are passed through as text).
    webFormJson, when present, must be valid JSON (it is a serialized form definition)."""
    import json as _json
    if config_format == "json" and config_body:
        _validate_json_config_body(config_body, tag_schema)
    if web_form_json:
        try:
            _json.loads(web_form_json)
        except (ValueError, TypeError):
            raise ValueError("webFormJson must be valid JSON")


def _validate_json_config_body(config_body, tag_schema=None):
    """Parse-check a json-format config body whose {{tag}} placeholders are still unresolved.

    Each tag is replaced by a JSON-valid stand-in for what it renders — a JSON object/array/number
    literal for the tags the renderer substitutes as JSON literals, bare text for every other tag,
    which parses only inside the template's own quotes — so the surrounding JSON structure is validated
    while the tags themselves are accepted. A tag that renders a JSON object or array is checked in a
    second pass against a quoted stand-in, which distinguishes a tag standing alone as a value from one
    written inside quotes: the quoted form renders an object literal inside the template's quotes, so
    it is valid at save and malformed at run time. Raises ValueError on failure.

    `tag_schema` is the template's own declared tags. It arrives in the same request as the body, so a
    user tag declared integer/number/boolean/string-list is classified by its declaration and its
    placeholder may stand alone as an unquoted value — which is where it renders that type. Without the
    schema all four fall back to the text stand-in, which accepts them only inside quotes: a quoted
    integer tag then saves and renders the string "5", and a quoted string-list renders JSON that does
    not parse at all."""
    import json as _json
    if not tr.uses_template_tags(config_body):
        try:
            _json.loads(config_body)
        except (ValueError, TypeError):
            raise ValueError("configBody is not valid JSON (configFormat is 'json')")
        return
    try:
        _json.loads(tr.json_body_placeholder_text(config_body, tag_schema=tag_schema))
    except (ValueError, TypeError):
        # The advice is split by what the body actually declares. Saying "a placeholder belongs inside
        # the JSON string it fills" is wrong for a tag declared integer/number/boolean/string-list —
        # following it produces the quoted form, which is what ships the wrong type.
        if tr.user_tag_shapes(tag_schema):
            raise ValueError(
                "configBody is not valid JSON (configFormat is 'json'). A {{tagName}} placeholder for "
                "a string or enum tag belongs inside the JSON string it fills "
                "(\"key\": \"{{tagName}}\"); a tag declared integer, number, boolean or string-list "
                "renders a JSON value of that type, so its placeholder is the whole value and takes no "
                "quotes (\"key\": {{tagName}}).")
        raise ValueError(
            "configBody is not valid JSON (configFormat is 'json'). A {{tagName}} placeholder for a "
            "text value belongs inside the JSON string it fills (\"key\": \"{{tagName}}\").")
    try:
        _json.loads(tr.json_body_placeholder_text(config_body, structured_as_string=True,
                                                  tag_schema=tag_schema))
    except (ValueError, TypeError):
        # Deliberately names the rule rather than the offending tag: a tagKey is caller-supplied and is
        # not charset-constrained at model level, and validator messages must not echo caller input
        # (Rule 11 — see models/common.py).
        #
        # Two different consequences share this arm, so the message covers both. A quoted ARRAY or
        # OBJECT tag renders a structure inside the string's own quotes, producing JSON that does not
        # parse at run time. A quoted integer/number/boolean tag parses perfectly well — and delivers
        # the STRING "5" where the schema promised 5, which nothing downstream reports.
        if tr.user_tag_shapes(tag_schema):
            raise ValueError(
                "configBody quotes a {{tagName}} placeholder for a tag declared integer, number, "
                "boolean or string-list. Such a tag renders a JSON value of that type, so its "
                "placeholder is the whole value and takes no quotes (\"key\": {{tagName}}). Quoted, a "
                "string-list renders a structure inside the string's own quotes and the pipeline "
                "receives malformed JSON, while a number or boolean is delivered as text.")
        raise ValueError(
            "configBody quotes a {{tagName}} placeholder that renders a JSON object or array. Such a "
            "placeholder is the whole value and takes no quotes (\"key\": {{tagName}}); quoted, it "
            "renders an object inside the string's own quotes and the pipeline receives malformed JSON.")


def _validate_aux_preview_pipeline_suffix(suffix):
    """Validate systemConfig.auxPreviewPipelineSuffix (e.g. '/PotreeViewer').

    The suffix is appended to an input file's auxiliary-bucket preview prefix to build an S3 key
    (templateRender), so it must not carry '..' traversal or backslashes that would resolve outside
    that prefix. No-op when empty."""
    if suffix is None or suffix == "":
        return
    if not isinstance(suffix, str):
        raise ValueError("systemConfig.auxPreviewPipelineSuffix must be a string")
    if len(suffix) > MAX_AUX_PREVIEW_SUFFIX_LENGTH:
        raise ValueError(
            f"systemConfig.auxPreviewPipelineSuffix must be at most "
            f"{MAX_AUX_PREVIEW_SUFFIX_LENGTH} characters")
    if ".." in suffix:
        raise ValueError("systemConfig.auxPreviewPipelineSuffix must not contain '..'")
    if "\\" in suffix:
        raise ValueError("systemConfig.auxPreviewPipelineSuffix must not contain backslashes")


def _validate_system_config_booleans(system_config, context="systemConfig"):
    """Reject a non-boolean value for a top-level systemConfig boolean gate. The shared shape
    validator polices the nested boolean maps; these scalars would otherwise store a truthy string
    such as "false" that reads back as True, inverting the gate."""
    if not isinstance(system_config, dict):
        return
    for key in _SYSTEM_CONFIG_BOOLEAN_KEYS:
        if key in system_config and not isinstance(system_config[key], bool):
            raise ValueError(f"{context}.{key} must be a boolean")


def validate_system_config_keys(system_config, context="systemConfig"):
    """Reject a top-level systemConfig key outside the documented set, and bound the block's
    serialized size. Shared by the pipeline and workflow system-config validation, which accept the
    same key set. No-op for a non-dict (the shape validator reports the type)."""
    _validate_config_block_size(system_config, context)
    if not isinstance(system_config, dict):
        return
    for key in system_config:
        if key not in _SYSTEM_CONFIG_KEYS:
            raise ValueError(
                f"{context} has unknown key '{key}'; allowed: {_SYSTEM_CONFIG_KEYS}")


def _validate_pipeline_system_config(system_config):
    """Validate a pipeline systemConfig block (inputFileArity enum + assetScope/metadataInputs/
    inputFileFilters value shapes, the boolean gates, the preview-suffix path shape, and the
    top-level key set + size bound). No-op when absent."""
    _validate_system_config_shape(system_config, "systemConfig")
    _validate_system_config_booleans(system_config)
    validate_system_config_keys(system_config)
    if isinstance(system_config, dict):
        _validate_aux_preview_pipeline_suffix(system_config.get("auxPreviewPipelineSuffix"))


class TemplateTagType(str, Enum):
    """Primitive tag-value types a template tag may declare (shared primitive subset)."""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string-list"
    ENUM = "enum"


class TemplateTagFieldModel(BaseModel, extra='ignore'):
    """Single tag definition within a template tag schema (mirrors MetadataSchemaFieldModel).

    The declared-schema rules (substitutable key charset, uniqueness, reserved-key collisions,
    enum-has-values) live in common/workflows/templateTagSchema.validate_tag_schema, which both this
    API path and the CDK ingest path run. The bounds here cap what a single definition contributes to
    the stored schema body."""
    tagKey: str = Field(..., min_length=1, max_length=MAX_TAG_KEY_LENGTH)
    type: TemplateTagType = TemplateTagType.STRING
    required: bool = False
    default: Optional[Any] = None
    label: Optional[str] = Field("", max_length=MAX_TAG_TEXT_LENGTH)
    description: Optional[str] = Field("", max_length=MAX_TAG_TEXT_LENGTH)
    # Allowed values for the enum type; item type for string-list is implicitly string.
    enumValues: Optional[List[str]] = Field(None, max_items=MAX_TAG_ENUM_VALUES)

    @validator("enumValues", each_item=True)
    def validate_enum_value_length(cls, v):
        if isinstance(v, str) and len(v) > MAX_TAG_ENUM_VALUE_LENGTH:
            raise ValueError(
                f"enumValues entries may be at most {MAX_TAG_ENUM_VALUE_LENGTH} characters")
        return v

    @validator("default")
    def validate_default_size(cls, v):
        # A default is typed Any (a string, number, boolean, or string list), so it is bounded by its
        # serialized length rather than a string max_length.
        if v is None:
            return v
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        if len(text) > MAX_TAG_DEFAULT_LENGTH:
            raise ValueError(
                f"default may be at most {MAX_TAG_DEFAULT_LENGTH} characters when serialized")
        return v

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


def _execution_config_sub_block(config, key):
    """The named per-execution-type sub-block as a dict. An absent/empty value yields {}; a value of
    any other JSON type is rejected, since every reader of these blocks calls .get() on them."""
    block = config.get(key)
    if not block:
        return {}
    if not isinstance(block, dict):
        raise ValueError(f"executionConfig.{key} must be an object")
    return block


def _validate_execution_config(execution_config, require_lambda_resource_id=False):
    """Validate the executionConfig block beyond executionType: the per-type resource sub-fields
    (which are baked into the deployed Step Functions definition), the top-level key set and size, and
    the callback/timeout scalars.

    Mirrors the V1 pipeline model's validation using the shared validators, so a malformed SQS url /
    EventBridge ARN/source/detailType, an out-of-bounds taskTimeout, or an invalid waitForCallback is
    rejected at parse time rather than emitted into a broken state machine.

    `require_lambda_resource_id` demands a Lambda target rather than accepting an empty one. It is set
    on the update path, where the stored config is replaced wholesale and nothing fills an absent
    resourceId, so an empty value would persist a state machine target of "". On create an empty value
    is the request to auto-provision a function, which the handler does before the row is written.
    Raises ValueError on failure."""
    from common.validators import validate
    config = execution_config or {}
    _validate_config_block_size(config, "executionConfig", max_bytes=MAX_EXECUTION_CONFIG_BYTES)
    for key in config:
        if key not in _EXECUTION_CONFIG_KEYS:
            raise ValueError(
                f"executionConfig has unknown key '{key}'; allowed: {_EXECUTION_CONFIG_KEYS}")
    exec_type = config.get("executionType", "Lambda")

    checks = {}
    if exec_type == "Lambda":
        # resourceId is the Lambda invoke target baked into the state machine. Accept either a
        # Lambda function ARN (partition-aware) or a bare function name/alias. Reject anything
        # else so a malformed target is caught at authoring time, not at execute time.
        resource_id = _execution_config_sub_block(config, "lambda").get("resourceId")
        if not resource_id and require_lambda_resource_id:
            raise ValueError("lambda.resourceId is required for the Lambda execution type")
        if resource_id:
            if not isinstance(resource_id, str):
                raise ValueError("lambda.resourceId must be a string")
            if resource_id.startswith("arn:"):
                checks["lambda.resourceId"] = {"value": resource_id, "validator": "ARN"}
            elif not _LAMBDA_FUNCTION_NAME_PATTERN.match(resource_id):
                raise ValueError(
                    "lambda.resourceId must be a Lambda function ARN or a valid function name")
    elif exec_type == "SQS":
        # The queue URL becomes the sendMessage task's QueueUrl. An absent value would emit a state
        # machine with an empty target, so it is required here rather than at workflow-save time.
        queue_url = _execution_config_sub_block(config, "sqs").get("queueUrl")
        if not queue_url:
            raise ValueError("sqs.queueUrl is required for the SQS execution type")
        checks["sqs.queueUrl"] = {"value": queue_url, "validator": "SQS_QUEUE_URL"}
    elif exec_type == "EventBridge":
        # busArn is optional (an absent bus resolves to the account's default event bus); source and
        # detailType have task-state defaults as well.
        eb = _execution_config_sub_block(config, "eventBridge")
        if eb.get("busArn"):
            checks["eventBridge.busArn"] = {"value": eb["busArn"], "validator": "EVENTBRIDGE_BUS_ARN"}
        if eb.get("source"):
            checks["eventBridge.source"] = {"value": eb["source"], "validator": "EVENTBRIDGE_SOURCE"}
        if eb.get("detailType"):
            checks["eventBridge.detailType"] = {
                "value": eb["detailType"], "validator": "EVENTBRIDGE_DETAIL_TYPE"}
    elif exec_type == "DeadlineCloud":
        # createJob only queues the job; completion arrives through the task-token callback, so the
        # callback is mandatory and the farm/queue the job is submitted to must be named.
        dc = _execution_config_sub_block(config, "deadlineCloud")
        if config.get("waitForCallback") != "Enabled":
            raise ValueError(
                "waitForCallback must be Enabled for the DeadlineCloud execution type: createJob "
                "only queues the job and completion is reported via the task-token callback")
        # farmId / queueId / storageProfileId are Deadline resource ids interpolated into the
        # createJob task parameters, so each carries the id character set rather than free text.
        for field in ("farmId", "queueId"):
            if not dc.get(field):
                raise ValueError(
                    f"deadlineCloud.{field} is required for the DeadlineCloud execution type")
            checks[f"deadlineCloud.{field}"] = {"value": dc[field], "validator": "ID"}
        if dc.get("storageProfileId"):
            checks["deadlineCloud.storageProfileId"] = {
                "value": dc["storageProfileId"], "validator": "ID"}
        # templateType selects how Deadline parses the job template; anything else is rejected by the
        # createJob call at launch, so it is caught at authoring time instead.
        template_type = dc.get("templateType")
        if template_type not in (None, "") and template_type not in DEADLINE_TEMPLATE_TYPES:
            raise ValueError(
                f"deadlineCloud.templateType must be one of {DEADLINE_TEMPLATE_TYPES}")
        # The OpenJD job template travels inline in the state-machine definition, which Step Functions
        # caps at 1 MB, so an unbounded body makes deploy_state_machine fail after the pipeline row is
        # already saved. Bounded well under that to leave room for the rest of the ASL.
        template_body = dc.get("template") or ""
        if len(template_body) > MAX_DEADLINE_TEMPLATE_LENGTH:
            raise ValueError(
                f"deadlineCloud.template must be at most {MAX_DEADLINE_TEMPLATE_LENGTH} characters")
        # The createJob task state casts these to int.
        for field in ("priority", "maxRetriesPerTask", "maxFailedTasksCount"):
            raw = dc.get(field)
            if raw in (None, ""):
                continue
            try:
                value = int(raw)
            except (ValueError, TypeError):
                raise ValueError(f"deadlineCloud.{field} must be an integer")
            if value < 0:
                raise ValueError(f"deadlineCloud.{field} cannot be negative")
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
        # pipelineName and category are the ABAC CONSTRAINT fields (surfaced as `name` / `category`
        # on the Tier-2 Casbin object): a `^<value>$` policy rule matches a trailing newline in
        # Python, so a distinct stored name would satisfy a grant written for another.
        # `description` is deliberately excluded — not an ABAC field, and the web offers it as a
        # multi-line textarea, so guarding it rejected exactly what that control produces.
        for field in ("pipelineName", "category"):
            validate_no_control_characters(values.get(field), field)
        return values


class UpdatePipelineRequestModel(BaseModel, extra='ignore'):
    """Update a pipeline (V2). Only supplied fields are changed."""
    pipelineName: Optional[str] = Field(None, min_length=1, max_length=256)
    category: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    executionConfig: Optional[Dict[str, Any]] = None
    systemConfig: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    # Soft-delete flag. DELETE sets it; an update setting it to False unarchives the pipeline (the
    # path re-registration of a built-in takes to restore an archived row).
    archived: Optional[bool] = None

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
        # pipelineName and category are the ABAC CONSTRAINT fields (surfaced as `name` / `category`
        # on the Tier-2 Casbin object): a `^<value>$` policy rule matches a trailing newline in
        # Python, so a distinct stored name would satisfy a grant written for another.
        # `description` is deliberately excluded — not an ABAC field, and the web offers it as a
        # multi-line textarea, so guarding it rejected exactly what that control produces.
        for field in ("pipelineName", "category"):
            validate_no_control_characters(values.get(field), field)
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

def _validate_tag_schema_field_bounds(tag_schema):
    """Run each raw tag definition through TemplateTagFieldModel so its per-field bounds (key/label/
    description/enum/default lengths) apply on the paths that carry the schema as a plain dict list.

    The entries stay dicts: the declared-schema rules run in
    common/workflows/templateTagSchema.validate_tag_schema, which inspects dicts, and the handler
    persists the caller's dicts verbatim. Parsing here is for validation only — replacing the dicts
    with models would make validate_tag_schema reject every entry as 'not an object'. No-op when
    absent."""
    if tag_schema is None:
        return
    if not isinstance(tag_schema, list):
        raise ValueError("tagSchema must be a list of tag definitions")
    if len(tag_schema) > MAX_TAG_SCHEMA_FIELDS:
        raise ValueError(f"tagSchema may contain at most {MAX_TAG_SCHEMA_FIELDS} tag definitions")
    for index, field in enumerate(tag_schema):
        if not isinstance(field, dict):
            raise ValueError(f"tagSchema[{index}] must be an object")
        try:
            TemplateTagFieldModel(**field)
        except PydanticValidationError as e:
            # The nested error's str() carries pydantic's wrapper — the TemplateTagFieldModel class
            # name and its error taxonomy — and this message becomes one `msg` on the OUTER model's
            # errors(), where a response projection cannot tell it from authored prose. Sanitizing it
            # here is what keeps the internals out of the response (backend Rule 11); the index prefix
            # is kept because it names WHICH entry failed.
            raise ValueError(f"tagSchema[{index}]: {validation_error_message(e)}")
        except Exception as e:
            raise ValueError(f"tagSchema[{index}]: {e}")


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
        # tagSchema travels in the same request as the body, so the json gate can classify a typed
        # user tag by its declaration without reading the stored schema.
        _validate_template_bodies(values.get("configFormat"), values.get("configBody"),
                                  values.get("webFormJson"), values.get("tagSchema"))
        # templateName reaches single-line log entries. The multi-line bodies (configBody,
        # webFormJson, inputInstructions) are exempt — they are authored documents.
        validate_no_control_characters(values.get("templateName"), "templateName")
        _validate_tag_schema_field_bounds(values.get("tagSchema"))
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
        # tagSchema travels in the same request as the body, so the json gate can classify a typed
        # user tag by its declaration without reading the stored schema.
        _validate_template_bodies(values.get("configFormat"), values.get("configBody"),
                                  values.get("webFormJson"), values.get("tagSchema"))
        # templateName reaches single-line log entries. The multi-line bodies (configBody,
        # webFormJson, inputInstructions) are exempt — they are authored documents.
        validate_no_control_characters(values.get("templateName"), "templateName")
        _validate_tag_schema_field_bounds(values.get("tagSchema"))
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
    fields: List[TemplateTagFieldModel] = Field(
        default_factory=list, max_items=MAX_TAG_SCHEMA_FIELDS)


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
