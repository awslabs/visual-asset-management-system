# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.compliance.evaluationEngine: the pure rule logic -- metadata checks (required fields,
types), relationship counts, pipeline tolerance comparisons, verdict-to-state mapping, and rule
resolution through a schema's `extends` chain."""

import json

import pytest

from common.compliance import evaluationEngine as engine
from models.compliance import (
    EvaluationVerdict, MetadataRule, PipelineRule, RelationshipRule, RuleResult, Tolerance,
    determine_verdict,
)

SCHEMA_REF = {"databaseId": "db1", "schemaName": "Asset Schema"}
SCHEMA_KEY = "db1/Asset Schema"
PIPELINE_REF = {"databaseId": "GLOBAL", "workflowId": "wf-1",
                "pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe-1"}


def _metadata_rule(enforcement="warn", **check):
    check.setdefault("name", "check")
    return MetadataRule(**{"ruleType": "metadata", "enforcement": enforcement,
                           "metadataSchemaRef": SCHEMA_REF, "checks": [check]})


def _relationship_rule(enforcement="quarantine", **check):
    check.setdefault("name", "check")
    check.setdefault("direction", "parents")
    check.setdefault("relationshipType", "parentChild")
    return RelationshipRule(**{"ruleType": "relationship", "enforcement": enforcement,
                               "checks": [check]})


def _pipeline_rule(enforcement="quarantine", checks=None, **extra):
    checks = checks or [{"name": "residual", "outputField": "residual",
                         "tolerance": {"operator": "lte", "value": 1}}]
    return PipelineRule(**{"ruleType": "pipeline", "enforcement": enforcement,
                           "pipelineRef": PIPELINE_REF, "checks": checks, **extra})


def _fields(*defs):
    return engine.normalize_metadata_schema_fields(json.dumps([
        {"metadataFieldKeyName": name, "metadataFieldValueType": type_, "required": required}
        for name, type_, required in defs]))


@pytest.mark.unit
class TestMetadataRules:

    def test_required_schema_fields_present_passes(self):
        rule = _metadata_rule(validateRequired=True)
        result = engine.evaluate_metadata_rule(
            "r", rule, {"units": "m"}, _fields(("units", "string", True)))
        assert result.passed is True
        assert result.ruleType == "metadata"
        assert result.enforcement == "warn"
        assert result.expected == {"schemaRef": SCHEMA_KEY}
        assert result.measured == {"metadataKeys": ["units"]}

    def test_a_missing_required_schema_field_fails_and_names_it(self):
        rule = _metadata_rule(validateRequired=True)
        result = engine.evaluate_metadata_rule("r", rule, {}, _fields(("units", "string", True)))
        assert result.passed is False
        assert "units" in result.message
        assert "missing required schema fields" in result.message

    def test_required_check_is_skipped_when_the_schema_is_unknown(self):
        rule = _metadata_rule(validateRequired=True)
        assert engine.evaluate_metadata_rule("r", rule, {}, None).passed is True

    def test_additional_required_fields_are_checked_without_a_schema(self):
        rule = _metadata_rule(additionalRequiredFields=["owner"])
        assert engine.evaluate_metadata_rule("r", rule, {"owner": "x"}, None).passed is True
        failed = engine.evaluate_metadata_rule("r", rule, {}, None)
        assert failed.passed is False
        assert "owner" in failed.message

    @pytest.mark.parametrize("value,type_,passes", [
        (3.0, "number", True), ("3", "number", False),
        (True, "boolean", True), ("true", "boolean", False),
        ("m", "string", True), (1, "string", False),
        ("anything", "geo", True),
    ])
    def test_type_validation(self, value, type_, passes):
        rule = _metadata_rule(validateTypes=True)
        result = engine.evaluate_metadata_rule(
            "r", rule, {"f": value}, _fields(("f", type_, False)))
        assert result.passed is passes
        if not passes:
            assert "type mismatches" in result.message

    def test_type_validation_ignores_fields_the_asset_does_not_carry(self):
        rule = _metadata_rule(validateTypes=True)
        assert engine.evaluate_metadata_rule("r", rule, {}, _fields(("f", "number", False))).passed

    def test_failures_from_several_checks_are_joined(self):
        rule = MetadataRule(**{"ruleType": "metadata", "enforcement": "warn",
                               "metadataSchemaRef": SCHEMA_REF,
                               "checks": [{"name": "a", "additionalRequiredFields": ["x"]},
                                          {"name": "b", "additionalRequiredFields": ["y"]}]})
        result = engine.evaluate_metadata_rule("r", rule, {}, None)
        assert result.passed is False
        assert result.message.startswith("a:") and "; b:" in result.message

    @pytest.mark.parametrize("raw,type_,expected", [
        ("3.5", "NUMBER", 3.5), ("nan-ish", "NUMBER", "nan-ish"),
        ("true", "BOOLEAN", True), ("0", "BOOLEAN", False),
        ("x", "STRING", "x"), (None, "STRING", None),
    ])
    def test_stored_metadata_values_are_coerced_by_their_type(self, raw, type_, expected):
        assert engine.coerce_metadata_value(raw, type_) == expected

    @pytest.mark.parametrize("raw", ["not json", json.dumps({"other": []}), 42])
    def test_a_schema_fields_attribute_that_is_not_a_field_list_is_none(self, raw):
        assert engine.normalize_metadata_schema_fields(raw) is None

    def test_non_object_entries_in_a_field_list_are_dropped(self):
        assert engine.normalize_metadata_schema_fields("[1, 2]") == []

    def test_a_wrapped_fields_object_is_unwrapped(self):
        fields = engine.normalize_metadata_schema_fields(
            {"fields": [{"field": "f", "type": "number", "required": True}]})
        assert fields == [{"field": "f", "required": True, "type": "number"}]


@pytest.mark.unit
class TestRelationshipRules:
    LINKS = {
        "parents": [{"relationshipType": "parentChild"}, {"relationshipType": "related"}],
        "children": [{"relationshipType": "parentChild"}, {"relationshipType": "parentChild"}],
    }

    @pytest.mark.parametrize("direction,relationship,count", [
        ("parents", "parentChild", 1), ("children", "parentChild", 2),
        ("related", "parentChild", 3), ("related", "related", 1), ("parents", "related", 1),
    ])
    def test_links_are_counted_by_direction_and_type(self, direction, relationship, count):
        assert engine.count_links(self.LINKS, direction, relationship) == count

    def test_a_count_below_min_fails(self):
        rule = _relationship_rule(minCount=2)
        result = engine.evaluate_relationship_rule("r", rule, self.LINKS)
        assert result.passed is False
        assert "found 1 links, minimum is 2" in result.message
        assert result.measured == {"check": 1}

    def test_a_count_above_max_fails(self):
        rule = _relationship_rule(direction="children", maxCount=1)
        result = engine.evaluate_relationship_rule("r", rule, self.LINKS)
        assert result.passed is False
        assert "maximum is 1" in result.message

    def test_a_count_inside_the_bounds_passes(self):
        rule = _relationship_rule(direction="children", minCount=1, maxCount=2)
        result = engine.evaluate_relationship_rule("r", rule, self.LINKS)
        assert result.passed is True
        assert result.enforcement == "quarantine"

    def test_no_links_at_all_fails_a_min_count(self):
        rule = _relationship_rule(minCount=1)
        assert engine.evaluate_relationship_rule("r", rule, {}).passed is False


@pytest.mark.unit
class TestToleranceCompare:

    @pytest.mark.parametrize("tolerance,value,passes", [
        ({"operator": "lte", "value": 1}, 1, True),
        ({"operator": "lte", "value": 1}, 1.01, False),
        ({"operator": "gte", "value": 1}, 1, True),
        ({"operator": "gte", "value": 1}, 0.99, False),
        ({"operator": "eq", "value": 1}, 1.0005, True),
        ({"operator": "eq", "value": 1}, 1.01, False),
        ({"operator": "eq", "value": 1, "epsilon": 0.1}, 1.05, True),
        ({"operator": "between", "min": 0, "max": 2}, 0, True),
        ({"operator": "between", "min": 0, "max": 2}, 2, True),
        ({"operator": "between", "min": 0, "max": 2}, 2.1, False),
        ({"operator": "lte", "value": 1}, "0.5", True),
        ({"operator": "lte", "value": 1}, "n/a", False),
        ({"operator": "lte", "value": 1}, None, False),
    ])
    def test_compare(self, tolerance, value, passes):
        passed, message = engine.compare_tolerance("c", value, Tolerance(**tolerance))
        assert passed is passes
        assert (message == "") is passes

    def test_a_non_numeric_value_says_so(self):
        _, message = engine.compare_tolerance("c", "abc", Tolerance(operator="lte", value=1))
        assert message == "c: value is not numeric"

    @pytest.mark.parametrize("tolerance", [
        {"operator": "lte"}, {"operator": "between", "min": 1},
        {"operator": "between", "min": 2, "max": 1}, {"operator": "approx", "value": 1},
    ])
    def test_a_malformed_tolerance_is_rejected_by_the_model(self, tolerance):
        with pytest.raises(ValueError):
            Tolerance(**tolerance)


@pytest.mark.unit
class TestPipelineRules:
    OUTPUT = {"complianceOutput": True, "status": "success", "measurements": {"residual": 0.5}}

    def test_a_succeeded_execution_with_output_runs_the_checks(self):
        results = engine.evaluate_pipeline_rules({"p": _pipeline_rule()}, "SUCCEEDED", self.OUTPUT)
        assert [r.passed for r in results] == [True]
        assert results[0].measured == {"residual": 0.5}
        assert results[0].expected["residual"]["operator"] == "lte"

    def test_a_measurement_outside_tolerance_fails(self):
        output = dict(self.OUTPUT, measurements={"residual": 2})
        results = engine.evaluate_pipeline_rules({"p": _pipeline_rule()}, "SUCCEEDED", output)
        assert results[0].passed is False
        assert "2.0 > 1.0 (max)" in results[0].message

    def test_a_missing_output_field_fails(self):
        output = dict(self.OUTPUT, measurements={})
        results = engine.evaluate_pipeline_rules({"p": _pipeline_rule()}, "SUCCEEDED", output)
        assert results[0].passed is False
        assert "output field 'residual' missing" in results[0].message

    @pytest.mark.parametrize("status", ["FAILED", "ABORTED", "TIMED_OUT"])
    def test_a_non_succeeded_execution_fails_every_rule(self, status):
        rules = {"p": _pipeline_rule(), "q": _pipeline_rule(enforcement="warn")}
        results = engine.evaluate_pipeline_rules(rules, status, self.OUTPUT)
        assert [r.passed for r in results] == [False, False]
        assert all(r.message == f"Pipeline execution {status}" for r in results)
        assert [r.enforcement for r in results] == ["quarantine", "warn"]

    def test_an_output_reporting_an_error_fails_every_rule(self):
        output = dict(self.OUTPUT, status="error")
        results = engine.evaluate_pipeline_rules({"p": _pipeline_rule()}, "SUCCEEDED", output)
        assert results[0].passed is False
        assert results[0].message == "Pipeline reported an error"

    def test_no_output_falls_back_to_the_default_execution_measurements(self):
        rule = _pipeline_rule(checks=[
            {"name": "ok", "outputField": "execution_success",
             "tolerance": {"operator": "eq", "value": 1}},
            {"name": "fast", "outputField": "processing_duration_seconds",
             "tolerance": {"operator": "lte", "value": 60}}])
        results = engine.evaluate_pipeline_rules(
            {"p": rule}, "SUCCEEDED", None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:30+00:00")
        assert results[0].passed is True
        assert results[0].measured == {"execution_success": 1.0, "processing_duration_seconds": 30.0}

    def test_default_measurements_omit_the_duration_when_a_timestamp_is_unparseable(self):
        measured = engine.default_pipeline_measurements("FAILED", "not-a-date", "2026-01-01T00:00:00Z")
        assert measured == {"execution_success": 0.0}

    def test_a_non_dict_measurements_attribute_is_treated_as_empty(self):
        output = dict(self.OUTPUT, measurements=[1, 2])
        results = engine.evaluate_pipeline_rules({"p": _pipeline_rule()}, "SUCCEEDED", output)
        assert results[0].passed is False

    @pytest.mark.parametrize("text", [
        "not json", json.dumps({"measurements": {}}), json.dumps({"complianceOutput": "true"}),
        json.dumps([1]), None,
    ])
    def test_a_document_not_flagged_as_compliance_output_is_none(self, text):
        assert engine.parse_compliance_output(text) is None

    def test_a_flagged_document_is_returned_from_text_or_dict(self):
        assert engine.parse_compliance_output(json.dumps(self.OUTPUT)) == self.OUTPUT
        assert engine.parse_compliance_output(self.OUTPUT) == self.OUTPUT

    def test_input_parameters_become_template_tags(self):
        assert engine.template_tags_from_input_parameters({"crs": "EPSG:27700", "n": 2}) == [
            {"key": "crs", "value": "EPSG:27700"}, {"key": "n", "value": 2}]
        assert engine.template_tags_from_input_parameters(None) == []

    def test_pending_pipeline_rules_round_trip_through_json(self):
        rules = {"p": _pipeline_rule(inputParameters={"crs": "EPSG:27700"})}
        restored = engine.pipeline_rules_from_json(engine.pipeline_rules_to_json(rules))
        assert set(restored) == {"p"}
        assert restored["p"].pipelineRef.pipelineId == "pipe-1"
        assert restored["p"].inputParameters == {"crs": "EPSG:27700"}

    @pytest.mark.parametrize("text", ["nope", json.dumps({"a": 1}),
                                      json.dumps([{"rule": {}}, {"ruleName": "x", "rule": {}}])])
    def test_unparseable_pending_rules_are_skipped(self, text):
        assert engine.pipeline_rules_from_json(text) == {}


@pytest.mark.unit
class TestVerdictAndState:

    def _result(self, enforcement, passed):
        return RuleResult(ruleName="r", ruleType="metadata", enforcement=enforcement, passed=passed)

    def test_all_passed_is_compliant(self):
        assert determine_verdict([self._result("quarantine", True)]) == EvaluationVerdict.compliant
        assert determine_verdict([]) == EvaluationVerdict.compliant

    def test_a_failed_warn_rule_is_non_compliant(self):
        results = [self._result("warn", False), self._result("inform", False)]
        assert determine_verdict(results) == EvaluationVerdict.non_compliant

    def test_a_failed_inform_rule_alone_stays_compliant(self):
        assert determine_verdict([self._result("inform", False)]) == EvaluationVerdict.compliant

    def test_a_failed_quarantine_rule_wins_over_warn(self):
        results = [self._result("warn", False), self._result("quarantine", False)]
        assert determine_verdict(results) == EvaluationVerdict.quarantined

    @pytest.mark.parametrize("verdict,state", [
        (EvaluationVerdict.compliant, "compliant"),
        (EvaluationVerdict.non_compliant, "non_compliant"),
        (EvaluationVerdict.quarantined, "quarantined"),
        (EvaluationVerdict.pending_pipeline, "pending_evaluation"),
        (EvaluationVerdict.error, "unknown"),
    ])
    def test_verdict_to_state(self, verdict, state):
        assert engine.verdict_to_state(verdict) == state

    def test_violations_and_failed_names_come_from_the_failed_results(self):
        results = [
            RuleResult(ruleName="a", ruleType="metadata", enforcement="warn", passed=False,
                       message="a broke"),
            RuleResult(ruleName="b", ruleType="metadata", enforcement="warn", passed=True),
            RuleResult(ruleName="c", ruleType="metadata", enforcement="warn", passed=False),
        ]
        assert engine.violations(results) == ["a broke"]
        assert engine.failed_rule_names(results) == ["a", "c"]

    def test_stored_rule_results_round_trip_and_skip_garbage(self):
        stored = json.dumps([{"ruleName": "a", "ruleType": "metadata", "enforcement": "warn",
                              "passed": True}, {"ruleName": "b"}, 7])
        restored = engine.rule_results_from_json(stored)
        assert [r.ruleName for r in restored] == ["a"]
        assert engine.rule_results_from_json("nope") == []


@pytest.mark.unit
class TestEvaluateRules:

    def test_metadata_and_relationship_rules_run_and_pipeline_rules_are_returned(self):
        typed = {
            "m": _metadata_rule(validateRequired=True, additionalRequiredFields=["owner"]),
            "r": _relationship_rule(minCount=1),
            "p": _pipeline_rule(),
        }
        results, pending = engine.evaluate_rules(
            typed,
            {"units": "m", "owner": "x"},
            {SCHEMA_KEY: _fields(("units", "string", True))},
            {"parents": [{"relationshipType": "parentChild"}], "children": []},
        )
        assert [(r.ruleName, r.passed) for r in results] == [("m", True), ("r", True)]
        assert list(pending) == ["p"]

    def test_a_metadata_rule_whose_schema_was_not_fetched_skips_schema_checks(self):
        typed = {"m": _metadata_rule(validateRequired=True, validateTypes=True)}
        results, _ = engine.evaluate_rules(typed, {}, {}, {})
        assert results[0].passed is True


@pytest.mark.unit
class TestSchemaResolution:
    PARENT = {"schemaFormat": "vams-rules-v1", "rules": {
        "base": {"ruleType": "relationship", "enforcement": "warn",
                 "checks": [{"name": "c", "direction": "parents", "relationshipType": "parentChild",
                             "minCount": 1}]},
        "shared": {"ruleType": "relationship", "enforcement": "warn",
                   "checks": [{"name": "parent-version", "direction": "parents",
                               "relationshipType": "parentChild", "minCount": 1}]},
    }}
    CHILD = {"schemaFormat": "vams-rules-v1", "extends": "parent", "rules": {
        "shared": {"ruleType": "relationship", "enforcement": "quarantine",
                   "checks": [{"name": "child-version", "direction": "parents",
                               "relationshipType": "parentChild", "minCount": 2}]},
        "own": {"ruleType": "relationship", "enforcement": "inform",
                "checks": [{"name": "c", "direction": "children", "relationshipType": "parentChild",
                            "maxCount": 5}]},
    }}

    def test_parent_rules_are_inherited_and_child_rules_override_by_name(self):
        rules = engine.resolve_rules(self.CHILD, lambda name: {"parent": self.PARENT}.get(name))
        assert set(rules) == {"base", "shared", "own"}
        assert rules["shared"]["enforcement"] == "quarantine"
        assert rules["shared"]["checks"][0]["name"] == "child-version"

    def test_a_chain_walks_more_than_one_level(self):
        grandparent = {"schemaFormat": "vams-rules-v1", "rules": {
            "root": self.PARENT["rules"]["base"]}}
        parent = dict(self.PARENT, extends="grandparent")
        loader = {"parent": parent, "grandparent": grandparent}.get
        assert set(engine.resolve_rules(self.CHILD, loader)) == {"root", "base", "shared", "own"}

    def test_a_missing_parent_falls_back_to_the_child_rules(self):
        rules = engine.resolve_rules(self.CHILD, lambda name: None)
        assert set(rules) == {"shared", "own"}

    def test_a_parent_that_is_not_vams_rules_is_ignored(self):
        rules = engine.resolve_rules(self.CHILD, lambda name: {"type": "object"})
        assert set(rules) == {"shared", "own"}

    def test_a_self_referencing_chain_stops_at_the_depth_bound(self):
        loop = {"schemaFormat": "vams-rules-v1", "extends": "loop", "rules": {"own": {"x": 1}}}
        calls = []

        def loader(name):
            calls.append(name)
            return loop

        assert engine.resolve_rules(loop, loader) == {"own": {"x": 1}}
        assert len(calls) == engine.MAX_SCHEMA_INHERITANCE_DEPTH

    def test_a_schema_without_extends_returns_its_own_rules(self):
        assert engine.resolve_rules(self.PARENT, lambda name: None) == self.PARENT["rules"]

    def test_parsed_rules_skip_unknown_types_and_malformed_definitions(self):
        rules = dict(self.PARENT["rules"], bad={"ruleType": "nope"}, worse="text",
                     broken={"ruleType": "relationship", "enforcement": "warn", "checks": []})
        parsed = engine.parse_resolved_rules(rules)
        assert set(parsed) == {"base", "shared"}
        assert isinstance(parsed["base"], RelationshipRule)

    @pytest.mark.parametrize("raw,expected", [
        (json.dumps({"a": 1}), {"a": 1}), ({"a": 1}, {"a": 1}),
        ("not json", None), (json.dumps([1]), None), (7, None),
    ])
    def test_a_stored_schema_body_is_parsed_or_none(self, raw, expected):
        assert engine.parse_schema_body(raw) == expected

    def test_is_vams_rules_schema(self):
        assert engine.is_vams_rules_schema(self.PARENT) is True
        assert engine.is_vams_rules_schema({"type": "object"}) is False
        assert engine.is_vams_rules_schema(None) is False
