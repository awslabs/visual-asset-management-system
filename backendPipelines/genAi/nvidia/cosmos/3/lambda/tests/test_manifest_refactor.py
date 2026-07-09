#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the genAi/nvidia/cosmos/3 (Cosmos 3 omni) pipeline:
vamsExecute threads metadata + input-configuration S3 LOCATIONS (never inline content) while
extracting the COSMOS3_* generation fields at the boundary, openPipeline location threading +
sub-process registration, and the constructPipeline definition carrying the locations only.

The cosmos/3 pipeline has a single vamsExecute entry point (vamsExecuteCosmos3Pipeline) and no
sqsExecute auto-trigger lambda."""

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

# Stub customLogging so the lambdas import without aws_lambda_powertools, and set the env vars the
# lambdas read at import time, BEFORE importing any lambda module.
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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:Cosmos3",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".mp4,.mov,.jpg,.jpeg,.png,.webp",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/Cosmos3",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/Cosmos3:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


# ============================ vamsExecute (vamsExecuteCosmos3Pipeline) ============================

@pytest.mark.unit
class TestVamsExecuteCosmos3Pipeline:
    def _load(self):
        if "vamsExecuteCosmos3Pipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteCosmos3Pipeline"])
        return importlib.import_module("vamsExecuteCosmos3Pipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/genAi/cosmos/3/",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"fileMetadata": {"COSMOS3_PROMPT": "A drone shot."}}},
            "inputParameters": '{"MODEL_VARIANT": "nano", "TASK_MODE": "image2video"}',
            "executingUserName": "user@x",
            "assetId": "legacyAsset",
            "databaseId": "legacyDb",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/clip.mp4", "assetId": "xidM",
                            "databaseId": "dbM", "assetRootS3Key": "xidM/",
                            "auxPreviewPrefix": "dbM/xidM/clip.mp4/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/cosmos3/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _metadata_envelope(self, prompt="A drone shot."):
        # The metadata file is a {schemaVersion, metadata} envelope; fetch_metadata unwraps it.
        return {"schemaVersion": 1,
                "metadata": {"VAMS": {"fileMetadata": {"COSMOS3_PROMPT": prompt}}}}

    def _s3_reader(self, manifest, metadata_envelope, config):
        """An s3 mock whose get_object returns the right body per key suffix."""
        def get_object(Bucket, Key):
            if Key.endswith("manifest.json"):
                payload = manifest
            elif Key.endswith("metadata.json"):
                payload = metadata_envelope
            elif Key.endswith("config.json"):
                payload = config
            else:
                raise Exception(f"unexpected key {Key}")
            return {"Body": MagicMock(read=lambda p=payload: json.dumps(p).encode("utf-8"))}
        s3 = MagicMock()
        s3.get_object.side_effect = get_object
        return s3

    def test_forwards_locations_not_inline_content(self):
        # The open-pipeline invoke payload forwards the metadata + config S3 LOCATIONS and the
        # orchestrationEventPrefix; it carries NO inline inputMetadata/inputParameters content.
        mod = self._load()
        s3 = self._s3_reader(self._manifest(), self._metadata_envelope(),
                             {"MODEL_VARIANT": "nano", "TASK_MODE": "image2video"})
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # Manifest-resolved input + identity + outputs + aux.
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/clip.mp4"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/cosmos3/E1/"
        assert payload["assetId"] == "xidM"
        assert payload["databaseId"] == "dbM"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        # COSMOS3 generation fields extracted at the boundary.
        assert payload["cosmosPrompt"] == "A drone shot."
        assert payload["modelVariant"] == "nano"
        assert payload["taskMode"] == "image2video"
        # The metadata + input-configuration S3 LOCATIONS are forwarded, never inline content.
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload
        # The orchestration event prefix rides along so openPipeline can register its sub-SFN.
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"

    def test_prompt_same_value_whether_read_from_s3_or_inline(self):
        # The boundary prompt extraction produces the SAME value when metadata is read from S3
        # (envelope-wrapped) as it would from the inline legacy field.
        mod = self._load()
        s3 = self._s3_reader(self._manifest(), self._metadata_envelope(prompt="Sweeping landscape."),
                             {"MODEL_VARIANT": "nano", "TASK_MODE": "image2video"})
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        payload_from_s3 = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

        legacy_body = self._body()
        legacy_body.pop("inputManifestS3Location")
        legacy_body.pop("inputConfigurationS3Location")
        legacy_body["inputMetadata"] = {"VAMS": {"fileMetadata": {"COSMOS3_PROMPT": "Sweeping landscape."}}}
        s3_fail = MagicMock()
        s3_fail.get_object.side_effect = Exception("no manifest/metadata in S3")
        invoke2 = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3_fail), patch.object(mod.lambda_client, "invoke", invoke2):
            mod.lambda_handler({"body": json.dumps(legacy_body)}, MagicMock())
        payload_inline = json.loads(invoke2.call_args.kwargs["Payload"].decode("utf-8"))

        assert payload_from_s3["cosmosPrompt"] == "Sweeping landscape."
        assert payload_inline["cosmosPrompt"] == "Sweeping landscape."

    def test_legacy_fallback_without_manifest(self):
        # No manifest pointer + S3 reads fail -> resolve uses the legacy payload fields.
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
        assert payload["inputS3AssetFilePath"] == "s3://abkt/legacy/clip.mp4"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/legacy/files/"
        # assetId no longer falls back to the SFN body; without a manifest it is empty.
        assert payload["assetId"] == ""
        assert payload["databaseId"] == ""
        # config location still threaded from the body; no inline content forwarded.
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload
        # Prompt resolves from the inline legacy metadata field on the fallback path.
        assert payload["cosmosPrompt"] == "A drone shot."

    def test_missing_task_token_errors(self):
        mod = self._load()
        s3 = MagicMock()
        body = self._body()
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
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
            "modelVariant": "nano",
            "taskMode": "image2video",
            "cosmosPrompt": "A drone shot.",
            "inputS3AssetFilePath": "s3://abkt/xidM/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/cosmos3/E1/",
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
            "executionArn": "arn:aws:states:us-east-1:1:execution:Cosmos3:cosmos3-nano-x",
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
        assert sfn_input["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert sfn_input["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert sfn_input["externalSfnTaskToken"] == "tok-123"
        assert "inputMetadata" not in sfn_input
        assert "inputParameters" not in sfn_input

    def test_registers_sub_execution_on_orchestration_bus(self):
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
        assert detail["subExecution"]["executionArn"].endswith("cosmos3-nano-x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/Cosmos3"

    def test_registration_skipped_without_event_prefix(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock()
        ev = self._event()
        ev.pop("orchestrationEventPrefix")
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(ev, MagicMock())
        assert resp["statusCode"] == 200
        put_events.assert_not_called()

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock(side_effect=Exception("AccessDenied: events:PutEvents"))
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

    def _event(self):
        return {
            "modelVariant": "nano",
            "taskMode": "image2video",
            "cosmosPrompt": "A drone shot.",
            "inputS3AssetFilePath": "s3://abkt/xidM/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/cosmos3/E1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
            "assetId": "xidM",
            "databaseId": "dbM",
        }

    def test_definition_carries_locations_not_content(self):
        mod = self._load()
        out = mod.lambda_handler(self._event(), MagicMock())
        # Top-level result forwards the LOCATIONS, never inline content.
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
        # The container definition (argv JSON) carries the locations too, and the generation fields.
        definition = json.loads(out["definition"][2])
        assert definition["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert definition["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert definition["cosmosPrompt"] == "A drone shot."
        assert definition["modelVariant"] == "nano"
        assert "inputMetadata" not in definition
        assert "inputParameters" not in definition
