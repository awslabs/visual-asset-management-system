#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the 3dRecon/splatToolbox pipeline: the vendored
manifestHelper, the vamsExecute lambda threading metadata + input-configuration S3 LOCATIONS
(never inline content), openPipeline location threading + sub-process registration, and the
container reading metadata/config from S3 (consumer-reads-from-S3)."""

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

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "S3_ASSETAUXILIARY_BUCKET_NAME": "test-aux",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:SplatToolbox",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".zip,.mp4,.mov",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/SplatToolbox",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/SplatToolbox:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


# ============================ vamsExecute ============================

@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteSplatToolboxPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteSplatToolboxPipeline"])
        return importlib.import_module("vamsExecuteSplatToolboxPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/scan.zip",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/3dRecon/splatToolbox",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"assetMetadata": {"MODEL": "splatfacto-big"}}},
            "inputParameters": '{"MAX_NUM_IMAGES": 500}',
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/scan.zip", "assetId": "xidM", "databaseId": "dbM",
                            "assetRootS3Key": "xidM/", "auxPreviewPrefix": "dbM/xidM/scan.zip/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/splatToolbox/E1/",
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
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/scan.zip"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/splatToolbox/E1/"
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        # No inline metadata or config content past the vamsExecute boundary.
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_legacy_fallback_without_manifest(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200})
        body = self._body()
        body.pop("inputManifestS3Location")
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/legacy/scan.zip"
        # config location still threaded from the body (no manifest needed for it)
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload

    def test_missing_task_token_errors(self):
        mod = self._load()
        s3 = MagicMock()
        body = self._body()
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500

    def test_failure_after_token_fails_the_task(self):
        """A failure once the task token is known must fail the waitForCallback task rather than
        leave it waiting for the full taskTimeout."""
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))}
        send_failure = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", MagicMock(side_effect=Exception("Throttled"))), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_multi_file_manifest_fails_the_task(self):
        mod = self._load()
        manifest = self._manifest()
        manifest["inputFiles"] = manifest["inputFiles"] * 2
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        send_failure = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", MagicMock()), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        send_failure.assert_called_once()

    def test_callback_failure_does_not_mask_the_error(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))}
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", MagicMock(side_effect=Exception("Throttled"))), \
                patch.object(mod.sfn_client, "send_task_failure",
                             MagicMock(side_effect=Exception("TaskTimedOut"))):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500


# ============================ openPipeline: threading + registration ============================

@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xidM/scan.zip",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/scan.zip/3dRecon/splatToolbox",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:SplatToolbox:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })

    def test_threads_locations_not_inline(self):
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
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
            mod.lambda_handler(self._event(), MagicMock())
        assert put_events.call_count == 1
        entry = put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["executionArn"].endswith("PipelineJob_x")
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/SplatToolbox"

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock(side_effect=Exception("AccessDenied"))
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200


# ============================ constructPipeline ============================

@pytest.mark.unit
class TestConstructPipeline:
    def _load(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def test_definition_carries_locations_not_content(self):
        mod = self._load()
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/scan.zip",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/scan.zip/3dRecon/splatToolbox",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
        }
        with patch.object(mod.s3_client, "head_object", side_effect=Exception("no lock")), \
                patch.object(mod.s3_client, "put_object", MagicMock()):
            out = mod.lambda_handler(event, MagicMock())
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
        # The container command embeds the definition JSON, which carries the locations only.
        definition = json.loads(out["definition"][2])
        assert definition["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert definition["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in definition
        assert "inputParameters" not in definition


# ============================ container reads metadata/config from S3 ============================

@pytest.mark.unit
class TestContainerReadsFromS3:
    """The container __main__ reads metadata + config from the S3 locations in the definition
    (consumer-reads-from-S3). Loads the container module from its separate code asset."""

    def _container_main(self):
        import importlib.util
        path = os.path.normpath(os.path.join(
            _LAMBDA_DIR, "..", "container", "__main__.py"))
        # The container imports `from vams_utils import manifest_io`; put the container root on path.
        container_root = os.path.dirname(path)
        if container_root not in sys.path:
            sys.path.insert(0, container_root)
        spec = importlib.util.spec_from_file_location("splat_container_main", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, container_root

    def test_set_config_parameters_metadata_priority_and_filtering(self):
        mod, container_root = self._container_main()
        captured = {}

        class FakeEnv(dict):
            def __setitem__(self, k, v):
                captured[k] = v
                dict.__setitem__(self, k, v)

        real = mod.os.environ
        mod.os.environ = FakeEnv(real)
        try:
            # cwd must hold config.json (container root); set_config_parameters opens 'config.json'.
            cwd = os.getcwd()
            os.chdir(container_root)
            try:
                mod.set_config_parameters(
                    {"MAX_NUM_IMAGES": 100, "NOT_A_KEY": "x"},
                    {"MODEL": "splatfacto-big", "MAX_NUM_IMAGES": 500})
            finally:
                os.chdir(cwd)
        finally:
            mod.os.environ = real
        assert captured.get("MODEL") == "splatfacto-big"
        assert captured.get("MAX_NUM_IMAGES") == "500"   # metadata priority over params
        assert "NOT_A_KEY" not in captured                # filtered to config.json keys

    def test_container_manifest_io_reads_and_unwraps(self):
        _mod, container_root = self._container_main()
        import importlib
        mio = importlib.import_module("vams_utils.manifest_io")
        envelope = {"schemaVersion": 1, "metadata": {"VAMS": {"assetMetadata": {"MODEL": "x"}}}}
        cfg = {"MAX_NUM_IMAGES": 500}
        s3_resp = {"Body": MagicMock(read=lambda: json.dumps(envelope).encode("utf-8"))}
        with patch.object(mio.client, "get_object", MagicMock(return_value=s3_resp)):
            md = mio.fetch_metadata("s3://abkt/.../metadata.json")
        assert md == {"VAMS": {"assetMetadata": {"MODEL": "x"}}}  # envelope unwrapped, VAMS kept
        cfg_resp = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        with patch.object(mio.client, "get_object", MagicMock(return_value=cfg_resp)):
            got = mio.fetch_input_configuration("s3://abkt/.../config.json")
        assert got == cfg
        # best-effort: empty location -> {}
        assert mio.fetch_metadata("") == {}
        assert mio.fetch_input_configuration("") == {}
