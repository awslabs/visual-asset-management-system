#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the genAi/nvidia/cosmos/predict pipeline: the
vamsExecute lambdas (Text2World + Video2World) threading metadata + input-configuration S3
LOCATIONS (never inline content) while extracting the COSMOS_PREDICT_PROMPT at the boundary,
openPipeline location threading + sub-process registration, and the constructPipeline definition
carrying locations only. The container is unchanged (it reads metadata/config from S3)."""

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

# The vamsExecute + openPipeline lambdas create boto3 clients and read env at import time.
for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CosmosPredict",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".mp4,.mov,.jpg,.jpeg,.png,.webp",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/CosmosPredict",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/CosmosPredict:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


# ============================ vamsExecute (both entry points) ============================

# Each entry point reads the prompt from a different VAMS metadata section: Text2World from
# assetMetadata, Video2World from fileMetadata. Parametrize the shared assertions across both.
_ENTRYPOINTS = {
    "text2world": {
        "module": "vamsExecuteCosmosText2WorldPipeline",
        "metadataSection": "assetMetadata",
        "modelType": "text2world",
        # Text2World ignores the input file (prompt-only generation).
        "expectedInputPath": "",
    },
    "video2world": {
        "module": "vamsExecuteCosmosVideo2WorldPipeline",
        "metadataSection": "fileMetadata",
        "modelType": "video2world",
        # Video2World forwards the manifest-resolved input file.
        "expectedInputPath": "s3://abkt/xidM/clip.mp4",
    },
}


@pytest.mark.unit
@pytest.mark.parametrize("variant", list(_ENTRYPOINTS), ids=list(_ENTRYPOINTS))
class TestVamsExecute:
    def _load(self, variant):
        name = _ENTRYPOINTS[variant]["module"]
        if name in sys.modules:
            return importlib.reload(sys.modules[name])
        return importlib.import_module(name)

    def _body(self, variant):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/genAi/cosmos/predict",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            # Inline content: the handler reads the S3 metadata/configuration objects first and only
            # falls back to these fields when the object is absent (transition support). The prompt
            # resolves CONFIG-FIRST, so an inline configuration prompt is a real source — tests that
            # measure the metadata SOURCE rather than precedence drop it explicitly.
            "inputMetadata": {"VAMS": {"assetMetadata": {"X": "y"}}},
            "inputParameters": '{"PROMPT": "from-inline-config"}',
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
            "auxTempPrefix": "pipelines/cosmosPredict/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _metadata_envelope(self, variant, prompt="a calm robot"):
        section = _ENTRYPOINTS[variant]["metadataSection"]
        return {"schemaVersion": 1, "metadata": {"VAMS": {section: {"COSMOS_PREDICT_PROMPT": prompt}}}}

    def _s3_with(self, metadata_body, config_body=None):
        # Return metadata for the metadata location and config for the config location.
        s3 = MagicMock()

        def get_object(Bucket, Key):
            if Key.endswith("metadata.json"):
                payload = metadata_body
            elif Key.endswith("config.json"):
                payload = config_body if config_body is not None else {}
            else:
                raise Exception("unexpected key")
            return {"Body": MagicMock(read=lambda p=payload: json.dumps(p).encode("utf-8"))}

        s3.get_object.side_effect = get_object
        return s3

    def test_forwards_locations_not_content(self, variant):
        mod = self._load(variant)
        cfg = _ENTRYPOINTS[variant]
        # First get_object is the manifest fetch; serve manifest there, then metadata/config.
        manifest = self._manifest()
        envelope = self._metadata_envelope(variant)
        s3 = MagicMock()

        def get_object(Bucket, Key):
            if Key.endswith("manifest.json"):
                payload = manifest
            elif Key.endswith("metadata.json"):
                payload = envelope
            elif Key.endswith("config.json"):
                payload = {}
            else:
                raise Exception("unexpected key")
            return {"Body": MagicMock(read=lambda p=payload: json.dumps(p).encode("utf-8"))}

        s3.get_object.side_effect = get_object
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body(variant))}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # Manifest-resolved values flow through.
        assert payload["modelType"] == cfg["modelType"]
        assert payload["inputS3AssetFilePath"] == cfg["expectedInputPath"]
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/cosmosPredict/E1/"
        assert payload["assetId"] == "xidM"
        assert payload["databaseId"] == "dbM"
        # LOCATIONS travel, never inline content (vamsExecute is the content boundary).
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_prompt_extracted_at_boundary_from_metadata(self, variant):
        # The prompt content read from S3 metadata is the value forwarded as cosmosPrompt.
        mod = self._load(variant)
        manifest = self._manifest()
        envelope = self._metadata_envelope(variant, prompt="a calm robot")
        s3 = MagicMock()

        def get_object(Bucket, Key):
            if Key.endswith("manifest.json"):
                payload = manifest
            elif Key.endswith("metadata.json"):
                payload = envelope
            else:
                payload = {}
            return {"Body": MagicMock(read=lambda p=payload: json.dumps(p).encode("utf-8"))}

        s3.get_object.side_effect = get_object
        invoke = MagicMock(return_value={"StatusCode": 200})
        # No configuration prompt: this test measures that the prompt is read from the S3 METADATA at
        # the boundary. The prompt resolves config-first, so a configuration value would win and the
        # test would no longer be measuring what it claims.
        body = self._body(variant)
        body.pop("inputParameters")
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["cosmosPrompt"] == "a calm robot"

    def test_prompt_same_value_whether_metadata_from_s3_or_inline(self, variant):
        # The boundary extraction must produce the SAME prompt whether the metadata is read from
        # S3 (manifest path) or only present inline (legacy fallback, no manifest).
        cfg = _ENTRYPOINTS[variant]
        prompt = "identical prompt"

        # (a) Read from S3: manifest present, metadata envelope at the manifest location.
        mod = self._load(variant)
        manifest = self._manifest()
        envelope = self._metadata_envelope(variant, prompt=prompt)
        s3a = MagicMock()

        def get_object_a(Bucket, Key):
            if Key.endswith("manifest.json"):
                payload = manifest
            elif Key.endswith("metadata.json"):
                payload = envelope
            else:
                payload = {}
            return {"Body": MagicMock(read=lambda p=payload: json.dumps(p).encode("utf-8"))}

        s3a.get_object.side_effect = get_object_a
        invoke_a = MagicMock(return_value={"StatusCode": 200})
        # No configuration prompt in either arm: this compares the metadata SOURCE (S3 envelope vs
        # inline legacy field). The prompt resolves config-first, so a configuration value would win in
        # both arms and the comparison would prove nothing.
        body_a = self._body(variant)
        body_a.pop("inputParameters")
        with patch.object(mod, "s3_client", s3a), patch.object(mod.lambda_client, "invoke", invoke_a):
            mod.lambda_handler({"body": json.dumps(body_a)}, MagicMock())
        prompt_from_s3 = json.loads(invoke_a.call_args.kwargs["Payload"].decode("utf-8"))["cosmosPrompt"]

        # (b) Legacy inline fallback: no manifest, no metadata file; inline metadata carries prompt.
        mod = self._load(variant)
        s3b = MagicMock()
        s3b.get_object.side_effect = Exception("no s3 object")
        invoke_b = MagicMock(return_value={"StatusCode": 200})
        body = self._body(variant)
        body.pop("inputManifestS3Location")
        body.pop("inputConfigurationS3Location")
        body.pop("inputParameters")
        body["inputMetadata"] = {"VAMS": {cfg["metadataSection"]: {"COSMOS_PREDICT_PROMPT": prompt}}}
        with patch.object(mod, "s3_client", s3b), patch.object(mod.lambda_client, "invoke", invoke_b):
            mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        prompt_from_inline = json.loads(invoke_b.call_args.kwargs["Payload"].decode("utf-8"))["cosmosPrompt"]

        assert prompt_from_s3 == prompt_from_inline == prompt

    def test_config_merge_same_value_whether_config_from_s3_or_inline(self, variant):
        # With no prompt in metadata, the prompt is sourced from the input configuration. Reading
        # the config from S3 must yield the same value as the legacy inline inputParameters.
        cfg = _ENTRYPOINTS[variant]
        config_prompt = "prompt-from-config"

        # (a) Config read from S3 (manifest present, metadata has no prompt).
        mod = self._load(variant)
        manifest = self._manifest()
        empty_md = {"schemaVersion": 1, "metadata": {"VAMS": {cfg["metadataSection"]: {}}}}
        cfg_body = {"PROMPT": config_prompt}
        s3a = MagicMock()

        def get_object_a(Bucket, Key):
            if Key.endswith("manifest.json"):
                payload = manifest
            elif Key.endswith("metadata.json"):
                payload = empty_md
            elif Key.endswith("config.json"):
                payload = cfg_body
            else:
                payload = {}
            return {"Body": MagicMock(read=lambda p=payload: json.dumps(p).encode("utf-8"))}

        s3a.get_object.side_effect = get_object_a
        invoke_a = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3a), patch.object(mod.lambda_client, "invoke", invoke_a):
            mod.lambda_handler({"body": json.dumps(self._body(variant))}, MagicMock())
        prompt_from_s3_config = json.loads(invoke_a.call_args.kwargs["Payload"].decode("utf-8"))["cosmosPrompt"]

        # (b) Config from legacy inline inputParameters (no manifest, no config file).
        mod = self._load(variant)
        s3b = MagicMock()
        s3b.get_object.side_effect = Exception("no s3 object")
        invoke_b = MagicMock(return_value={"StatusCode": 200})
        body = self._body(variant)
        body.pop("inputManifestS3Location")
        body.pop("inputConfigurationS3Location")
        body["inputMetadata"] = {"VAMS": {cfg["metadataSection"]: {}}}
        body["inputParameters"] = json.dumps({"PROMPT": config_prompt})
        with patch.object(mod, "s3_client", s3b), patch.object(mod.lambda_client, "invoke", invoke_b):
            mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        prompt_from_inline_config = json.loads(invoke_b.call_args.kwargs["Payload"].decode("utf-8"))["cosmosPrompt"]

        assert prompt_from_s3_config == prompt_from_inline_config == config_prompt

    def test_legacy_fallback_without_manifest(self, variant):
        # No manifest pointer + S3 unreadable: resolution uses the legacy payload fields, and the
        # prompt is extracted from the inline metadata.
        cfg = _ENTRYPOINTS[variant]
        mod = self._load(variant)
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200})
        body = self._body(variant)
        body.pop("inputManifestS3Location")
        # No configuration prompt: this asserts the LEGACY metadata path supplies the prompt.
        body.pop("inputParameters")
        body["inputMetadata"] = {"VAMS": {cfg["metadataSection"]: {"COSMOS_PREDICT_PROMPT": "legacy prompt"}}}
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # assetId no longer falls back to the SFN body; without a manifest it is empty
        # (only the manifest's first input file supplies it).
        assert payload["assetId"] == ""
        assert payload["databaseId"] == ""
        if cfg["modelType"] == "video2world":
            assert payload["inputS3AssetFilePath"] == "s3://abkt/legacy/clip.mp4"
        # The config LOCATION still threads from the body even on the legacy path.
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert payload["cosmosPrompt"] == "legacy prompt"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_missing_task_token_errors(self, variant):
        # Missing TaskToken returns a clean 500 (external_task_token is initialized to None, so
        # the except block's task-failure callback is skipped rather than raising UnboundLocalError).
        mod = self._load(variant)
        s3 = MagicMock()
        body = self._body(variant)
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

    def _event(self, model_type="video2world"):
        ev = {
            "modelType": model_type,
            "cosmosPrompt": "a calm robot",
            "inputS3AssetFilePath": "s3://abkt/xidM/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/clip.mp4/genAi/cosmos/predict",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "assetId": "xidM",
            "databaseId": "dbM",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }
        if model_type == "text2world":
            ev["inputS3AssetFilePath"] = ""
        return ev

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:CosmosPredict:cosmos-video2world_x",
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
        # The nested SFN input carries the LOCATIONS, never inline content.
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
        assert detail["subExecution"]["executionArn"].endswith("cosmos-video2world_x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/CosmosPredict"

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
            "modelType": "video2world",
            "cosmosPrompt": "a calm robot",
            "inputS3AssetFilePath": "s3://abkt/xidM/clip.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/clip.mp4/genAi/cosmos/predict",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "assetId": "xidM",
            "databaseId": "dbM",
            "externalSfnTaskToken": "tok",
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


# ============================ manifestHelper boundary helpers ============================

@pytest.mark.unit
class TestManifestHelperBoundary:
    def test_fetch_metadata_unwraps_envelope(self):
        envelope = {"schemaVersion": 1, "metadata": {"VAMS": {"assetMetadata": {"k": "v"}}}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(envelope).encode("utf-8"))}
        assert mh.fetch_metadata(s3, "s3://abkt/.../metadata.json") == {"VAMS": {"assetMetadata": {"k": "v"}}}

    def test_fetch_metadata_legacy_unenveloped(self):
        legacy = {"VAMS": {"assetMetadata": {"k": "v"}}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(legacy).encode("utf-8"))}
        assert mh.fetch_metadata(s3, "s3://abkt/.../metadata.json") == legacy

    def test_fetch_metadata_s3_error_returns_empty(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        assert mh.fetch_metadata(s3, "s3://abkt/.../metadata.json") == {}

    def test_fetch_input_configuration_parses_raw_json(self):
        cfg = {"PROMPT": "hi"}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        assert mh.fetch_input_configuration(s3, "s3://abkt/.../config.json") == cfg

    def test_fetch_input_configuration_empty_location(self):
        s3 = MagicMock()
        assert mh.fetch_input_configuration(s3, "") == {}
        s3.get_object.assert_not_called()

    def test_pipeline_execution_id_from_event_prefix(self):
        assert mh.pipeline_execution_id_from_event_prefix("vams.prod.execution.E1.pipeline.P1") == "P1"
        assert mh.pipeline_execution_id_from_event_prefix("") == ""
        assert mh.pipeline_execution_id_from_event_prefix("vams.prod.execution.E1") == ""
