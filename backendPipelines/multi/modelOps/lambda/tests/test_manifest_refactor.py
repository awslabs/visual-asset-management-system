#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the multi/modelOps pipeline: vamsExecute threads
metadata + input-configuration S3 LOCATIONS (never inline content), openPipeline threads the
locations + registers the sub-SFN, and constructPipeline reads the input configuration from S3
to build the ECS command (lambda-side config consumer)."""

import os
import sys
import json
import shlex
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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:ModelOps",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/ModelOps",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/ModelOps:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteModelOps" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteModelOps"])
        return importlib.import_module("vamsExecuteModelOps")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/model.glb",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/modelOps",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"k": "v"}},
            "inputParameters": '{"state": {}}',
            "outputType": "glb",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/model.glb", "assetId": "xidM", "databaseId": "dbM",
                            "assetRootS3Key": "xidM/", "auxPreviewPrefix": "dbM/xidM/model.glb/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/modelOps/E1/",
            "auxPreviewPipelineSuffix": "",
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
        assert payload["sfnExternalTaskToken"] == "tok-123"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        # outputType no longer travels in the pipeline body (modelOps never consumed it); it is
        # carried in the input configuration when a pipeline needs it.
        assert "outputFileType" not in payload
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
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/model.glb/modelOps",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "outputFileType": "glb",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:ModelOps:PipelineJob_x",
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
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
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
            "inputS3AssetFilePath": "s3://abkt/xidM/sub/model.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/model.glb/modelOps",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
        }

    def test_reads_config_from_s3_and_builds_command(self):
        mod = self._load()
        config = {"state": {}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(self._event(), MagicMock())
        # Locations forwarded, not content.
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputParameters" not in out
        # The config read from S3 is baked into the ECS command with the asset-derived state.
        cmd = out["commands"]
        assert cmd[0] == "/bin/bash" and cmd[1] == "-c"
        assert "index.js" in cmd[2]
        # The asset identity fields were injected into the config state. The config travels as a
        # shell-quoted literal argument to printf ("printf '%s' '<json>'"), so recover it with the
        # same quoting rules rather than a bare single-quote split.
        printed = shlex.split(cmd[2].split("|", 1)[0])[-1]
        state = json.loads(printed)["state"]
        assert state["name"] == "model"
        assert state["bucket"] == "abkt"
        assert state["extension"] == "glb"

    def test_shipped_template_config_without_state_block(self):
        """The registered per-format templates ship a config body of just {"outputType": ".x"} with
        no state block. Building the command must succeed (the asset identity is injected into a
        created state block) rather than raising KeyError on every execution."""
        mod = self._load()
        config = {"outputType": ".usdz"}  # verbatim shipped template configBody
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(self._event(), MagicMock())
        printed = shlex.split(out["commands"][2].split("|", 1)[0])[-1]
        sent = json.loads(printed)
        assert sent["state"]["name"] == "model"
        assert sent["state"]["extension"] == "glb"
        # The template's own keys must survive alongside the injected state.
        assert sent["outputType"] == ".usdz"

    def test_no_config_returns_error_string(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("missing")  # fetch_input_configuration -> {}
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(self._event(), MagicMock())
        assert out["commands"] == "Error: No configuration file detected."
