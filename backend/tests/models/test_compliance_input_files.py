# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`PipelineInputFiles` on the vams-rules-v1 pipeline rule: the three modes and what each accepts,
the bounds on `filter` and `keys`, the leading-slash normalization of keys, the trimming of entries,
the default a rule without a selection gets, and Rule 11 on the messages (no caller value echoed)."""

import json

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from models.common import validation_error_message
from models.compliance import (
    MAX_EXPLICIT_INPUT_KEYS,
    MAX_INPUT_FILE_FILTER_LENGTH,
    MAX_INPUT_FILE_FILTERS,
    PipelineInputFiles,
    PipelineRule,
)

RULE = {
    "ruleType": "pipeline",
    "enforcement": "quarantine",
    "pipelineRef": {"databaseId": "GLOBAL", "workflowId": "conversion-3d-basic",
                    "pipelineDatabaseId": "GLOBAL", "pipelineId": "conversion-3d-basic",
                    "templateId": "convert-to-glb"},
    "checks": [{"name": "ok", "outputField": "execution_success",
                "tolerance": {"operator": "eq", "value": 1}}],
}

SECRET = "secret<value>"


def _rejects(**selection):
    with pytest.raises(ValidationError) as raised:
        PipelineInputFiles(**selection)
    return validation_error_message(raised.value)


@pytest.mark.unit
class TestModes:

    def test_the_default_selection_is_matching_with_no_filter(self):
        selection = PipelineInputFiles()
        assert selection.mode == "matching"
        assert selection.filter is None
        assert selection.keys is None

    def test_a_rule_without_a_selection_gets_the_default(self):
        rule = PipelineRule(**RULE)
        assert rule.inputFiles.mode == "matching"
        assert rule.dict()["inputFiles"] == {"mode": "matching", "filter": None, "keys": None}

    def test_matching_accepts_filter_globs(self):
        selection = PipelineInputFiles(mode="matching", filter=["*.stl", " *.obj "])
        assert selection.filter == ["*.stl", "*.obj"]

    def test_whole_asset_takes_no_filter_or_keys(self):
        assert PipelineInputFiles(mode="wholeAsset").mode == "wholeAsset"
        assert "filter" in _rejects(mode="wholeAsset", filter=["*.stl"])
        assert "keys" in _rejects(mode="wholeAsset", keys=["/a.stl"])

    def test_explicit_requires_keys_and_takes_no_filter(self):
        selection = PipelineInputFiles(mode="explicit", keys=["/models/a.stl"])
        assert selection.keys == ["/models/a.stl"]
        assert "keys" in _rejects(mode="explicit")
        assert "keys" in _rejects(mode="explicit", keys=[])
        assert "filter" in _rejects(mode="explicit", keys=["/a.stl"], filter=["*.stl"])

    def test_keys_are_refused_with_matching(self):
        assert "keys" in _rejects(mode="matching", keys=["/a.stl"])

    @pytest.mark.parametrize("mode", ["", "all", "MATCHING", "whole-asset", SECRET])
    def test_an_unknown_mode_is_refused_without_being_echoed(self, mode):
        message = _rejects(mode=mode)
        assert mode == "" or mode not in message
        assert "mode" in message

    def test_the_mode_trims(self):
        assert PipelineInputFiles(mode=" wholeAsset ").mode == "wholeAsset"


@pytest.mark.unit
class TestKeys:

    def test_keys_gain_a_single_leading_slash(self):
        selection = PipelineInputFiles(mode="explicit", keys=["a.stl", "//b.obj", " /c/d.ply "])
        assert selection.keys == ["/a.stl", "/b.obj", "/c/d.ply"]

    @pytest.mark.parametrize("key", ["/../x.stl", "/a/../b.stl", "/a", ""],
                             ids=["traversal", "inner-traversal", "too-short", "empty"])
    def test_a_key_the_relative_path_rule_refuses_is_rejected(self, key):
        message = _rejects(mode="explicit", keys=[key])
        assert "keys" in message

    def test_the_key_count_is_bounded(self):
        keys = [f"/file-{i}.stl" for i in range(MAX_EXPLICIT_INPUT_KEYS)]
        assert len(PipelineInputFiles(mode="explicit", keys=keys).keys) == MAX_EXPLICIT_INPUT_KEYS
        _rejects(mode="explicit", keys=keys + ["/one-more.stl"])

    def test_the_bound_is_named_by_a_constant(self):
        assert MAX_EXPLICIT_INPUT_KEYS == 64
        assert PipelineInputFiles.__fields__["keys"].field_info.max_items == MAX_EXPLICIT_INPUT_KEYS


@pytest.mark.unit
class TestFilters:

    def test_the_filter_count_is_bounded(self):
        filters = [f"*.e{i}" for i in range(MAX_INPUT_FILE_FILTERS)]
        assert len(PipelineInputFiles(filter=filters).filter) == MAX_INPUT_FILE_FILTERS
        _rejects(filter=filters + ["*.x"])
        assert PipelineInputFiles.__fields__["filter"].field_info.max_items == MAX_INPUT_FILE_FILTERS

    def test_each_filter_entry_is_bounded_and_non_empty(self):
        assert PipelineInputFiles(filter=["a" * MAX_INPUT_FILE_FILTER_LENGTH]).filter
        message = _rejects(filter=["a" * (MAX_INPUT_FILE_FILTER_LENGTH + 1)])
        assert str(MAX_INPUT_FILE_FILTER_LENGTH) in message
        assert "filter" in _rejects(filter=[""])
        assert "filter" in _rejects(filter=["   "])

    def test_a_filter_entry_that_is_not_a_string_is_refused(self):
        """Pydantic v1 coerces a number to `str` for a `List[str]` field, so `7` becomes the glob
        "7"; an entry it cannot coerce (an object) is refused."""
        assert PipelineInputFiles(filter=[7]).filter == ["7"]
        _rejects(filter=[{"glob": "*.stl"}])

    def test_the_bounds_are_live_constraints_not_swallowed_kwargs(self):
        for field in ("mode", "filter", "keys"):
            assert not PipelineInputFiles.__fields__[field].field_info.extra
        assert PipelineInputFiles.__fields__["mode"].field_info.regex is not None


@pytest.mark.unit
class TestRuleIntegration:

    def test_a_pipeline_rule_carries_its_selection_through_the_stored_json_round_trip(self):
        """The evaluation record stores a pending pipeline rule as `rule.dict()` JSON and rebuilds
        it with `PipelineRule(**...)` at callback time; the selection survives that trip."""
        rule = PipelineRule(**dict(RULE, inputFiles={"mode": "matching", "filter": ["*.stl"]}))
        stored = json.dumps(rule.dict())
        restored = PipelineRule(**json.loads(stored))
        assert restored.inputFiles.mode == "matching"
        assert restored.inputFiles.filter == ["*.stl"]
        assert json.loads(stored)["inputFiles"] == {"mode": "matching", "filter": ["*.stl"],
                                                    "keys": None}

    def test_a_pipeline_rule_with_an_invalid_selection_is_refused_without_the_rule_name_or_value(self):
        with pytest.raises(ValidationError) as raised:
            PipelineRule(**dict(RULE, inputFiles={"mode": "explicit", "keys": [f"/{SECRET}/../x"]}))
        message = validation_error_message(raised.value)
        assert SECRET not in message
        assert "inputFiles" in message
