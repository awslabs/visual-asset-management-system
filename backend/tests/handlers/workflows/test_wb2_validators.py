# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the WB2 pure validators + metadata-v2 helpers:
  - common/workflows/templateTagSchema.py  (tag schema + tag value validation, reserved keys, Q1)
  - common/workflows/executionValidation.py (cross-entity matrix + workflow-save checks, overrides)
  - common/workflows/executionRecords.py     (metadata-format-v2 grouped envelope + accessor)
  - common/workflows/templateTags.py         (SYSTEM_TAG_NAMES ↔ renderer catalog no-drift guard)
All are dependency-free (no AWS/env), so they import and run in isolation."""

import pytest

from backend.backend.common.workflows import templateTagSchema as ts
from backend.backend.common.workflows import executionValidation as ev
from backend.backend.common.workflows import executionRecords as er
from backend.backend.common.workflows import templateTags as tt
from backend.backend.common.workflows import templateRender as tr


# ============================ templateTagSchema: schema declaration ============================

@pytest.mark.unit
class TestValidateTagSchema:
    def test_valid_schema(self):
        assert ts.validate_tag_schema([
            {"tagKey": "prompt", "type": "string", "required": True},
            {"tagKey": "count", "type": "integer", "default": 3},
            {"tagKey": "mode", "type": "enum", "enumValues": ["a", "b"], "default": "a"},
        ]) == []

    def test_missing_tag_key(self):
        errs = ts.validate_tag_schema([{"type": "string"}])
        assert any("tagKey is required" in e for e in errs)

    def test_duplicate_keys(self):
        errs = ts.validate_tag_schema([{"tagKey": "x"}, {"tagKey": "x"}])
        assert any("duplicate" in e.lower() for e in errs)

    def test_unknown_type(self):
        errs = ts.validate_tag_schema([{"tagKey": "x", "type": "matrix4x4"}])
        assert any("unknown type" in e for e in errs)

    def test_reserved_system_key_rejected(self):
        for reserved in ("executionId", "workflowId", "outputBucket", "assetFileKeyArray"):
            errs = ts.validate_tag_schema([{"tagKey": reserved, "type": "string"}])
            assert any("reserved" in e for e in errs), reserved

    def test_reserved_metadata_prefix_rejected(self):
        errs = ts.validate_tag_schema([{"tagKey": "metadata_foo", "type": "string"}])
        assert any("reserved" in e for e in errs)

    def test_enum_requires_values(self):
        errs = ts.validate_tag_schema([{"tagKey": "m", "type": "enum"}])
        assert any("enumValues" in e for e in errs)

    def test_invalid_default_for_type(self):
        errs = ts.validate_tag_schema([{"tagKey": "n", "type": "integer", "default": "notanint"}])
        assert any("default value invalid" in e for e in errs)

    def test_none_and_non_list(self):
        assert ts.validate_tag_schema(None) == []
        assert any("must be a list" in e for e in ts.validate_tag_schema({"tagKey": "x"}))


# ============================ templateTagSchema: value validation ============================

@pytest.mark.unit
class TestValidateTags:
    SCHEMA = [
        {"tagKey": "prompt", "type": "string", "required": True},
        {"tagKey": "count", "type": "integer", "default": 5},
        {"tagKey": "ratio", "type": "number"},
        {"tagKey": "flag", "type": "boolean"},
        {"tagKey": "tags", "type": "string-list"},
        {"tagKey": "mode", "type": "enum", "enumValues": ["fast", "slow"]},
    ]

    def test_required_present_defaults_filled(self):
        errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "hi"})
        assert errs == []
        assert filled["prompt"] == "hi"
        assert filled["count"] == 5  # default applied

    def test_missing_required_errors(self):
        errs, _ = ts.validate_tags(self.SCHEMA, {})
        assert any("prompt' is required" in e for e in errs)

    def test_extra_tags_ignored_not_error(self):
        # Q1: extra provided tags with no schema entry are ignored, not an error.
        errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "hi", "somethingExtra": "x"})
        assert errs == []
        assert "somethingExtra" not in filled

    def test_integer_coercion_and_reject(self):
        errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "p", "count": "10"})
        assert errs == [] and filled["count"] == 10
        errs2, _ = ts.validate_tags(self.SCHEMA, {"prompt": "p", "count": "3.5"})
        assert any("count" in e for e in errs2)

    def test_number_coercion(self):
        errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "p", "ratio": "1.5"})
        assert errs == [] and filled["ratio"] == 1.5

    def test_boolean_coercion(self):
        _, filled = ts.validate_tags(self.SCHEMA, {"prompt": "p", "flag": "true"})
        assert filled["flag"] is True
        errs, _ = ts.validate_tags(self.SCHEMA, {"prompt": "p", "flag": "maybe"})
        assert any("flag" in e for e in errs)

    def test_string_list(self):
        errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "p", "tags": ["a", "b"]})
        assert errs == [] and filled["tags"] == ["a", "b"]
        errs2, _ = ts.validate_tags(self.SCHEMA, {"prompt": "p", "tags": "notalist"})
        assert any("tags" in e for e in errs2)

    def test_enum(self):
        errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "p", "mode": "fast"})
        assert errs == [] and filled["mode"] == "fast"
        errs2, _ = ts.validate_tags(self.SCHEMA, {"prompt": "p", "mode": "warp"})
        assert any("mode" in e for e in errs2)

    def test_reserved_provided_key_rejected(self):
        errs, _ = ts.validate_tags(self.SCHEMA, {"prompt": "p", "workflowId": "w"})
        assert any("reserved system tag" in e for e in errs)

    def test_provided_as_list_of_key_value(self):
        errs, filled = ts.validate_tags(self.SCHEMA, [{"key": "prompt", "value": "hi"}])
        assert errs == [] and filled["prompt"] == "hi"

    def test_empty_required_list_errors(self):
        # An empty required list is treated as absent (same as an empty required string).
        schema = [{"tagKey": "items", "type": "string-list", "required": True}]
        errs, _ = ts.validate_tags(schema, {"items": []})
        assert any("items' is required" in e for e in errs)

    def test_empty_required_string_errors(self):
        schema = [{"tagKey": "p", "type": "string", "required": True}]
        errs, _ = ts.validate_tags(schema, {"p": ""})
        assert any("p' is required" in e for e in errs)

    def test_non_finite_numbers_rejected(self):
        schema = [{"tagKey": "n", "type": "number", "required": True}]
        for bad in ("nan", "inf", "infinity", "-inf"):
            errs, _ = ts.validate_tags(schema, {"n": bad})
            assert any("finite" in e for e in errs), bad
        # a real float is fine
        errs2, filled = ts.validate_tags(schema, {"n": "3.14"})
        assert errs2 == [] and filled["n"] == 3.14


# ============================ SYSTEM_TAG_NAMES ↔ renderer no-drift guard ============================

@pytest.mark.unit
class TestReservedTagRegistry:
    def test_system_tag_names_match_renderer_catalog(self):
        # SYSTEM_TAG_NAMES must equal exactly what the renderer resolves (base + metadata context),
        # so the reserved-key check and the renderer never drift.
        ctx = tr.build_template_context({}, {})
        ctx.update(tr._metadata_context({}))
        assert set(tt.SYSTEM_TAG_NAMES) == set(ctx.keys())

    def test_is_reserved_tag_key(self):
        assert tt.is_reserved_tag_key("executionId") is True
        assert tt.is_reserved_tag_key("metadata_anything") is True
        assert tt.is_reserved_tag_key("myCustomPrompt") is False
        assert tt.is_reserved_tag_key("") is False


# ============================ executionValidation: effective config + filters ============================

@pytest.mark.unit
class TestEffectiveConfigAndFilters:
    def test_template_overrides_win_for_overridable_keys(self):
        base = {"inputFileArity": "one", "requireTemplate": True,
                "inputFileFilters": {"allow": [".glb"], "exclude": []}}
        eff = ev.resolve_effective_pipeline_config(base, {"inputFileArity": "multi"})
        assert eff["inputFileArity"] == "multi"       # overridden
        assert eff["requireTemplate"] is True          # not overridable, preserved
        assert eff["inputFileFilters"] == {"allow": [".glb"], "exclude": []}

    def test_empty_overrides_no_change(self):
        base = {"inputFileArity": "one"}
        assert ev.resolve_effective_pipeline_config(base, {}) == base
        assert ev.resolve_effective_pipeline_config(base, None) == base

    def test_extension_filter(self):
        inputs = [{"relativeFileKey": "/a.glb"}, {"relativeFileKey": "/b.obj"}]
        out = ev.apply_input_file_filters(inputs, {"allow": [".glb"], "exclude": []})
        assert out == [{"relativeFileKey": "/a.glb"}]

    def test_exclude_after_allow(self):
        inputs = [{"relativeFileKey": "/keep.glb"}, {"relativeFileKey": "/skip.glb"}]
        out = ev.apply_input_file_filters(inputs, {"allow": [".glb"], "exclude": ["*skip*"]})
        assert out == [{"relativeFileKey": "/keep.glb"}]

    def test_empty_allow_is_allow_all(self):
        inputs = [{"relativeFileKey": "/a.glb"}, {"relativeFileKey": "/b.obj"}]
        assert ev.apply_input_file_filters(inputs, {"allow": [], "exclude": []}) == inputs

    def test_glob_is_case_insensitive_os_independent(self):
        # Matching is case-insensitive: '*Skip*' and '*skip*' both exclude '/skip.glb'. It is still
        # OS-independent (lowercased fnmatchcase, not OS-cased fnmatch).
        inputs = [{"relativeFileKey": "/skip.glb"}]
        assert ev.apply_input_file_filters(inputs, {"allow": [], "exclude": ["*Skip*"]}) == []
        assert ev.apply_input_file_filters(inputs, {"allow": [], "exclude": ["*skip*"]}) == []

    def test_canonical_extension_and_midstring_glob(self):
        inputs = [
            {"relativeFileKey": "/a.ZIP"},
            {"relativeFileKey": "/b.obj"},
            {"relativeFileKey": "/c.glb.previewFile.png"},
        ]
        # '*.zip' canonical extension form matches by extension, case-insensitively.
        assert ev.apply_input_file_filters(inputs, {"allow": ["*.zip"], "exclude": []}) == [
            {"relativeFileKey": "/a.ZIP"}
        ]
        # mid-string glob (e.g. selecting preview files) matches via fnmatch.
        assert ev.apply_input_file_filters(inputs, {"allow": ["*.previewFile.*"], "exclude": []}) == [
            {"relativeFileKey": "/c.glb.previewFile.png"}
        ]


# ============================ executionValidation: execute matrix ============================

@pytest.mark.unit
class TestValidateExecution:
    def _wf(self, **over):
        cfg = {"inputFileArity": "one",
               "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                              "wholeAssetAllowed": False, "folderAllowed": False},
               "metadataInputs": {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True},
               "inputFileFilters": {"allow": [], "exclude": []},
               "concurrencyRestriction": "none",
               "outputTarget": {"locationType": "asset", "allowOverride": False}}
        cfg.update(over)
        return cfg

    def _pipe(self, pid="p1", arity="one", **sc):
        systemConfig = {"inputFileArity": arity, "inputFileFilters": {"allow": [], "exclude": []}}
        systemConfig.update(sc)
        return {"pipelineId": pid, "pipelineDatabaseId": "db", "enabled": True, "archived": False,
                "systemConfig": systemConfig}

    def test_happy_path_single_file(self):
        errs, filt = ev.validate_execution(
            self._wf(), [self._pipe()], [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert errs == []
        assert filt["p1"] == [{"assetId": "a", "relativeFileKey": "/x.glb"}]

    def test_multi_when_single_arity_errors(self):
        errs, _ = ev.validate_execution(
            self._wf(inputFileArity="multi",
                     assetScope={"crossAssetAllowed": True, "singleAssetOnly": False,
                                 "wholeAssetAllowed": False, "folderAllowed": False}),
            [self._pipe(arity="one")],
            [{"assetId": "a", "relativeFileKey": "/x.glb"},
             {"assetId": "a", "relativeFileKey": "/y.glb"}])
        assert any("single input file" in e for e in errs)

    def test_none_arity_pipeline_gets_no_files_soft(self):
        # Matrix row (b): single/multi file + pipeline none -> pass NO files to that pipeline, SOFT
        # (no error), even when the workflow has selected files.
        errs, filt = ev.validate_execution(
            self._wf(), [self._pipe(arity="none")],
            [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert errs == []
        assert filt["p1"] == []  # none pipeline receives no inputs

    def test_none_arity_in_mixed_workflow(self):
        # A none-arity metadata pipeline can coexist with a file-consuming pipeline.
        errs, filt = ev.validate_execution(
            self._wf(), [self._pipe(pid="conv", arity="one"), self._pipe(pid="label", arity="none")],
            [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert errs == []
        assert filt["conv"] == [{"assetId": "a", "relativeFileKey": "/x.glb"}]
        assert filt["label"] == []

    def test_error_label_includes_database_for_same_id(self):
        p = self._pipe(pid="convert")
        p["archived"] = True
        errs, _ = ev.validate_execution(self._wf(), [p], [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert any("db:convert" in e for e in errs)

    def test_filter_to_empty_on_file_requiring_pipeline_is_hard_error(self):
        errs, _ = ev.validate_execution(
            self._wf(), [self._pipe(arity="one", inputFileFilters={"allow": [".obj"], "exclude": []})],
            [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert any("exclude all selected inputs" in e for e in errs)

    def test_disabled_pipeline_errors(self):
        p = self._pipe()
        p["enabled"] = False
        errs, _ = ev.validate_execution(self._wf(), [p], [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert any("disabled" in e for e in errs)

    def test_archived_pipeline_errors(self):
        p = self._pipe()
        p["archived"] = True
        errs, _ = ev.validate_execution(self._wf(), [p], [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert any("archived" in e for e in errs)

    def test_multi_asset_when_single_only_errors(self):
        errs, _ = ev.validate_execution(
            self._wf(inputFileArity="multi"),
            [self._pipe(arity="multi")],
            [{"assetId": "a", "relativeFileKey": "/x.glb"},
             {"assetId": "b", "relativeFileKey": "/y.glb"}])
        assert any("single asset" in e for e in errs)

    def test_whole_asset_not_allowed(self):
        errs, _ = ev.validate_execution(
            self._wf(), [self._pipe()], [{"assetId": "a", "relativeFileKey": "/"}])
        assert any("whole-asset" in e for e in errs)

    def test_folder_not_allowed(self):
        errs, _ = ev.validate_execution(
            self._wf(), [self._pipe()], [{"assetId": "a", "relativeFileKey": "/folder/"}])
        assert any("folder" in e for e in errs)


# ============================ executionValidation: workflow-save checks ============================

@pytest.mark.unit
class TestValidateWorkflowSave:
    def test_metadata_mismatch_warns(self):
        wf = {"metadataInputs": {"assetMetadata": True, "fileMetadata": False, "fileAttributes": False},
              "inputFileArity": "one", "inputFileFilters": {}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"metadataInputs": {"fileMetadata": True}}}]
        errs, warns = ev.validate_workflow_save(wf, pipes)
        assert errs == []
        assert any("fileMetadata" in w for w in warns)

    def test_arity_mismatch_warns(self):
        wf = {"inputFileArity": "multi", "metadataInputs": {}, "inputFileFilters": {}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"inputFileArity": "one"}}]
        _, warns = ev.validate_workflow_save(wf, pipes)
        assert any("single input file" in w for w in warns)

    def test_archived_pipeline_is_error(self):
        wf = {"inputFileArity": "one", "metadataInputs": {}, "inputFileFilters": {}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": True, "systemConfig": {}}]
        errs, _ = ev.validate_workflow_save(wf, pipes)
        assert any("archived" in e for e in errs)

    def test_trigger_default_undefaulted_warns(self):
        wf = {"inputFileArity": "one", "metadataInputs": {}, "inputFileFilters": {}}
        errs, warns = ev.validate_workflow_save(
            wf, [], trigger={"undefaultedRequiredTagsByTemplateId": {"t1": ["prompt"]}})
        assert any("auto-trigger would fail" in w for w in warns)


# ============================ metadata-format v2 ============================

@pytest.mark.unit
class TestMetadataV2:
    def test_grouped_envelope_shape(self):
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "xid1", asset_data={"assetName": "n"}, files=[
                er.build_metadata_file_record("/", {"VAMS": {"assetMetadata": {"k": "v"}}}),
                er.build_metadata_file_record("/a.glb", {"VAMS": {"fileMetadata": {"p": "q"}}},
                                              attributes={"attr": "1"}),
                er.build_metadata_file_record("/folder/", None),
            ])
        ])
        assert env["schemaVersion"] == 2
        assert env["assets"][0]["databaseId"] == "db1"
        assert env["assets"][0]["files"][2]["metadata"] is None       # folder
        assert "attributes" not in env["assets"][0]["files"][0]        # asset record: no attributes
        assert env["assets"][0]["files"][1]["attributes"] == {"attr": "1"}

    def test_file_record_normalizes_key(self):
        assert er.build_metadata_file_record("a.glb")["fileKey"] == "/a.glb"

    def test_get_asset_file_record_accessor(self):
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "xid1", files=[
                er.build_metadata_file_record("/a.glb", {"m": 1})])])
        assert er.get_asset_file_record(env, "db1", "xid1", "a.glb")["metadata"] == {"m": 1}
        assert er.get_asset_file_record(env, "db1", "xid1", "/a.glb")["metadata"] == {"m": 1}
        assert er.get_asset_file_record(env, "db1", "xid1", "/missing") is None
        assert er.get_asset_file_record(env, "db1", "other", "/a.glb") is None

    def test_v1_envelope_still_v1(self):
        # The current single-file execute path keeps writing v1 envelopes stamped schemaVersion 1.
        assert er.build_metadata_envelope({"VAMS": {}})["schemaVersion"] == 1
        assert er.METADATA_SCHEMA_VERSION == 1
        assert er.METADATA_SCHEMA_VERSION_GROUPED == 2
