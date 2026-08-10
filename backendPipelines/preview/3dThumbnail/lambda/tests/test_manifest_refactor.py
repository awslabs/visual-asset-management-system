#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the preview/3dThumbnail pipeline:
the vendored manifestHelper (manifest-preferred, legacy-fallback resolution) and the
vamsExecute lambda's use of it. The container is unchanged (A-shallow refactor)."""

import os
import sys
import datetime
import json
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# The pipeline lambdas import customLogging.logger and (vamsExecute) read env at import time.
# Provide a lightweight customLogging stub + the OPEN_PIPELINE_FUNCTION_NAME env var so the
# modules import without the aws_lambda_powertools dependency or real CDK env.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

os.environ.setdefault("OPEN_PIPELINE_FUNCTION_NAME", "test-open-pipeline")
# The vamsExecute lambda creates boto3 clients at import time; give botocore a region.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
# openPipeline reads these at import time.
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:1:stateMachine:Preview3dThumbnail")
os.environ.setdefault("ALLOWED_INPUT_FILEEXTENSIONS", ".glb,.gltf,.obj,.stl")
os.environ.setdefault("ORCHESTRATION_BUS_NAME", "vams-orchestration")
os.environ.setdefault("STATE_MACHINE_LOG_GROUP_NAME", "/aws/vendedlogs/Preview3dThumbnail")
os.environ.setdefault("STATE_MACHINE_LOG_GROUP_ARN",
                      "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/Preview3dThumbnail:*")

import manifestHelper as mh  # noqa: E402


# ============================ manifestHelper ============================

@pytest.mark.unit
class TestParseS3Uri:
    def test_bucket_and_key(self):
        assert mh.parse_s3_uri("s3://bkt/path/to/x.glb") == ("bkt", "path/to/x.glb")

    def test_empty_and_non_s3(self):
        assert mh.parse_s3_uri("") == ("", "")
        assert mh.parse_s3_uri("https://example") == ("", "")

    def test_bucket_only(self):
        assert mh.parse_s3_uri("s3://bktonly") == ("bktonly", "")


@pytest.mark.unit
class TestResolveInputs:
    def _legacy(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xid/test/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/JOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/JOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/JOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xid/test/pump.glb/preview/p1/",
            "assetId": "xid",
            "databaseId": "db",
        }

    def _manifest(self):
        return {
            "schemaVersion": 1,
            "inputFiles": [{
                "relativePath": "/test/pump.glb", "databaseId": "dbM", "assetId": "xidM",
                "assetRootS3Key": "xidM/", "auxPreviewPrefix": "dbM/xidM/test/pump.glb/preview",
                "bucket": "abkt", "key": "xidM/test/pump.glb", "versionId": "v3",
            }],
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "outputs": {
                "bucket": "abkt",
                "files": "pipelines/p1/MJOB/output/E1/files/",
                "previews": "pipelines/p1/MJOB/output/E1/previews/",
                "metadata": "pipelines/p1/MJOB/output/E1/metadata/",
                "results": "pipelines/p1/MJOB/output/E1/results/",
            },
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/p1/E1/",
            "auxPreviewPipelineSuffix": "",
            "systemConfig": {
                "orchestrationBusArn": "arn:bus",
                "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
            },
        }

    def test_no_manifest_falls_back_to_legacy(self):
        legacy = self._legacy()
        r = mh.resolve_inputs(legacy, None)
        assert r["manifestUsed"] is False
        assert r["inputS3AssetFilePath"] == legacy["inputS3AssetFilePath"]
        assert r["outputS3AssetFilesPath"] == legacy["outputS3AssetFilesPath"]
        assert r["inputOutputS3AssetAuxiliaryFilesPath"] == legacy["inputOutputS3AssetAuxiliaryFilesPath"]
        # assetId/databaseId no longer fall back to the SFN body; without a manifest they are
        # empty (only the manifest's first input file supplies them).
        assert r["assetId"] == "" and r["databaseId"] == ""
        assert r["inputFiles"] == []
        assert r["orchestrationBusArn"] == ""

    def test_manifest_preferred_and_self_locating(self):
        r = mh.resolve_inputs(self._legacy(), self._manifest())
        assert r["manifestUsed"] is True
        # input path + identity come from the manifest's first resolved file
        assert r["inputS3AssetFilePath"] == "s3://abkt/xidM/test/pump.glb"
        assert r["assetId"] == "xidM" and r["databaseId"] == "dbM"
        # outputs + aux are RECONSTRUCTED from the manifest's bucket + relative prefixes
        assert r["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert r["outputS3AssetPreviewPath"].endswith("/previews/")
        assert r["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/p1/E1/"
        # The per-input-file aux preview path = auxBucket + the file's auxPreviewPrefix (+ empty
        # pipeline suffix here).
        assert r["auxPreviewS3Path"] == "s3://aux/dbM/xidM/test/pump.glb/preview"
        assert r["inputMetadataS3Location"].endswith("/metadata.json")
        assert r["orchestrationBusArn"] == "arn:bus"
        assert r["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert len(r["inputFiles"]) == 1

    def test_partial_manifest_degrades_per_field(self):
        # An empty outputs map keeps the legacy output paths; only present fields override.
        partial = {"inputFiles": [{"bucket": "abkt", "key": "xid/a.glb", "assetId": "xid"}],
                   "outputs": {}}
        r = mh.resolve_inputs(self._legacy(), partial)
        assert r["inputS3AssetFilePath"] == "s3://abkt/xid/a.glb"   # from manifest
        assert r["outputS3AssetFilesPath"] == self._legacy()["outputS3AssetFilesPath"]  # fell back

    def test_aux_prefix_single_slash(self):
        # The manifest aux prefix must not introduce a double slash after the bucket.
        r = mh.resolve_inputs(self._legacy(), self._manifest())
        assert "//" not in r["inputOutputS3AssetAuxiliaryFilesPath"].split("s3://", 1)[1]

    def test_identity_stays_coupled_to_input_path(self):
        # A malformed first entry (identity present, no usable bucket/key) must NOT pair a
        # manifest assetId with the legacy input key — both fall back together.
        legacy = self._legacy()
        malformed = {"inputFiles": [{"assetId": "xidM", "databaseId": "dbM",
                                     "bucket": "", "key": ""}],
                     "outputs": {}}
        r = mh.resolve_inputs(legacy, malformed)
        assert r["inputS3AssetFilePath"] == legacy["inputS3AssetFilePath"]
        # A malformed first entry yields no identity (no legacy fallback): the input path stays
        # on the legacy value, and assetId/databaseId are empty rather than the manifest's xidM.
        assert r["assetId"] == ""
        assert r["databaseId"] == ""


@pytest.mark.unit
class TestFetchManifest:
    def test_fetch_success(self):
        manifest = {"schemaVersion": 1, "inputFiles": []}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        got = mh.fetch_manifest(s3, "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json")
        assert got == manifest

    def test_fetch_empty_location_returns_none(self):
        s3 = MagicMock()
        assert mh.fetch_manifest(s3, "") is None
        s3.get_object.assert_not_called()

    def test_fetch_s3_error_raises(self):
        # A supplied-but-unreadable manifest must fail here. It is the only carrier of the asset,
        # database, and output paths, so swallowing the error starts a job with blank identity that
        # provisions its compute before failing.
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        with pytest.raises(Exception, match="Could not read the workflow input manifest"):
            mh.fetch_manifest(s3, "s3://abkt/k/manifest.json")

    def test_fetch_malformed_location_raises(self):
        s3 = MagicMock()
        with pytest.raises(Exception, match="malformed input manifest location"):
            mh.fetch_manifest(s3, "not-an-s3-uri")
        s3.get_object.assert_not_called()

    def test_resolve_pipeline_inputs_uses_fetched_manifest(self):
        manifest = {"inputFiles": [{"bucket": "abkt", "key": "xid/a.glb", "assetId": "xid"}],
                    "outputs": {"files": "s3://abkt/out/files/"}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        data = {"inputManifestS3Location": "s3://abkt/k/manifest.json",
                "inputS3AssetFilePath": "s3://abkt/legacy.glb"}
        r = mh.resolve_pipeline_inputs(data, s3)
        assert r["manifestUsed"] is True
        assert r["inputS3AssetFilePath"] == "s3://abkt/xid/a.glb"

    def test_resolve_pipeline_inputs_falls_back_when_no_manifest_is_referenced(self):
        # A payload carrying its paths inline and NO manifest pointer still resolves. This is the
        # only shape the legacy fallback serves; a real task body from stepfunctions_builder carries
        # inputManifestS3Location and none of these keys.
        s3 = MagicMock()
        data = {"inputS3AssetFilePath": "s3://abkt/legacy.glb",
                "outputS3AssetFilesPath": "s3://abkt/legacy/files/"}
        r = mh.resolve_pipeline_inputs(data, s3)
        assert r["manifestUsed"] is False
        assert r["inputS3AssetFilePath"] == "s3://abkt/legacy.glb"
        s3.get_object.assert_not_called()

    def test_resolve_pipeline_inputs_raises_when_a_referenced_manifest_is_unreadable(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("missing")
        data = {"inputManifestS3Location": "s3://abkt/k/manifest.json"}
        with pytest.raises(Exception, match="Could not read the workflow input manifest"):
            mh.resolve_pipeline_inputs(data, s3)


@pytest.mark.unit
class TestFetchMetadata:
    def test_unwraps_schema_versioned_envelope(self):
        envelope = {"schemaVersion": 1, "metadata": {"VAMS": {"k": "v"}}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(envelope).encode("utf-8"))}
        md = mh.fetch_metadata(s3, "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json")
        assert md == {"VAMS": {"k": "v"}}

    def test_legacy_unenveloped_file_returned_as_is(self):
        legacy = {"VAMS": {"k": "v"}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(legacy).encode("utf-8"))}
        assert mh.fetch_metadata(s3, "s3://abkt/k/metadata.json") == legacy

    def test_empty_location_returns_empty_dict(self):
        s3 = MagicMock()
        assert mh.fetch_metadata(s3, "") == {}
        s3.get_object.assert_not_called()

    def test_s3_error_returns_empty_dict_best_effort(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        assert mh.fetch_metadata(s3, "s3://abkt/k/metadata.json") == {}


# ============================ vamsExecute lambda ============================

@pytest.mark.unit
class TestVamsExecuteUsesManifest:
    def _load_module(self):
        # Import fresh so the customLogging stub + env are in place.
        if "vamsExecutePreview3dThumbnailPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecutePreview3dThumbnailPipeline"])
        return importlib.import_module("vamsExecutePreview3dThumbnailPipeline")

    def _manifest_body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/preview/p1/",
            # config-location is delivered in the SFN body (not the manifest envelope).
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"k": "v"}},
            "inputParameters": '{"overwriteExistingPreviewFiles": true}',
            "executingUserName": "user@x",
            "assetId": "legacyAsset",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/test/pump.glb", "assetId": "xidM",
                            "databaseId": "dbM", "assetRootS3Key": "xidM/",
                            "auxPreviewPrefix": "dbM/xidM/test/pump.glb/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/p1/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {
                "orchestrationBusArn": "arn:bus",
                "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
            },
        }

    def test_handler_forwards_manifest_resolved_values(self):
        mod = self._load_module()
        manifest = self._manifest()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._manifest_body())}, MagicMock())
        assert resp["statusCode"] == 200
        # The open-pipeline invoke carries the MANIFEST-resolved values (reconstructed s3://), not
        # the legacy ones.
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/test/pump.glb"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/p1/E1/"
        assert payload["assetId"] == "xidM"
        # The task token is taken from the payload, not the manifest.
        assert payload["sfnExternalTaskToken"] == "tok-123"
        # The metadata + input-configuration S3 LOCATIONS are forwarded, never the inline content
        # (the vamsExecute lambda is the content boundary for both).
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload
        # The orchestration event prefix rides along so openPipeline can register its sub-SFN.
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"

    def test_handler_falls_back_to_legacy_without_manifest(self):
        mod = self._load_module()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200})
        body = self._manifest_body()
        body.pop("inputManifestS3Location")  # no manifest pointer
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/legacy/pump.glb"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/legacy/files/"
        # assetId no longer falls back to the SFN body; it comes only from the manifest's first
        # input file, so without a manifest it is empty.
        assert payload["assetId"] == ""

    def test_handler_rejects_folder_input(self):
        mod = self._load_module()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200})
        sfn = MagicMock()
        body = self._manifest_body()
        body.pop("inputManifestS3Location")
        body["inputS3AssetFilePath"] = "s3://abkt/legacy/folder/"
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 400
        invoke.assert_not_called()
        # The workflow task waits on the callback token, so the rejection must be reported rather
        # than only returned.
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_pre_invoke_failure_fails_the_task_token(self):
        mod = self._load_module()
        manifest = self._manifest()
        manifest["inputFiles"].append(
            {"bucket": "abkt", "key": "xidM/test/second.glb", "assetId": "xidM", "databaseId": "dbM"})
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._manifest_body())}, MagicMock())
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_no_task_token_skips_the_callback(self):
        mod = self._load_module()
        body = self._manifest_body()
        del body["TaskToken"]
        sfn = MagicMock()
        with patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500
        sfn.send_task_failure.assert_not_called()

    def test_handler_missing_task_token_errors(self):
        mod = self._load_module()
        s3 = MagicMock()
        body = self._manifest_body()
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500


# ============================ fetch_input_configuration + prefix parse ============================

@pytest.mark.unit
class TestFetchInputConfiguration:
    def test_parses_config_object(self):
        cfg = {"overwriteExistingPreviewFiles": True}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        assert mh.fetch_input_configuration(s3, "s3://abkt/.../config.json") == cfg

    def test_empty_location_returns_empty_dict(self):
        s3 = MagicMock()
        assert mh.fetch_input_configuration(s3, "") == {}
        s3.get_object.assert_not_called()

    def test_s3_error_returns_empty_dict_best_effort(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        assert mh.fetch_input_configuration(s3, "s3://abkt/k/config.json") == {}

    def test_empty_body_returns_empty_dict(self):
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"")}
        assert mh.fetch_input_configuration(s3, "s3://abkt/k/config.json") == {}


@pytest.mark.unit
class TestPipelineExecutionIdFromEventPrefix:
    def test_extracts_trailing_pipeline_id(self):
        assert mh.pipeline_execution_id_from_event_prefix(
            "vams.prod.execution.E1.pipeline.P1") == "P1"

    def test_empty_prefix(self):
        assert mh.pipeline_execution_id_from_event_prefix("") == ""

    def test_unrecognized_prefix(self):
        assert mh.pipeline_execution_id_from_event_prefix("vams.prod.execution.E1") == ""


# ============================ openPipeline: config threading + registration ============================

@pytest.mark.unit
class TestOpenPipelineRegistration:
    def _load_module(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/pump.glb/preview/p1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "assetId": "xidM",
            "databaseId": "dbM",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:Preview3dThumbnail:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })

    def test_sfn_input_threads_config_location_not_inline(self):
        mod = self._load_module()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        # The nested SFN input carries the config LOCATION, never inline inputParameters content.
        assert sfn_input["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert sfn_input["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert "inputParameters" not in sfn_input
        assert "inputMetadata" not in sfn_input

    def test_registers_sub_execution_on_orchestration_bus(self):
        mod = self._load_module()
        start = self._mock_start()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), MagicMock())
        assert put_events.call_count == 1
        entry = put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["executionArn"].endswith("PipelineJob_x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/Preview3dThumbnail"

    def test_concurrent_runs_get_distinct_execution_names(self):
        """A state-machine execution name must be unique, and one upload can fan out to several
        simultaneous runs of the same pipeline.

        Caught live: two triggers on one workflow both matched an upload, both openPipeline invocations
        landed in the same second, and the second StartExecution failed with ExecutionAlreadyExists on
        the name PipelineJob_20260806_012019. The SFN retry hid it — it only succeeded because the
        retry landed in the NEXT second."""
        mod = self._load_module()
        # A fresh response per call: the handler rewrites startDate in place, so a single shared dict
        # would hand the second call a string where it expects a datetime.
        start = MagicMock(side_effect=lambda **kw: {
            "executionArn": f"arn:aws:states:us-east-1:1:execution:Preview3dThumbnail:{kw['name']}",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        with patch.object(mod.sfn, "start_execution", start),                 patch.object(mod.events_client, "put_events", MagicMock()):
            # Same wall-clock second for every run: what the timestamp alone cannot distinguish.
            # Only the module's own datetime reference is frozen — patching datetime globally also
            # freezes botocore's credential-expiry clock, which then raises on a tz-aware subtraction.
            fixed = datetime.datetime(2026, 8, 6, 1, 20, 19, 500000)

            class _FixedClock:
                """Stands in for the module's `datetime` module: `now()` is pinned, everything else
                delegates to the real one so the response's own strftime still works."""

                class datetime(datetime.datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return fixed

            with patch.object(mod, "datetime", _FixedClock):
                for _ in range(25):
                    assert mod.lambda_handler(self._event(), MagicMock())["statusCode"] == 200

        names = [c.kwargs["name"] for c in start.call_args_list]
        assert len(names) == 25
        assert len(set(names)) == 25, f"duplicate execution names: {names}"
        # The name is also the S3 config-key namespace in other pipelines, so it must stay SFN-legal.
        for name in names:
            assert len(name) <= 80, name
            assert ":" not in name and "/" not in name and " " not in name, name

    def test_registration_skipped_without_event_prefix(self):
        mod = self._load_module()
        start = self._mock_start()
        put_events = MagicMock()
        ev = self._event()
        ev.pop("orchestrationEventPrefix")
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(ev, MagicMock())
        assert resp["statusCode"] == 200
        put_events.assert_not_called()  # no prefix → no registration, pipeline still starts

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load_module()
        start = self._mock_start()
        put_events = MagicMock(side_effect=Exception("AccessDenied: events:PutEvents"))
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(self._event(), MagicMock())
        # Registration raised, but the pipeline start still succeeds.
        assert resp["statusCode"] == 200


# ============================ container contract (the regression that was missed) ============================

@pytest.mark.unit
class TestContainerContractAcceptsConstructOutput:
    """The constructPipeline definition dict must instantiate the container's PipelineDefinition
    dataclass. A field-name mismatch here is exactly the regression that slipped through before —
    this test exercises the producer→consumer contract directly."""

    def _construct_module(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def _container_pipeline_definition(self):
        # Load the container's dataclass from its source path (separate code asset).
        import importlib.util
        container_objects = os.path.normpath(os.path.join(
            _LAMBDA_DIR, "..", "container", "preview_pipeline", "utils", "pipeline", "objects.py"))
        spec = importlib.util.spec_from_file_location("container_objects", container_objects)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PipelineDefinition

    def test_construct_definition_instantiates_container_dataclass(self):
        construct = self._construct_module()
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/pump.glb/preview/p1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
            "assetId": "xidM",
        }
        out = construct.lambda_handler(event, MagicMock())
        # The construct output threads the locations, not inline content.
        assert out["inputMetadataS3Location"] == event["inputMetadataS3Location"]
        assert out["inputConfigurationS3Location"] == event["inputConfigurationS3Location"]
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
        # The serialized definition (the container command arg) must instantiate PipelineDefinition.
        definition_dict = json.loads(out["definition"][0])
        PipelineDefinition = self._container_pipeline_definition()
        d = PipelineDefinition(**definition_dict)
        assert d.inputConfigurationS3Location == event["inputConfigurationS3Location"]
        assert d.inputMetadataS3Location == event["inputMetadataS3Location"]
        assert d.assetId == "xidM"
