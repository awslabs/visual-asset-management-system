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
from typing import Any, Dict, List, Literal, Optional, Union

from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from pydantic import Field

from common.validators import (
    MAX_REGEX_SUBJECT_LENGTH, MAX_S3_OBJECT_KEY_BYTES, id_pattern, trim_name, validate,
)
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
    fileStructure = "fileStructure"
    versionCoverage = "versionCoverage"
    assetAttributes = "assetAttributes"


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
    """Reference to a VAMS metadata schema by name, and where it is looked up.

    `databaseId` omitted (the default): the evaluated asset's database, then GLOBAL — so a GLOBAL
    compliance schema bound to several databases uses each database's own metadata schema of that
    name where one exists. `databaseId` given pins the lookup to that database, then GLOBAL; the
    schema service accepts only `GLOBAL` or the compliance schema's own database there
    (`complianceSchemaService.schema_reference_error`)."""
    databaseId: Optional[str] = Field(None, min_length=3, max_length=63)
    schemaName: str = Field(min_length=1, max_length=256)

    _trim_names = validator('databaseId', 'schemaName', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_ids(cls, values):
        checks = {'schemaName': {'value': values.get('schemaName'), 'validator': 'OBJECT_NAME'}}
        if values.get('databaseId') is not None:
            checks['databaseId'] = {
                'value': values.get('databaseId'), 'validator': 'ID', 'allowGlobalKeyword': True,
            }
        (valid, message) = validate(checks)
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


# Bounds on the metadata, file-structure, version-coverage and asset-attribute rules: the number of
# checks a rule carries, the path patterns and tags a check (or a metadata rule's file scope) lists,
# and the length of one pattern. Every
# pattern is also held to the `REGEX` validator's compile check and backtracking budget, the budget
# computed for the longest value the pattern is searched in (`validate_patterns`).
MAX_CHECKS_PER_RULE = 25
MAX_PATH_PATTERNS = 25
MAX_PATTERN_LENGTH = 512
MAX_TAGS_PER_CHECK = 50

# The asset-row attributes an asset-attribute check reads.
ASSET_ATTRIBUTE_NAME = "assetName"
ASSET_ATTRIBUTE_DESCRIPTION = "description"
ASSET_ATTRIBUTE_TAGS = "tags"
ASSET_ATTRIBUTE_TEXT_FIELDS = (ASSET_ATTRIBUTE_NAME, ASSET_ATTRIBUTE_DESCRIPTION)


def validate_patterns(field_name: str, patterns: Optional[List[str]],
                      subject_length: int = MAX_REGEX_SUBJECT_LENGTH) -> None:
    """Raise ValueError unless every entry of `patterns` is a 1 to MAX_PATTERN_LENGTH character
    regular expression the `REGEX` validator accepts, its backtracking budget computed for values of
    `subject_length` characters: MAX_S3_OBJECT_KEY_BYTES for a path pattern, which is searched in a
    file's S3 key, and the validator's 256-character default for a name or description pattern
    (an asset's name and description are at most 256 characters). The budget also counts the start
    positions `re.search` tries for a pattern not anchored with '^' (`searched`), since every rule
    pattern is searched rather than matched. The message names the field, never a pattern."""
    for pattern in patterns or []:
        if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_LENGTH:
            raise ValueError(
                f"Every '{field_name}' pattern must be 1 to {MAX_PATTERN_LENGTH} characters")
        (valid, message) = validate({field_name: {'value': pattern, 'validator': 'REGEX',
                                                  'subjectLength': subject_length,
                                                  'searched': True}})
        if not valid:
            raise ValueError(message)


def validate_tags(field_name: str, tags: Optional[List[str]]) -> None:
    """Raise ValueError unless every entry of `tags` is a non-empty string of at most 256
    characters (`STRING_256_ARRAY`). The message names the field, never a tag."""
    if not tags:
        return
    if any(not isinstance(tag, str) or not tag for tag in tags):
        raise ValueError(f"Every '{field_name}' entry must be a non-empty string")
    (valid, message) = validate({field_name: {'value': tags, 'validator': 'STRING_256_ARRAY'}})
    if not valid:
        raise ValueError(message)


class MetadataScope(str, Enum):
    """What a metadata rule validates. Each scope reads the metadata of one VAMS entity type, and
    the rule's metadata schema must be of that type (`evaluationEngine.SCOPE_ENTITY_TYPE`)."""
    asset = "asset"                    # the asset's own metadata (the default)
    files = "files"                    # file metadata, per current non-archived file
    fileAttributes = "fileAttributes"  # file attributes, per current non-archived file
    links = "links"                    # asset-link metadata, per link of the asset


# The scopes that iterate the asset's current files and accept `includePaths` / `excludePaths`.
METADATA_FILE_SCOPES = (MetadataScope.files, MetadataScope.fileAttributes)


class MetadataRule(BaseModel, extra='ignore'):
    """Metadata rule: validates metadata against a VAMS metadata schema.

    `scope` selects the metadata: the asset's own (`asset`, the default), each current file's
    metadata (`files`) or attributes (`fileAttributes`), or each asset link's metadata (`links`).
    `includePaths` / `excludePaths` narrow a file scope to the files whose path matches (Python
    regular expressions searched in the asset-relative path without its leading '/'); they are
    refused on any other scope. `linkDirection` / `relationshipType` narrow the links scope (absent:
    every link of the asset, of either type) and are refused on any other scope.
    """
    ruleType: RuleType = Field(RuleType.metadata, const=True)
    enforcement: EnforcementLevel
    metadataSchemaRef: MetadataSchemaRef
    scope: MetadataScope = MetadataScope.asset
    includePaths: List[str] = Field(default_factory=list, max_items=MAX_PATH_PATTERNS)
    excludePaths: List[str] = Field(default_factory=list, max_items=MAX_PATH_PATTERNS)
    linkDirection: Optional[Literal["parents", "children", "related"]] = None
    relationshipType: Optional[Literal["parentChild", "related"]] = None
    checks: List[MetadataCheck] = Field(min_items=1, max_items=MAX_CHECKS_PER_RULE)

    _trim_patterns = validator('includePaths', 'excludePaths', pre=True,
                               allow_reuse=True)(trim_entries)

    @root_validator(skip_on_failure=True)
    def validate_scope_fields(cls, values):
        scope = values.get("scope")
        if scope in METADATA_FILE_SCOPES:
            validate_patterns("includePaths", values.get("includePaths"), MAX_S3_OBJECT_KEY_BYTES)
            validate_patterns("excludePaths", values.get("excludePaths"), MAX_S3_OBJECT_KEY_BYTES)
        elif values.get("includePaths") or values.get("excludePaths"):
            raise ValueError(
                "'includePaths' and 'excludePaths' apply to the files and fileAttributes scopes only")
        if scope != MetadataScope.links and (values.get("linkDirection") is not None
                                             or values.get("relationshipType") is not None):
            raise ValueError("'linkDirection' and 'relationshipType' apply to the links scope only")
        return values


class FileStructureCheck(BaseModel, extra='ignore'):
    """A single check within a file-structure rule, over the asset's current non-archived files.

    `requiredPaths`: every pattern must match at least one file. `forbiddenPaths`: no file may match
    any pattern. `minFiles` / `maxFiles`: bounds on the number of files. Patterns are Python regular
    expressions searched (`re.search`) in each file's asset-relative path without its leading '/'.
    """
    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_LENGTH)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    requiredPaths: List[str] = Field(default_factory=list, max_items=MAX_PATH_PATTERNS)
    forbiddenPaths: List[str] = Field(default_factory=list, max_items=MAX_PATH_PATTERNS)
    minFiles: Optional[int] = Field(None, ge=0)
    maxFiles: Optional[int] = Field(None, ge=0)

    _trim_names = validator('name', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator(skip_on_failure=True)
    def validate_check(cls, values):
        min_files = values.get("minFiles")
        max_files = values.get("maxFiles")
        if not (values.get("requiredPaths") or values.get("forbiddenPaths")
                or min_files is not None or max_files is not None):
            raise ValueError(
                "At least one of 'requiredPaths', 'forbiddenPaths', 'minFiles' or 'maxFiles' "
                "is required")
        if min_files is not None and max_files is not None and min_files > max_files:
            raise ValueError("'minFiles' must be <= 'maxFiles'")
        validate_patterns("requiredPaths", values.get("requiredPaths"), MAX_S3_OBJECT_KEY_BYTES)
        validate_patterns("forbiddenPaths", values.get("forbiddenPaths"), MAX_S3_OBJECT_KEY_BYTES)
        return values


class FileStructureRule(BaseModel, extra='ignore'):
    """File-structure rule: checks the paths and the number of the asset's current files."""
    ruleType: RuleType = Field(RuleType.fileStructure, const=True)
    enforcement: EnforcementLevel
    checks: List[FileStructureCheck] = Field(min_items=1, max_items=MAX_CHECKS_PER_RULE)


class VersionCoverageCheck(BaseModel, extra='ignore'):
    """A single check within a version-coverage rule: every current non-archived file not matched by
    an `excludePaths` pattern has its current S3 version recorded in some asset version."""
    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_LENGTH)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    excludePaths: List[str] = Field(default_factory=list, max_items=MAX_PATH_PATTERNS)

    _trim_names = validator('name', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator(skip_on_failure=True)
    def validate_check(cls, values):
        validate_patterns("excludePaths", values.get("excludePaths"), MAX_S3_OBJECT_KEY_BYTES)
        return values


class VersionCoverageRule(BaseModel, extra='ignore'):
    """Version-coverage rule: checks that the asset's current files are captured in asset versions."""
    ruleType: RuleType = Field(RuleType.versionCoverage, const=True)
    enforcement: EnforcementLevel
    checks: List[VersionCoverageCheck] = Field(min_items=1, max_items=MAX_CHECKS_PER_RULE)


class AssetAttributeCheck(BaseModel, extra='ignore'):
    """A single check within an asset-attribute rule, over one attribute of the asset row.

    For `assetName` and `description` exactly one of `matches` (the value must match; a missing or
    empty value fails) or `notMatches` (the value must not match; a missing or empty value passes) is
    given, as a Python regular expression searched in the value. For `tags` at least one of
    `requiredTags`, `forbiddenTags` or `minTags` is given; tags compare by exact value.
    """
    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_LENGTH)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    field: Literal["assetName", "description", "tags"]
    matches: Optional[str] = None
    notMatches: Optional[str] = None
    requiredTags: List[str] = Field(default_factory=list, max_items=MAX_TAGS_PER_CHECK)
    forbiddenTags: List[str] = Field(default_factory=list, max_items=MAX_TAGS_PER_CHECK)
    minTags: Optional[int] = Field(None, ge=0)

    _trim_names = validator('name', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)
    _trim_tags = validator('requiredTags', 'forbiddenTags', pre=True, allow_reuse=True)(trim_entries)

    @root_validator(skip_on_failure=True)
    def validate_check(cls, values):
        field = values.get("field")
        has_pattern = values.get("matches") is not None or values.get("notMatches") is not None
        has_tag_criteria = bool(values.get("requiredTags") or values.get("forbiddenTags")
                                or values.get("minTags") is not None)
        if field in ASSET_ATTRIBUTE_TEXT_FIELDS:
            if has_tag_criteria:
                raise ValueError(
                    "'requiredTags', 'forbiddenTags' and 'minTags' apply to the tags field only")
            if (values.get("matches") is None) == (values.get("notMatches") is None):
                raise ValueError(
                    "Exactly one of 'matches' or 'notMatches' is required for this field")
            for key in ("matches", "notMatches"):
                if values.get(key) is not None:
                    validate_patterns(key, [values[key]])
        else:
            if has_pattern:
                raise ValueError("'matches' and 'notMatches' do not apply to the tags field")
            if not has_tag_criteria:
                raise ValueError(
                    "At least one of 'requiredTags', 'forbiddenTags' or 'minTags' is required")
            validate_tags("requiredTags", values.get("requiredTags"))
            validate_tags("forbiddenTags", values.get("forbiddenTags"))
        return values


class AssetAttributesRule(BaseModel, extra='ignore'):
    """Asset-attribute rule: checks the asset's name, description or tags."""
    ruleType: RuleType = Field(RuleType.assetAttributes, const=True)
    enforcement: EnforcementLevel
    checks: List[AssetAttributeCheck] = Field(min_items=1, max_items=MAX_CHECKS_PER_RULE)


ComplianceRule = Union[
    PipelineRule, MetadataRule, RelationshipRule, FileStructureRule, VersionCoverageRule,
    AssetAttributesRule,
]

RULE_TYPE_MODELS = {
    RuleType.pipeline.value: PipelineRule,
    RuleType.metadata.value: MetadataRule,
    RuleType.relationship.value: RelationshipRule,
    RuleType.fileStructure.value: FileStructureRule,
    RuleType.versionCoverage.value: VersionCoverageRule,
    RuleType.assetAttributes.value: AssetAttributesRule,
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


class EvaluateDatabaseRequestModel(BaseModel, extra='ignore'):
    """Request model for evaluating every tracked asset of a database
    (POST /compliance/evaluate/{databaseId}); no fields."""
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
    """Request model for binding a schema to a database or an asset. `complianceAutoEval` (database
    binding only) is None when the request omits it: a new binding then defaults it to true and an
    existing one keeps its stored value."""
    schemaName: str = Field(min_length=3, max_length=63, regex=id_pattern)
    complianceAutoEval: Optional[bool] = None

    _trim_names = validator('schemaName', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'schemaName': {'value': values.get('schemaName'), 'validator': 'ID'},
        })
        if not valid:
            raise ValueError(message)
        return values
