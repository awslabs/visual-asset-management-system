# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""models.compliance: the fileStructure, versionCoverage and assetAttributes rule models -- the
criteria each check requires, the bounds on checks, patterns and tags, every pattern held to the
`REGEX` validator (compile check and backtracking budget), the one-criterion-per-field rule of an
asset-attribute check, and messages that name the field and never the submitted pattern or tag."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from models.common import validation_error_message
from models.compliance import (
    MAX_CHECKS_PER_RULE, MAX_PATH_PATTERNS, MAX_PATTERN_LENGTH, MAX_TAGS_PER_CHECK,
    RULE_TYPE_MODELS, AssetAttributeCheck, AssetAttributesRule, FileStructureCheck,
    FileStructureRule, RuleType, VamsRulesV1Schema, VersionCoverageCheck, VersionCoverageRule,
)

# Five unbounded quantifiers separated by literals: well past the backtracking budget.
CATASTROPHIC = ".*a.*b.*c.*d.*e"
# A quantifier over a group that itself repeats.
NESTED_REPEAT = "(a+)+$"
SECRET_PATTERN = "^secret<pattern>("


def _message(model, **fields):
    with pytest.raises(ValidationError) as raised:
        model(**fields)
    return validation_error_message(raised.value)


@pytest.mark.unit
class TestRuleTypeVocabulary:

    def test_six_rule_types_each_with_its_model(self):
        assert [t.value for t in RuleType] == [
            "pipeline", "metadata", "relationship", "fileStructure", "versionCoverage",
            "assetAttributes"]
        assert RULE_TYPE_MODELS["fileStructure"] is FileStructureRule
        assert RULE_TYPE_MODELS["versionCoverage"] is VersionCoverageRule
        assert RULE_TYPE_MODELS["assetAttributes"] is AssetAttributesRule

    def test_an_unknown_rule_type_lists_all_six(self):
        message = _message(VamsRulesV1Schema, schemaFormat="vams-rules-v1",
                           rules={"r": {"ruleType": "not-a-type"}})
        assert message == ("Every rule must declare a ruleType of pipeline, metadata, relationship, "
                           "fileStructure, versionCoverage, assetAttributes")

    def test_a_body_with_each_new_type_parses_to_its_model(self):
        body = {"schemaFormat": "vams-rules-v1", "rules": {
            "files": {"ruleType": "fileStructure", "enforcement": "warn",
                      "checks": [{"name": "glb", "requiredPaths": [r"\.glb$"]}]},
            "versions": {"ruleType": "versionCoverage", "enforcement": "warn",
                         "checks": [{"name": "all"}]},
            "naming": {"ruleType": "assetAttributes", "enforcement": "inform",
                       "checks": [{"name": "n", "field": "assetName", "matches": "^[A-Z]"}]},
        }}
        parsed = VamsRulesV1Schema(**body).parse_rules()
        assert {name: type(rule) for name, rule in parsed.items()} == {
            "files": FileStructureRule, "versions": VersionCoverageRule,
            "naming": AssetAttributesRule}

    @pytest.mark.parametrize("model,check", [
        (FileStructureRule, {"name": "c", "minFiles": 1}),
        (VersionCoverageRule, {"name": "c"}),
        (AssetAttributesRule, {"name": "c", "field": "tags", "minTags": 1}),
    ], ids=["fileStructure", "versionCoverage", "assetAttributes"])
    def test_checks_are_bounded_on_both_sides(self, model, check):
        rule_type = model.__fields__["ruleType"].default.value
        model(ruleType=rule_type, enforcement="warn", checks=[check] * MAX_CHECKS_PER_RULE)
        assert "checks: ensure this value has at most 25 items" in _message(
            model, ruleType=rule_type, enforcement="warn",
            checks=[check] * (MAX_CHECKS_PER_RULE + 1))
        assert "checks: ensure this value has at least 1 items" in _message(
            model, ruleType=rule_type, enforcement="warn", checks=[])


@pytest.mark.unit
class TestFileStructureCheck:

    def test_a_check_needs_at_least_one_criterion(self):
        assert _message(FileStructureCheck, name="c") == (
            "At least one of 'requiredPaths', 'forbiddenPaths', 'minFiles' or 'maxFiles' is required")

    @pytest.mark.parametrize("criterion", [
        {"requiredPaths": ["^models/"]}, {"forbiddenPaths": [r"\.tmp$"]}, {"minFiles": 0},
        {"maxFiles": 10},
    ], ids=["required", "forbidden", "min", "max"])
    def test_any_one_criterion_is_enough(self, criterion):
        FileStructureCheck(name="c", **criterion)

    def test_min_files_may_not_exceed_max_files(self):
        assert _message(FileStructureCheck, name="c", minFiles=3, maxFiles=2) == (
            "'minFiles' must be <= 'maxFiles'")
        FileStructureCheck(name="c", minFiles=2, maxFiles=2)

    def test_counts_are_non_negative(self):
        assert "minFiles" in _message(FileStructureCheck, name="c", minFiles=-1)

    @pytest.mark.parametrize("field", ["requiredPaths", "forbiddenPaths"])
    def test_pattern_lists_are_bounded(self, field):
        FileStructureCheck(name="c", **{field: ["a"] * MAX_PATH_PATTERNS})
        assert f"{field}: ensure this value has at most 25 items" == _message(
            FileStructureCheck, name="c", **{field: ["a"] * (MAX_PATH_PATTERNS + 1)})

    @pytest.mark.parametrize("field", ["requiredPaths", "forbiddenPaths"])
    def test_a_pattern_is_1_to_512_characters(self, field):
        FileStructureCheck(name="c", **{field: ["a" * MAX_PATTERN_LENGTH]})
        for pattern in ("", "a" * (MAX_PATTERN_LENGTH + 1)):
            assert _message(FileStructureCheck, name="c", **{field: [pattern]}) == (
                f"Every '{field}' pattern must be 1 to 512 characters")

    @pytest.mark.parametrize("pattern,reason", [
        (CATASTROPHIC, "Too many open-ended quantifiers"),
        (NESTED_REPEAT, "Cannot repeat a group that itself repeats"),
        (SECRET_PATTERN, "Must be a properly formatted regex expression"),
    ], ids=["backtracking-budget", "nested-repeat", "does-not-compile"])
    def test_every_pattern_passes_the_regex_validator(self, pattern, reason):
        message = _message(FileStructureCheck, name="c", forbiddenPaths=["ok", pattern])
        assert message.startswith("forbiddenPaths is invalid.")
        assert reason in message
        assert pattern not in message


@pytest.mark.unit
class TestVersionCoverageCheck:

    def test_a_check_without_exclusions_is_valid(self):
        assert VersionCoverageCheck(name="c").excludePaths == []

    def test_exclusions_are_bounded_and_validated(self):
        VersionCoverageCheck(name="c", excludePaths=[r"\.previewFile\."] * MAX_PATH_PATTERNS)
        assert "excludePaths: ensure this value has at most 25 items" == _message(
            VersionCoverageCheck, name="c", excludePaths=["a"] * (MAX_PATH_PATTERNS + 1))
        message = _message(VersionCoverageCheck, name="c", excludePaths=[CATASTROPHIC])
        assert message.startswith("excludePaths is invalid.")
        assert CATASTROPHIC not in message


@pytest.mark.unit
class TestAssetAttributeCheck:

    @pytest.mark.parametrize("field", ["assetName", "description"])
    def test_a_text_field_takes_exactly_one_of_matches_or_not_matches(self, field):
        AssetAttributeCheck(name="c", field=field, matches="^A")
        AssetAttributeCheck(name="c", field=field, notMatches="draft")
        expected = "Exactly one of 'matches' or 'notMatches' is required for this field"
        assert _message(AssetAttributeCheck, name="c", field=field) == expected
        assert _message(AssetAttributeCheck, name="c", field=field, matches="a",
                        notMatches="b") == expected

    @pytest.mark.parametrize("criterion", [
        {"requiredTags": ["t"]}, {"forbiddenTags": ["t"]}, {"minTags": 1},
    ], ids=["required", "forbidden", "min"])
    def test_tag_criteria_belong_to_the_tags_field_only(self, criterion):
        AssetAttributeCheck(name="c", field="tags", **criterion)
        assert _message(AssetAttributeCheck, name="c", field="assetName", matches="a",
                        **criterion) == (
            "'requiredTags', 'forbiddenTags' and 'minTags' apply to the tags field only")

    def test_the_tags_field_needs_a_tag_criterion_and_takes_no_pattern(self):
        assert _message(AssetAttributeCheck, name="c", field="tags") == (
            "At least one of 'requiredTags', 'forbiddenTags' or 'minTags' is required")
        assert _message(AssetAttributeCheck, name="c", field="tags", minTags=1,
                        matches="a") == "'matches' and 'notMatches' do not apply to the tags field"

    def test_the_field_is_one_of_three_and_is_not_echoed(self):
        message = _message(AssetAttributeCheck, name="c", field="secret<field>", matches="a")
        assert message == "field: unexpected value; permitted: 'assetName', 'description', 'tags'"

    @pytest.mark.parametrize("key", ["matches", "notMatches"])
    def test_a_pattern_passes_the_regex_validator(self, key):
        message = _message(AssetAttributeCheck, name="c", field="description",
                           **{key: CATASTROPHIC})
        assert message.startswith(f"{key} is invalid.")
        assert CATASTROPHIC not in message
        assert _message(AssetAttributeCheck, name="c", field="description",
                        **{key: "a" * (MAX_PATTERN_LENGTH + 1)}) == (
            f"Every '{key}' pattern must be 1 to 512 characters")

    @pytest.mark.parametrize("key", ["requiredTags", "forbiddenTags"])
    def test_tag_lists_are_bounded_trimmed_and_validated(self, key):
        check = AssetAttributeCheck(name="c", field="tags", **{key: [" engineering "]})
        assert getattr(check, key) == ["engineering"]
        AssetAttributeCheck(name="c", field="tags", **{key: ["t"] * MAX_TAGS_PER_CHECK})
        assert f"{key}: ensure this value has at most 50 items" == _message(
            AssetAttributeCheck, name="c", field="tags", **{key: ["t"] * (MAX_TAGS_PER_CHECK + 1)})
        assert _message(AssetAttributeCheck, name="c", field="tags", **{key: ["  "]}) == (
            f"Every '{key}' entry must be a non-empty string")
        message = _message(AssetAttributeCheck, name="c", field="tags", **{key: ["x" * 257]})
        assert message.startswith(key) and "x" * 257 not in message

    def test_min_tags_is_non_negative(self):
        assert "minTags" in _message(AssetAttributeCheck, name="c", field="tags", minTags=-1)
