# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from enum import Enum
from typing import Dict, List, Optional, Any, Union
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="FMMModels")


# --- VAMS-Rules-v1 Schema Format Models ---


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
    """Tolerance definition for pipeline output checks."""
    operator: ToleranceOperator
    value: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    epsilon: Optional[float] = Field(None, description="For eq operator")

    @root_validator
    def validate_tolerance(cls, values):
        op = values.get("operator")
        if op in (ToleranceOperator.lte, ToleranceOperator.gte, ToleranceOperator.eq):
            if values.get("value") is None:
                raise ValueError(
                    f"'value' is required for operator '{op}'"
                )
        if op == ToleranceOperator.between:
            if values.get("min") is None or values.get("max") is None:
                raise ValueError(
                    "'min' and 'max' are required for operator 'between'"
                )
            if values["min"] > values["max"]:
                raise ValueError("'min' must be <= 'max'")
        return values


class PipelineRef(BaseModel, extra='ignore'):
    """Reference to a VAMS workflow for pipeline rule execution."""
    databaseId: str = Field(min_length=1, max_length=256)
    workflowId: str = Field(min_length=1, max_length=256)


class PipelineCheck(BaseModel, extra='ignore'):
    """A single check within a pipeline rule."""
    name: str = Field(min_length=1, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    outputField: str = Field(min_length=1, max_length=256)
    tolerance: Tolerance


class PipelineRule(BaseModel, extra='ignore'):
    """Pipeline rule: invokes a VAMS workflow, compares outputs to tolerances."""
    ruleType: RuleType = Field(RuleType.pipeline, const=True)
    enforcement: EnforcementLevel
    pipelineRef: PipelineRef
    inputParameters: Optional[Dict[str, Any]] = {}
    checks: List[PipelineCheck] = Field(min_items=1)


class MetadataSchemaRef(BaseModel, extra='ignore'):
    """Reference to a VAMS metadata schema."""
    databaseId: str = Field(min_length=1, max_length=256)
    schemaName: str = Field(min_length=1, max_length=256)


class MetadataCheck(BaseModel, extra='ignore'):
    """A single check within a metadata rule."""
    name: str = Field(min_length=1, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    validateRequired: bool = False
    validateTypes: bool = False
    additionalRequiredFields: Optional[List[str]] = []


class MetadataRule(BaseModel, extra='ignore'):
    """Metadata rule: validates asset metadata against a VAMS metadata schema."""
    ruleType: RuleType = Field(RuleType.metadata, const=True)
    enforcement: EnforcementLevel
    metadataSchemaRef: MetadataSchemaRef
    checks: List[MetadataCheck] = Field(min_items=1)


class RelationshipCheck(BaseModel, extra='ignore'):
    """A single check within a relationship rule."""
    name: str = Field(min_length=1, max_length=256)
    description: Optional[str] = Field(None, max_length=1024)
    direction: str = Field(..., regex=r"^(parents|children|related)$")
    relationshipType: str = Field(..., regex=r"^(parentChild|related)$")
    minCount: Optional[int] = Field(None, ge=0)
    maxCount: Optional[int] = Field(None, ge=0)

    @root_validator
    def validate_counts(cls, values):
        min_count = values.get("minCount")
        max_count = values.get("maxCount")
        if min_count is not None and max_count is not None:
            if min_count > max_count:
                raise ValueError("'minCount' must be <= 'maxCount'")
        if min_count is None and max_count is None:
            raise ValueError(
                "At least one of 'minCount' or 'maxCount' is required"
            )
        return values


class RelationshipRule(BaseModel, extra='ignore'):
    """Relationship rule: validates asset has required links."""
    ruleType: RuleType = Field(RuleType.relationship, const=True)
    enforcement: EnforcementLevel
    checks: List[RelationshipCheck] = Field(min_items=1)


ComplianceRule = Union[PipelineRule, MetadataRule, RelationshipRule]


class VamsRulesV1Schema(BaseModel, extra='ignore'):
    """Top-level schema body for vams-rules-v1 format."""
    schemaFormat: str = Field("vams-rules-v1", const=True)
    extends: Optional[str] = Field(
        None, max_length=256,
        description="Parent schema name to inherit rules from"
    )
    rules: Dict[str, Any]

    @root_validator
    def validate_rules(cls, values):
        rules = values.get("rules")
        if not rules:
            raise ValueError("At least one rule is required")
        if not isinstance(rules, dict):
            raise ValueError("'rules' must be an object")
        for rule_name, rule_def in rules.items():
            if not isinstance(rule_def, dict):
                raise ValueError(
                    f"Rule '{rule_name}' must be an object"
                )
            rule_type = rule_def.get("ruleType")
            if rule_type not in ("pipeline", "metadata", "relationship"):
                raise ValueError(
                    f"Rule '{rule_name}' has invalid ruleType: {rule_type}"
                )
        return values

    def parse_rules(self) -> Dict[str, ComplianceRule]:
        """Parse raw rule dicts into typed rule models."""
        parsed = {}
        for rule_name, rule_def in self.rules.items():
            rule_type = rule_def["ruleType"]
            if rule_type == "pipeline":
                parsed[rule_name] = PipelineRule(**rule_def)
            elif rule_type == "metadata":
                parsed[rule_name] = MetadataRule(**rule_def)
            elif rule_type == "relationship":
                parsed[rule_name] = RelationshipRule(**rule_def)
        return parsed


def resolve_schema_inheritance(
    child_body: Dict[str, Any],
    parent_body: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Resolve schema inheritance by merging parent rules into child.

    Child rules override parent rules with the same name.
    Returns a merged rules dict ready for evaluation.
    """
    if parent_body is None:
        return child_body.get("rules", {})

    parent_rules = parent_body.get("rules", {})
    child_rules = child_body.get("rules", {})

    merged = {**parent_rules, **child_rules}
    return merged


# --- Evaluation Result Models ---


class RuleResult(BaseModel, extra='ignore'):
    """Result of evaluating a single compliance rule."""
    ruleName: str
    ruleType: str
    enforcement: str
    passed: bool
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
    """Determine final compliance verdict from rule results.

    Precedence: quarantine > warn > inform.
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


# --- Schema Models ---

class CreateSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a compliance schema."""
    schemaName: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    description: Optional[str] = Field(None, max_length=1024)
    schemaBody: Dict[str, Any]

    @root_validator
    def validate_fields(cls, values):
        schema_body = values.get("schemaBody")
        if not schema_body or not isinstance(schema_body, dict):
            raise ValueError("schemaBody must be a non-empty object")
        return values


class UpdateSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a compliance schema."""
    description: Optional[str] = Field(None, max_length=1024)
    schemaBody: Optional[Dict[str, Any]] = None

    @root_validator
    def validate_fields(cls, values):
        schema_body = values.get("schemaBody")
        if schema_body is not None and not isinstance(schema_body, dict):
            raise ValueError("schemaBody must be an object")
        return values


# --- Evaluation Models ---

class EvaluateAssetRequestModel(BaseModel, extra='ignore'):
    """Request model for triggering asset compliance evaluation."""
    schemaName: Optional[str] = Field(None, max_length=256)
    datasetPath: Optional[str] = None


class SweepSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for triggering a schema sweep."""
    pass


# --- Quarantine Models ---

class ReleaseQuarantineRequestModel(BaseModel, extra='ignore'):
    """Request model for releasing an asset from quarantine."""
    reason: Optional[str] = Field("released via API", max_length=1024)


class GrantExceptionRequestModel(BaseModel, extra='ignore'):
    """Request model for granting a quarantine exception."""
    reason: str = Field(min_length=1, max_length=1024)


# --- Cascade Models ---

class CreateCascadeRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a cascade execution."""
    databaseId: str = Field(min_length=1, max_length=256)
    assetId: str = Field(min_length=1, max_length=256)
    reason: Optional[str] = Field("manual trigger", max_length=1024)
    requireApproval: bool = True
    nodes: Optional[Dict[str, Any]] = {}
    executionOrder: Optional[List[str]] = []

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID'
            },
        })
        if not valid:
            raise ValueError(message)
        return values


class ApproveCascadeRequestModel(BaseModel, extra='ignore'):
    """Request model for approving a cascade."""
    reason: Optional[str] = Field("approved", max_length=1024)


class RejectCascadeRequestModel(BaseModel, extra='ignore'):
    """Request model for rejecting a cascade."""
    reason: Optional[str] = Field("rejected", max_length=1024)


# --- Schema Binding Models ---

class BindSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for binding a schema to a database or asset."""
    schemaName: str = Field(min_length=1, max_length=256)
