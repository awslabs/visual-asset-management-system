#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the preview/pcPotreeViewer pipeline (threading-only;
the container does not consume metadata/config). Covers: vamsExecute threads metadata + config S3
LOCATIONS and resolves the per-file aux preview output location, openPipeline threading +
registration, constructPipeline definition carries locations, and the container PipelineDefinition
accepts the new definition keys."""

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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:PcPotree",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".e57,.ply,.las,.laz",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/PcPotree",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/PcPotree:*",
    "PDAL_JOB_LOG_GROUP_NAME": "/aws/vendedlogs/Pipelines/PcPotreeViewerPDALabc1234567",
    "PDAL_JOB_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:123456789012:log-group:"
                              "/aws/vendedlogs/Pipelines/PcPotreeViewerPDALabc1234567:*",
    "PDAL_JOB_DEFINITION_NAME": "PcPotreePdalJobDefabc1234567",
    "POTREE_JOB_LOG_GROUP_NAME": "/aws/vendedlogs/Pipelines/PcPotreeViewerPotreedef7654321",
    "POTREE_JOB_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:123456789012:log-group:"
                                "/aws/vendedlogs/Pipelines/PcPotreeViewerPotreedef7654321:*",
    "POTREE_JOB_DEFINITION_NAME": "PcPotreePotreeJobDefabc1234567",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


def _repo_root():
    """Walk up to the repo root rather than counting `..` segments — pipeline directories sit at
    differing depths, and a miscounted relative path fails as a missing file."""
    path = _LAMBDA_DIR
    while path != os.path.dirname(path):
        if os.path.isdir(os.path.join(path, "infra")) and os.path.isdir(
                os.path.join(path, "backend", "backend", "common")):
            return path
        path = os.path.dirname(path)
    raise RuntimeError("repo root not found from " + _LAMBDA_DIR)


def _backend_validators():
    """The backend's real validators module, so registered values are checked against the regexes
    registerPipelineExecution applies rather than a copy of them. Appended to the END of sys.path so
    nothing under backend/backend shadows this pipeline's own modules."""
    backend_pkg = os.path.join(_repo_root(), "backend", "backend")
    if backend_pkg not in sys.path:
        sys.path.append(backend_pkg)
    return importlib.import_module("common.validators")


@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecutePreviewPcPotreeViewerPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecutePreviewPcPotreeViewerPipeline"])
        return importlib.import_module("vamsExecutePreviewPcPotreeViewerPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/scan.e57",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/x",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"k": "v"}},
            "inputParameters": "{}",
            "bucketAssetAuxiliary": "aux-bkt",
            "inputAssetFileKey": "xidM/scan.e57",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/scan.e57", "assetId": "xidM",
                            "databaseId": "dbM", "assetRootS3Key": "xidM/",
                            "auxPreviewPrefix": "dbM/xidM/scan.e57/preview"}],
            "outputs": {"bucket": "abkt", "files": "dbM/xidM/", "previews": "dbM/xidM/preview/",
                        "metadata": "dbM/xidM/metadata/"},
            "auxBucket": "aux-bkt",
            # A pipeline that declares no viewer subfolder; a value like "/PotreeViewer" would
            # append one to the per-file aux preview prefix.
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def test_forwards_locations_and_preserves_aux_override(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/scan.e57"
        # Potree writes to the per-input-file aux preview location: auxBucket + the file's own
        # aux preview prefix + the per-pipeline viewer subfolder. auxPreviewPipelineSuffix is empty
        # here, so the pipeline applies its own "PotreeViewer" default to stay intact.
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux-bkt/dbM/xidM/scan.e57/preview/PotreeViewer"

    def test_uses_manifest_pipeline_prefix_when_present(self):
        # When the manifest supplies a viewer subfolder, it is used instead of the hardcoded
        # fallback.
        mod = self._load()
        manifest = self._manifest()
        manifest["auxPreviewPipelineSuffix"] = "/CustomViewer"
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux-bkt/dbM/xidM/scan.e57/preview/CustomViewer"
        # The container writes only to the aux preview location, but the resolved asset output
        # paths are still forwarded rather than blanked.
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/dbM/xidM/"
        assert payload["outputS3AssetPreviewPath"] == "s3://abkt/dbM/xidM/preview/"
        assert payload["outputS3AssetMetadataPath"] == "s3://abkt/dbM/xidM/metadata/"
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload


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

    def test_missing_task_token_reports_no_callback(self):
        """No token to fail: the handler still reports 500 and issues no callback."""
        mod = self._load()
        body = self._body()
        body.pop("TaskToken")
        s3 = MagicMock()
        send_failure = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500
        send_failure.assert_not_called()

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


@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xidM/scan.e57",
            "outputS3AssetFilesPath": "",
            "outputS3AssetPreviewPath": "",
            "outputS3AssetMetadataPath": "",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux-bkt/xidM/scan.e57/preview/PotreeViewer",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:PcPotree:PipelineJob_x",
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
        assert json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])["pipelineExecutionId"] == "P1"

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock(side_effect=Exception("denied"))):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200

    def test_registers_one_container_log_source_per_batch_stage(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), MagicMock())
        detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert detail["subExecution"]["label"] == "Potree viewer processing"
        sfn_log, pdal_log, potree_log = detail["logs"]
        assert sfn_log["sourceType"] == "stateMachine"
        assert sfn_log["label"] == "Potree viewer state machine"
        assert pdal_log["stageName"] == "PdalConverterBatchJob"
        assert pdal_log["logStreamPrefix"] == "PcPotreePdalJobDefabc1234567/default/"
        assert pdal_log["label"] == "PdalConverterBatchJob container"
        assert potree_log["stageName"] == "PotreeConverterBatchJob"
        assert potree_log["logStreamPrefix"] == "PcPotreePotreeJobDefabc1234567/default/"
        # Each stage registers the group ITS job definition writes to, not a shared Batch default.
        assert pdal_log["logGroupName"] == "/aws/vendedlogs/Pipelines/PcPotreeViewerPDALabc1234567"
        assert potree_log["logGroupName"] == "/aws/vendedlogs/Pipelines/PcPotreeViewerPotreedef7654321"
        assert pdal_log["logGroupArn"] != potree_log["logGroupArn"]
        for entry in (pdal_log, potree_log):
            assert entry["sourceType"] == "batch"
            assert entry["logGroupName"] in entry["logGroupArn"]
            assert entry["logStreamName"] == ""
        validators = _backend_validators()
        for entry in (pdal_log, potree_log):
            assert validators.validate_cloudwatch_log_group_arn("logGroupArn", entry["logGroupArn"])[0]
            assert validators.validate_log_stream_name("logStreamPrefix", entry["logStreamPrefix"])[0]
        assert not validators.validate_cloudwatch_log_group_arn(
            "logGroupArn", "arn:aws:logs:us-east-1:123456789012:log-group//aws/vendedlogs/x")[0]

    def test_only_the_configured_converter_gets_a_container_log_source(self):
        with patch.dict(os.environ):
            os.environ.pop("POTREE_JOB_DEFINITION_NAME", None)
            mod = self._load()
        start = self._mock_start()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), MagicMock())
        detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert [log.get("stageName") for log in detail["logs"]] == [None, "PdalConverterBatchJob"]


@pytest.mark.unit
class TestConstructAndContainerContract:
    def _construct(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def _container_pipeline_definition(self):
        import importlib.util
        path = os.path.normpath(os.path.join(
            _LAMBDA_DIR, "..", "container", "utils", "pipeline", "objects.py"))
        spec = importlib.util.spec_from_file_location("pcpotree_objects", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PipelineDefinition

    def test_definition_carries_locations_and_instantiates_container(self):
        mod = self._construct()
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/scan.e57",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux-bkt/xidM/scan.e57/preview/PotreeViewer",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
        }
        out = mod.lambda_handler(event, MagicMock())
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out and "inputParameters" not in out
        # The serialized definition (the container command arg) must instantiate PipelineDefinition.
        definition_dict = json.loads(out["definition"][0])
        PipelineDefinition = self._container_pipeline_definition()
        d = PipelineDefinition(**definition_dict)
        assert d.inputMetadataS3Location == "s3://abkt/.../metadata.json"
        assert d.inputConfigurationS3Location == "s3://abkt/.../config.json"
