#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance evaluation engine: pure rule logic for vams-rules-v1 schemas.

Every function here is a pure function over data the caller has already fetched (schema
bodies, asset metadata, metadata-schema field definitions, asset links, pipeline
measurements). DynamoDB / S3 / Lambda access lives in
`handlers/compliance/complianceEvaluationStore.py`, which calls into this module.
"""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from customLogging.logger import safeLogger
from models.compliance import (
    ComplianceRule,
    EvaluationVerdict,
    MetadataCheck,
    MetadataRule,
    PipelineRule,
    RelationshipCheck,
    RelationshipRule,
    RuleResult,
    RULE_TYPE_MODELS,
    Tolerance,
    ToleranceOperator,
    resolve_schema_inheritance,
)

logger = safeLogger(service_name="ComplianceEvaluationEngine")

VAMS_RULES_V1 = "vams-rules-v1"

# Compliance states stored on the asset-state table.
STATE_COMPLIANT = "compliant"
STATE_NON_COMPLIANT = "non_compliant"
STATE_QUARANTINED = "quarantined"
STATE_PENDING_EVALUATION = "pending_evaluation"
STATE_UNKNOWN = "unknown"
# An asset whose latest evaluation failed while a quarantine exception is active: released, never
# quarantined, with the violations kept on the evaluation row.
STATE_EXCEPTION = "exception"

# Asset-state attributes a quarantine exception writes. `exceptionSchemaName` /
# `exceptionSchemaVersion` scope the exception to the schema name and `internalVersion` it was
# granted against; an evaluation against any other schema or version supersedes it.
EXCEPTION_GRANTED_FIELD = "exceptionGranted"
EXCEPTION_SCHEMA_NAME_FIELD = "exceptionSchemaName"
EXCEPTION_SCHEMA_VERSION_FIELD = "exceptionSchemaVersion"
EXCEPTION_FIELDS = (
    EXCEPTION_GRANTED_FIELD,
    "exceptionReason",
    "exceptionGrantedBy",
    "exceptionGrantedAt",
    EXCEPTION_SCHEMA_NAME_FIELD,
    EXCEPTION_SCHEMA_VERSION_FIELD,
)

# Evaluation record statuses.
EVALUATION_STATUS_COMPLETED = "completed"
EVALUATION_STATUS_PENDING_PIPELINE = "pending_pipeline"
EVALUATION_STATUS_ERROR = "error"
EVALUATION_STATUS_FAILED = "failed"

# The measurement file a pipeline writes under its execution's results output prefix.
COMPLIANCE_OUTPUT_FILE_NAME = "compliance-output.json"

# Bound on the schema `extends` chain, so a self-referencing schema cannot recurse forever.
MAX_SCHEMA_INHERITANCE_DEPTH = 10

# Asset-link directions on the relationship-check `direction` field.
LINK_DIRECTION_PARENTS = "parents"
LINK_DIRECTION_CHILDREN = "children"
LINK_DIRECTION_RELATED = "related"

_VERDICT_TO_STATE = {
    EvaluationVerdict.compliant: STATE_COMPLIANT,
    EvaluationVerdict.non_compliant: STATE_NON_COMPLIANT,
    EvaluationVerdict.quarantined: STATE_QUARANTINED,
    EvaluationVerdict.pending_pipeline: STATE_PENDING_EVALUATION,
    EvaluationVerdict.error: STATE_UNKNOWN,
}

_METADATA_TYPE_MAP = {
    "STRING": str,
    "NUMBER": (int, float),
    "BOOLEAN": bool,
}


# --- Schema body helpers ---


def parse_schema_body(raw: Any) -> Optional[Dict[str, Any]]:
    """A stored schema body (JSON string or dict) as a dict; None when it is not parseable."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def is_vams_rules_schema(schema_body: Optional[Dict[str, Any]]) -> bool:
    """True when the body declares the vams-rules-v1 format."""
    return bool(schema_body) and schema_body.get("schemaFormat") == VAMS_RULES_V1


def resolve_rules(
    schema_body: Dict[str, Any],
    load_parent: Callable[[str], Optional[Dict[str, Any]]],
    depth: int = 0,
) -> Dict[str, Any]:
    """Merged rule dicts for a schema body, following its `extends` chain.

    `load_parent(schema_name)` returns a parent's schema body (or None). A missing parent, a
    non-vams-rules parent, or a chain deeper than MAX_SCHEMA_INHERITANCE_DEPTH stops the walk
    and the child's own rules are used from that point.
    """
    parent_name = schema_body.get("extends")
    if not parent_name:
        return schema_body.get("rules", {}) or {}
    if depth >= MAX_SCHEMA_INHERITANCE_DEPTH:
        logger.warning("Schema inheritance chain exceeds the depth bound; using child rules only")
        return schema_body.get("rules", {}) or {}

    parent_body = load_parent(parent_name)
    if parent_body is None or not is_vams_rules_schema(parent_body):
        logger.warning("Parent schema not found or not vams-rules-v1; using child rules only")
        return schema_body.get("rules", {}) or {}

    parent_rules = resolve_rules(parent_body, load_parent, depth + 1)
    return resolve_schema_inheritance(schema_body, {"rules": parent_rules})


def parse_resolved_rules(rules: Dict[str, Any]) -> Dict[str, ComplianceRule]:
    """Typed rule models for raw rule dicts; a rule that fails to parse is logged and skipped."""
    parsed: Dict[str, ComplianceRule] = {}
    for rule_name, rule_def in (rules or {}).items():
        if not isinstance(rule_def, dict):
            continue
        model = RULE_TYPE_MODELS.get(rule_def.get("ruleType"))
        if model is None:
            logger.warning(f"Unknown rule type in rule '{rule_name}'")
            continue
        try:
            parsed[rule_name] = model(**rule_def)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse rule '{rule_name}': {e}")
    return parsed


def split_rules(
    typed_rules: Dict[str, ComplianceRule],
) -> Tuple[Dict[str, MetadataRule], Dict[str, RelationshipRule], Dict[str, PipelineRule]]:
    """Partition typed rules by kind: (metadata, relationship, pipeline)."""
    metadata_rules: Dict[str, MetadataRule] = {}
    relationship_rules: Dict[str, RelationshipRule] = {}
    pipeline_rules: Dict[str, PipelineRule] = {}
    for rule_name, rule in typed_rules.items():
        if isinstance(rule, MetadataRule):
            metadata_rules[rule_name] = rule
        elif isinstance(rule, RelationshipRule):
            relationship_rules[rule_name] = rule
        elif isinstance(rule, PipelineRule):
            pipeline_rules[rule_name] = rule
    return metadata_rules, relationship_rules, pipeline_rules


# --- Metadata rules ---


def coerce_metadata_value(value: Optional[str], value_type: str) -> Any:
    """A stored metadata value string in its typed form (number / boolean / string)."""
    if value is None:
        return None
    upper = (value_type or "").upper()
    if upper == "NUMBER":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    if upper == "BOOLEAN":
        return str(value).lower() in ("true", "1", "yes")
    return value


def normalize_metadata_schema_fields(fields_raw: Any) -> Optional[List[Dict[str, Any]]]:
    """A metadata schema's `fields` attribute (JSON string, list, or {"fields": [...]}) as
    `[{"field", "required", "type"}]`; None when the value is not a field list."""
    if isinstance(fields_raw, str):
        try:
            fields_raw = json.loads(fields_raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(fields_raw, dict) and "fields" in fields_raw:
        fields_raw = fields_raw["fields"]
    if not isinstance(fields_raw, list):
        return None
    return [
        {
            "field": f.get("metadataFieldKeyName") or f.get("field", ""),
            "required": bool(f.get("required", False)),
            "type": f.get("metadataFieldValueType") or f.get("type", "string"),
        }
        for f in fields_raw
        if isinstance(f, dict)
    ]


def check_type_match(value: Any, expected_type: str) -> bool:
    """True when a metadata value matches the declared type (unknown types always match)."""
    expected = _METADATA_TYPE_MAP.get((expected_type or "").upper())
    if expected is None:
        return True
    return isinstance(value, expected)


def run_metadata_check(
    check: MetadataCheck,
    asset_metadata: Dict[str, Any],
    schema_fields: Optional[List[Dict[str, Any]]],
) -> Tuple[bool, str]:
    """One metadata check against the asset's metadata. Returns (passed, message)."""
    if check.validateRequired and schema_fields is not None:
        required_from_schema = [f["field"] for f in schema_fields if f.get("required")]
        missing = [f for f in required_from_schema if f not in asset_metadata]
        if missing:
            return False, f"{check.name}: missing required schema fields: {missing}"

    if check.additionalRequiredFields:
        missing = [f for f in check.additionalRequiredFields if f not in asset_metadata]
        if missing:
            return False, f"{check.name}: missing additional required fields: {missing}"

    if check.validateTypes and schema_fields is not None:
        type_errors = []
        for field_def in schema_fields:
            field_name = field_def.get("field")
            expected_type = field_def.get("type")
            if field_name in asset_metadata and expected_type:
                if not check_type_match(asset_metadata[field_name], expected_type):
                    type_errors.append(f"{field_name}: expected {expected_type}")
        if type_errors:
            return False, f"{check.name}: type mismatches: {type_errors}"

    return True, ""


def evaluate_metadata_rule(
    rule_name: str,
    rule: MetadataRule,
    asset_metadata: Dict[str, Any],
    schema_fields: Optional[List[Dict[str, Any]]],
) -> RuleResult:
    """A metadata rule against the asset's metadata and its referenced schema's fields."""
    all_passed = True
    messages = []
    for check in rule.checks:
        passed, msg = run_metadata_check(check, asset_metadata, schema_fields)
        if not passed:
            all_passed = False
            messages.append(msg)

    ref = rule.metadataSchemaRef
    return RuleResult(
        ruleName=rule_name,
        ruleType="metadata",
        enforcement=rule.enforcement.value,
        passed=all_passed,
        message="; ".join(messages) if messages else None,
        measured={"metadataKeys": sorted(asset_metadata.keys())},
        expected={"schemaRef": f"{ref.databaseId}/{ref.schemaName}"},
    )


# --- Relationship rules ---


def count_links(
    asset_links: Dict[str, List[Dict[str, Any]]], direction: str, relationship_type: str,
) -> int:
    """Links of `relationship_type` in `direction` for an asset.

    `asset_links` is `{"parents": [link rows where the asset is the `to` side],
    "children": [link rows where the asset is the `from` side]}`; `related` counts both.
    """
    if direction == LINK_DIRECTION_PARENTS:
        rows = asset_links.get(LINK_DIRECTION_PARENTS, [])
    elif direction == LINK_DIRECTION_CHILDREN:
        rows = asset_links.get(LINK_DIRECTION_CHILDREN, [])
    else:
        rows = (asset_links.get(LINK_DIRECTION_PARENTS, [])
                + asset_links.get(LINK_DIRECTION_CHILDREN, []))
    return sum(1 for row in rows if row.get("relationshipType") == relationship_type)


def check_count(check: RelationshipCheck, count: int) -> Tuple[bool, str]:
    """Whether a link count satisfies the check's min/max bounds. Returns (passed, message)."""
    if check.minCount is not None and count < check.minCount:
        return False, f"{check.name}: found {count} links, minimum is {check.minCount}"
    if check.maxCount is not None and count > check.maxCount:
        return False, f"{check.name}: found {count} links, maximum is {check.maxCount}"
    return True, ""


def evaluate_relationship_rule(
    rule_name: str,
    rule: RelationshipRule,
    asset_links: Dict[str, List[Dict[str, Any]]],
) -> RuleResult:
    """A relationship rule against the asset's already-fetched links."""
    all_passed = True
    messages = []
    measured = {}
    for check in rule.checks:
        count = count_links(asset_links, check.direction, check.relationshipType)
        measured[check.name] = count
        passed, msg = check_count(check, count)
        if not passed:
            all_passed = False
            messages.append(msg)

    return RuleResult(
        ruleName=rule_name,
        ruleType="relationship",
        enforcement=rule.enforcement.value,
        passed=all_passed,
        message="; ".join(messages) if messages else None,
        measured=measured,
    )


# --- Pipeline rules ---


def compare_tolerance(check_name: str, value: Any, tolerance: Tolerance) -> Tuple[bool, str]:
    """A measured value against a tolerance. Returns (passed, message)."""
    try:
        measured = float(value)
    except (ValueError, TypeError):
        return False, f"{check_name}: value is not numeric"

    op = tolerance.operator
    if op == ToleranceOperator.lte:
        if measured <= tolerance.value:
            return True, ""
        return False, f"{check_name}: {measured} > {tolerance.value} (max)"
    if op == ToleranceOperator.gte:
        if measured >= tolerance.value:
            return True, ""
        return False, f"{check_name}: {measured} < {tolerance.value} (min)"
    if op == ToleranceOperator.eq:
        epsilon = tolerance.epsilon or 0.001
        if abs(measured - tolerance.value) <= epsilon:
            return True, ""
        return False, f"{check_name}: {measured} != {tolerance.value} (epsilon={epsilon})"
    if op == ToleranceOperator.between:
        if tolerance.min <= measured <= tolerance.max:
            return True, ""
        return False, f"{check_name}: {measured} not in [{tolerance.min}, {tolerance.max}]"
    return False, f"{check_name}: unknown operator '{op}'"


def evaluate_pipeline_rule(
    rule_name: str, rule: PipelineRule, measurements: Dict[str, Any],
) -> RuleResult:
    """A pipeline rule's checks against the measurements the pipeline reported."""
    rule_passed = True
    messages = []
    measured = {}
    expected = {}
    for check in rule.checks:
        value = measurements.get(check.outputField)
        measured[check.outputField] = value
        expected[check.outputField] = {
            "operator": check.tolerance.operator.value,
            "value": check.tolerance.value,
            "min": check.tolerance.min,
            "max": check.tolerance.max,
        }
        if value is None:
            rule_passed = False
            messages.append(f"{check.name}: output field '{check.outputField}' missing")
            continue
        check_passed, msg = compare_tolerance(check.name, value, check.tolerance)
        if not check_passed:
            rule_passed = False
            messages.append(msg)

    return RuleResult(
        ruleName=rule_name,
        ruleType="pipeline",
        enforcement=rule.enforcement.value,
        passed=rule_passed,
        message="; ".join(messages) if messages else None,
        measured=measured,
        expected=expected,
    )


def failed_pipeline_rule_results(
    pipeline_rules: Dict[str, PipelineRule], message: str,
) -> List[RuleResult]:
    """Every pipeline rule marked failed with one message (execution failed / no output)."""
    return [
        RuleResult(
            ruleName=rule_name,
            ruleType="pipeline",
            enforcement=rule.enforcement.value,
            passed=False,
            message=message,
        )
        for rule_name, rule in pipeline_rules.items()
    ]


def parse_compliance_output(text: Any) -> Optional[Dict[str, Any]]:
    """The measurement document a pipeline wrote (`compliance-output.json`), or None when the
    text is not a JSON object flagged with `complianceOutput: true`."""
    if isinstance(text, dict):
        document = text
    else:
        try:
            document = json.loads(text or "")
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(document, dict) or document.get("complianceOutput") is not True:
        return None
    return document


def default_pipeline_measurements(
    execution_status: str, started_at: Optional[str], completed_at: Optional[str],
) -> Dict[str, Any]:
    """Measurements available for every execution, used when a pipeline writes no
    compliance-output document: `execution_success` (1.0 / 0.0) and, when both timestamps
    parse, `processing_duration_seconds`."""
    from datetime import datetime

    measurements: Dict[str, Any] = {
        "execution_success": 1.0 if execution_status == "SUCCEEDED" else 0.0,
    }
    if started_at and completed_at:
        try:
            start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            stop = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
            measurements["processing_duration_seconds"] = round((stop - start).total_seconds(), 2)
        except (ValueError, TypeError):
            pass
    return measurements


def evaluate_pipeline_rules(
    pipeline_rules: Dict[str, PipelineRule],
    execution_status: str,
    compliance_output: Optional[Dict[str, Any]],
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> List[RuleResult]:
    """Pipeline-rule results once the workflow execution has completed.

    A non-succeeded execution fails every rule; an output document with `status: error` fails
    every rule; otherwise each rule's checks run against the document's measurements (or the
    default execution measurements when the pipeline wrote no document).
    """
    if execution_status != "SUCCEEDED":
        return failed_pipeline_rule_results(
            pipeline_rules, f"Pipeline execution {execution_status}")

    if compliance_output is None:
        measurements = default_pipeline_measurements(execution_status, started_at, completed_at)
    elif compliance_output.get("status") == "error":
        return failed_pipeline_rule_results(pipeline_rules, "Pipeline reported an error")
    else:
        measurements = compliance_output.get("measurements") or {}
        if not isinstance(measurements, dict):
            measurements = {}

    return [
        evaluate_pipeline_rule(rule_name, rule, measurements)
        for rule_name, rule in pipeline_rules.items()
    ]


# --- Aggregation ---


def evaluate_rules(
    typed_rules: Dict[str, ComplianceRule],
    asset_metadata: Dict[str, Any],
    metadata_schema_fields: Dict[str, Optional[List[Dict[str, Any]]]],
    asset_links: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[RuleResult], Dict[str, PipelineRule]]:
    """Run every metadata and relationship rule; return their results and the pipeline rules
    that still need a workflow execution.

    `metadata_schema_fields` is keyed by `metadata_schema_ref_key(rule.metadataSchemaRef)`.
    """
    metadata_rules, relationship_rules, pipeline_rules = split_rules(typed_rules)
    results: List[RuleResult] = []
    for rule_name, rule in metadata_rules.items():
        fields = metadata_schema_fields.get(metadata_schema_ref_key(rule.metadataSchemaRef))
        results.append(evaluate_metadata_rule(rule_name, rule, asset_metadata, fields))
    for rule_name, rule in relationship_rules.items():
        results.append(evaluate_relationship_rule(rule_name, rule, asset_links))
    return results, pipeline_rules


def metadata_schema_ref_key(ref) -> str:
    """Lookup key for a metadata schema reference: 'databaseId/schemaName'."""
    return f"{ref.databaseId}/{ref.schemaName}"


def verdict_to_state(verdict: EvaluationVerdict) -> str:
    """The asset-state value for an evaluation verdict."""
    return _VERDICT_TO_STATE.get(verdict, STATE_UNKNOWN)


# --- Quarantine exceptions ---


def schema_version_number(value: Any) -> Optional[int]:
    """A stored `internalVersion` (int, Decimal or numeric string) as an int; None when absent or
    not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def exception_applies(state_row: Optional[Dict[str, Any]], schema_name: str,
                      schema_version: Any) -> bool:
    """Whether the asset-state row carries an exception granted against exactly this schema name
    and `internalVersion`. An exception granted against another schema or an earlier version does
    not apply; the evaluation supersedes it."""
    if not state_row or not state_row.get(EXCEPTION_GRANTED_FIELD):
        return False
    if state_row.get(EXCEPTION_SCHEMA_NAME_FIELD) != schema_name:
        return False
    granted_version = schema_version_number(state_row.get(EXCEPTION_SCHEMA_VERSION_FIELD))
    return granted_version is not None and granted_version == schema_version_number(schema_version)


def exception_is_superseded(state_row: Optional[Dict[str, Any]], schema_name: str,
                            schema_version: Any) -> bool:
    """Whether the row carries an exception that this evaluation's schema name / version does not
    match, so the evaluation clears it and applies its verdict normally."""
    return bool((state_row or {}).get(EXCEPTION_GRANTED_FIELD)) and not exception_applies(
        state_row, schema_name, schema_version)


def cleared_exception_fields() -> Dict[str, Any]:
    """The asset-state update that removes an exception: `exceptionGranted` false and every other
    exception attribute null. Written when an exception is revoked or superseded."""
    return {field: (False if field == EXCEPTION_GRANTED_FIELD else None)
            for field in EXCEPTION_FIELDS}


def exception_state(verdict: EvaluationVerdict) -> str:
    """The asset state an evaluation produces while an exception is active: a compliant verdict is
    `compliant`, a verdict still awaiting pipeline rules is `pending_evaluation`, an evaluation that
    could not run is `unknown`, and any failing verdict is `exception` — never `quarantined` or
    `non_compliant`."""
    if verdict in (EvaluationVerdict.compliant, EvaluationVerdict.pending_pipeline,
                   EvaluationVerdict.error):
        return verdict_to_state(verdict)
    return STATE_EXCEPTION


def violations(rule_results: List[RuleResult]) -> List[str]:
    """Messages of the rules that did not pass."""
    return [r.message for r in rule_results if not r.passed and r.message]


def failed_rule_names(rule_results: List[RuleResult]) -> List[str]:
    """Names of the rules that did not pass."""
    return [r.ruleName for r in rule_results if not r.passed]


def rule_results_from_json(text: Any) -> List[RuleResult]:
    """Stored rule results (JSON string or list) as RuleResult models."""
    if isinstance(text, str):
        try:
            text = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(text, list):
        return []
    results = []
    for entry in text:
        if isinstance(entry, dict):
            try:
                results.append(RuleResult(**entry))
            except (ValueError, TypeError):
                continue
    return results


def pipeline_rules_from_json(text: Any) -> Dict[str, PipelineRule]:
    """Stored pending pipeline rules (JSON string or list of {ruleName, rule}) as models."""
    if isinstance(text, str):
        try:
            text = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(text, list):
        return {}
    rules: Dict[str, PipelineRule] = {}
    for entry in text:
        if not isinstance(entry, dict) or not entry.get("ruleName"):
            continue
        try:
            rules[entry["ruleName"]] = PipelineRule(**(entry.get("rule") or {}))
        except (ValueError, TypeError):
            logger.warning(f"Stored pipeline rule '{entry.get('ruleName')}' failed to parse")
    return rules


def pipeline_rules_to_json(pipeline_rules: Dict[str, PipelineRule]) -> str:
    """Pending pipeline rules serialized for the evaluation record."""
    return json.dumps(
        [{"ruleName": name, "rule": rule.dict()} for name, rule in pipeline_rules.items()])


def template_tags_from_input_parameters(input_parameters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A pipeline rule's `inputParameters` as execute-request template tag entries."""
    return [{"key": key, "value": value} for key, value in (input_parameters or {}).items()]
