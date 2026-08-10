#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the genAi/nvidia/cosmos/reason pipeline: the
vamsExecute lambda threading metadata + input-configuration S3 LOCATIONS (never inline content)
while resolving the cosmos prompt at the boundary, openPipeline location threading + sub-process
registration, and the constructPipeline definition carrying the locations only.

The cosmos/reason pipeline has a single vamsExecute entry point
(vamsExecuteCosmosReasonPipeline) and no sqsExecute auto-trigger lambda."""

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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CosmosReason",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".mp4,.mov,.jpg,.jpeg,.png,.webp",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/CosmosReason",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/CosmosReason:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


# ============================ vamsExecute (vamsExecuteCosmosReasonPipeline) ============================

@pytest.mark.unit
class TestVamsExecuteCosmosReasonPipeline:
    def _load(self):
        if "vamsExecuteCosmosReasonPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteCosmosReasonPipeline"])
        return importlib.import_module("vamsExecuteCosmosReasonPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/genAi/cosmos/reason/",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"fileMetadata": {"COSMOS_REASON_PROMPT": "Describe the scene."}}},
            "inputParameters": '{"PROMPT": "Describe the scene."}',
            "executingUserName": "user@x",
            "assetId": "legacyAsset",
            "databaseId": "legacyDb",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/test/clip.mp4", "assetId": "xidM",
                            "databaseId": "dbM", "assetRootS3Key": "xidM/",
                            "auxPreviewPrefix": "dbM/xidM/test/clip.mp4/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/cosmosReason/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _metadata_envelope(self, prompt="Describe the scene."):
        # The metadata file is a {schemaVersion, metadata} envelope; fetch_metadata unwraps it.
        return {"schemaVersion": 1,
                "metadata": {"VAMS": {"fileMetadata": {"COSMOS_REASON_PROMPT": prompt}}}}

    def _s3_reader(self, manifest, metadata_envelope, config):
        """An s3 mock whose get_object returns the right body per key suffix
        (manifest / metadata / config)."""
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
                             {"PROMPT": "Describe the scene."})
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # Manifest-resolved input + identity + outputs + aux.
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/test/clip.mp4"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/cosmosReason/E1/"
        assert payload["assetId"] == "xidM"
        assert payload["databaseId"] == "dbM"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        # The metadata + input-configuration S3 LOCATIONS are forwarded, never the inline content.
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
        # This test compares the metadata SOURCE (S3 envelope vs inline legacy field), not precedence,
        # so BOTH input-configuration sources must be empty — the prompt resolves config-first, and
        # _body() otherwise supplies an inline `inputParameters` prompt that would legitimately win.
        s3 = self._s3_reader(self._manifest(), self._metadata_envelope(prompt="Caption everything."),
                             {})
        body_no_config = self._body()
        body_no_config.pop("inputParameters")
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            mod.lambda_handler({"body": json.dumps(body_no_config)}, MagicMock())
        payload_from_s3 = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

        # Now the legacy path: no manifest, no config location read; metadata comes inline with the
        # same VAMS shape the S3 envelope unwraps to.
        legacy_body = self._body()
        legacy_body.pop("inputManifestS3Location")
        legacy_body.pop("inputConfigurationS3Location")
        legacy_body.pop("inputParameters")
        legacy_body["inputMetadata"] = {"VAMS": {"fileMetadata": {"COSMOS_REASON_PROMPT": "Caption everything."}}}
        s3_fail = MagicMock()
        s3_fail.get_object.side_effect = Exception("no manifest/metadata in S3")
        invoke2 = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3_fail), patch.object(mod.lambda_client, "invoke", invoke2):
            mod.lambda_handler({"body": json.dumps(legacy_body)}, MagicMock())
        payload_inline = json.loads(invoke2.call_args.kwargs["Payload"].decode("utf-8"))

        assert payload_from_s3["cosmosPrompt"] == "Caption everything."
        assert payload_inline["cosmosPrompt"] == "Caption everything."
        assert payload_from_s3["cosmosPrompt"] == payload_inline["cosmosPrompt"]

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
        # assetId no longer falls back to the SFN body; without a manifest it is empty
        # (only the manifest's first input file supplies it).
        assert payload["assetId"] == ""
        assert payload["databaseId"] == ""
        # config location still threaded from the body; no inline content forwarded.
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload
        # Prompt resolves from the inline legacy metadata field on the fallback path.
        assert payload["cosmosPrompt"] == "Describe the scene."

    def test_default_prompt_when_none_supplied(self):
        # Reason's prompt is optional; with no metadata/config prompt present, the boundary
        # supplies the sensible default.
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200})
        body = self._body()
        body.pop("inputManifestS3Location")
        body.pop("inputMetadata")
        body.pop("inputParameters")
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["cosmosPrompt"] == "Caption the video in detail."

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
            "inputS3AssetFilePath": "s3://abkt/xidM/test/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/clip.mp4/genAi/cosmos/reason/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "assetId": "xidM",
            "databaseId": "dbM",
            "cosmosPrompt": "Caption everything.",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:CosmosReason:cosmos-reason-x",
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
        # The nested SFN input carries the config + metadata LOCATIONS, never inline content.
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
        # pipelineExecutionId is parsed from the orchestration event prefix trailing segment.
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["executionArn"].endswith("cosmos-reason-x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/CosmosReason"

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
        put_events.assert_not_called()  # no prefix -> no registration, pipeline still starts

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock(side_effect=Exception("AccessDenied: events:PutEvents"))
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(self._event(), MagicMock())
        # Registration raised, but the pipeline start still succeeds.
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
            "modelType": "reason",
            "cosmosPrompt": "Caption everything.",
            "inputS3AssetFilePath": "s3://abkt/xidM/test/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/clip.mp4/genAi/cosmos/reason/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
            "assetId": "xidM",
        }
        out = mod.lambda_handler(event, MagicMock())
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
        # The container command embeds the definition JSON, which carries the locations only.
        definition = json.loads(out["definition"][2])
        assert definition["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert definition["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert definition["assetId"] == "xidM"
        assert "inputMetadata" not in definition
        assert "inputParameters" not in definition


# ============================ container reads metadata/config from S3 ============================

@pytest.mark.unit
class TestContainerReadsFromS3:
    """The container manifest_io reads metadata + config from the S3 locations in the definition
    (consumer-reads-from-S3). Loads the container module from its separate code asset."""

    def _container_manifest_io(self):
        import importlib.util
        path = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "container", "manifest_io.py"))
        spec = importlib.util.spec_from_file_location("reason_container_manifest_io", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_metadata_envelope_unwrapped_and_config_parsed(self):
        mio = self._container_manifest_io()
        envelope = {"schemaVersion": 1,
                    "metadata": {"VAMS": {"fileMetadata": {"COSMOS_REASON_PROMPT": "x"}}}}
        cfg = {"PROMPT": "x"}
        with patch.object(mio, "_get_json", MagicMock(return_value=envelope)):
            md = mio.fetch_metadata("s3://abkt/.../metadata.json")
        assert md == {"VAMS": {"fileMetadata": {"COSMOS_REASON_PROMPT": "x"}}}
        # fetch_input_configuration reads the RAW text and parses it itself, so a present-but-
        # unparseable body raises instead of degrading to {} (which would look like "no
        # configuration" and silently drop the caller's parameters).
        with patch.object(mio, "_read_text", MagicMock(return_value=json.dumps(cfg))):
            got = mio.fetch_input_configuration("s3://abkt/.../config.json")
        assert got == cfg
        # best-effort: empty location -> {}
        assert mio.fetch_metadata("") == {}
        assert mio.fetch_input_configuration("") == {}
