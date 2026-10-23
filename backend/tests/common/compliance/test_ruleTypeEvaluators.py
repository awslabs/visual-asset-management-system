# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The evaluation engine's file-structure, version-coverage and asset-attribute evaluators: pass and
fail per criterion, the messages and the `measured` / `expected` shapes, path patterns searched in
the key without its leading '/', a pattern that no longer compiles and a missing input recorded as a
`status: error` result, and `evaluate_rules` handing each rule type the input it reads."""

import re
from unittest.mock import patch

import pytest

from common.compliance import evaluationEngine as engine
from models.compliance import (
    AssetAttributesRule, FileStructureRule, RuleResult, VersionCoverageRule,
)

FILES = [
    engine.CurrentFile("/models/part.glb", "v-part"),
    engine.CurrentFile("/models/part.stl", "v-stl"),
    engine.CurrentFile("/readme.txt", "v-readme"),
]


def _file_structure(enforcement="quarantine", **check):
    check.setdefault("name", "layout")
    return FileStructureRule(ruleType="fileStructure", enforcement=enforcement, checks=[check])


def _version_coverage(enforcement="warn", **check):
    check.setdefault("name", "versioned")
    return VersionCoverageRule(ruleType="versionCoverage", enforcement=enforcement, checks=[check])


def _asset_attributes(*checks, enforcement="inform"):
    return AssetAttributesRule(ruleType="assetAttributes", enforcement=enforcement,
                               checks=[dict({"name": f"c{i}"}, **c) for i, c in enumerate(checks)])


@pytest.mark.unit
class TestFileStructure:

    def test_every_required_pattern_matching_some_file_passes(self):
        result = engine.evaluate_file_structure_rule(
            "files", _file_structure(requiredPaths=["^models/", r"\.glb$"]), FILES)
        assert result.passed is True
        assert result.status == "evaluated"
        assert result.ruleType == "fileStructure"
        assert result.enforcement == "quarantine"
        assert result.message is None
        assert result.measured == {"fileCount": 3, "unmatchedRequired": [], "forbiddenMatches": []}
        assert result.expected == {"layout": {"requiredPaths": ["^models/", r"\.glb$"]}}

    def test_patterns_are_searched_without_the_leading_slash(self):
        anchored = engine.evaluate_file_structure_rule(
            "files", _file_structure(requiredPaths=["^readme"]), FILES)
        slashed = engine.evaluate_file_structure_rule(
            "files", _file_structure(requiredPaths=["^/readme"]), FILES)
        assert anchored.passed is True
        assert slashed.passed is False

    def test_an_unmatched_required_pattern_fails_and_is_named(self):
        result = engine.evaluate_file_structure_rule(
            "files", _file_structure(requiredPaths=[r"\.glb$", r"\.usdz$", "^textures/"]), FILES)
        assert result.passed is False
        assert result.message == (r"layout: no file matches required path pattern(s) \.usdz$, "
                                  "^textures/")
        assert result.measured["unmatchedRequired"] == [r"\.usdz$", "^textures/"]

    def test_a_forbidden_match_fails_and_names_the_files(self):
        result = engine.evaluate_file_structure_rule(
            "files", _file_structure(forbiddenPaths=[r"\.stl$", r"\.tmp$"]), FILES)
        assert result.passed is False
        assert result.message == "layout: 1 file(s) match a forbidden path pattern: /models/part.stl"
        assert result.measured["forbiddenMatches"] == ["/models/part.stl"]

    def test_more_than_ten_forbidden_matches_are_counted(self):
        files = [engine.CurrentFile(f"/tmp/{i:02d}.tmp", f"v{i}") for i in range(13)]
        result = engine.evaluate_file_structure_rule(
            "files", _file_structure(forbiddenPaths=[r"\.tmp$"]), files)
        assert result.message.startswith("layout: 13 file(s) match a forbidden path pattern: ")
        assert result.message.endswith("/tmp/09.tmp and 3 more")
        assert len(result.measured["forbiddenMatches"]) == 10

    @pytest.mark.parametrize("bounds,passed,message", [
        ({"minFiles": 3, "maxFiles": 3}, True, None),
        ({"minFiles": 4}, False, "layout: found 3 files, minimum is 4"),
        ({"maxFiles": 2}, False, "layout: found 3 files, maximum is 2"),
    ], ids=["within", "below-min", "above-max"])
    def test_the_file_count_bounds(self, bounds, passed, message):
        result = engine.evaluate_file_structure_rule("files", _file_structure(**bounds), FILES)
        assert result.passed is passed
        assert result.message == message

    def test_an_asset_without_files_fails_a_required_pattern(self):
        result = engine.evaluate_file_structure_rule(
            "files", _file_structure(requiredPaths=["."]), [])
        assert result.passed is False
        assert result.measured["fileCount"] == 0

    def test_a_listing_that_could_not_be_made_is_an_error_result(self):
        result = engine.evaluate_file_structure_rule(
            "files", _file_structure(minFiles=1), None)
        assert result.status == "error"
        assert result.passed is False
        assert result.message == engine.FILES_UNAVAILABLE_MESSAGE

    def test_a_pattern_that_no_longer_compiles_is_an_error_result(self):
        rule = _file_structure(requiredPaths=["^models/"])
        with patch.object(engine.re, "compile", side_effect=re.error("bad")):
            result = engine.evaluate_file_structure_rule("files", rule, FILES)
        assert result.status == "error"
        assert result.message == "Invalid pattern in rule"


@pytest.mark.unit
class TestVersionCoverage:

    VERSIONED = {("/models/part.glb", "v-part"), ("/models/part.stl", "v-stl"),
                 ("/readme.txt", "v-readme")}

    def test_every_current_version_recorded_passes(self):
        result = engine.evaluate_version_coverage_rule(
            "versions", _version_coverage(), FILES, self.VERSIONED)
        assert result.passed is True
        assert result.ruleType == "versionCoverage"
        assert result.measured == {"fileCount": 3, "uncoveredCount": 0, "uncovered": []}

    def test_a_file_whose_current_version_is_not_recorded_fails(self):
        versioned = {("/models/part.glb", "v-part"), ("/models/part.stl", "v-older"),
                     ("/readme.txt", "v-readme")}
        result = engine.evaluate_version_coverage_rule(
            "versions", _version_coverage(), FILES, versioned)
        assert result.passed is False
        assert result.message == ("versioned: 1 file(s) whose current version is not recorded in "
                                  "any asset version: /models/part.stl")
        assert result.measured == {"fileCount": 3, "uncoveredCount": 1,
                                   "uncovered": ["/models/part.stl"]}

    def test_a_file_without_an_s3_version_is_uncovered_with_its_own_message(self):
        files = FILES + [engine.CurrentFile("/legacy.obj", None)]
        result = engine.evaluate_version_coverage_rule(
            "versions", _version_coverage(), files, self.VERSIONED)
        assert result.passed is False
        assert result.message == ("versioned: 1 file(s) without an S3 version id cannot be "
                                  "recorded in an asset version: /legacy.obj")
        assert result.measured["uncovered"] == ["/legacy.obj"]

    def test_excluded_files_are_not_checked(self):
        files = FILES + [engine.CurrentFile("/models/part.glb.previewFile.png", "v-preview")]
        result = engine.evaluate_version_coverage_rule(
            "versions", _version_coverage(excludePaths=[r"\.previewFile\."]), files,
            self.VERSIONED)
        assert result.passed is True
        assert result.expected == {"versioned": {"excludePaths": [r"\.previewFile\."]}}

    def test_an_asset_without_files_passes(self):
        assert engine.evaluate_version_coverage_rule(
            "versions", _version_coverage(), [], set()).passed is True

    @pytest.mark.parametrize("files,versioned,message", [
        (None, set(), engine.FILES_UNAVAILABLE_MESSAGE),
        (FILES, None, engine.ASSET_VERSIONS_UNAVAILABLE_MESSAGE),
    ], ids=["no-listing", "no-versions"])
    def test_a_missing_input_is_an_error_result(self, files, versioned, message):
        result = engine.evaluate_version_coverage_rule(
            "versions", _version_coverage(), files, versioned)
        assert (result.status, result.passed, result.message) == ("error", False, message)


@pytest.mark.unit
class TestAssetAttributes:

    ASSET = {"assetName": "Turbine-Blade", "description": "Blade for unit 7",
             "tags": ["engineering", "approved"]}

    def test_a_matching_name_passes_and_the_value_is_measured_not_the_pattern(self):
        rule = _asset_attributes({"field": "assetName", "matches": "^[A-Z][A-Za-z-]+$"})
        result = engine.evaluate_asset_attributes_rule("naming", rule, self.ASSET)
        assert result.passed is True
        assert result.ruleType == "assetAttributes"
        assert result.measured == {"assetName": "Turbine-Blade"}
        assert result.expected == {"c0": {"field": "assetName", "condition": "matches"}}
        assert "^[A-Z]" not in str(result.dict())

    def test_a_name_that_does_not_match_fails_without_the_pattern(self):
        rule = _asset_attributes({"field": "assetName", "matches": "^[a-z]+$"})
        result = engine.evaluate_asset_attributes_rule("naming", rule, self.ASSET)
        assert result.passed is False
        assert result.message == "c0: assetName does not match the required pattern"

    @pytest.mark.parametrize("asset", [{}, {"description": ""}, {"description": 7}],
                             ids=["absent", "empty", "not-a-string"])
    def test_a_missing_value_fails_matches_and_passes_not_matches(self, asset):
        failing = engine.evaluate_asset_attributes_rule(
            "r", _asset_attributes({"field": "description", "matches": "."}), asset)
        assert failing.passed is False
        assert failing.message == "c0: description is empty"
        assert engine.evaluate_asset_attributes_rule(
            "r", _asset_attributes({"field": "description", "notMatches": "."}), asset).passed

    def test_a_forbidden_description_pattern_fails(self):
        rule = _asset_attributes({"field": "description", "notMatches": "(?i)unit \\d+"})
        result = engine.evaluate_asset_attributes_rule("r", rule, self.ASSET)
        assert result.passed is False
        assert result.message == "c0: description matches a forbidden pattern"

    def test_a_long_value_is_measured_cut_to_256_characters(self):
        rule = _asset_attributes({"field": "description", "matches": "x"})
        result = engine.evaluate_asset_attributes_rule("r", rule, {"description": "x" * 400})
        assert result.measured["description"] == "x" * 256

    def test_tag_criteria(self):
        rule = _asset_attributes(
            {"field": "tags", "requiredTags": ["engineering", "released"]},
            {"field": "tags", "forbiddenTags": ["approved", "draft"]},
            {"field": "tags", "minTags": 3},
        )
        result = engine.evaluate_asset_attributes_rule("r", rule, self.ASSET)
        assert result.passed is False
        assert result.message == ("c0: missing required tags released; c1: carries forbidden tags "
                                  "approved; c2: has 2 tags, minimum is 3")
        assert result.measured == {"tags": ["engineering", "approved"]}
        assert result.expected == {"c0": {"requiredTags": ["engineering", "released"]},
                                   "c1": {"forbiddenTags": ["approved", "draft"]},
                                   "c2": {"minTags": 3}}

    def test_tags_compare_by_exact_value_and_accept_a_set_attribute(self):
        rule = _asset_attributes({"field": "tags", "requiredTags": ["Engineering"]})
        assert engine.evaluate_asset_attributes_rule("r", rule, self.ASSET).passed is False
        as_set = engine.evaluate_asset_attributes_rule(
            "r", _asset_attributes({"field": "tags", "requiredTags": ["b"]}), {"tags": {"b", "a"}})
        assert as_set.passed is True
        assert as_set.measured == {"tags": ["a", "b"]}

    def test_an_asset_without_tags_has_none(self):
        result = engine.evaluate_asset_attributes_rule(
            "r", _asset_attributes({"field": "tags", "minTags": 1}), {"tags": None})
        assert result.message == "c0: has 0 tags, minimum is 1"

    def test_a_missing_asset_row_is_an_error_result(self):
        result = engine.evaluate_asset_attributes_rule(
            "r", _asset_attributes({"field": "tags", "minTags": 1}), None)
        assert (result.status, result.message) == ("error", engine.ASSET_UNAVAILABLE_MESSAGE)

    def test_a_pattern_that_no_longer_compiles_is_an_error_result(self):
        rule = _asset_attributes({"field": "assetName", "matches": "^A"})
        with patch.object(engine.re, "compile", side_effect=re.error("bad")):
            result = engine.evaluate_asset_attributes_rule("r", rule, self.ASSET)
        assert (result.status, result.message) == ("error", "Invalid pattern in rule")


@pytest.mark.unit
class TestEvaluateRulesWithTheNewTypes:

    def _rules(self):
        return {
            "files": _file_structure(requiredPaths=[r"\.glb$"]),
            "versions": _version_coverage(),
            "naming": _asset_attributes({"field": "assetName", "matches": "^T"}),
        }

    def test_each_rule_reads_its_own_input(self):
        results, pending = engine.evaluate_rules(
            self._rules(), {}, {}, {}, current_files=FILES,
            versioned_file_keys=TestVersionCoverage.VERSIONED,
            asset_row={"assetName": "Turbine"})
        assert pending == {}
        assert [(r.ruleName, r.ruleType, r.passed) for r in results] == [
            ("files", "fileStructure", True), ("versions", "versionCoverage", True),
            ("naming", "assetAttributes", True)]

    def test_without_inputs_every_new_rule_is_an_error_result(self):
        results, _ = engine.evaluate_rules(self._rules(), {}, {}, {})
        assert all(isinstance(r, RuleResult) and r.status == "error" for r in results)
        assert engine.determine_verdict(results).value == "error"

    def test_the_groups_say_which_inputs_are_needed(self):
        none = engine.split_rules({})
        assert not (engine.needs_asset_row(none) or engine.needs_current_files(none)
                    or engine.needs_versioned_file_keys(none))
        attributes = engine.split_rules({"naming": self._rules()["naming"]})
        assert engine.needs_asset_row(attributes)
        assert not engine.needs_current_files(attributes)
        files = engine.split_rules({"files": self._rules()["files"]})
        assert engine.needs_current_files(files) and engine.needs_asset_row(files)
        assert not engine.needs_versioned_file_keys(files)
        versions = engine.split_rules({"versions": self._rules()["versions"]})
        assert engine.needs_current_files(versions) and engine.needs_versioned_file_keys(versions)

    def test_split_rules_names_six_groups(self):
        groups = engine.split_rules(self._rules())
        assert groups._fields == ("metadata", "relationship", "pipeline", "fileStructure",
                                  "versionCoverage", "assetAttributes")
        assert list(groups.fileStructure) == ["files"]
        assert list(groups.versionCoverage) == ["versions"]
        assert list(groups.assetAttributes) == ["naming"]
