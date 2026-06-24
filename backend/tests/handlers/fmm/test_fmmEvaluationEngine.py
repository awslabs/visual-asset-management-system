# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch

from backend.backend.models.fmm import (
    EnforcementLevel,
    EvaluationVerdict,
    MetadataCheck,
    MetadataRule,
    MetadataSchemaRef,
    PipelineCheck,
    PipelineRule,
    PipelineRef,
    RelationshipCheck,
    RelationshipRule,
    RuleResult,
    Tolerance,
    ToleranceOperator,
    VamsRulesV1Schema,
    determine_verdict,
    resolve_schema_inheritance,
)


class TestVamsRulesV1Schema:
    """Tests for the vams-rules-v1 schema format models."""

    def test_valid_metadata_rule(self):
        schema = VamsRulesV1Schema(
            schemaFormat="vams-rules-v1",
            rules={
                "check-metadata": {
                    "ruleType": "metadata",
                    "enforcement": "quarantine",
                    "metadataSchemaRef": {
                        "databaseId": "my-db",
                        "schemaName": "scan-schema",
                    },
                    "checks": [
                        {
                            "name": "required_fields",
                            "validateRequired": True,
                            "validateTypes": True,
                        }
                    ],
                }
            },
        )
        parsed = schema.parse_rules()
        assert "check-metadata" in parsed
        assert isinstance(parsed["check-metadata"], MetadataRule)
        assert parsed["check-metadata"].enforcement == EnforcementLevel.quarantine

    def test_valid_relationship_rule(self):
        schema = VamsRulesV1Schema(
            schemaFormat="vams-rules-v1",
            rules={
                "has-parent": {
                    "ruleType": "relationship",
                    "enforcement": "warn",
                    "checks": [
                        {
                            "name": "parent_link",
                            "direction": "parents",
                            "relationshipType": "parentChild",
                            "minCount": 1,
                        }
                    ],
                }
            },
        )
        parsed = schema.parse_rules()
        assert "has-parent" in parsed
        assert isinstance(parsed["has-parent"], RelationshipRule)

    def test_valid_pipeline_rule(self):
        schema = VamsRulesV1Schema(
            schemaFormat="vams-rules-v1",
            rules={
                "coord-check": {
                    "ruleType": "pipeline",
                    "enforcement": "quarantine",
                    "pipelineRef": {
                        "databaseId": "GLOBAL",
                        "workflowId": "coord-validate",
                    },
                    "checks": [
                        {
                            "name": "residual_check",
                            "outputField": "residual_error_mm",
                            "tolerance": {"operator": "lte", "value": 1.0},
                        }
                    ],
                }
            },
        )
        parsed = schema.parse_rules()
        assert "coord-check" in parsed
        rule = parsed["coord-check"]
        assert isinstance(rule, PipelineRule)
        assert rule.checks[0].tolerance.operator == ToleranceOperator.lte

    def test_empty_rules_rejected(self):
        with pytest.raises(ValueError, match="At least one rule"):
            VamsRulesV1Schema(schemaFormat="vams-rules-v1", rules={})

    def test_invalid_rule_type_rejected(self):
        with pytest.raises(ValueError, match="invalid ruleType"):
            VamsRulesV1Schema(
                schemaFormat="vams-rules-v1",
                rules={"bad": {"ruleType": "unknown"}},
            )

    def test_tolerance_lte_requires_value(self):
        with pytest.raises(ValueError, match="'value' is required"):
            Tolerance(operator=ToleranceOperator.lte)

    def test_tolerance_between_requires_min_max(self):
        with pytest.raises(ValueError, match="'min' and 'max' are required"):
            Tolerance(operator=ToleranceOperator.between, min=1.0)

    def test_tolerance_between_min_gt_max_rejected(self):
        with pytest.raises(ValueError, match="'min' must be <= 'max'"):
            Tolerance(operator=ToleranceOperator.between, min=5.0, max=2.0)

    def test_relationship_check_requires_count(self):
        with pytest.raises(ValueError, match="At least one of"):
            RelationshipCheck(
                name="test",
                direction="parents",
                relationshipType="parentChild",
            )

    def test_relationship_check_min_gt_max_rejected(self):
        with pytest.raises(ValueError, match="'minCount' must be <= 'maxCount'"):
            RelationshipCheck(
                name="test",
                direction="parents",
                relationshipType="parentChild",
                minCount=5,
                maxCount=2,
            )


class TestSchemaInheritance:
    """Tests for schema inheritance resolution."""

    def test_child_overrides_parent(self):
        parent = {
            "rules": {
                "rule-a": {"ruleType": "metadata", "enforcement": "warn"},
                "rule-b": {"ruleType": "relationship", "enforcement": "inform"},
            }
        }
        child = {
            "rules": {
                "rule-a": {"ruleType": "metadata", "enforcement": "quarantine"},
                "rule-c": {"ruleType": "pipeline", "enforcement": "quarantine"},
            }
        }
        merged = resolve_schema_inheritance(child, parent)
        assert merged["rule-a"]["enforcement"] == "quarantine"
        assert merged["rule-b"]["enforcement"] == "inform"
        assert "rule-c" in merged

    def test_no_parent_returns_child_rules(self):
        child = {"rules": {"rule-x": {"ruleType": "metadata"}}}
        merged = resolve_schema_inheritance(child, None)
        assert merged == {"rule-x": {"ruleType": "metadata"}}


class TestDetermineVerdict:
    """Tests for verdict determination logic."""

    def test_all_pass_is_compliant(self):
        results = [
            RuleResult(ruleName="a", ruleType="metadata", enforcement="quarantine", passed=True),
            RuleResult(ruleName="b", ruleType="relationship", enforcement="warn", passed=True),
        ]
        assert determine_verdict(results) == EvaluationVerdict.compliant

    def test_quarantine_failure_overrides_warn(self):
        results = [
            RuleResult(ruleName="a", ruleType="metadata", enforcement="quarantine", passed=False),
            RuleResult(ruleName="b", ruleType="relationship", enforcement="warn", passed=False),
        ]
        assert determine_verdict(results) == EvaluationVerdict.quarantined

    def test_warn_failure_is_non_compliant(self):
        results = [
            RuleResult(ruleName="a", ruleType="metadata", enforcement="warn", passed=False),
            RuleResult(ruleName="b", ruleType="relationship", enforcement="inform", passed=False),
        ]
        assert determine_verdict(results) == EvaluationVerdict.non_compliant

    def test_inform_only_is_compliant(self):
        results = [
            RuleResult(ruleName="a", ruleType="metadata", enforcement="inform", passed=False),
        ]
        assert determine_verdict(results) == EvaluationVerdict.compliant

    def test_empty_results_is_compliant(self):
        assert determine_verdict([]) == EvaluationVerdict.compliant


class TestSchemaValidation:
    """Tests for validate_schema_body with vams-rules-v1 format."""

    def test_vams_rules_v1_valid(self):
        from backend.backend.handlers.fmm.fmmSchemaService import validate_schema_body

        body = {
            "schemaFormat": "vams-rules-v1",
            "rules": {
                "metadata-check": {
                    "ruleType": "metadata",
                    "enforcement": "quarantine",
                    "metadataSchemaRef": {
                        "databaseId": "db1",
                        "schemaName": "schema1",
                    },
                    "checks": [{"name": "req", "validateRequired": True}],
                }
            },
        }
        valid, err = validate_schema_body(body)
        assert valid is True
        assert err is None

    def test_vams_rules_v1_invalid_rule_type(self):
        from backend.backend.handlers.fmm.fmmSchemaService import validate_schema_body

        body = {
            "schemaFormat": "vams-rules-v1",
            "rules": {
                "bad-rule": {"ruleType": "invalid", "enforcement": "warn"},
            },
        }
        valid, err = validate_schema_body(body)
        assert valid is False
        assert "invalid ruleType" in err

    def test_legacy_json_schema_still_works(self):
        from backend.backend.handlers.fmm.fmmSchemaService import validate_schema_body

        body = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer", "minimum": 0},
            },
            "required": ["name"],
        }
        valid, err = validate_schema_body(body)
        assert valid is True
        assert err is None
