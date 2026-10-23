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

    def test_unsubstitutable_tag_key_rejected(self):
        # A key outside the {{tag}} name charset could never be substituted.
        for bad in ("my-tag", "my tag", "my.tag", " padded "):
            errs = ts.validate_tag_schema([{"tagKey": bad, "type": "string"}])
            assert any("letters, digits and underscores" in e for e in errs), bad
        assert ts.validate_tag_schema([{"tagKey": "my_tag2", "type": "string"}]) == []

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

    def test_integral_float_accepted_fractional_rejected(self):
        errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "p", "count": 3.0})
        assert errs == [] and filled["count"] == 3
        errs2, _ = ts.validate_tags(self.SCHEMA, {"prompt": "p", "count": 3.5})
        assert any("count" in e for e in errs2)

    def test_integral_decimal_accepted_fractional_rejected(self):
        """DynamoDB returns numbers as Decimal, so a stored default must coerce like a float."""
        from decimal import Decimal
        for value in (Decimal("3"), Decimal("3.0")):
            errs, filled = ts.validate_tags(self.SCHEMA, {"prompt": "p", "count": value})
            assert errs == [] and filled["count"] == 3
        errs2, _ = ts.validate_tags(self.SCHEMA, {"prompt": "p", "count": Decimal("3.5")})
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

    def test_pipeline_scope_blocks_whole_asset_the_workflow_allows(self):
        # The pipeline's own assetScope is enforced against the inputs it receives, so a pipeline
        # that declares no whole-asset support rejects a '/' selection the workflow gate permits.
        wf = self._wf(assetScope={"crossAssetAllowed": False, "singleAssetOnly": True,
                                  "wholeAssetAllowed": True, "folderAllowed": True})
        errs, _ = ev.validate_execution(
            wf, [self._pipe(assetScope={"wholeAssetAllowed": False})],
            [{"assetId": "a", "relativeFileKey": "/"}])
        assert any("db:p1" in e and "whole-asset" in e for e in errs)

    def test_pipeline_scope_wholeasset_shorthand_normalized(self):
        # The registration shorthand `wholeAsset` is read identically to `wholeAssetAllowed`.
        wf = self._wf(assetScope={"crossAssetAllowed": False, "singleAssetOnly": True,
                                  "wholeAssetAllowed": True, "folderAllowed": False})
        inputs = [{"assetId": "a", "relativeFileKey": "/"}]
        errs, _ = ev.validate_execution(wf, [self._pipe(assetScope={"wholeAsset": False})], inputs)
        assert any("whole-asset" in e for e in errs)
        errs, _ = ev.validate_execution(wf, [self._pipe(assetScope={"wholeAsset": True})], inputs)
        assert errs == []

    def test_pipeline_scope_undeclared_keys_defer_to_workflow(self):
        # A pipeline assetScope declaring only wholeAssetAllowed does not additionally deny a folder
        # selection the workflow permits.
        wf = self._wf(assetScope={"crossAssetAllowed": False, "singleAssetOnly": True,
                                  "wholeAssetAllowed": False, "folderAllowed": True})
        errs, _ = ev.validate_execution(
            wf, [self._pipe(assetScope={"wholeAssetAllowed": True})],
            [{"assetId": "a", "relativeFileKey": "/folder/"}])
        assert errs == []

    def test_extension_allow_list_does_not_exclude_container_selections(self):
        # An extension allow list cannot describe a whole-asset or folder selection, so those pass
        # the filter and their admissibility is left to the assetScope gates.
        wf = self._wf(assetScope={"crossAssetAllowed": False, "singleAssetOnly": True,
                                  "wholeAssetAllowed": True, "folderAllowed": True},
                      inputFileFilters={"allow": ["*.glb"], "exclude": []})
        pipe = self._pipe(assetScope={"wholeAssetAllowed": True, "folderAllowed": True},
                          inputFileFilters={"allow": ["*.glb"], "exclude": []})
        for file_key in ("/", "/models/"):
            errs, filt = ev.validate_execution(
                wf, [pipe], [{"assetId": "a", "relativeFileKey": file_key}])
            assert errs == [], (file_key, errs)
            assert filt["p1"] == [{"assetId": "a", "relativeFileKey": file_key}]

    def test_null_arity_is_treated_as_one(self):
        # An explicitly-null inputFileArity must not disable arity enforcement.
        errs, _ = ev.validate_execution(self._wf(inputFileArity=None), [self._pipe()], [])
        assert any("exactly one input file" in e for e in errs)
        errs2, _ = ev.validate_execution(
            self._wf(inputFileArity="multi",
                     assetScope={"crossAssetAllowed": True, "singleAssetOnly": False,
                                 "wholeAssetAllowed": False, "folderAllowed": False}),
            [self._pipe(arity=None)],
            [{"assetId": "a", "relativeFileKey": "/x.glb"},
             {"assetId": "a", "relativeFileKey": "/y.glb"}])
        assert any("single input file" in e for e in errs2)

    def test_pipeline_is_judged_on_the_workflow_filtered_list_not_the_raw_selection(self):
        # Ordering matters: the workflow gate narrows the selection first, and each pipeline is judged
        # against THAT list. Here the workflow admits only .glb, so the .obj never reaches the
        # pipeline — a single-arity pipeline therefore sees one file and passes. Judged on the raw
        # two-file selection it would wrongly fail with "accepts a single input file".
        # Workflow arity is multi so the workflow's own arity gate stays quiet and the assertion below
        # measures only the PIPELINE's arity verdict.
        wf = self._wf(inputFileArity="multi",
                      inputFileFilters={"allow": ["*.glb"], "exclude": []})
        errs, filt = ev.validate_execution(
            wf, [self._pipe(arity="one")],
            [{"assetId": "a", "relativeFileKey": "/x.glb"},
             {"assetId": "a", "relativeFileKey": "/y.obj"}])
        # The workflow-level filter check still reports the excluded file as a hard error...
        assert any("workflow input-file filters" in e for e in errs)
        # ...but the pipeline is not additionally blamed for an arity it does not violate.
        assert not any("single input file" in e for e in errs)
        assert filt["p1"] == [{"assetId": "a", "relativeFileKey": "/x.glb"}]

    def test_pipeline_arity_unmet_after_workflow_filtering_fails_execution(self):
        # The converse: the workflow admits nothing the pipeline needs, so the pipeline's requirement
        # is unmet and the whole execution must fail rather than launch with an empty manifest.
        wf = self._wf(inputFileFilters={"allow": ["*.txt"], "exclude": []})
        errs, filt = ev.validate_execution(
            wf, [self._pipe(arity="one", inputFileFilters={"allow": ["*.glb"], "exclude": []})],
            [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert errs, "an execution no pipeline can consume must not validate"
        # The message names the WORKFLOW filters, since they are what emptied the list — blaming the
        # pipeline's own .glb filter for excluding a .glb file would send the user to the wrong config.
        assert any("workflow's input-file filters" in e for e in errs)
        assert filt["p1"] == []

    def test_an_unmet_first_step_fails_the_whole_execution(self):
        # The first step's needs must be met by the filtered list; an unmet first step is fatal even
        # when a later step would be satisfied by the selection. (A later step is judged when the run
        # reaches it — see TestLaterStepsJudgedAtTheirTransition.)
        wf = self._wf(inputFileArity="multi",
                      assetScope={"crossAssetAllowed": False, "singleAssetOnly": True,
                                  "wholeAssetAllowed": False, "folderAllowed": False})
        errs, _ = ev.validate_execution(
            wf,
            [self._pipe(pid="unmet", arity="multi",
                        inputFileFilters={"allow": ["*.e57"], "exclude": []}),
             self._pipe(pid="ok", arity="multi",
                        inputFileFilters={"allow": ["*.glb"], "exclude": []})],
            [{"assetId": "a", "relativeFileKey": "/x.glb"}])
        assert any("db:unmet" in e for e in errs)
        assert not any("db:ok" in e for e in errs)

    def test_path_glob_still_filters_container_selections(self):
        # A non-extension pattern (path glob) still applies to a folder selection.
        inputs = [{"relativeFileKey": "/models/"}, {"relativeFileKey": "/textures/"}]
        assert ev.apply_input_file_filters(inputs, {"allow": ["/models/*"], "exclude": []}) == [
            {"relativeFileKey": "/models/"}]
        assert ev.apply_input_file_filters(inputs, {"allow": ["/models/"], "exclude": []}) == [
            {"relativeFileKey": "/models/"}]
        assert ev.apply_input_file_filters(inputs, {"allow": [], "exclude": ["*models*"]}) == [
            {"relativeFileKey": "/textures/"}]


@pytest.mark.unit
class TestLaterStepsJudgedAtTheirTransition:
    """Launch judges only the FIRST step's filters and arity against the run's selection (#715).

    From the second step on, a step receives the selection overlaid by every earlier step's output,
    narrowed by its own filters and arity-checked when the run reaches it
    (interimPipelineTracking.narrow_next_pipeline_inputs, which fails the run with a named reason).
    Its inputs are therefore not knowable at launch, so judging it against the selection alone both
    refuses valid chains (a step that reads only an earlier step's output type) and cannot catch
    the runtime failures it was meant to.

    One later-step failure IS knowable at launch: two or more selected files passing a single-file
    step's filters. The overlay only adds entries or replaces one at the same path, so that step is
    certain to receive more than one file, and launch refuses it.

    The checks that do not depend on which files a step ends up with — disabled/archived, and the
    step's own assetScope against the selection entries that reach it — still run for every step."""

    OBJ = [{"assetId": "a", "relativeFileKey": "/model.obj"}]

    @staticmethod
    def _wf(**over):
        cfg = {"inputFileArity": "one",
               "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                              "wholeAssetAllowed": False, "folderAllowed": False},
               "inputFileFilters": {"allow": [], "exclude": []}}
        cfg.update(over)
        return cfg

    @staticmethod
    def _step(pid, allow=None, arity="one", exclude=None, **sc):
        system_config = {"inputFileArity": arity,
                         "inputFileFilters": {"allow": allow or [], "exclude": exclude or []}}
        system_config.update(sc)
        return {"pipelineId": pid, "pipelineDatabaseId": "db", "enabled": True, "archived": False,
                "systemConfig": system_config}

    def test_later_step_limited_to_an_earlier_steps_output_type_is_accepted(self):
        # The #715 chain: obj -> glb conversion, then a step that reads only the glb it produced.
        errs, filt = ev.validate_execution(
            self._wf(),
            [self._step("convert", allow=["*.obj"]), self._step("consume", allow=["*.glb"])],
            self.OBJ)
        assert errs == []
        assert filt["convert"] == self.OBJ
        # The later step's share of the SELECTION is empty; its glb arrives from step 1 at runtime.
        assert filt["consume"] == []

    def test_later_step_excluding_the_selected_type_is_accepted(self):
        # The same chain spelled with an exclude list instead of an allow list.
        errs, _ = ev.validate_execution(
            self._wf(),
            [self._step("convert", allow=["*.obj"]), self._step("consume", exclude=["*.obj"])],
            self.OBJ)
        assert errs == []

    def test_two_selected_files_reaching_a_single_file_later_step_are_refused(self):
        # Two selected objs pass an arity-one later step's filters. The run hands that step the
        # selection overlaid by earlier outputs, and an overlay only adds entries or replaces one at
        # the same path, so the step is certain to receive two or more: refused at launch.
        wf = self._wf(inputFileArity="multi")
        two_objs = [{"assetId": "a", "relativeFileKey": "/a.obj"},
                    {"assetId": "a", "relativeFileKey": "/b.obj"}]
        errs, _ = ev.validate_execution(
            wf,
            [self._step("convert", allow=["*.obj"], arity="multi"),
             self._step("consume", allow=["*.obj", "*.glb"], arity="one")],
            two_objs)
        assert errs == [
            "pipeline 'db:consume' accepts a single input file, but 2 selected input files pass "
            "its input-file filters, so the selection alone already gives it more files than it "
            "takes."]

    def test_one_selected_file_reaching_a_single_file_later_step_launches(self):
        # One selected obj reaches the arity-one later step. Whether step 1 then writes a second file
        # the step admits is known only at the transition, which fails the run there if it does.
        errs, _ = ev.validate_execution(
            self._wf(),
            [self._step("convert", allow=["*.obj"]),
             self._step("consume", allow=["*.obj", "*.glb"], arity="one")],
            self.OBJ)
        assert errs == []

    def test_selected_files_a_single_file_later_step_filters_out_do_not_count(self):
        # Two files are selected but only the glb passes the later step's filters.
        wf = self._wf(inputFileArity="multi")
        errs, _ = ev.validate_execution(
            wf,
            [self._step("convert", arity="multi"),
             self._step("consume", allow=["*.glb"], arity="one")],
            [{"assetId": "a", "relativeFileKey": "/a.obj"},
             {"assetId": "a", "relativeFileKey": "/b.glb"}])
        assert errs == []

    def test_a_multi_file_later_step_is_not_counted(self):
        wf = self._wf(inputFileArity="multi")
        errs, _ = ev.validate_execution(
            wf,
            [self._step("convert", allow=["*.obj"], arity="multi"),
             self._step("consume", allow=["*.obj", "*.glb"], arity="multi")],
            [{"assetId": "a", "relativeFileKey": "/a.obj"},
             {"assetId": "a", "relativeFileKey": "/b.obj"}])
        assert errs == []

    def test_two_selected_files_after_an_arity_none_first_step_are_refused(self):
        # The run hands every later step the whole selection, whatever the first step consumed.
        wf = self._wf(inputFileArity="multi")
        errs, _ = ev.validate_execution(
            wf,
            [self._step("generate", arity="none"),
             self._step("consume", allow=["*.obj"], arity="one")],
            [{"assetId": "a", "relativeFileKey": "/a.obj"},
             {"assetId": "a", "relativeFileKey": "/b.obj"}])
        assert len(errs) == 1 and "db:consume" in errs[0] and "selection alone" in errs[0]

    @pytest.mark.parametrize("second", [
        {"assetId": "a", "relativeFileKey": "/a.obj", "versionId": "v2"},
        {"assetId": "a", "relativeFileKey": "//a.obj"},
    ])
    def test_one_file_selected_twice_counts_once(self, second):
        # The run writes one input row per database, asset and normalized file key, so two
        # versions (or two spellings) of one file reach a later step as a single entry.
        wf = self._wf(inputFileArity="multi")
        errs, _ = ev.validate_execution(
            wf,
            [self._step("convert", allow=["*.obj"], arity="multi"),
             self._step("consume", allow=["*.obj"], arity="one")],
            [{"assetId": "a", "relativeFileKey": "/a.obj", "versionId": "v1"}, second])
        assert errs == []

    def test_an_arity_none_first_step_is_still_the_first_step(self):
        # Step 1 consumes no files but still writes outputs a later step can read, so no later step's
        # inputs are knowable at launch — "first step" means first in workflow order, not the first
        # file-consuming step.
        errs, filt = ev.validate_execution(
            self._wf(),
            [self._step("generate", arity="none"), self._step("consume", allow=["*.glb"])],
            self.OBJ)
        assert errs == []
        assert filt["generate"] == []

    def test_first_step_filter_mismatch_is_still_refused(self):
        errs, _ = ev.validate_execution(
            self._wf(),
            [self._step("convert", allow=["*.glb"]), self._step("consume", allow=["*.glb"])],
            self.OBJ)
        assert any("db:convert" in e and "exclude all selected inputs" in e for e in errs)
        assert not any("db:consume" in e for e in errs)

    def test_first_step_arity_is_still_refused(self):
        wf = self._wf(inputFileArity="multi")
        two_objs = [{"assetId": "a", "relativeFileKey": "/a.obj"},
                    {"assetId": "a", "relativeFileKey": "/b.obj"}]
        errs, _ = ev.validate_execution(
            wf, [self._step("convert", allow=["*.obj"], arity="one"),
                 self._step("consume", allow=["*.glb"])],
            two_objs)
        assert any("db:convert" in e and "single input file" in e for e in errs)

    def test_step_order_decides_which_step_is_judged(self):
        # The same two pipelines in the other order: the glb-only step is now first, and the obj
        # selection is refused against it.
        errs, _ = ev.validate_execution(
            self._wf(),
            [self._step("consume", allow=["*.glb"]), self._step("convert", allow=["*.obj"])],
            self.OBJ)
        assert any("db:consume" in e and "exclude all selected inputs" in e for e in errs)
        assert not any("db:convert" in e for e in errs)

    def test_later_step_whole_asset_scope_is_still_refused(self):
        # A whole-asset selection reaches the later step (an extension allow list cannot describe a
        # container), and that step declines whole-asset input.
        wf = self._wf(assetScope={"crossAssetAllowed": False, "singleAssetOnly": True,
                                  "wholeAssetAllowed": True, "folderAllowed": False})
        errs, _ = ev.validate_execution(
            wf,
            [self._step("convert", allow=["*.obj"], arity="multi",
                        assetScope={"wholeAssetAllowed": True}),
             self._step("consume", allow=["*.glb"], arity="multi",
                        assetScope={"wholeAssetAllowed": False})],
            [{"assetId": "a", "relativeFileKey": "/"}])
        assert any("db:consume" in e and "whole-asset" in e for e in errs)
        assert not any("db:convert" in e for e in errs)

    def test_later_step_asset_span_is_still_refused(self):
        # Selected files from two assets pass the later step's filters, so they reach it; the step
        # allows a single asset only.
        wf = self._wf(inputFileArity="multi",
                      assetScope={"crossAssetAllowed": True, "singleAssetOnly": False,
                                  "wholeAssetAllowed": False, "folderAllowed": False})
        errs, _ = ev.validate_execution(
            wf,
            [self._step("convert", allow=["*.obj"], arity="multi"),
             self._step("consume", arity="multi", assetScope={"singleAssetOnly": True})],
            [{"assetId": "a", "relativeFileKey": "/a.obj"},
             {"assetId": "b", "relativeFileKey": "/b.obj"}])
        assert any("db:consume" in e and "single asset" in e for e in errs)

    def test_disabled_or_archived_later_step_is_still_refused(self):
        disabled = self._step("consume", allow=["*.glb"])
        disabled["enabled"] = False
        archived = self._step("render", allow=["*.glb"])
        archived["archived"] = True
        errs, _ = ev.validate_execution(
            self._wf(), [self._step("convert", allow=["*.obj"]), disabled, archived], self.OBJ)
        assert any("db:consume" in e and "disabled" in e for e in errs)
        assert any("db:render" in e and "archived" in e for e in errs)
        # Neither is additionally blamed for its filters.
        assert not any("exclude all selected inputs" in e for e in errs)


# ==================== executionValidation: open-include semantics + aggregates ====================

@pytest.mark.unit
class TestOpenAllowList:
    @pytest.mark.parametrize("allow", [None, [], [""], ["  "], ["*"], ["**"], ["*.*"], ["/*"],
                                       ["*", "  "]])
    def test_open_forms(self, allow):
        # "No restriction" has several spellings; all must read identically so a '*' at one level of
        # the chain defers to the next rather than acting as a pattern.
        assert ev.is_open_allow_list(allow) is True

    @pytest.mark.parametrize("allow", [["*.glb"], [".glb"], ["/models/*"], ["*", "*.glb"]])
    def test_restrictive_forms(self, allow):
        # A list carrying ANY real pattern is a restriction, even alongside a '*'.
        assert ev.is_open_allow_list(allow) is False

    def test_star_allow_admits_a_container_selection(self):
        # A '*' allow list must behave exactly like an absent one. It previously did not: extension
        # patterns are stripped for container selections, so ['*.glb'] became empty (allow-all) while
        # ['*'] survived stripping and was matched — two spellings of "open" behaving differently.
        inputs = [{"relativeFileKey": "/"}, {"relativeFileKey": "/models/"}]
        assert ev.apply_input_file_filters(inputs, {"allow": ["*"]}) == inputs
        assert ev.apply_input_file_filters(inputs, {"allow": []}) == inputs

    def test_star_allow_does_not_restrict_files(self):
        inputs = [{"relativeFileKey": "/a.glb"}, {"relativeFileKey": "/b.txt"}]
        assert ev.apply_input_file_filters(inputs, {"allow": ["*"]}) == inputs

    def test_absent_filters_allow_everything(self):
        # The documented rule: no include entry == all files allowed, no excludes == no exclusions.
        inputs = [{"relativeFileKey": "/a.glb"}, {"relativeFileKey": "/b.txt"}]
        assert ev.apply_input_file_filters(inputs, None) == inputs
        assert ev.apply_input_file_filters(inputs, {}) == inputs


@pytest.mark.unit
class TestAggregateInputFileFilters:
    def _agg(self, wf_filters, pipeline_filters):
        return ev.aggregate_input_file_filters(
            {"inputFileFilters": wf_filters},
            [{"inputFileFilters": f} for f in pipeline_filters])

    def test_workflow_allow_wins_when_restrictive(self):
        # The workflow gate is the outer boundary, so nothing a pipeline allows can widen it.
        agg = self._agg({"allow": ["*.glb"]}, [{"allow": ["*.obj", "*.e57"]}])
        assert agg["allow"] == ["*.glb"]
        assert agg["source"] == "workflow"

    def test_open_workflow_allow_falls_through_to_the_pipelines(self):
        agg = self._agg({"allow": ["*"]}, [{"allow": ["*.glb"]}, {"allow": ["*.obj"]}])
        assert sorted(agg["allow"]) == ["*.glb", "*.obj"]
        assert agg["source"] == "pipelines"

    @pytest.mark.parametrize("wf_allow", [None, [], ["*"]])
    def test_every_open_spelling_falls_through(self, wf_allow):
        agg = self._agg({"allow": wf_allow}, [{"allow": ["*.glb"]}])
        assert agg["allow"] == ["*.glb"]

    def test_pipelines_are_unioned_not_intersected(self):
        # From a file's point of view the pipelines are alternatives: a file ANY pipeline can consume
        # is a file the workflow can do something with. Intersecting would report an empty (i.e.
        # nothing-allowed) restriction for a perfectly runnable workflow.
        agg = self._agg({}, [{"allow": ["*.glb"]}, {"allow": ["*.las"]}])
        assert sorted(agg["allow"]) == ["*.glb", "*.las"]

    def test_one_open_pipeline_makes_the_aggregate_open(self):
        # A pipeline accepting anything means the workflow as a whole restricts nothing, so reporting
        # the other pipeline's list would understate what can be selected.
        agg = self._agg({}, [{"allow": ["*.glb"]}, {"allow": []}])
        assert agg["allow"] == []

    def test_excludes_union_across_every_level(self):
        # An exclusion anywhere removes the file, so excludes accumulate regardless of the allow logic.
        agg = self._agg({"exclude": ["*.tmp"]},
                        [{"exclude": ["*.previewFile.*"]}, {"exclude": ["*.tmp"]}])
        assert sorted(agg["exclude"]) == ["*.previewFile.*", "*.tmp"]

    def test_duplicates_collapse_case_insensitively(self):
        # The matcher is case-insensitive, so '*.GLB' and '*.glb' are one restriction, not two.
        agg = self._agg({}, [{"allow": ["*.GLB"]}, {"allow": ["*.glb"]}])
        assert agg["allow"] == ["*.GLB"]

    def test_flags_that_template_overrides_are_not_included(self):
        # The contract that keeps callers from validating against this value: a template is chosen per
        # execution, so its overrides cannot be folded in here.
        agg = self._agg({"allow": ["*.glb"]}, [{}])
        assert agg["includesTemplateOverrides"] is False

    def test_no_filters_anywhere_reports_no_restriction(self):
        agg = ev.aggregate_input_file_filters({}, [{}, {}])
        assert agg["allow"] == []
        assert agg["exclude"] == []


@pytest.mark.unit
class TestAggregateMetadataInputs:
    def test_requires_both_the_workflow_gate_and_a_pipeline_asking(self):
        # The gate loads the payload; a pipeline that does not ask is not handed it. So a type is
        # actually delivered only when both are true. A pipeline declines a type EXPLICITLY — an
        # omitted key defaults on — so fileMetadata is false on the pipeline rather than absent.
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"assetMetadata": True, "fileMetadata": True}},
            [{"metadataInputs": {"assetMetadata": True, "fileMetadata": False}}])
        assert agg["assetMetadata"] is True
        assert agg["fileMetadata"] is False   # gate on, but nothing asks for it
        assert agg["gatedOffByWorkflow"] == []

    def test_names_a_type_the_workflow_gates_off(self):
        # Worth surfacing: the pipeline declared it uses this and will run without it.
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"assetMetadata": False}},
            [{"metadataInputs": {"assetMetadata": True}}])
        assert agg["assetMetadata"] is False
        assert agg["gatedOffByWorkflow"] == ["assetMetadata"]

    def test_any_pipeline_asking_is_enough(self):
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"fileAttributes": True}},
            [{"metadataInputs": {}}, {"metadataInputs": {"fileAttributes": True}}])
        assert agg["fileAttributes"] is True

    def test_carries_the_template_override_caveat(self):
        agg = ev.aggregate_metadata_inputs({}, [{}])
        assert agg["includesTemplateOverrides"] is False

    def test_a_map_omitting_databaseMetadata_reports_it_on(self):
        # A systemConfig stored without the key carries the builder default (True), which is what the
        # execute path collects on — reporting it off would contradict the run.
        stored = {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True}
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": stored}, [{"metadataInputs": {"databaseMetadata": True}}])
        assert agg["databaseMetadata"] is True
        assert agg["gatedOffByWorkflow"] == []
        # A pipeline map omitting the key likewise asks for it.
        assert ev.aggregate_metadata_inputs(
            {"metadataInputs": stored}, [{"metadataInputs": {}}])["databaseMetadata"] is True

    def test_every_key_a_map_omits_reports_on(self):
        # The same rule for all four, not databaseMetadata alone: the record builders default every
        # key to True, and the API stores systemConfig wholesale, so a client that sends only the
        # keys it cares about persists a partial map whose omissions still collect. Reporting an
        # omitted key off would tell the caller a run gathers less than it does.
        agg = ev.aggregate_metadata_inputs({"metadataInputs": {}}, [{"metadataInputs": {}}])
        for key in ("assetMetadata", "fileMetadata", "fileAttributes", "databaseMetadata"):
            assert agg[key] is True, key
        assert agg["gatedOffByWorkflow"] == []
        # An entirely absent metadataInputs block reads the same way.
        assert ev.aggregate_metadata_inputs({}, [{}])["fileAttributes"] is True

    def test_an_explicit_false_databaseMetadata_still_gates_off(self):
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"databaseMetadata": False}},
            [{"metadataInputs": {"databaseMetadata": True}}])
        assert agg["databaseMetadata"] is False
        assert agg["gatedOffByWorkflow"] == ["databaseMetadata"]

    def test_gating_a_type_off_takes_an_explicit_false(self):
        # An omitted key is not a gate: only `False` suppresses a type the pipeline asks for. This is
        # the direction that matters for a partial map — an omission that read as a gate would report
        # every type suppressed while the execute path went on collecting them.
        assert ev.aggregate_metadata_inputs(
            {"metadataInputs": {}},
            [{"metadataInputs": {"assetMetadata": True}}])["gatedOffByWorkflow"] == []
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"assetMetadata": False}},
            [{"metadataInputs": {"assetMetadata": True}}])
        assert agg["assetMetadata"] is False
        assert agg["gatedOffByWorkflow"] == ["assetMetadata"]


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

    def test_a_workflow_map_omitting_databaseMetadata_does_not_warn(self):
        # The key is absent, not off, so the run does collect it — warning would misdescribe the save.
        wf = {"metadataInputs": {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True},
              "inputFileArity": "one", "inputFileFilters": {}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"metadataInputs": {"databaseMetadata": True}}}]
        errs, warns = ev.validate_workflow_save(wf, pipes)
        assert errs == []
        assert not [w for w in warns if "databaseMetadata" in w]

    def test_an_explicitly_off_databaseMetadata_still_warns(self):
        wf = {"metadataInputs": {"databaseMetadata": False},
              "inputFileArity": "one", "inputFileFilters": {}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"metadataInputs": {"databaseMetadata": True}}}]
        errs, warns = ev.validate_workflow_save(wf, pipes)
        assert errs == []
        assert any("databaseMetadata" in w for w in warns)

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

    def test_save_error_label_includes_database(self):
        wf = {"inputFileArity": "one", "metadataInputs": {}, "inputFileFilters": {}}
        pipes = [{"pipelineId": "convert", "pipelineDatabaseId": "GLOBAL", "enabled": True,
                  "archived": True, "systemConfig": {}}]
        errs, _ = ev.validate_workflow_save(wf, pipes)
        assert any("GLOBAL:convert" in e for e in errs)

    def test_equivalent_extension_forms_do_not_warn(self):
        # '.glb' and '*.glb' are the same filter to the matcher, so they are not shadowing.
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"allow": [".glb"]}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"inputFileFilters": {"allow": ["*.glb"]}}}]
        _, warns = ev.validate_workflow_save(wf, pipes)
        assert not any("may exclude everything" in w for w in warns)

    def test_disjoint_extensions_still_warn(self):
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"allow": ["*.glb"]}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"inputFileFilters": {"allow": ["*.obj"]}}}]
        _, warns = ev.validate_workflow_save(wf, pipes)
        assert any("may exclude everything" in w for w in warns)

    def test_wildcard_allow_does_not_warn(self):
        wf = {"inputFileArity": "one", "metadataInputs": {}, "inputFileFilters": {"allow": ["*"]}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"inputFileFilters": {"allow": ["*.glb"]}}}]
        _, warns = ev.validate_workflow_save(wf, pipes)
        assert not any("may exclude everything" in w for w in warns)

    def test_workflow_exclude_of_a_pipelines_only_type_warns(self):
        # A workflow can starve a pipeline through its EXCLUDE list even when the allow-lists agree
        # perfectly, because exclude is applied second. Check (1) sees an overlap and stays quiet.
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"allow": ["*.glb", "*.obj"], "exclude": ["*.glb"]}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"inputFileFilters": {"allow": ["*.glb"]}}}]
        _, warns = ev.validate_workflow_save(wf, pipes)
        assert not any("may exclude everything" in w for w in warns)
        assert any("no accepted input type" in w for w in warns)

    def test_workflow_exclude_of_one_of_several_types_names_what_remains(self):
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"exclude": [".glb"]}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"inputFileFilters": {"allow": ["*.glb", "*.obj"]}}}]
        _, warns = ev.validate_workflow_save(wf, pipes)
        # The equivalent extension forms ('.glb' vs '*.glb') must still be recognised as the same type.
        assert any("only *.obj" in w for w in warns)

    def test_workflow_exclude_glob_does_not_warn(self):
        # A wildcard exclude cannot be resolved pattern-to-pattern, so it must not warn — a false
        # positive on every glob-filtered workflow would train users to ignore the panel.
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"exclude": ["*.previewFile.*"]}}
        pipes = [{"pipelineId": "p", "enabled": True, "archived": False,
                  "systemConfig": {"inputFileFilters": {"allow": ["*.glb"]}}}]
        _, warns = ev.validate_workflow_save(wf, pipes)
        assert not any("exclude" in w for w in warns)

    # #715: the workflow's filters bound the run's SELECTION only. From the second step on, a step
    # also receives earlier steps' output, which the workflow's filters never narrow, so the two
    # filter-shadowing checks describe the first step alone.
    @staticmethod
    def _chain(first_allow, later_allow):
        return [{"pipelineId": "convert", "pipelineDatabaseId": "db", "enabled": True,
                 "archived": False, "systemConfig": {"inputFileFilters": {"allow": first_allow}}},
                {"pipelineId": "consume", "pipelineDatabaseId": "db", "enabled": True,
                 "archived": False, "systemConfig": {"inputFileFilters": {"allow": later_allow}}}]

    def test_workflow_allow_list_does_not_starve_a_later_step(self):
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"allow": ["*.obj"]}}
        _, warns = ev.validate_workflow_save(wf, self._chain(["*.obj"], ["*.glb"]))
        assert warns == []

    def test_workflow_exclude_does_not_starve_a_later_step(self):
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"exclude": ["*.glb"]}}
        _, warns = ev.validate_workflow_save(wf, self._chain(["*.obj"], ["*.glb"]))
        assert warns == []

    def test_workflow_filters_still_shadow_the_first_step(self):
        wf = {"inputFileArity": "one", "metadataInputs": {},
              "inputFileFilters": {"allow": ["*.glb"]}}
        _, warns = ev.validate_workflow_save(wf, self._chain(["*.obj"], ["*.glb"]))
        assert any("db:convert" in w and "may exclude everything" in w for w in warns)
        assert not any("db:consume" in w for w in warns)

    def test_trigger_default_undefaulted_warns(self):
        wf = {"inputFileArity": "one", "metadataInputs": {}, "inputFileFilters": {}}
        errs, warns = ev.validate_workflow_save(
            wf, [], trigger={"undefaultedRequiredTagsByTemplateId": {"t1": ["prompt"]}})
        assert any("auto-trigger would fail" in w for w in warns)


# ============================ metadata-format v2 ============================

@pytest.mark.unit
class TestDefaultOutputPathExtensionValidator:
    """systemConfig.defaultOutputFileBaseExecutionPathExtension shape rules.

    It is stored UNRESOLVED, so `{{tag}}` text is legal and only the rules that survive templating are
    enforced here; the RENDERED value is re-checked at launch.
    """

    def _validate(self, value):
        from backend.backend.models.workflows import _validate_default_output_path_extension
        _validate_default_output_path_extension(
            {"defaultOutputFileBaseExecutionPathExtension": value})

    def test_template_tags_are_accepted(self):
        for value in ("/{{executionId}}/", "{{jobName}}", "/runs/{{executionId}}/out/"):
            self._validate(value)

    def test_plain_paths_and_absent_values_are_accepted(self):
        for value in ("/YOLO/", "YOLO", "/", "", None):
            self._validate(value)
        from backend.backend.models.workflows import _validate_default_output_path_extension
        _validate_default_output_path_extension({})          # key absent
        _validate_default_output_path_extension(None)        # no systemConfig at all

    def test_traversal_is_rejected(self):
        with pytest.raises(ValueError, match="must not contain '\.\.'"):
            self._validate("/../escape/")

    def test_backslashes_are_rejected(self):
        with pytest.raises(ValueError, match="backslashes"):
            self._validate("\windows\path")

    def test_a_non_string_is_rejected(self):
        for value in (123, True, ["/a/"], {"a": 1}):
            with pytest.raises(ValueError, match="must be a string"):
                self._validate(value)

    def test_an_oversized_value_is_rejected(self):
        self._validate("/" + "a" * 1022 + "/")               # exactly 1024
        with pytest.raises(ValueError, match="1024 characters"):
            self._validate("/" + "a" * 1024 + "/")


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
