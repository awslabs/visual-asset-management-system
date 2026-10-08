#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 models for the compliance feature.

Schema-body models (the `vams-rules-v1` format), evaluation result models, and the request
models for the compliance API handlers. Ids validate through the `validate()` dispatcher
(`ID` for database ids / schema names / workflow and pipeline ids, `ASSET_ID` for asset ids);
names and free text trim their surrounding whitespace through `trim_name` before the length
and regex checks run.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from pydantic import Field

from common.validators import id_pattern, trim_name, validate
from customLogging.logger import safeLogger

logger = safeLogger(service_name="ComplianceModels")

GLOBAL_DATABASE_ID = "GLOBAL"

# Bounds on schema-body prose. A rule or check name is an identifier-like label; a description
# is free text shown in the UI.
MAX_RULE_NAME_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 1024
MAX_REASON_LENGTH = 1024


# --- vams-rules-v1 schema format ---


class EnforcementLevel(str, Enum):
    """Enforcement level for a compliance rule."""
    quarantine = "quarantine"
    warn = "warn"
    inform = "inform"


class RuleType(str, Enum):
    """Type of compliance rule."""
    pipeline = "pipeline"
    metadata = "metadata"
    relationship = "relationship"


class ToleranceOperator(str, Enum):
    """Tolerance comparison operators."""
    lte = "lte"
    gte = "gte"
    eq = "eq"
    between = "between"


class Tolerance(BaseModel, extra='ignore'):
    """Tolerance definition for a pipeline output check."""
    operator: ToleranceOperator
    value: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    epsilon: Optional[float] = Field(None, description="Absolute tolerance for the eq operator")

    @root_validator
    def validate_tolerance(cls, values):
        op = values.get("operator")
        if op in (ToleranceOperator.lte, ToleranceOperator.gte, ToleranceOperator.eq):
            if values.get("value") is None:
                raise ValueError(f"'value' is required for operator '{op}'")
        if op == ToleranceOperator.between:
            if values.get("min") is None or values.get("max") is None:
                raise ValueError("'min' and 'max' are required for operator 'between'")
            if values["min"] > values["max"]:
                raise ValueError("'min' must be <= 'max'")
        return values


class PipelineRef(BaseModel, extra='ignore'):
    """The workflow a pipeline rule executes and the pipeline (within that workflow) whose
    measurements the rule's checks read.

    `databaseId` is the WORKFLOW's database; `pipelineDatabaseId` is the pipeline's. Both accept
    the GLOBAL keyword. `templateId` selects the pipeline's template for the execution.
    """
    databaseId: str = Field(min_length=3, max_length=63)
    workflowId: str = Field(min_length=3, max_length=63, regex=id_pattern)
    pipelineDatabaseId: str = Field(min_length=3, max_length=63)
    pipelineId: str = Field(min_length=3, max_length=63, regex=id_pattern)
    templateId: Optional[str] = Field(None, max_length=63)

    _trim_ids = validator(
        'databaseId', 'workflowId', 'pipelineDatabaseId', 'pipelineId', 'templateId',
        pre=True, allow_reuse=True,
    )(trim_name)

    @root_validator
    def validate_ids(cls, values):
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'), 'validator': 'ID', 'allowGlobalKeyword': True,
            },
            'workflowId': {'value': values.get('workflowId'), 'validator': 'ID'},
            'pipelineDatabaseId': {
                'value': values.get('pipelineDatabaseId'), 'validator': 'ID',
                'allowGlobalKeyword': True,
            },
            'pipelineId': {'value': values.get('pipelineId'), 'validator': 'ID'},
            'templateId': {'value': values.get('templateId'), 'validator': 'ID', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        return values


class PipelineCheck(BaseModel, extra='ignore'):
    """A single check within a pipeline rule: one measurement compared against a tolerance."""
    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_LENGTH)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    outputField: str = Field(min_length=1, max_length=MAX_RULE_NAME_LENGTH)
    tolerance: Tolerance

    _trim_names = validator('name', 'outputField', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)


# Input-file selection modes of a pipeline rule.
INPUT_FILES_MODE_MATCHING = "matching"
INPUT_FILES_MODE_WHOLE_ASSET = "wholeAsset"
INPUT_FILES_MODE_EXPLICIT = "explicit"
INPUT_FILES_MODES = (INPUT_FILES_MODE_MATCHING, INPUT_FILES_MODE_WHOLE_ASSET, INPUT_FILES_MODE_EXPLICIT)

# Bounds on a pipeline rule's input selection: `filter` globs and `explicit` keys.
MAX_INPUT_FILE_FILTERS = 32
MAX_INPUT_FILE_FILTER_LENGTH = 256
MAX_EXPLICIT_INPUT_KEYS = 64


def trim_entries(value):
    """Surrounding whitespace removed from every string entry of a list; other values pass through
    to the field's own type check."""
    if isinstance(value, list):
        return [trim_name(entry) for entry in value]
    return value


class PipelineInputFiles(BaseModel, extra='ignore'):
    """Which of the asset's files a pipeline rule hands to its workflow execution.

    `matching` (the default) selects the asset's current files that pass the workflow's and the
    pipeline's input-file filters, narrowed by the rule's own `filter` globs; `wholeAsset` selects
    the asset root (`/`) and is accepted only by a workflow that allows whole-asset selection;
    `explicit` selects the listed asset-relative `keys`, every one of which must exist. `filter`
    is accepted only with `matching`; `keys` is required with `explicit` and refused otherwise.
    """
    mode: str = Field(INPUT_FILES_MODE_MATCHING, regex="^(" + "|".join(INPUT_FILES_MODES) + ")$")
    filter: Optional[List[str]] = Field(None, max_items=MAX_INPUT_FILE_FILTERS)
    keys: Optional[List[str]] = Field(None, min_items=1, max_items=MAX_EXPLICIT_INPUT_KEYS)

    _trim_mode = validator('mode', pre=True, allow_reuse=True)(trim_name)
    _trim_entries = validator('filter', 'keys', pre=True, allow_reuse=True)(trim_entries)

    @root_validator
    def validate_selection(cls, values):
        mode = values.get("mode")
        filters = values.get("filter")
        keys = values.get("keys")
        if filters is not None and mode != INPUT_FILES_MODE_MATCHING:
            raise ValueError("'filter' is only accepted with the matching mode")
        if mode == INPUT_FILES_MODE_EXPLICIT and not keys:
            raise ValueError("'keys' is required with the explicit mode")
        if keys is not None and mode != INPUT_FILES_MODE_EXPLICIT:
            raise ValueError("'keys' is only accepted with the explicit mode")
        if filters is not None:
            for entry in filters:
                if not isinstance(entry, str) or not entry \
                        or len(entry) > MAX_INPUT_FILE_FILTER_LENGTH:
                    raise ValueError(
                        f"Every 'filter' entry must be 1 to {MAX_INPUT_FILE_FILTER_LENGTH} characters")
        if keys is not None:
            # Keys are asset-relative and stored with a single leading '/'.
            normalized = ["/" + str(key).lstrip("/") for key in keys]
            (valid, message) = validate({
                'keys': {'value': normalized, 'validator': 'RELATIVE_FILE_PATH_ARRAY'},
            })
            if not valid:
                raise ValueError(message)
            values["keys"] = normalized
        return values


class PipelineRule(BaseModel, extra='ignore'):
    """Pipeline rule: executes a VAMS workflow and compares the pipeline's measurements to
    tolerances. `inputParameters` are handed to the pipeline as template tag values; `inputFiles`
    selects which of the asset's files the execution receives."""
    ruleType: RuleType = Field(RuleType.pipeline, const=True)
    enforcement: EnforcementLevel
    pipelineRef: PipelineRef
    inputParameters: Optional[Dict[str, Any]] = {}
    inputFiles: PipelineInputFiles = Field(default_factory=PipelineInputFiles)
    checks: List[PipelineCheck] = Field(min_items=1)


class MetadataSchemaRef(BaseModel, extra='ignore'):
    """Reference to a VAMS metadata schema (database-scoped or GLOBAL)."""
    databaseId: str = Field(min_length=3, max_length=63)
    schemaName: str = Field(min_length=1, max_length=256)

    _trim_names = validator('databaseId', 'schemaName', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_ids(cls, values):
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'), 'validator': 'ID', 'allowGlobalKeyword': True,
            },
            'schemaName': {'value': values.get('schemaName'), 'validator': 'OBJECT_NAME'},
        })
        if not valid:
            raise ValueError(message)
        return values


class MetadataCheck(BaseModel, extra='ignore'):
    """A single check within a metadata rule."""
    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_LENGTH)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    validateRequired: bool = False
    validateTypes: bool = False
    additionalRequiredFields: Optional[List[str]] = []

    _trim_names = validator('name', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'additionalRequiredFields': {
                'value': values.get('additionalRequiredFields'),
                'validator': 'STRING_256_ARRAY', 'optional': True,
            },
        })
        if not valid:
            raise ValueError(message)
        return values


class MetadataRule(BaseModel, extra='ignore'):
    """Metadata rule: validates asset metadata against a VAMS metadata schema."""
    ruleType: RuleType = Field(RuleType.metadata, const=True)
    enforcement: EnforcementLevel
    metadataSchemaRef: MetadataSchemaRef
    checks: List[MetadataCheck] = Field(min_items=1)


class RelationshipCheck(BaseModel, extra='ignore'):
    """A single check within a relationship rule."""
    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_LENGTH)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    direction: str = Field(..., regex=r"^(parents|children|related)$")
    relationshipType: str = Field(..., regex=r"^(parentChild|related)$")
    minCount: Optional[int] = Field(None, ge=0)
    maxCount: Optional[int] = Field(None, ge=0)

    _trim_names = validator('name', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_counts(cls, values):
        min_count = values.get("minCount")
        max_count = values.get("maxCount")
        if min_count is not None and max_count is not None:
            if min_count > max_count:
                raise ValueError("'minCount' must be <= 'maxCount'")
        if min_count is None and max_count is None:
            raise ValueError("At least one of 'minCount' or 'maxCount' is required")
        return values


class RelationshipRule(BaseModel, extra='ignore'):
    """Relationship rule: validates that an asset carries the required links."""
    ruleType: RuleType = Field(RuleType.relationship, const=True)
    enforcement: EnforcementLevel
    checks: List[RelationshipCheck] = Field(min_items=1)


ComplianceRule = Union[PipelineRule, MetadataRule, RelationshipRule]

RULE_TYPE_MODELS = {
    RuleType.pipeline.value: PipelineRule,
    RuleType.metadata.value: MetadataRule,
    RuleType.relationship.value: RelationshipRule,
}


class VamsRulesV1Schema(BaseModel, extra='ignore'):
    """Top-level schema body for the vams-rules-v1 format."""
    schemaFormat: str = Field("vams-rules-v1", const=True)
    extends: Optional[str] = Field(
        None, max_length=256, description="Parent schema name to inherit rules from",
    )
    rules: Dict[str, Any]

    _trim_names = validator('extends', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_rules(cls, values):
        rules = values.get("rules")
        if not rules:
            raise ValueError("At least one rule is required")
        if not isinstance(rules, dict):
            raise ValueError("'rules' must be an object")
        (valid, message) = validate({
            'extends': {'value': values.get('extends'), 'validator': 'ID', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        for rule_name, rule_def in rules.items():
            if not isinstance(rule_def, dict):
                logger.info(f"vams-rules-v1 rule '{rule_name}' is not an object")
                raise ValueError("Every rule must be an object")
            rule_type = rule_def.get("ruleType")
            if rule_type not in RULE_TYPE_MODELS:
                logger.info(f"vams-rules-v1 rule '{rule_name}' has unsupported ruleType '{rule_type}'")
                raise ValueError(
                    "Every rule must declare a ruleType of "
                    + ", ".join(RULE_TYPE_MODELS))
        return values

    def parse_rules(self) -> Dict[str, ComplianceRule]:
        """Parse the raw rule dicts into typed rule models."""
        parsed = {}
        for rule_name, rule_def in self.rules.items():
            parsed[rule_name] = RULE_TYPE_MODELS[rule_def["ruleType"]](**rule_def)
        return parsed


def resolve_schema_inheritance(
    child_body: Dict[str, Any],
    parent_body: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge parent rules into child rules; a child rule overrides a parent rule of the same name.

    Returns a merged rules dict ready for evaluation.
    """
    if parent_body is None:
        return child_body.get("rules", {})

    parent_rules = parent_body.get("rules", {})
    child_rules = child_body.get("rules", {})

    return {**parent_rules, **child_rules}


# --- Evaluation result models ---

# Statuses of a rule result. `evaluated` is a rule whose checks ran (and passed or failed);
# `error` is a rule the tooling could not evaluate at all — a pipeline rule whose input selection
# the workflow does not accept, matches no single file or names a file the asset lacks, or whose
# workflow execution could not be started. Whether an `error` result bears on the verdict is decided
# by `common.compliance.evaluationEngine.TOOLING_FAILURES_APPLY_ENFORCEMENT`.
RULE_STATUS_EVALUATED = "evaluated"
RULE_STATUS_ERROR = "error"
RULE_STATUSES = (RULE_STATUS_EVALUATED, RULE_STATUS_ERROR)


class RuleResult(BaseModel, extra='ignore'):
    """Result of evaluating a single compliance rule.

    `status` is `evaluated` (the checks ran; `passed` is their outcome) or `error` (the rule could
    not be evaluated; `passed` is False and `message` says why). A stored result without a status
    reads as `evaluated`.
    """
    ruleName: str
    ruleType: str
    enforcement: str
    passed: bool
    status: str = Field(RULE_STATUS_EVALUATED, regex="^(" + "|".join(RULE_STATUSES) + ")$")
    message: Optional[str] = None
    measured: Optional[Dict[str, Any]] = None
    expected: Optional[Dict[str, Any]] = None


class EvaluationVerdict(str, Enum):
    """Final verdict of a compliance evaluation."""
    compliant = "compliant"
    non_compliant = "non_compliant"
    quarantined = "quarantined"
    pending_pipeline = "pending_pipeline"
    error = "error"


def determine_verdict(rule_results: List[RuleResult]) -> EvaluationVerdict:
    """Determine the final compliance verdict from rule results.

    Precedence: quarantine > warn > inform. Every result given counts: the caller decides which
    results bear on the verdict (`common.compliance.evaluationEngine.determine_verdict` leaves out
    `status: error` results unless `TOOLING_FAILURES_APPLY_ENFORCEMENT` is set).
    """
    has_quarantine_failure = False
    has_warn_failure = False

    for result in rule_results:
        if result.passed:
            continue
        if result.enforcement == EnforcementLevel.quarantine:
            has_quarantine_failure = True
        elif result.enforcement == EnforcementLevel.warn:
            has_warn_failure = True

    if has_quarantine_failure:
        return EvaluationVerdict.quarantined
    if has_warn_failure:
        return EvaluationVerdict.non_compliant
    return EvaluationVerdict.compliant


# --- Schema request models ---


class CreateSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for registering a compliance schema (POST /compliance/schemas)."""
    schemaName: str = Field(min_length=3, max_length=63, regex=id_pattern)
    description: Optional[str] = Field("", max_length=MAX_DESCRIPTION_LENGTH)
    schemaBody: Dict[str, Any]
    databaseId: str = Field(GLOBAL_DATABASE_ID, min_length=3, max_length=63)
    isSystem: Optional[bool] = False

    _trim_names = validator('schemaName', 'databaseId', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'schemaName': {'value': values.get('schemaName'), 'validator': 'ID'},
            'databaseId': {
                'value': values.get('databaseId'), 'validator': 'ID', 'allowGlobalKeyword': True,
            },
        })
        if not valid:
            raise ValueError(message)
        schema_body = values.get("schemaBody")
        if not schema_body or not isinstance(schema_body, dict):
            raise ValueError("schemaBody must be a non-empty object")
        return values


class UpdateSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a compliance schema (PUT /compliance/schemas/{schemaName})."""
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    schemaBody: Optional[Dict[str, Any]] = None
    databaseId: Optional[str] = Field(None, min_length=3, max_length=63)

    _trim_names = validator('databaseId', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'), 'validator': 'ID',
                'allowGlobalKeyword': True, 'optional': True,
            },
        })
        if not valid:
            raise ValueError(message)
        schema_body = values.get("schemaBody")
        if schema_body is not None and not isinstance(schema_body, dict):
            raise ValueError("schemaBody must be an object")
        return values


# --- Evaluation request models ---


class EvaluateAssetRequestModel(BaseModel, extra='ignore'):
    """Request model for evaluating an asset (POST /compliance/evaluate/{databaseId}/{assetId})."""
    schemaName: Optional[str] = Field(None, max_length=63)

    _trim_names = validator('schemaName', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'schemaName': {'value': values.get('schemaName'), 'validator': 'ID', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        return values


class SweepSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for sweeping a schema (POST /compliance/sweep/{schemaName}); no fields."""
    pass


# --- Quarantine request models ---


class ReleaseQuarantineRequestModel(BaseModel, extra='ignore'):
    """Request model for releasing an asset from quarantine."""
    reason: Optional[str] = Field("released via API", max_length=MAX_REASON_LENGTH)

    _trim_text = validator('reason', pre=True, allow_reuse=True)(trim_name)


class GrantExceptionRequestModel(BaseModel, extra='ignore'):
    """Request model for granting a quarantine exception."""
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)

    _trim_text = validator('reason', pre=True, allow_reuse=True)(trim_name)


# --- Cascade request models ---


class CreateCascadeRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a cascade (POST /compliance/cascades)."""
    databaseId: str = Field(min_length=3, max_length=63)
    assetId: str = Field(min_length=1, max_length=256)
    reason: Optional[str] = Field("manual trigger", max_length=MAX_REASON_LENGTH)
    requireApproval: bool = True

    _trim_names = validator('databaseId', 'assetId', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('reason', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'), 'validator': 'ID', 'allowGlobalKeyword': True,
            },
            'assetId': {'value': values.get('assetId'), 'validator': 'ASSET_ID'},
        })
        if not valid:
            raise ValueError(message)
        return values


class ApproveCascadeRequestModel(BaseModel, extra='ignore'):
    """Request model for approving a cascade."""
    reason: Optional[str] = Field("approved", max_length=MAX_REASON_LENGTH)

    _trim_text = validator('reason', pre=True, allow_reuse=True)(trim_name)


class RejectCascadeRequestModel(BaseModel, extra='ignore'):
    """Request model for rejecting a cascade."""
    reason: Optional[str] = Field("rejected", max_length=MAX_REASON_LENGTH)

    _trim_text = validator('reason', pre=True, allow_reuse=True)(trim_name)


# --- Schema binding request models ---


class BindSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for binding a schema to a database or an asset."""
    schemaName: str = Field(min_length=3, max_length=63, regex=id_pattern)
    complianceAutoEval: Optional[bool] = True

    _trim_names = validator('schemaName', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'schemaName': {'value': values.get('schemaName'), 'validator': 'ID'},
        })
        if not valid:
            raise ValueError(message)
        return values
