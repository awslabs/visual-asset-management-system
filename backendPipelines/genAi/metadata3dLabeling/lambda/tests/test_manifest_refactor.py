#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the genAi/metadata3dLabeling pipeline: the
vamsExecute lambda threading metadata + input-configuration S3 LOCATIONS (never inline content)
and falling back to legacy payload fields, openPipeline location threading + sub-process
registration, constructPipeline carrying the locations into the definition, and the downstream
metadataGenerationPipeline lambda reading metadata + config from S3 (consumer-reads-from-S3) with
the seedMetadataGenerationWithInputMetadata gate still working."""

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

# The pipeline lambdas create boto3 clients and read env at import time; provide the required env.
for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:GenAiMetadata3dLabeling",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.obj,.stl,.fbx",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/GenAiMetadata3dLabeling",
    "STATE_MACHINE_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/GenAiMetadata3dLabeling:*",
    "BEDROCK_MODEL_ID": "anthropic.claude-3-sonnet-20240229-v1:0",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


# ============================ vamsExecute ============================

@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteGenAiMetadata3dLabelingPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteGenAiMetadata3dLabelingPipeline"])
        return importlib.import_module("vamsExecuteGenAiMetadata3dLabelingPipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/genAi/metadata3dLabeling/",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"assetMetadata": {"PART": "pump"}}},
            "inputParameters": '{"seedMetadataGenerationWithInputMetadata": "True"}',
            "executingUserName": "user@x",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/test/pump.glb", "assetId": "xidM",
                            "databaseId": "dbM", "assetRootS3Key": "xidM/",
                            "auxPreviewPrefix": "dbM/xidM/test/pump.glb/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/",
                        "metadata": "pipelines/p1/MJOB/output/E1/metadata/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/metadata3dLabeling/E1/",
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
        # The open-pipeline invoke carries the MANIFEST-resolved values, not the legacy ones.
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/test/pump.glb"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["outputS3AssetMetadataPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/metadata3dLabeling/E1/"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        # The metadata + input-configuration S3 LOCATIONS are forwarded, never the inline content
        # (the vamsExecute lambda is the content boundary for both).
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload
        # The orchestration event prefix rides along so openPipeline can register its sub-SFN.
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"

    def test_pre_invoke_failure_fails_the_task_token(self):
        # A multi-file manifest is rejected before the pipeline starts; the workflow task waits on
        # the callback token, so the rejection must be reported rather than only returned.
        mod = self._load()
        manifest = self._manifest()
        manifest["inputFiles"].append(
            {"bucket": "abkt", "key": "xidM/test/second.glb", "assetId": "xidM", "databaseId": "dbM"})
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

    def test_legacy_fallback_without_manifest(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200})
        body = self._body()
        body.pop("inputManifestS3Location")  # no manifest pointer
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # Resolution falls back to the legacy payload fields.
        assert payload["inputS3AssetFilePath"] == "s3://abkt/legacy/pump.glb"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/legacy/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/legacy/genAi/metadata3dLabeling/"
        # The config location is still threaded from the body (it rides the SFN body, not the manifest).
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        # No inline content past the boundary even on the legacy path.
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_missing_task_token_errors(self):
        mod = self._load()
        s3 = MagicMock()
        body = self._body()
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 500

    def test_missing_body_errors(self):
        mod = self._load()
        resp = mod.lambda_handler({}, MagicMock())
        assert resp["statusCode"] == 400


# ============================ boundary extraction parity (manifestHelper) ============================

@pytest.mark.unit
class TestBoundaryExtractionParity:
    """The downstream consumer extracts the same prompt-seed metadata / config whether it is read
    from the S3 metadata envelope + raw config file or from the legacy inline payload. This proves
    the vamsExecute->downstream boundary (locations only) yields the SAME values the inline path did."""

    def test_metadata_envelope_unwrap_matches_inline(self):
        # Inline (legacy) metadata content...
        inline_metadata = {"VAMS": {"assetMetadata": {"PART": "pump", "MATERIAL": "steel"}}}
        # ...and the same content wrapped in the Stage-3 schema envelope written to S3.
        envelope = {"schemaVersion": 1, "metadata": inline_metadata}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(envelope).encode("utf-8"))}
        from_s3 = mh.fetch_metadata(s3, "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json")
        assert from_s3 == inline_metadata  # envelope unwrapped to exactly the inline value

    def test_config_read_matches_inline_parameters(self):
        # The inline inputParameters string and the raw config.json file must parse identically.
        inline_parameters = '{"seedMetadataGenerationWithInputMetadata": "True"}'
        cfg = json.loads(inline_parameters)
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        from_s3 = mh.fetch_input_configuration(s3, "s3://abkt/.../config.json")
        assert from_s3 == cfg

    def test_legacy_unenveloped_metadata_returned_as_is(self):
        legacy = {"VAMS": {"assetMetadata": {"PART": "pump"}}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(legacy).encode("utf-8"))}
        assert mh.fetch_metadata(s3, "s3://abkt/k/metadata.json") == legacy

    def test_best_effort_empty_and_error(self):
        s3 = MagicMock()
        assert mh.fetch_metadata(s3, "") == {}
        assert mh.fetch_input_configuration(s3, "") == {}
        s3.get_object.assert_not_called()
        s3.get_object.side_effect = Exception("AccessDenied")
        assert mh.fetch_metadata(s3, "s3://abkt/k/metadata.json") == {}
        assert mh.fetch_input_configuration(s3, "s3://abkt/k/config.json") == {}


# ============================ openPipeline: threading + registration ============================

@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/MJOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/pump.glb/genAi/metadata3dLabeling/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:GenAiMetadata3dLabeling:PipelineJob_x",
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
        assert sfn_input["externalSfnTaskToken"] == "tok-123"
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
        # pipelineExecutionId is parsed from the orchestration event prefix.
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["executionArn"].endswith("PipelineJob_x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/GenAiMetadata3dLabeling"

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
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/pump.glb/genAi/metadata3dLabeling/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
        }
        out = mod.lambda_handler(event, MagicMock())
        # The construct output threads the locations, not inline content.
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
        # The serialized definition (the downstream lambda's command arg) carries the locations only.
        definition = json.loads(out["definition"][0])
        assert definition["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert definition["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in definition
        assert "inputParameters" not in definition
        # A METADATAGENERATION stage is present for the downstream lambda to consume.
        stage_types = [s["type"] for s in definition["stages"]]
        assert "METADATAGENERATION" in stage_types


# ============================ metadataGenerationPipeline reads metadata/config from S3 ============================

@pytest.mark.unit
class TestMetadataGenerationReadsFromS3:
    """The downstream metadataGenerationPipeline lambda reads metadata + config from the S3
    locations carried in the definition (consumer-reads-from-S3) and the
    seedMetadataGenerationWithInputMetadata gate still seeds the prompt from that metadata."""

    def _load(self):
        if "metadataGenerationPipeline" in sys.modules:
            return importlib.reload(sys.modules["metadataGenerationPipeline"])
        return importlib.import_module("metadataGenerationPipeline")

    def _definition(self):
        return {
            "jobName": "PipelineJob_x",
            "stages": [
                {"type": "BLENDERRENDERER"},
                {
                    "type": "METADATAGENERATION",
                    "inputFile": {"bucketName": "aux", "objectDir": "xidM/imgs/"},
                    "outputFiles": {"bucketName": "", "objectDir": ""},
                    "outputMetadata": {"bucketName": "", "objectDir": ""},
                    "temporaryFiles": {"bucketName": "aux", "objectDir": "xidM/imgs/"},
                },
            ],
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "externalSfnTaskToken": "tok",
        }

    def _run(self, mod, config_obj, metadata_envelope):
        """Drive lambda_handler far enough to exercise the S3 metadata/config read and the seed
        gate, capturing the prompts passed to Claude. Returns the captured prompt list."""
        prompts = []

        def fake_get_object(Bucket, Key):
            # Route by key: config vs metadata. Both arrive via manifestHelper.fetch_* using s3_client.
            if Key.endswith("config.json"):
                payload = config_obj
            else:
                payload = metadata_envelope
            return {"Body": MagicMock(read=lambda: json.dumps(payload).encode("utf-8"))}

        def fake_claude(prompt, base64_image_data=None):
            prompts.append(prompt)
            # First call(s) are per-image; final call is the summarization. Return a parseable JSON.
            return {"content": [{"text": json.dumps({"autoGeneratedKeywords": ["pump"]})}]}

        event = {"definition": [json.dumps(self._definition())]}
        with patch.object(mod, "s3_client") as s3, \
                patch.object(mod, "get_all_image_files_in_path",
                             MagicMock(return_value=[{"key": "xidM/imgs/a.png", "relativePath": "a.png"}])), \
                patch.object(mod, "image_to_base64", MagicMock(return_value="b64")), \
                patch.object(mod, "invoke_claude_3_with_text", side_effect=fake_claude), \
                patch.object(mod, "RekognitionImage") as rek, \
                patch("os.path.exists", MagicMock(return_value=False)):
            s3.get_object.side_effect = fake_get_object
            s3.download_file = MagicMock()
            s3.put_object = MagicMock()
            rek.from_file.return_value.detect_labels.return_value = []
            result = mod.lambda_handler(event, MagicMock())
        return prompts, result, s3

    def test_reads_config_and_metadata_from_s3_seed_gate_on(self):
        mod = self._load()
        config_obj = {"seedMetadataGenerationWithInputMetadata": "True"}
        metadata_envelope = {"schemaVersion": 1,
                             "metadata": {"VAMS": {"assetMetadata": {"PART": "centrifugal pump"}}}}
        prompts, result, s3 = self._run(mod, config_obj, metadata_envelope)
        # The metadata read from S3 (envelope unwrapped) seeded the per-image prompt.
        per_image_prompt = prompts[0]
        assert "PART:::centrifugal pump" in per_image_prompt
        # The handler completed and wrote the generated metadata back to S3.
        assert s3.put_object.called
        assert result == {"definition": [json.dumps(self._definition())]} or "definition" in result

    def test_grouped_metadata_envelope_seeds_prompt(self):
        """The run metadata file is the grouped-by-asset envelope; every scope (asset metadata,
        file metadata, file attributes) still seeds the prompt."""
        mod = self._load()
        config_obj = {"seedMetadataGenerationWithInputMetadata": "True"}
        metadata_envelope = {
            "schemaVersion": 2,
            "assets": [{
                "databaseId": "dbM", "assetId": "xidM",
                "assetData": {"assetName": "Pump"},
                "files": [
                    {"fileKey": "/", "metadata": {"PART": "centrifugal pump"}},
                    {"fileKey": "/test/pump.glb", "metadata": {"REVISION": "C"},
                     "attributes": {"UNITS": "mm"}},
                ],
            }],
        }
        prompts, _result, s3 = self._run(mod, config_obj, metadata_envelope)
        per_image_prompt = prompts[0]
        assert "PART:::centrifugal pump" in per_image_prompt
        assert "REVISION:::C" in per_image_prompt
        assert "UNITS:::mm" in per_image_prompt
        assert s3.put_object.called

    def test_grouped_metadata_envelope_ignored_when_gate_off(self):
        mod = self._load()
        config_obj = {"seedMetadataGenerationWithInputMetadata": "False"}
        metadata_envelope = {
            "schemaVersion": 2,
            "assets": [{
                "databaseId": "dbM", "assetId": "xidM", "assetData": {},
                "files": [{"fileKey": "/", "metadata": {"PART": "centrifugal pump"}}],
            }],
        }
        prompts, _result, _s3 = self._run(mod, config_obj, metadata_envelope)
        assert "PART:::centrifugal pump" not in prompts[0]

    def test_seed_gate_off_does_not_seed_prompt(self):
        mod = self._load()
        # Gate defaults to off / not "True": metadata content is NOT injected into the prompt.
        config_obj = {"seedMetadataGenerationWithInputMetadata": "False"}
        metadata_envelope = {"schemaVersion": 1,
                             "metadata": {"VAMS": {"assetMetadata": {"PART": "centrifugal pump"}}}}
        prompts, result, s3 = self._run(mod, config_obj, metadata_envelope)
        per_image_prompt = prompts[0]
        assert "PART:::centrifugal pump" not in per_image_prompt

    def test_no_metadata_generation_stage_raises(self):
        mod = self._load()
        definition = {"jobName": "x", "stages": [{"type": "BLENDERRENDERER"}],
                      "inputMetadataS3Location": "", "inputConfigurationS3Location": ""}
        event = {"definition": [json.dumps(definition)]}
        with patch.object(mod, "s3_client", MagicMock()):
            with pytest.raises(Exception):
                mod.lambda_handler(event, MagicMock())
