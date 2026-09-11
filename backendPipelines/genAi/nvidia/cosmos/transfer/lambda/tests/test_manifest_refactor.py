#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the genAi/nvidia/cosmos/transfer pipeline:
the vamsExecute lambda forwarding metadata + input-configuration S3 LOCATIONS (never inline
content) and extracting the transfer prompt / control flags at the boundary by reading from
S3, the openPipeline location threading + sub-process registration, the constructPipeline
definition carrying locations only, and the container reading the input configuration from
S3 (consumer-reads-from-S3)."""

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

# The pipeline lambdas create boto3 clients and read env at import time. Provide a region and
# the env vars vamsExecute/openPipeline read so the modules import without real CDK env.
for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CosmosTransfer",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".mp4,.mov,.avi",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/CosmosTransfer",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/CosmosTransfer:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


# ============================ vamsExecute ============================

@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteCosmosTransferPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteCosmosTransferPipeline"])
        return importlib.import_module("vamsExecuteCosmosTransferPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/source.mp4",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/cosmos/transfer",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "executingUserName": "user@x",
            "assetId": "legacyAsset",
            "databaseId": "legacyDb",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/clips/source.mp4",
                            "assetId": "xidM", "databaseId": "dbM", "assetRootS3Key": "xidM/",
                            "auxPreviewPrefix": "dbM/xidM/clips/source.mp4/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/cosmosTransfer/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _metadata_envelope(self, prompt="A rainy city street", control_type="seg",
                           control_path="signals/control.mp4"):
        # The metadata file on S3 is a {schemaVersion, metadata} envelope; the transfer keys
        # live under VAMS.fileMetadata.
        return {
            "schemaVersion": 1,
            "metadata": {
                "VAMS": {
                    "assetMetadata": {},
                    "fileMetadata": {
                        "COSMOS_TRANSFER_PROMPT": prompt,
                        "COSMOS_TRANSFER_CONTROL_TYPE": control_type,
                        "COSMOS_TRANSFER_CONTROL_PATH": control_path,
                    },
                }
            },
        }

    def _s3_with(self, *bodies):
        """An s3 mock whose get_object returns the given JSON bodies in call order."""
        s3 = MagicMock()
        responses = [
            {"Body": MagicMock(read=lambda b=b: json.dumps(b).encode("utf-8"))}
            for b in bodies
        ]
        s3.get_object.side_effect = responses
        return s3

    def test_forwards_locations_not_content(self):
        mod = self._load()
        # get_object call order in the handler: manifest, metadata, configuration.
        s3 = self._s3_with(self._manifest(), self._metadata_envelope(),
                           {"DISABLE_GUARDRAILS": "false", "CONTROL_WEIGHT": "0.5"})
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # Manifest-resolved input + outputs + aux.
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/clips/source.mp4"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/cosmosTransfer/E1/"
        assert payload["assetId"] == "xidM"
        assert payload["databaseId"] == "dbM"
        # The metadata + input-configuration S3 LOCATIONS are forwarded.
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        # No inline metadata or config content past the vamsExecute boundary.
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_boundary_extraction_from_s3_metadata(self):
        # The transfer prompt + control flags are extracted from the S3-read metadata envelope.
        mod = self._load()
        s3 = self._s3_with(self._manifest(),
                           self._metadata_envelope(prompt="A snowy mountain", control_type="depth",
                                                   control_path="ctrl/depth.mp4"),
                           {})
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["cosmosPrompt"] == "A snowy mountain"
        assert payload["controlType"] == "depth"
        assert payload["controlPath"] == "ctrl/depth.mp4"

    def test_boundary_extraction_same_value_from_s3_vs_legacy(self):
        # The boundary extraction (prompt / control) yields the SAME value whether the
        # metadata is read from S3 (manifest path) or from the legacy inline payload field.
        mod = self._load()
        envelope = self._metadata_envelope(prompt="Cyberpunk alley", control_type="seg",
                                           control_path="c/seg.mp4")

        # (a) read from S3 via the manifest's inputMetadataS3Location.
        s3_a = self._s3_with(self._manifest(), envelope, {})
        invoke_a = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3_a), patch.object(mod.lambda_client, "invoke", invoke_a):
            mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        payload_a = json.loads(invoke_a.call_args.kwargs["Payload"].decode("utf-8"))

        # (b) no manifest, no S3 metadata; the same metadata content arrives inline (legacy).
        mod = self._load()
        s3_b = MagicMock()
        s3_b.get_object.side_effect = Exception("no S3 metadata")
        body_b = self._body()
        body_b.pop("inputManifestS3Location")
        # Legacy inline metadata is the UNWRAPPED metadata dict (no envelope).
        body_b["inputMetadata"] = envelope["metadata"]
        invoke_b = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3_b), patch.object(mod.lambda_client, "invoke", invoke_b):
            mod.lambda_handler({"body": json.dumps(body_b)}, MagicMock())
        payload_b = json.loads(invoke_b.call_args.kwargs["Payload"].decode("utf-8"))

        assert payload_a["cosmosPrompt"] == payload_b["cosmosPrompt"] == "Cyberpunk alley"
        assert payload_a["controlType"] == payload_b["controlType"] == "seg"
        assert payload_a["controlPath"] == payload_b["controlPath"] == "c/seg.mp4"

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
        # Falls back to the legacy payload fields when no manifest is available.
        assert payload["inputS3AssetFilePath"] == "s3://abkt/legacy/source.mp4"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/legacy/files/"
        # assetId no longer falls back to the SFN body; without a manifest it is empty
        # (only the manifest's first input file supplies it).
        assert payload["assetId"] == ""
        # The config location is still threaded from the body (no manifest needed for it).
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        # Default prompt when no metadata/config supplies one.
        assert payload["cosmosPrompt"] == "Transform the video"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_missing_task_token_errors(self):
        mod = self._load()
        s3 = MagicMock()
        body = self._body()
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn_client, "send_task_failure", MagicMock()):
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
            "modelType": "transfer",
            "inputS3AssetFilePath": "s3://abkt/xidM/clips/source.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/clips/source.mp4/cosmos/transfer/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "assetId": "xidM",
            "databaseId": "dbM",
            "cosmosPrompt": "A rainy street",
            "controlType": "seg",
            "controlPath": "c/seg.mp4",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:CosmosTransfer:cosmos-transfer-job-x",
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
        # The nested SFN input carries the metadata + config LOCATIONS, never inline content.
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
        assert detail["subExecution"]["executionArn"].endswith("cosmos-transfer-job-x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/CosmosTransfer"

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
            "modelType": "transfer",
            "cosmosPrompt": "A rainy street",
            "controlType": "seg",
            "controlPath": "c/seg.mp4",
            "inputS3AssetFilePath": "s3://abkt/xidM/clips/source.mp4",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/clips/source.mp4/cosmos/transfer/",
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
        assert definition["controlType"] == "seg"
        assert "inputMetadata" not in definition
        assert "inputParameters" not in definition


# ============================ container reads input configuration from S3 ============================

@pytest.mark.unit
class TestContainerReadsFromS3:
    """The container reads the input configuration (DISABLE_GUARDRAILS / CONTROL_WEIGHT flags)
    and metadata from the S3 locations in the definition (consumer-reads-from-S3). Loads the
    container's manifest_io module from its separate code asset."""

    def _container_manifest_io(self):
        import importlib.util
        path = os.path.normpath(os.path.join(
            _LAMBDA_DIR, "..", "container", "manifest_io.py"))
        spec = importlib.util.spec_from_file_location("transfer_container_manifest_io", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fetch_input_configuration_reads_flags(self):
        mio = self._container_manifest_io()
        cfg = {"DISABLE_GUARDRAILS": "false", "CONTROL_WEIGHT": "0.5"}
        # fetch_input_configuration reads the RAW text and parses it itself, so a present-but-
        # unparseable body can be told apart from an absent one (it raises rather than returning {},
        # which would look like "no configuration" and silently drop the caller's parameters).
        with patch.object(mio, "_read_text", MagicMock(return_value=json.dumps(cfg))):
            got = mio.fetch_input_configuration("s3://abkt/.../config.json")
        assert got == cfg
        # The container's main flag derivation reads these keys from the config dict.
        disable_guardrails = str(got.get("DISABLE_GUARDRAILS", "true")).lower() != "false"
        control_weight = float(got.get("CONTROL_WEIGHT", "1.0"))
        assert disable_guardrails is False  # DISABLE_GUARDRAILS="false" -> guardrails enabled
        assert control_weight == 0.5

    def test_fetch_metadata_unwraps_envelope(self):
        mio = self._container_manifest_io()
        envelope = {"schemaVersion": 1, "metadata": {"VAMS": {"fileMetadata": {"COSMOS_TRANSFER_PROMPT": "x"}}}}
        with patch.object(mio, "_get_json", MagicMock(return_value=envelope)):
            md = mio.fetch_metadata("s3://abkt/.../metadata.json")
        assert md == {"VAMS": {"fileMetadata": {"COSMOS_TRANSFER_PROMPT": "x"}}}  # envelope unwrapped

    def test_empty_or_unreadable_yields_empty_dict(self):
        mio = self._container_manifest_io()
        # An EMPTY location means "no configuration was supplied" -> {} for both readers, and neither
        # reader touches S3 at all. An unreadable NON-empty configuration location is a different
        # case and raises (see test_a_present_but_unreadable_configuration_raises).
        with patch.object(mio, "_get_json", MagicMock(return_value=None)),              patch.object(mio, "_read_text", MagicMock(return_value=None)):
            assert mio.fetch_input_configuration("") == {}
            assert mio.fetch_metadata("") == {}

    def test_a_present_but_unreadable_configuration_raises(self):
        # The distinction the loud reader exists for: a supplied-but-unreadable configuration must
        # NOT degrade to {}, or the job runs on its defaults and reports SUCCESS with the caller's
        # parameters silently gone.
        mio = self._container_manifest_io()
        with patch.object(mio, "_read_text", MagicMock(return_value=None)):
            with pytest.raises(mio.InputConfigurationError):
                mio.fetch_input_configuration("s3://abkt/.../config.json")
