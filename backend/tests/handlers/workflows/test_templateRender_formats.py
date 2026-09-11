# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for config-format-aware tag escaping, the pipelineName system tag, blank optional tag
materialization, and the dynamic-metadata prefix rejection.

Covers common/workflows/templateRender.py, templateResolution.py and templateTagSchema.py."""

import json

import pytest

from backend.backend.common.workflows import templateRender as tr
from backend.backend.common.workflows import templateResolution as tres
from backend.backend.common.workflows import templateTagSchema as ts


def _manifest():
    return {
        "schemaVersion": 1,
        "inputFiles": [{"relativePath": "/a & b.e57", "databaseId": "db1", "assetId": "xidA",
                        "bucket": "abkt", "key": "xidA/a & b.e57", "versionId": "v1"}],
        "outputs": {"bucket": "abkt", "files": "", "previews": "", "metadata": "", "results": ""},
        "outputTarget": {"locationType": "asset", "assetId": "xidOut", "databaseId": "dbOut",
                         "assetRootS3Key": "xidOut/", "fileBaseExecutionPathExtension": "/"},
        "auxBucket": "auxbkt",
        "auxTempPrefix": "pipelines/p1/E1/",
        "systemConfig": {},
    }


def _execution(**overrides):
    execution = {
        "executionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wdb1",
        "pipelineExecutionId": "P1", "pipelineId": "myPipe", "pipelineDatabaseId": "pdb1",
        "jobName": "abcde-myPipe", "triggerType": "Manual", "executingUserName": "user@x",
        "executionStartTimestamp": "2026-07-09T00:00:00Z",
    }
    execution.update(overrides)
    return execution


# ============================ pipelineName system tag ============================

@pytest.mark.unit
class TestPipelineNameTag:
    def test_pipeline_name_uses_the_name_not_the_id(self):
        ctx = tr.build_template_context({}, _execution(pipelineName="Point Cloud Decimation"))
        assert ctx["pipelineName"] == ("scalar", "Point Cloud Decimation")
        assert ctx["pipelineId"] == ("scalar", "myPipe")

    def test_pipeline_name_falls_back_to_the_id(self):
        ctx = tr.build_template_context({}, _execution())
        assert ctx["pipelineName"] == ("scalar", "myPipe")

    def test_rendered_body_carries_the_name(self):
        out = json.loads(tr.render_config(
            '{"label": "{{pipelineName}}", "id": "{{pipelineId}}"}',
            _manifest(), _execution(pipelineName="Nice Name")))
        assert out == {"label": "Nice Name", "id": "myPipe"}


# ============================ format-aware scalar escaping ============================

@pytest.mark.unit
class TestEscapeScalar:
    def test_json_escapes_quotes_and_newlines(self):
        assert tr.escape_scalar('a"b', "json") == 'a\\"b'
        assert tr.escape_scalar("a\nb", "json") == "a\\nb"

    def test_yaml_openjd_raw_use_the_json_string_escape(self):
        for fmt in ("yaml", "openjd", "raw"):
            assert tr.escape_scalar('a"b', fmt) == 'a\\"b', fmt

    def test_xml_escapes_markup_characters(self):
        assert tr.escape_scalar("a & b</arg>", "xml") == "a &amp; b&lt;/arg&gt;"
        assert tr.escape_scalar('q"x\'y', "xml") == "q&quot;x&apos;y"

    def test_xml_format_is_case_insensitive(self):
        assert tr.escape_scalar("a & b", "XML") == "a &amp; b"

    def test_none_value_is_empty(self):
        assert tr.escape_scalar(None, "xml") == "" and tr.escape_scalar(None, "json") == ""


@pytest.mark.unit
class TestRenderConfigFormat:
    def test_xml_body_escapes_a_system_tag_value(self):
        rendered = tr.render_config(
            "<key>{{firstAssetFileKey}}</key>", _manifest(), _execution(), config_format="xml")
        assert rendered == "<key>xidA/a &amp; b.e57</key>"

    def test_json_body_default_still_json_escapes(self):
        out = json.loads(tr.render_config(
            '{"k": "{{firstAssetFileKey}}"}', _manifest(), _execution()))
        assert out["k"] == "xidA/a & b.e57"


# ============================ user-tag escaping per template format ============================

@pytest.mark.unit
class TestUserTagEscaping:
    def test_xml_template_escapes_a_user_tag_value(self):
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": "<arg>{{userValue}}</arg>", "configFormat": "xml"},
            [{"tagKey": "userValue", "type": "string"}],
            {"templateId": "t1",
             "templateTags": [{"key": "userValue", "value": "a & b</arg><arg>--rm"}]})
        assert errors == []
        assert result["renderedConfig"] == "<arg>a &amp; b&lt;/arg&gt;&lt;arg&gt;--rm</arg>"

    def test_json_template_json_escapes_a_user_tag_value(self):
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": '{"v": "{{userValue}}"}', "configFormat": "json"},
            [{"tagKey": "userValue", "type": "string"}],
            {"templateId": "t1", "templateTags": [{"key": "userValue", "value": 'say "hi"'}]})
        assert errors == []
        assert json.loads(result["renderedConfig"])["v"] == 'say "hi"'

    def test_user_tag_value_carrying_a_placeholder_is_rejected(self):
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": '{"v": "{{userValue}}"}', "configFormat": "json"},
            [{"tagKey": "userValue", "type": "string"}],
            {"templateId": "t1",
             "templateTags": [{"key": "userValue", "value": "{{firstAssetFileS3Uri}}"}]})
        assert result is None
        assert any("template placeholder" in e for e in errors)

    def test_user_tag_list_value_carrying_a_placeholder_is_rejected(self):
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": '{"v": {{items}}}', "configFormat": "json"},
            [{"tagKey": "items", "type": "string-list"}],
            {"templateId": "t1", "templateTags": [{"key": "items", "value": ["{{executionId}}"]}]})
        assert result is None
        assert any("template placeholder" in e for e in errors)


# ============================ blank optional tag materialization ============================

@pytest.mark.unit
class TestBlankOptionalTags:
    def test_blank_optional_string_materializes_empty(self):
        errors, filled = ts.validate_tags([{"tagKey": "suffix", "type": "string"}], {"suffix": ""})
        assert errors == [] and filled["suffix"] == ""

    def test_omitted_optional_string_materializes_empty(self):
        errors, filled = ts.validate_tags([{"tagKey": "suffix", "type": "string"}], {})
        assert errors == [] and filled["suffix"] == ""

    def test_blank_optional_string_list_materializes_empty_list(self):
        errors, filled = ts.validate_tags([{"tagKey": "items", "type": "string-list"}], {})
        assert errors == [] and filled["items"] == []

    def test_blank_optional_enum_materializes_empty(self):
        errors, filled = ts.validate_tags(
            [{"tagKey": "mode", "type": "enum", "enumValues": ["a", "b"]}], {})
        assert errors == [] and filled["mode"] == ""

    def test_numeric_and_boolean_optionals_stay_absent(self):
        errors, filled = ts.validate_tags(
            [{"tagKey": "n", "type": "integer"}, {"tagKey": "r", "type": "number"},
             {"tagKey": "f", "type": "boolean"}], {})
        assert errors == [] and filled == {}

    def test_required_blank_still_errors(self):
        errors, filled = ts.validate_tags(
            [{"tagKey": "p", "type": "string", "required": True}], {"p": ""})
        assert any("required" in e for e in errors) and "p" not in filled

    def test_blank_optional_tag_renders_empty_instead_of_erroring(self):
        schema = [{"tagKey": "suffix", "type": "string"}]
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": '{"name": "out_{{suffix}}"}', "configFormat": "json"},
            schema, {"templateId": "t1", "templateTags": [{"key": "suffix", "value": ""}]})
        assert errors == []
        assert json.loads(result["renderedConfig"])["name"] == "out_"


# ============================ dynamic metadata prefix ============================

@pytest.mark.unit
class TestDynamicMetadataPrefixRejected:
    def test_metadata_prefixed_body_tag_errors_at_resolution(self):
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": '{"v": "{{metadata_partNumber}}"}',
             "configFormat": "json"},
            [], {"templateId": "t1"})
        assert result is None
        assert any("metadata_partNumber" in e and "metadata_" in e for e in errors)

    def test_metadata_content_system_tags_still_pass_through(self):
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False},
            {"templateId": "t1", "configBody": '{"m": {{assetMetadataObject}}}',
             "configFormat": "json"},
            [], {"templateId": "t1"})
        assert errors == []
        assert result["renderedConfig"] == '{"m": {{assetMetadataObject}}}'

    def test_metadata_prefixed_tag_in_an_override_errors(self):
        errors, result = tres.resolve_pipeline_config(
            {"requireTemplate": False, "allowCustomTemplateOverride": True},
            None, None, {"customTemplateOverride": "v: {{metadata_location}}"})
        assert result is None and any("metadata_location" in e for e in errors)
