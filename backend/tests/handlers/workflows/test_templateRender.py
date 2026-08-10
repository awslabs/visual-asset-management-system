# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the input-configuration template-tag renderer (common/workflows/templateRender)."""

import json

import pytest

from backend.backend.common.workflows import executionRecords as er
from backend.backend.common.workflows import templateRender as tr


def _manifest(with_inputs=True):
    """A per-pipeline manifest envelope with (or without) input files."""
    input_files = []
    if with_inputs:
        input_files = [
            {"relativePath": "/test/pump.e57", "databaseId": "db1", "assetId": "xidA",
             "assetRootS3Key": "xidA/", "auxPreviewPrefix": "db1/xidA/test/pump.e57/preview",
             "bucket": "abkt", "key": "xidA/test/pump.e57", "versionId": "v3"},
            {"relativePath": "/scan.las", "databaseId": "db1", "assetId": "xidA",
             "assetRootS3Key": "xidA/", "auxPreviewPrefix": "db1/xidA/scan.las/preview",
             "bucket": "abkt", "key": "xidA/scan.las", "versionId": ""},
        ]
    return {
        "schemaVersion": 1,
        "inputFiles": input_files,
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "outputs": {"bucket": "abkt", "files": "pipelines/p1/JOB/output/E1/files/",
                    "previews": "pipelines/p1/JOB/output/E1/previews/",
                    "metadata": "pipelines/p1/JOB/output/E1/metadata/",
                    "results": "pipelines/p1/JOB/output/E1/results/"},
        "outputTarget": {"locationType": "asset", "assetId": "xidOut", "databaseId": "dbOut",
                         "assetRootS3Key": "xidOut/", "fileBaseExecutionPathExtension": "/"},
        "auxBucket": "auxbkt",
        "auxTempPrefix": "pipelines/p1/E1/",
        "auxPreviewPipelineSuffix": "/PotreeViewer",
        "systemConfig": {"orchestrationBusArn": "arn:bus",
                         "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
    }


def _execution():
    return {
        "executionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wdb1",
        "pipelineExecutionId": "P1", "pipelineId": "myPipe", "pipelineDatabaseId": "pdb1",
        "jobName": "abcde-myPipe", "triggerType": "Manual", "executingUserName": "user@x",
        "executionStartTimestamp": "2026-07-09T00:00:00Z",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
    }


@pytest.mark.unit
class TestScalarTags:
    def test_no_tags_returns_unchanged(self):
        text = '{"quality": "high"}'
        assert tr.render_config(text, _manifest(), _execution()) == text

    def test_first_asset_file_scalars(self):
        text = ('{"db": "{{firstAssetFileDatabaseId}}", "asset": "{{firstAssetFileAssetId}}", '
                '"bucket": "{{firstAssetFileAssetBucket}}", "root": "{{firstAssetFileAssetRootS3Key}}", '
                '"key": "{{firstAssetFileKey}}", "uri": "{{firstAssetFileS3Uri}}", '
                '"name": "{{firstAssetFileFileName}}", "stem": "{{firstAssetFileFileNameNoExt}}", '
                '"ext": "{{firstAssetFileFileExtension}}"}')
        out = json.loads(tr.render_config(text, _manifest(), _execution()))
        assert out["db"] == "db1" and out["asset"] == "xidA" and out["bucket"] == "abkt"
        assert out["root"] == "xidA/" and out["key"] == "xidA/test/pump.e57"
        assert out["uri"] == "s3://abkt/xidA/test/pump.e57"
        assert out["name"] == "pump.e57" and out["stem"] == "pump" and out["ext"] == ".e57"

    def test_first_asset_file_aux_preview_uri_includes_suffix(self):
        # auxBucket + the file's auxPreviewPrefix + the manifest's auxPreviewPipelineSuffix.
        out = json.loads(tr.render_config(
            '{"p": "{{firstAssetFileAuxPreviewS3Uri}}"}', _manifest(), _execution()))
        assert out["p"] == "s3://auxbkt/db1/xidA/test/pump.e57/preview/PotreeViewer"

    def test_execution_and_pipeline_identity(self):
        text = ('{"e": "{{executionId}}", "w": "{{workflowId}}", "pe": "{{pipelineExecutionId}}", '
                '"pn": "{{pipelineName}}", "job": "{{jobName}}", "trig": "{{triggerType}}"}')
        out = json.loads(tr.render_config(text, _manifest(), _execution()))
        assert out == {"e": "E1", "w": "wf1", "pe": "P1", "pn": "myPipe",
                       "job": "abcde-myPipe", "trig": "Manual"}

    def test_output_and_aux_locations(self):
        text = ('{"of": "{{outputFilesS3Uri}}", "ob": "{{outputBucket}}", '
                '"at": "{{auxTempS3Uri}}", "ota": "{{outputTargetAssetId}}", '
                '"otd": "{{outputTargetDatabaseId}}"}')
        out = json.loads(tr.render_config(text, _manifest(), _execution()))
        assert out["of"] == "s3://abkt/pipelines/p1/JOB/output/E1/files/"
        assert out["ob"] == "abkt" and out["at"] == "s3://auxbkt/pipelines/p1/E1/"
        assert out["ota"] == "xidOut" and out["otd"] == "dbOut"

    def test_timestamp_tags_injected(self):
        import datetime
        now = datetime.datetime(2026, 7, 9, 14, 3, 22, tzinfo=datetime.timezone.utc)
        out = json.loads(tr.render_config(
            '{"ts": "{{jobStartTimestamp}}", "d": "{{jobStartDate}}"}',
            _manifest(), _execution(), now=now))
        assert out["ts"] == "2026-07-09T14:03:22Z" and out["d"] == "2026-07-09"

    def test_scalar_json_escaping(self):
        # A value containing a quote must be escaped so the result stays valid JSON.
        m = _manifest()
        m["inputFiles"][0]["key"] = 'xidA/wei"rd.e57'
        out = json.loads(tr.render_config('{"k": "{{firstAssetFileKey}}"}', m, _execution()))
        assert out["k"] == 'xidA/wei"rd.e57'


@pytest.mark.unit
class TestArrayTags:
    def test_key_and_id_arrays(self):
        text = ('{"keys": {{assetFileKeyArray}}, "assets": {{assetFileAssetIdArray}}, '
                '"uAssets": {{assetFileUniqueAssetIdArray}}, "count": {{assetFileCount}}}')
        out = json.loads(tr.render_config(text, _manifest(), _execution()))
        assert out["keys"] == ["xidA/test/pump.e57", "xidA/scan.las"]
        assert out["assets"] == ["xidA", "xidA"]
        assert out["uAssets"] == ["xidA"]
        assert out["count"] == 2

    def test_object_array(self):
        out = json.loads(tr.render_config('{"files": {{assetFileObjectArray}}}',
                                          _manifest(), _execution()))
        assert len(out["files"]) == 2 and out["files"][0]["assetId"] == "xidA"

    def test_s3_uri_array(self):
        out = json.loads(tr.render_config('{"u": {{assetFileS3UriArray}}}', _manifest(), _execution()))
        assert out["u"] == ["s3://abkt/xidA/test/pump.e57", "s3://abkt/xidA/scan.las"]


@pytest.mark.unit
class TestNoInputFiles:
    def test_scalars_empty_arrays_empty(self):
        m = _manifest(with_inputs=False)
        text = ('{"asset": "{{firstAssetFileAssetId}}", "keys": {{assetFileKeyArray}}, '
                '"count": {{assetFileCount}}, "ota": "{{outputTargetAssetId}}"}')
        out = json.loads(tr.render_config(text, m, _execution()))
        # First-file scalars empty, arrays empty, count 0 — never errors.
        assert out["asset"] == "" and out["keys"] == [] and out["count"] == 0
        # Output-target identity still resolves (the no-input key-lookup basis).
        assert out["ota"] == "xidOut"


@pytest.mark.unit
class TestMetadataTags:
    def _payload(self):
        return {"VAMS": {"assetData": {"assetName": "Pump"},
                         "assetMetadata": {"MODEL": "x"},
                         "fileMetadata": {"k": "v"},
                         "fileAttributes": {"a": "b"}}}

    def test_metadata_object_lazy_loaded_only_when_used(self):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return self._payload()

        # No metadata tag -> loader not called.
        tr.render_config('{"q": "{{executionId}}"}', _manifest(), _execution(), metadata_loader=loader)
        assert calls["n"] == 0

        # Metadata tag present -> loader called once.
        out = json.loads(tr.render_config(
            '{"m": {{assetMetadataObject}}, "a": {{assetDataObject}}}',
            _manifest(), _execution(), metadata_loader=loader))
        assert calls["n"] == 1
        assert out["m"] == {"MODEL": "x"} and out["a"] == {"assetName": "Pump"}

    def test_full_metadata_object(self):
        out = json.loads(tr.render_config(
            '{"all": {{inputMetadataObject}}}', _manifest(), _execution(),
            metadata_loader=self._payload))
        assert out["all"]["VAMS"]["assetData"]["assetName"] == "Pump"

    def test_missing_metadata_path_yields_empty_object(self):
        out = json.loads(tr.render_config(
            '{"m": {{assetMetadataObject}}}', _manifest(), _execution(),
            metadata_loader=lambda: {}))
        assert out["m"] == {}

    def test_database_metadata_object_renders_database_section(self):
        # The projected legacy view a metadata_loader hands the renderer carries the entry the grouped
        # envelope's top-level 'databases' list holds for the projected databaseId as its
        # databaseMetadata scope.
        envelope = er.build_grouped_metadata_envelope(
            [er.build_metadata_asset_group("db1", "xidA", {"assetName": "Pump"},
                                           [er.build_metadata_file_record("/", {"MODEL": "x"})])],
            databases=[er.build_metadata_database_group("db1", {"SITE": "plant-7"})])
        out = json.loads(tr.render_config(
            '{"d": {{databaseMetadataObject}}, "m": {{assetMetadataObject}}}',
            _manifest(), _execution(),
            metadata_loader=lambda: er.to_legacy_vams_view(envelope, "db1", "xidA", "/")))
        assert out["d"] == {"SITE": "plant-7"}
        assert out["m"] == {"MODEL": "x"}

    def test_database_metadata_object_empty_without_database_source(self):
        # No metadata-source database -> no envelope section -> the tag renders as an empty object
        # rather than tripping the strict unknown-tag check.
        envelope = er.build_grouped_metadata_envelope(
            [er.build_metadata_asset_group("db1", "xidA", {}, [])])
        out = json.loads(tr.render_config(
            '{"d": {{databaseMetadataObject}}}', _manifest(), _execution(),
            metadata_loader=lambda: er.to_legacy_vams_view(envelope, "db1", "xidA", "/")))
        assert out["d"] == {}

    def test_database_metadata_object_empty_without_metadata_read(self):
        out = json.loads(tr.render_config(
            '{"d": {{databaseMetadataObject}}}', _manifest(), _execution()))
        assert out["d"] == {}


@pytest.mark.unit
class TestStrictUnknownTags:
    def test_unknown_tag_raises(self):
        with pytest.raises(tr.MissingTemplateTagError) as ei:
            tr.render_config('{"x": "{{notARealTag}}"}', _manifest(), _execution())
        assert "notARealTag" in str(ei.value)

    def test_future_metadata_key_tag_errors_today(self):
        # {{metadata_<key>}} dynamic lookup is a documented FUTURE feature; it errors now.
        with pytest.raises(tr.MissingTemplateTagError):
            tr.render_config('{"loc": "{{metadata_location}}"}', _manifest(), _execution())

    def test_error_lists_all_unknown(self):
        with pytest.raises(tr.MissingTemplateTagError) as ei:
            tr.render_config('{"a": "{{fooBar}}", "b": "{{bazQux}}"}', _manifest(), _execution())
        assert ei.value.unknown_tags == ["bazQux", "fooBar"]


@pytest.mark.unit
class TestConfigFormatIsThreaded:
    """A body's declared format decides how a scalar tag value is escaped. The JSON escape leaves &, <
    and > intact, so applying it to an xml body emits a value that its parser rejects or reads as
    markup — every caller rendering a template's configuration body has to pass that template's
    configFormat rather than relying on the default."""

    def _xml_manifest(self):
        m = _manifest()
        m["inputFiles"][0]["key"] = "xidA/a & b<x>.e57"
        return m

    def test_an_xml_body_escapes_markup_in_a_system_tag_value(self):
        rendered = tr.render_config("<key>{{firstAssetFileKey}}</key>", self._xml_manifest(),
                                    _execution(), config_format=tr.CONFIG_FORMAT_XML)
        assert rendered == "<key>xidA/a &amp; b&lt;x&gt;.e57</key>"

    def test_the_default_is_the_json_escape(self):
        # Which is why an xml body rendered without its format emits bare markup — the failure the
        # explicit argument prevents.
        rendered = tr.render_config("<key>{{firstAssetFileKey}}</key>", self._xml_manifest(),
                                    _execution())
        assert rendered == "<key>xidA/a & b<x>.e57</key>"

    def test_yaml_openjd_and_raw_use_the_default_escape(self):
        for fmt in ("yaml", "openjd", "raw", tr.CONFIG_FORMAT_JSON):
            rendered = tr.render_config("key: {{firstAssetFileKey}}", self._xml_manifest(),
                                        _execution(), config_format=fmt)
            assert rendered == "key: xidA/a & b<x>.e57", fmt


@pytest.mark.unit
class TestRenderedSizeCap:
    """A metadata-content tag emits the whole payload at every occurrence, so a small body that
    repeats one renders to payload x occurrences. Without a cap that amplification is materialized as
    one Python string inside the execute lambda."""

    def _payload(self, size):
        return {"VAMS": {"assetMetadata": {"blob": "x" * size}}}

    def test_amplifying_body_is_refused_rather_than_materialized(self):
        # 300 occurrences of a ~64 KiB payload renders past the cap; the body itself is ~8 KB.
        body = "{" + ",".join(f'"k{i}": {{{{assetMetadataObject}}}}' for i in range(300)) + "}"
        with pytest.raises(tr.RenderedConfigTooLargeError) as ei:
            tr.render_config(body, _manifest(), _execution(),
                             metadata_loader=lambda: self._payload(64 * 1024))
        assert ei.value.limit == tr.MAX_RENDERED_CONFIG_LENGTH

    def test_cap_measures_the_rendered_output_not_the_body(self):
        # One occurrence of the same payload is well under the cap, so the body length is not what is
        # being measured.
        out = json.loads(tr.render_config(
            '{"m": {{assetMetadataObject}}}', _manifest(), _execution(),
            metadata_loader=lambda: self._payload(64 * 1024)))
        assert len(out["m"]["blob"]) == 64 * 1024

    def test_error_message_names_no_caller_content(self):
        with pytest.raises(tr.RenderedConfigTooLargeError) as ei:
            tr._substitute("{{executionId}}" * 20, {"executionId": ("scalar", "E1")}, limit=10)
        assert "10" in str(ei.value) and "E1" not in str(ei.value)

    def test_a_body_at_the_limit_still_renders(self):
        # The boundary is inclusive: exactly `limit` characters is not too large.
        rendered = tr._substitute("ab{{t}}", {"t": ("scalar", "cd")}, limit=4)
        assert rendered == "abcd"


@pytest.mark.unit
class TestDeadlineCloudTags:
    def test_deadline_tags_defined_and_default_empty(self):
        # The Deadline Cloud tags are DEFINED (do not trip the strict unknown-tag check) but resolve
        # to empty strings until the pipeline configuration supplies them.
        out = json.loads(tr.render_config(
            '{"farm": "{{deadlineFarmId}}", "queue": "{{deadlineQueueId}}", '
            '"sp": "{{deadlineStorageProfileId}}"}',
            _manifest(), _execution()))
        assert out == {"farm": "", "queue": "", "sp": ""}

    def test_deadline_tags_resolve_from_execution_context(self):
        # When a future pipeline configuration supplies them via the execution context, they render.
        ex = dict(_execution())
        ex["deadlineFarmId"] = "farm-abc"
        ex["deadlineQueueId"] = "queue-def"
        out = json.loads(tr.render_config(
            '{"farm": "{{deadlineFarmId}}", "queue": "{{deadlineQueueId}}"}', _manifest(), ex))
        assert out == {"farm": "farm-abc", "queue": "queue-def"}


@pytest.mark.unit
class TestTagConstantsAreSourceOfTruth:
    def test_render_uses_tag_name_constants(self):
        # Every documented tag NAME comes from the templateTags constants module (single source of
        # truth). Spot-check that a constant's value renders as expected.
        from backend.backend.common.workflows import templateTags as tags
        text = '{"x": "{{' + tags.EXECUTION_ID + '}}", "f": "{{' + tags.DEADLINE_FARM_ID + '}}"}'
        out = json.loads(tr.render_config(text, _manifest(), _execution()))
        assert out["x"] == "E1" and out["f"] == ""


@pytest.mark.unit
class TestWhitespaceAndHelpers:
    def test_whitespace_inside_braces_tolerated(self):
        out = json.loads(tr.render_config('{"e": "{{  executionId  }}"}', _manifest(), _execution()))
        assert out["e"] == "E1"

    def test_uses_template_tags(self):
        assert tr.uses_template_tags("{{executionId}}") is True
        assert tr.uses_template_tags('{"q": 1}') is False
        assert tr.uses_template_tags("") is False
