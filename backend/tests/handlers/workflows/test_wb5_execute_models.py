# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for WB5.1: the asset-less execute request models (models/executionsV2.py) and the
pure template-resolution 5-case contract (common/workflows/templateResolution.py)."""

import pytest

from backend.backend.models import executions as m
from backend.backend.common.workflows import templateResolution as tr


# ============================ execute request models ============================

@pytest.mark.unit
class TestExecuteModels:
    def test_execute_request_defaults(self):
        req = m.ExecuteWorkflowRequestV2Model(
            inputFiles=[{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/x.glb"}])
        assert req.triggerType == "manual"
        assert req.inputFiles[0].relativeFileKey == "/x.glb"
        assert m.TRIGGER_TYPE_TO_STORED["manual"] == "Manual"
        assert m.TRIGGER_TYPE_TO_STORED["fileUpload"] == "File-Upload"

    def test_blank_trigger_normalizes_to_manual(self):
        assert m.ExecuteWorkflowRequestV2Model(triggerType="").triggerType == "manual"
        assert m.ExecuteWorkflowRequestV2Model(triggerType=None).triggerType == "manual"

    def test_bad_trigger_rejected(self):
        with pytest.raises(Exception):
            m.ExecuteWorkflowRequestV2Model(triggerType="bogus")

    def test_zero_input_files_allowed(self):
        # Arity is enforced by the cross-entity validator, not the request model.
        req = m.ExecuteWorkflowRequestV2Model(inputFiles=[])
        assert req.inputFiles == []

    def test_output_asset_id_uses_asset_id_rule_not_strict_id(self):
        # An assetId valid under ASSET_ID (dots/spaces) but invalid under the strict ID pattern must
        # be accepted for outputAssetId — else a legitimately-named output asset is rejected on both
        # execute (override) and re-run.
        req = m.ExecuteWorkflowRequestV2Model(
            outputAssetId="my.model.v2", outputDatabaseId="db1",
            inputFiles=[{"databaseId": "db1", "assetId": "my.model.v2", "relativeFileKey": "/x.glb"}])
        assert req.outputAssetId == "my.model.v2"
        assert req.inputFiles[0].assetId == "my.model.v2"

    def test_input_file_ids_validated(self):
        # A blank/garbage databaseId or assetId is rejected by the input-file model.
        with pytest.raises(Exception):
            m.ExecuteWorkflowRequestV2Model(
                inputFiles=[{"databaseId": "x", "assetId": "a1", "relativeFileKey": "/f"}])  # db too short for ID

    def test_relative_file_key_must_be_asset_relative(self):
        # relativeFileKey must begin with "/" (asset-relative). An absolute-looking key is rejected.
        with pytest.raises(Exception):
            m.ExecuteWorkflowRequestV2Model(
                inputFiles=[{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "no-leading-slash"}])

    def test_relative_file_key_folder_and_root_forms_ok(self):
        # "/" (whole asset), "/folder/" (folder), "/folder/file" (file) are all valid.
        for key in ("/", "/folder/", "/folder/file.glb"):
            req = m.ExecuteWorkflowRequestV2Model(
                inputFiles=[{"databaseId": "db1", "assetId": "a1", "relativeFileKey": key}])
            assert req.inputFiles[0].relativeFileKey == key

    def test_permanent_delete_guard_fires_on_omission(self):
        # always=True: the confirm guard fires even when confirmDelete is omitted/false.
        for kwargs in ({}, {"confirmDelete": False}):
            with pytest.raises(Exception):
                m.PermanentDeleteRequestModel(**kwargs)
        assert m.PermanentDeleteRequestModel(confirmDelete=True).confirmDelete is True


# ============================ template resolution 5-case ============================

@pytest.mark.unit
class TestTemplateResolution:
    def test_case1_template_plus_tags_leaves_system_tags(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": "p: {{prompt}} b: {{outputBucket}}", "configFormat": "yaml"},
            [{"tagKey": "prompt", "type": "string", "required": True}],
            {"templateId": "t1", "templateTags": [{"key": "prompt", "value": "hello"}]})
        assert errs == []
        assert "p: hello" in res["renderedConfig"]
        assert "{{outputBucket}}" in res["renderedConfig"]  # system tag left for launch renderer
        assert res["templateId"] == "t1" and res["customTemplateOverrideUsed"] is False

    def test_case1_missing_required_tag_errors(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": "p: {{prompt}}", "configFormat": "yaml"},
            [{"tagKey": "prompt", "type": "string", "required": True}],
            {"templateId": "t1", "templateTags": []})
        assert res is None and any("required" in e for e in errs)

    def test_case2_override_with_template_requires_allow(self):
        # allowCustomTemplateOverride False -> override rejected even with a templateId.
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False, "allowCustomTemplateOverride": False},
            {"templateId": "t1", "configBody": "x", "configFormat": "json"},
            [], {"templateId": "t1", "customTemplateOverride": "y: {{p}}"})
        assert res is None and any("does not allow" in e for e in errs)

    def test_case2_override_with_template_allowed(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False, "allowCustomTemplateOverride": True},
            # configFormat is yaml: these bodies are YAML, and a json-format override is now checked
            # against the same parse gate the save path applies, which "over: {{p}}" fails (as would
            # the stored "stored" — a template declaring json could never have held either).
            {"templateId": "t1", "configBody": "stored", "configFormat": "yaml"},
            [{"tagKey": "p", "type": "string"}],
            {"templateId": "t1", "customTemplateOverride": "over: {{p}}",
             "templateTags": [{"key": "p", "value": "v"}]})
        assert errs == [] and res["renderedConfig"] == "over: v"
        assert res["customTemplateOverrideUsed"] is True

    def test_case2_override_allowed_by_template_allow_custom_edit(self):
        # The pipeline does NOT allow overrides, but the chosen template allows custom edit, so an
        # edited body is accepted (the unified "Customize configuration" grant).
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": True, "allowCustomTemplateOverride": False},
            {"templateId": "t1", "configBody": "stored", "configFormat": "yaml",
             "allowCustomEdit": True},
            [], {"templateId": "t1", "customTemplateOverride": "edited: 1"})
        assert errs == [] and res["renderedConfig"] == "edited: 1"
        assert res["customTemplateOverrideUsed"] is True

    def test_case3_override_no_template(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False, "allowCustomTemplateOverride": True},
            None, None,
            {"customTemplateOverride": "x: {{n}}", "templateTags": [{"key": "n", "value": 5}]})
        assert errs == [] and res["renderedConfig"] == "x: 5"  # int -> JSON literal
        assert res["templateId"] == "" and res["customTemplateOverrideUsed"] is True

    def test_case3_blocked_when_require_template(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": True, "allowCustomTemplateOverride": True},
            None, None, {"customTemplateOverride": "x"})
        assert res is None and any("requires a template" in e for e in errs)

    def test_case3_reserved_tag_rejected(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False, "allowCustomTemplateOverride": True},
            None, None,
            {"customTemplateOverride": "x", "templateTags": [{"key": "executionId", "value": "e"}]})
        assert res is None and any("reserved" in e for e in errs)

    def test_case4_no_template_no_override(self):
        errs, res = tr.resolve_pipeline_config({"requireTemplate": False}, None, None, {})
        assert errs == [] and res["renderedConfig"] == "" and res["templateId"] == ""

    def test_case4_require_template_errors(self):
        errs, res = tr.resolve_pipeline_config({"requireTemplate": True}, None, None, {})
        assert res is None and any("requires a template" in e for e in errs)

    def test_unmatched_user_tag_errors(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": "{{missingUserTag}}", "configFormat": "raw"},
            [], {"templateId": "t1"})
        assert res is None and any("unmatched" in e for e in errs)

    def test_missing_template_row_errors(self):
        errs, res = tr.resolve_pipeline_config(
            {"requireTemplate": False}, None, [], {"templateId": "nope"})
        assert res is None and any("not found" in e for e in errs)
