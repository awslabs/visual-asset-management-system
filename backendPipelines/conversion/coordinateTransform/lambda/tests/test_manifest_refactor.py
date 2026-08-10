#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the conversion/coordinateTransform pipeline:
vamsExecute resolves inputs from the manifest envelope and threads metadata +
input-configuration S3 LOCATIONS (never inline content), openPipeline threads the locations +
registers the sub-SFN with the orchestration bus, and constructPipeline reads the input
configuration + shared metadata from S3 to build the transform definition."""

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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CoordTransform",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".e57,.las,.laz,.ply",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/CoordTransform",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/CoordTransform:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteCoordinateTransformPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteCoordinateTransformPipeline"])
        return importlib.import_module("vamsExecuteCoordinateTransformPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidC/scan.e57", "assetId": "xidC", "databaseId": "dbC"}],
            "outputs": {"files": "s3://abkt/pipelines/p1/CJOB/output/E1/files/",
                        "metadata": "s3://abkt/pipelines/p1/CJOB/output/E1/metadata/"},
            "auxTempPrefix": "s3://aux/xidC/scan.e57/pipelines/coordinateTransform/",
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
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidC/scan.e57"
        assert payload["assetId"] == "xidC" and payload["databaseId"] == "dbC"
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_missing_task_token_errors(self):
        mod = self._load()
        body = self._body()
        del body["TaskToken"]
        sfn = MagicMock()
        with patch.object(mod, "sfn_client", sfn):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500
        # No token to report against, so no callback is attempted.
        sfn.send_task_failure.assert_not_called()

    def test_pre_invoke_failure_fails_the_task_token(self):
        # A multi-file manifest is rejected before the pipeline starts; the workflow task waits on
        # the callback token, so the rejection must be reported rather than only returned.
        mod = self._load()
        manifest = self._manifest()
        manifest["inputFiles"].append(
            {"bucket": "abkt", "key": "xidC/second.e57", "assetId": "xidC", "databaseId": "dbC"})
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


@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xidC/scan.e57",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/CJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidC/scan.e57/pipelines/coordinateTransform",
            "assetId": "xidC",
            "databaseId": "dbC",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:CoordTransform:CoordXform_x",
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
        assert sfn_input["assetId"] == "xidC" and sfn_input["databaseId"] == "dbC"
        assert "inputParameters" not in sfn_input and "inputMetadata" not in sfn_input
        entry = put_events.call_args.kwargs["Entries"][0]
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["executionArn"].endswith("CoordXform_x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock(side_effect=Exception("denied"))):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200

    def test_rejects_disallowed_extension(self):
        mod = self._load()
        event = self._event()
        event["inputS3AssetFilePath"] = "s3://abkt/xidC/model.glb"
        send_failure = MagicMock()
        with patch.object(mod.sfn, "send_task_failure", send_failure):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 400
        send_failure.assert_called_once()


@pytest.mark.unit
class TestConstructPipelineReadsFromS3:
    def _load(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def _event(self):
        return {
            "jobName": "CoordXform_x",
            "inputS3AssetFilePath": "s3://abkt/xidC/scan.e57",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/CJOB/output/E1/files/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/CJOB/output/E1/metadata/",
            "assetId": "xidC",
            "databaseId": "dbC",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "externalSfnTaskToken": "tok-123",
        }

    def _s3_returning(self, mapping):
        """s3 stub returning per-key JSON bodies (keyed by S3 object key)."""
        def get_object(Bucket, Key):
            return {"Body": MagicMock(read=lambda: json.dumps(mapping[Key]).encode("utf-8"))}
        s3 = MagicMock()
        s3.get_object.side_effect = get_object
        return s3

    def test_reads_config_and_metadata_from_s3(self):
        mod = self._load()
        s3 = self._s3_returning({
            "pipelines/workflowExecutionInputs/E1/pipeline1/config.json":
                {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700", "outputFormats": ["laz"]},
            "pipelines/workflowExecutionInputs/E1/metadata.json":
                {"schemaVersion": 1, "metadata": {"VAMS": {"assetMetadata": {"targetCrs": "EPSG:3857"}}}},
        })
        with patch.object(mod, "s3", s3):
            result = mod.lambda_handler(self._event(), MagicMock())
        definition = json.loads(result["definition"][0])
        params = json.loads(definition["inputParameters"])
        # Config comes from S3; the asset-metadata targetCrs override wins.
        assert params["sourceCrs"] == "EPSG:4326"
        assert params["targetCrs"] == "EPSG:3857"
        assert params["outputFormats"] == ["laz"]
        stage = definition["stages"][0]
        assert stage["inputFile"]["bucketName"] == "abkt"
        assert stage["inputFile"]["objectKey"] == "xidC/scan.e57"
        assert definition["externalSfnTaskToken"] == "tok-123"

    def test_grouped_metadata_envelope_overrides_config(self):
        """The run metadata file is the grouped-by-asset envelope; the asset-level override for
        this pipeline's (databaseId, assetId, fileKey) still wins over the config value."""
        mod = self._load()
        s3 = self._s3_returning({
            "pipelines/workflowExecutionInputs/E1/pipeline1/config.json":
                {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700", "outputFormats": ["laz"]},
            "pipelines/workflowExecutionInputs/E1/metadata.json": {
                "schemaVersion": 2,
                "assets": [{
                    "databaseId": "dbC", "assetId": "xidC",
                    "assetData": {"assetName": "Site scan"},
                    "files": [
                        {"fileKey": "/", "metadata": {"targetCrs": "EPSG:3857",
                                                      "outputFormats": "las,ply"}},
                        {"fileKey": "/scan.e57", "metadata": {}, "attributes": {}},
                    ],
                }],
            },
        })
        with patch.object(mod, "s3", s3):
            result = mod.lambda_handler(self._event(), MagicMock())
        definition = json.loads(result["definition"][0])
        params = json.loads(definition["inputParameters"])
        assert params["sourceCrs"] == "EPSG:4326"
        assert params["targetCrs"] == "EPSG:3857"
        assert params["outputFormats"] == ["las", "ply"]
        # The container consumes the legacy-projected metadata view.
        assert json.loads(definition["inputMetadata"])["VAMS"]["assetMetadata"]["targetCrs"] == "EPSG:3857"

    def test_grouped_metadata_envelope_for_other_asset_does_not_override(self):
        mod = self._load()
        s3 = self._s3_returning({
            "pipelines/workflowExecutionInputs/E1/pipeline1/config.json":
                {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700"},
            "pipelines/workflowExecutionInputs/E1/metadata.json": {
                "schemaVersion": 2,
                "assets": [{
                    "databaseId": "dbOther", "assetId": "xidOther", "assetData": {},
                    "files": [{"fileKey": "/", "metadata": {"targetCrs": "EPSG:3857"}}],
                }],
            },
        })
        with patch.object(mod, "s3", s3):
            result = mod.lambda_handler(self._event(), MagicMock())
        params = json.loads(json.loads(result["definition"][0])["inputParameters"])
        assert params["targetCrs"] == "EPSG:27700"

    def test_no_config_no_metadata_yields_empty_params(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("missing")
        event = self._event()
        with patch.object(mod, "s3", s3):
            result = mod.lambda_handler(event, MagicMock())
        definition = json.loads(result["definition"][0])
        assert definition["inputParameters"] == ""
        assert definition["inputMetadata"] == ""
