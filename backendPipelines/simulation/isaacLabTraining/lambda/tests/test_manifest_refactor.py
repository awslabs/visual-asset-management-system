#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the simulation/isaacLabTraining pipeline. This
pipeline has no constructPipeline lambda — vamsExecute IS the entry point that reads the input
configuration from S3 (to extract trainingConfig/computeConfig), threads metadata + config S3
LOCATIONS into the internal SFN (never inline content), and best-effort registers the sub-SFN
execution. openPipeline threads the locations into the job-config return."""

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
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:IsaacLab",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "",
    "STATE_MACHINE_LOG_GROUP_ARN": "",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteIsaacLabPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteIsaacLabPipeline"])
        return importlib.import_module("vamsExecuteIsaacLabPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/scene.usd",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "",
            "outputS3AssetMetadataPath": "",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/isaac",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"k": "v"}},
            "inputParameters": '{"trainingConfig": {"x": 1}}',
            "bucketAsset": "abkt",
            "inputAssetLocationKey": "xidM/",
            "inputAssetFileKey": "xidM/scene.usd",
            "assetId": "legacyAsset",
            "databaseId": "legacyDb",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/scene.usd", "assetId": "xidM", "databaseId": "dbM"}],
            "outputs": {"files": "s3://abkt/pipelines/p1/MJOB/output/E1/files/"},
            "auxTempPrefix": "s3://aux/xidM/scene.usd/isaac/",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _s3_for(self, manifest, config):
        """An S3 client whose get_object returns the manifest then the config (by key)."""
        s3 = MagicMock()

        def _get_object(Bucket, Key, **kw):
            if Key.endswith("manifest.json"):
                return {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
            if Key.endswith("config.json"):
                return {"Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
            raise Exception(f"unexpected key {Key}")

        s3.get_object.side_effect = _get_object
        return s3

    def test_reads_config_from_s3_threads_locations_and_registers(self):
        mod = self._load()
        config = {"trainingConfig": {"epochs": 10}, "computeConfig": {"numNodes": 2}}
        s3 = self._s3_for(self._manifest(), config)
        start = MagicMock(return_value={"executionArn": "arn:aws:states:us-east-1:1:execution:IsaacLab:isaaclab-training-abcd1234"})
        put_events = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn_client, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        # Config read from S3 -> trainingConfig/computeConfig extracted at the boundary.
        assert sfn_input["trainingConfig"] == {"epochs": 10}
        assert sfn_input["computeConfig"] == {"numNodes": 2}
        # Manifest-resolved input + identity.
        assert sfn_input["inputS3AssetFilePath"] == "s3://abkt/xidM/scene.usd"
        assert sfn_input["assetId"] == "xidM"
        # Locations threaded, never inline content.
        assert sfn_input["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert sfn_input["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in sfn_input
        assert "inputParameters" not in sfn_input
        # External task token preserved (dual-token model).
        assert sfn_input["externalSfnTaskToken"] == "tok-123"
        # Sub-SFN registered.
        entry = put_events.call_args.kwargs["Entries"][0]
        assert json.loads(entry["Detail"])["pipelineExecutionId"] == "P1"
        assert json.loads(entry["Detail"])["subExecution"]["executionArn"].endswith("isaaclab-training-abcd1234")

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        s3 = self._s3_for(self._manifest(), {"trainingConfig": {}})
        start = MagicMock(return_value={"executionArn": "arn:ex"})
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn_client, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock(side_effect=Exception("denied"))):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200

    def test_missing_task_token_errors(self):
        mod = self._load()
        s3 = self._s3_for(self._manifest(), {})
        body = self._body()
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500

    def test_no_task_token_skips_the_callback(self):
        # A direct invoke carries no token; the callback must be skipped rather than crash.
        mod = self._load()
        s3 = self._s3_for(self._manifest(), {})
        body = self._body()
        del body["TaskToken"]
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500
        sfn.send_task_failure.assert_not_called()

    def test_missing_body_skips_the_callback(self):
        # The 400 early return happens before the body is parsed, so no token is known yet.
        mod = self._load()
        sfn = MagicMock()
        with patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({}, MagicMock())
        assert resp["statusCode"] == 400
        sfn.send_task_failure.assert_not_called()

    def test_multi_file_manifest_fails_the_task_token(self):
        # enforce_single_input_file rejects the run before the internal SFN starts; the workflow
        # task waits on the callback token, so the rejection must be reported, not only returned.
        mod = self._load()
        manifest = self._manifest()
        manifest["inputFiles"].append(
            {"bucket": "abkt", "key": "xidM/second.usd", "assetId": "xidM", "databaseId": "dbM"})
        s3 = self._s3_for(manifest, {})
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        sfn.start_execution.assert_not_called()
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_bad_input_configuration_fails_the_task_token(self):
        # fetch_input_configuration raises InputConfigurationError for a malformed config body.
        mod = self._load()
        s3 = MagicMock()

        def _get_object(Bucket, Key, **kw):
            if Key.endswith("manifest.json"):
                return {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))}
            return {"Body": MagicMock(read=lambda: b"not json at all")}

        s3.get_object.side_effect = _get_object
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        sfn.start_execution.assert_not_called()
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_start_execution_failure_fails_the_task_token(self):
        mod = self._load()
        s3 = self._s3_for(self._manifest(), {"trainingConfig": {}})
        sfn = MagicMock()
        sfn.start_execution.side_effect = Exception("state machine unavailable")
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"
        assert sfn.send_task_failure.call_args.kwargs["error"] == "IsaacLabPipelineError"
        assert len(sfn.send_task_failure.call_args.kwargs["cause"]) <= 256


@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "jobName": "isaaclab-training-abcd1234",
            "mode": "train",
            "trainingConfig": {"epochs": 10},
            "computeConfig": {"numNodes": 1},
            "inputS3AssetFilePath": "s3://abkt/xidM/scene.usd",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/scene.usd/isaac/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok-123",
        }

    def test_open_threads_locations_not_content(self):
        mod = self._load()
        # The optional S3 file-config load returns nothing; the build uses the event config.
        s3 = MagicMock(get_object=MagicMock(side_effect=Exception("no file config")))
        with patch.object(mod, "s3_client", s3):
            out = mod.lambda_handler(self._event(), MagicMock())
        # openPipeline returns the job-config envelope carrying the LOCATIONS (not inline content).
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
