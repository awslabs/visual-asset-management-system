#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the multi/rapidPipeline pipeline: vamsExecute
threads metadata + input-configuration S3 LOCATIONS (never inline content), openPipeline threads
the locations + registers the sub-SFN, and constructPipeline reads the input configuration from
S3 to write rp_config.json (lambda-side config consumer)."""

import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:RapidPipeline",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/RapidPipeline",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/RapidPipeline:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteRapidPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteRapidPipeline"])
        return importlib.import_module("vamsExecuteRapidPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/model.glb",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/rapidPipeline",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"k": "v"}},
            "inputParameters": '{"settings": {}}',
            "outputType": ".glb",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/model.glb", "assetId": "xidM", "databaseId": "dbM"}],
            "outputs": {"files": "s3://abkt/pipelines/p1/MJOB/output/E1/files/"},
            "auxTempPrefix": "s3://aux/xidM/model.glb/rapidPipeline/",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def test_forwards_locations_not_content(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/model.glb"
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert payload["outputFileType"] == ".glb"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_pre_invoke_failure_fails_the_task_token(self):
        # A multi-file manifest is rejected before the pipeline starts; the workflow task waits on
        # the callback token, so the rejection must be reported rather than only returned.
        mod = self._load()
        manifest = self._manifest()
        manifest["inputFiles"].append(
            {"bucket": "abkt", "key": "xidM/second.glb", "assetId": "xidM", "databaseId": "dbM"})
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_no_task_token_skips_the_callback(self):
        mod = self._load()
        body = self._body()
        del body["TaskToken"]
        sfn = MagicMock()
        with patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500
        sfn.send_task_failure.assert_not_called()


@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xidM/model.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/model.glb/rapidPipeline",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "outputFileType": ".glb",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:RapidPipeline:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })

    def test_threads_locations_and_registers(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert sfn_input["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert "inputParameters" not in sfn_input and "inputMetadata" not in sfn_input
        entry = put_events.call_args.kwargs["Entries"][0]
        assert json.loads(entry["Detail"])["pipelineExecutionId"] == "P1"

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock(side_effect=Exception("denied"))):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestConstructPipelineReadsConfigFromS3:
    def _load(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def _event(self):
        return {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/model.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/auxbkt/rapidPipeline",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
            "outputFileType": ".glb",
        }

    def test_reads_config_from_s3_and_writes_rp_config(self):
        mod = self._load()
        config = {"settings": {"quality": "high"}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
        put_object = MagicMock()
        s3.put_object = put_object
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(self._event(), MagicMock())
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputParameters" not in out
        # The config read from S3 is written to a per-execution rp_config object (config travels
        # S3->S3, not inline). The key is namespaced by jobName so concurrent runs can't clobber
        # each other's config, then downloaded to rp_config.json inside the container command.
        put_object.assert_called_once()
        assert put_object.call_args.kwargs["Key"] == "rp_config_PipelineJob_x.json"
        assert json.loads(put_object.call_args.kwargs["Body"]) == config
        # The command uses the with-config variant.
        cmd = out["commands"]
        assert cmd[0] == "/bin/sh"
        assert "rp_config.json" in cmd[2] and "--read_config rp_config.json" in cmd[2]

    def test_no_config_uses_no_config_command(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("missing")  # fetch_input_configuration -> {}
        put_object = MagicMock()
        s3.put_object = put_object
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(self._event(), MagicMock())
        put_object.assert_not_called()  # no config -> nothing written
        assert "--read_config" not in out["commands"][2]

    def test_outputType_from_config_is_popped_and_drives_extension(self):
        # outputType is a VAMS-reserved key: it selects the output extension and must not leak
        # into rp_config.json. With only outputType in the config, nothing rpdx-relevant remains,
        # so no rp_config.json is written.
        mod = self._load()
        config = {"outputType": ".glb"}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
        put_object = MagicMock()
        s3.put_object = put_object
        event = self._event()
        del event["outputFileType"]  # value now comes from the config
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(event, MagicMock())
        # outputType drove the output filename extension in the command...
        assert ".glb" in out["commands"][2]
        # ...and was removed, leaving no real rpdx config to write.
        put_object.assert_not_called()

    def test_outputType_config_coexists_with_rpdx_config(self):
        # A config with both outputType and a real rpdx option: outputType is popped for the
        # extension, the remaining option is written to rp_config.json.
        mod = self._load()
        config = {"outputType": ".glb", "settings": {"quality": "high"}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
        put_object = MagicMock()
        s3.put_object = put_object
        event = self._event()
        del event["outputFileType"]
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(event, MagicMock())
        assert ".glb" in out["commands"][2]
        put_object.assert_called_once()
        assert json.loads(put_object.call_args.kwargs["Body"]) == {"settings": {"quality": "high"}}
        assert "--read_config rp_config.json" in out["commands"][2]
