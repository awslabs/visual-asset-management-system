#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the multi/rapidPipelineEKS pipeline: the vendored
manifestHelper (manifest-preferred, legacy-fallback resolution), the vamsExecute lambda threading
metadata + input-configuration S3 LOCATIONS (never inline content), openPipeline location threading
+ sub-process registration, and the consolidated_handler CONSTRUCT_PIPELINE op reading the config
from S3 (consumer-reads-from-S3) and writing rp_config.json."""

import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest


def _ctx():
    """A Lambda context mock whose get_remaining_time_in_millis returns a real int — the EKS
    handlers compare the remaining time against a threshold (a bare MagicMock would raise)."""
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.function_name = "test-fn"
    ctx.function_version = "$LATEST"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME_EKS": "test-open-pipeline-eks",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:RapidPipelineEKS",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/RapidPipelineEKS",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/RapidPipelineEKS:*",
    # consolidated_handler reads these at import/use time; a non-placeholder image lets
    # CONSTRUCT_PIPELINE build a job manifest instead of raising.
    "CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:latest",
    "EKS_CLUSTER_NAME": "test-cluster",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(k, v)

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
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xid/test/pump.glb/eks/p1/",
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
            "auxTempPrefix": "pipelines/rapidPipelineEKS/E1/",
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
        assert r["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/rapidPipelineEKS/E1/"
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


@pytest.mark.unit
class TestFetchInputConfiguration:
    def test_parses_config_object(self):
        cfg = {"someRapidPipelineOption": True}
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


# ============================ vamsExecute ============================

@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteRapidPipelineEKS" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteRapidPipelineEKS"])
        return importlib.import_module("vamsExecuteRapidPipelineEKS")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/eks/p1/",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"assetMetadata": {"FORMAT": "glb"}}},
            "inputParameters": '{"someRapidPipelineOption": true}',
            "executingUserName": "user@x",
            # assetId/databaseId are NOT carried in the SFN body anymore; they come from the
            # manifest's first input file (so the no-manifest case yields empty identity).
            "outputType": ".all",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/test/pump.glb", "assetId": "xidM",
                            "databaseId": "dbM", "assetRootS3Key": "xidM/",
                            "auxPreviewPrefix": "dbM/xidM/test/pump.glb/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/rapidPipelineEKS/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def test_forwards_locations_not_content(self):
        mod = self._load()
        manifest = self._manifest()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, _ctx())
        assert resp["statusCode"] == 200
        # The open-pipeline invoke carries the MANIFEST-resolved values (reconstructed s3://).
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/test/pump.glb"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/rapidPipelineEKS/E1/"
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

    def test_legacy_fallback_without_manifest(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        body = self._body()
        body.pop("inputManifestS3Location")  # no manifest pointer
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, _ctx())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/legacy/pump.glb"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/legacy/files/"
        # assetId no longer falls back to the SFN body; without a manifest it is empty.
        assert payload["assetId"] == ""
        # config location still threaded from the body (no manifest needed for it)
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_boundary_extraction_same_from_s3_as_inline(self):
        """The metadata/config S3 boundary: regardless of whether the manifest is present, the
        config + metadata LOCATIONS resolved at the vamsExecute boundary produce the SAME forwarded
        values. (metadata location comes from the manifest envelope when present; config location
        always travels in the body.)"""
        mod = self._load()
        # With manifest: metadata location comes from the envelope.
        s3_mf = MagicMock()
        s3_mf.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))}
        invoke_mf = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        with patch.object(mod, "s3_client", s3_mf), patch.object(mod.lambda_client, "invoke", invoke_mf):
            mod.lambda_handler({"body": json.dumps(self._body())}, _ctx())
        payload_mf = json.loads(invoke_mf.call_args.kwargs["Payload"].decode("utf-8"))
        # Same config location resolves with or without the manifest.
        assert payload_mf["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"

        # Without manifest: metadata location can ride in the body directly and resolves identically.
        body = self._body()
        body.pop("inputManifestS3Location")
        body["inputMetadataS3Location"] = "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        s3_legacy = MagicMock()
        s3_legacy.get_object.side_effect = Exception("no manifest")
        invoke_legacy = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        with patch.object(mod, "s3_client", s3_legacy), patch.object(mod.lambda_client, "invoke", invoke_legacy):
            mod.lambda_handler({"body": json.dumps(body)}, _ctx())
        payload_legacy = json.loads(invoke_legacy.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload_legacy["inputMetadataS3Location"] == payload_mf["inputMetadataS3Location"]
        assert payload_legacy["inputConfigurationS3Location"] == payload_mf["inputConfigurationS3Location"]

    def test_missing_task_token_errors(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        body = self._body()
        body.pop("inputManifestS3Location")
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3):
            resp = mod.lambda_handler({"body": json.dumps(body)}, _ctx())
        # No task token -> validation fails (the EKS validator reports the missing token as a 400).
        assert resp["statusCode"] == 400

    def test_pre_invoke_failure_fails_the_task_token(self):
        # A multi-file manifest is rejected before the pipeline starts; the workflow task waits on
        # the callback token, so the rejection must be reported rather than only returned.
        mod = self._load()
        manifest = self._manifest()
        manifest["inputFiles"].append(
            {"bucket": "abkt", "key": "xidM/second.glb", "assetId": "xidM", "databaseId": "dbM"})
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, _ctx())
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_validation_rejection_fails_the_task_token(self):
        # The EKS validator returns a 400 early-return; a 400 that leaves the callback pending
        # strands the workflow task for its full taskTimeout, so it reports the token too.
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        body = self._body()
        body.pop("inputManifestS3Location")
        body.pop("outputS3AssetFilesPath")  # required parameter -> validation failure
        sfn = MagicMock()
        invoke = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, _ctx())
        assert resp["statusCode"] == 400
        invoke.assert_not_called()
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_pipeline_invoke_failure_fails_the_task_token(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))}
        invoke = MagicMock(side_effect=Exception("open pipeline unavailable"))
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke), patch.object(mod.time, "sleep", MagicMock()):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, _ctx())
        assert resp["statusCode"] == 500
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_no_task_token_skips_the_callback(self):
        # A direct invoke carries no token: the error response is still returned, without a callback.
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        body = self._body()
        body.pop("inputManifestS3Location")
        body.pop("TaskToken")
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(body)}, _ctx())
        assert resp["statusCode"] == 400
        sfn.send_task_failure.assert_not_called()


# ============================ openPipeline: threading + registration ============================

@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/MJOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/pump.glb/eks/p1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "assetId": "xidM",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
            "outputFileType": ".all",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:RapidPipelineEKS:PipelineJobEKS_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })

    def test_threads_locations_not_inline(self):
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(self._event(), _ctx())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        # The nested SFN input carries the config + metadata LOCATIONS, never inline content.
        assert sfn_input["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert sfn_input["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in sfn_input
        assert "inputParameters" not in sfn_input

    def test_registers_sub_execution(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), _ctx())
        assert put_events.call_count == 1
        entry = put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["executionArn"].endswith("PipelineJobEKS_x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/RapidPipelineEKS"

    def test_registration_skipped_without_event_prefix(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock()
        ev = self._event()
        ev.pop("orchestrationEventPrefix")
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(ev, _ctx())
        assert resp["statusCode"] == 200
        put_events.assert_not_called()  # no prefix -> no registration, pipeline still starts

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock(side_effect=Exception("AccessDenied: events:PutEvents"))
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(self._event(), _ctx())
        # Registration raised, but the pipeline start still succeeds.
        assert resp["statusCode"] == 200


# ============================ consolidated_handler: CONSTRUCT_PIPELINE ============================

@pytest.mark.unit
class TestConsolidatedConstructPipeline:
    """The CONSTRUCT_PIPELINE op reads the per-pipeline config via manifestHelper.fetch_input_configuration
    (consumer-reads-from-S3) and, when a config is present, writes rp_config.json to S3 and threads the
    download/read of it into the container command. The construct output threads the metadata + config
    LOCATIONS, never inline content."""

    def _load(self):
        if "consolidated_handler" in sys.modules:
            return importlib.reload(sys.modules["consolidated_handler"])
        return importlib.import_module("consolidated_handler")

    def _event(self):
        return {
            "operation": "CONSTRUCT_PIPELINE",
            "jobName": "PipelineJobEKS_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/pump.glb/eks/p1",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
            "outputFileType": ".all",
        }

    def test_reads_config_from_s3_and_writes_rp_config(self):
        mod = self._load()
        cfg = {"someRapidPipelineOption": True}
        s3 = MagicMock()
        # CONSTRUCT_PIPELINE reads the config from its S3 location...
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        put_object = MagicMock()
        s3.put_object = put_object
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(self._event(), _ctx())
        # The construct output threads the locations, not inline content.
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
        # A non-empty config triggers an rp_config.json write to the auxiliary location...
        assert put_object.call_count == 1
        put_kwargs = put_object.call_args.kwargs
        assert put_kwargs["Key"].endswith("/rp_config.json")
        assert json.loads(put_kwargs["Body"]) == cfg
        # ...and the container command downloads + reads that rp_config.json.
        command = out["jobManifest"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        assert "rp_config.json" in command
        assert "--read_config rp_config.json" in command

    def test_no_config_skips_rp_config_write(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")  # no config readable from S3
        put_object = MagicMock()
        s3.put_object = put_object
        event = self._event()
        # No inline inputParameters fallback either -> no config at all.
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(event, _ctx())
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        put_object.assert_not_called()
        command = out["jobManifest"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        assert "rp_config.json" not in command

    def test_outputType_from_config_is_popped_before_rp_config_write(self):
        # outputType is a VAMS-reserved key in the input configuration: it selects the output
        # extension and must NOT leak into the rpdx rp_config.json.
        mod = self._load()
        cfg = {"someRapidPipelineOption": True, "outputType": ".glb"}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        put_object = MagicMock()
        s3.put_object = put_object
        event = self._event()
        del event["outputFileType"]  # value comes from the config, not the threaded body field
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(event, _ctx())
        # The config written to rp_config.json has outputType removed (only the real rpdx option).
        written = json.loads(put_object.call_args.kwargs["Body"])
        assert "outputType" not in written
        assert written == {"someRapidPipelineOption": True}
        # The .glb output extension drove the container command (non-.all single-format path).
        command = out["jobManifest"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        assert ".glb" in command

    def test_outputType_falls_back_to_threaded_outputFileType(self):
        # Legacy executions (config has no outputType) still use the threaded outputFileType.
        mod = self._load()
        cfg = {"someRapidPipelineOption": True}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        s3.put_object = MagicMock()
        event = self._event()
        event["outputFileType"] = ".glb"
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(event, _ctx())
        command = out["jobManifest"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        assert ".glb" in command
