#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the Stage-3 manifest refactor of the genAi/nvidia/gr00t (Gr00t fine-tune) pipeline:
the vendored manifestHelper, the vamsExecute lambda threading the metadata + input-configuration
S3 LOCATIONS (never inline content) while merging the gr00t training config at the boundary,
and openPipeline location threading + sub-process registration.

The gr00t pipeline has no consolidated_handler (rapidPipelineEKS) nor metadataGenerationPipeline
(metadata3dLabeling) module; the analogous Stage-3 surface for gr00t is constructPipeline, which
threads the locations into the container command definition (covered below)."""

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

# vamsExecute + openPipeline read these at import time (boto3 clients + module-level env).
for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:Gr00tFinetune",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/Gr00tFinetune",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/Gr00tFinetune:*",
}.items():
    os.environ.setdefault(k, v)

import manifestHelper as mh  # noqa: E402


# ============================ manifestHelper (resolution + S3 readers) ============================

@pytest.mark.unit
class TestResolveInputs:
    def _legacy(self):
        return {
            "inputS3AssetFilePath": "s3://abkt/xid/",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xid/gr00t/",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "assetId": "xid",
            "databaseId": "db",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/asset/", "assetId": "xidM", "databaseId": "dbM",
                            "assetRootS3Key": "xidM/", "auxPreviewPrefix": "dbM/xidM/asset/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/gr00t/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def test_no_manifest_falls_back_to_legacy(self):
        legacy = self._legacy()
        r = mh.resolve_inputs(legacy, None)
        assert r["manifestUsed"] is False
        assert r["inputS3AssetFilePath"] == legacy["inputS3AssetFilePath"]
        assert r["outputS3AssetFilesPath"] == legacy["outputS3AssetFilesPath"]
        assert r["inputConfigurationS3Location"] == legacy["inputConfigurationS3Location"]
        # assetId/databaseId no longer fall back to the SFN body; without a manifest they are
        # empty (only the manifest's first input file supplies them).
        assert r["assetId"] == "" and r["databaseId"] == ""
        assert r["orchestrationEventPrefix"] == ""

    def test_manifest_preferred(self):
        r = mh.resolve_inputs(self._legacy(), self._manifest())
        assert r["manifestUsed"] is True
        assert r["inputS3AssetFilePath"] == "s3://abkt/xidM/asset/"
        assert r["assetId"] == "xidM" and r["databaseId"] == "dbM"
        assert r["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert r["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/gr00t/E1/"
        assert r["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        # config location rides in the SFN body, not the manifest envelope -> stays from legacy
        assert r["inputConfigurationS3Location"] == self._legacy()["inputConfigurationS3Location"]
        assert r["orchestrationBusArn"] == "arn:bus"
        assert r["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"


@pytest.mark.unit
class TestFetchMetadata:
    def test_unwraps_schema_versioned_envelope(self):
        envelope = {"schemaVersion": 1, "metadata": {"VAMS": {"assetMetadata": {"GROOT_MAX_STEPS": 2000}}}}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(envelope).encode("utf-8"))}
        md = mh.fetch_metadata(s3, "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json")
        assert md == {"VAMS": {"assetMetadata": {"GROOT_MAX_STEPS": 2000}}}

    def test_empty_location_returns_empty_dict(self):
        s3 = MagicMock()
        assert mh.fetch_metadata(s3, "") == {}
        s3.get_object.assert_not_called()

    def test_s3_error_returns_empty_dict_best_effort(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        assert mh.fetch_metadata(s3, "s3://abkt/k/metadata.json") == {}


@pytest.mark.unit
class TestFetchInputConfiguration:
    def test_parses_raw_config_object(self):
        cfg = {"maxSteps": 5000, "batchSize": 16}
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(cfg).encode("utf-8"))}
        assert mh.fetch_input_configuration(s3, "s3://abkt/.../config.json") == cfg

    def test_empty_location_returns_empty_dict(self):
        s3 = MagicMock()
        assert mh.fetch_input_configuration(s3, "") == {}
        s3.get_object.assert_not_called()

    def test_s3_error_returns_empty_dict_best_effort(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        assert mh.fetch_input_configuration(s3, "s3://abkt/k/config.json") == {}


@pytest.mark.unit
class TestPipelineExecutionIdFromEventPrefix:
    def test_extracts_trailing_pipeline_id(self):
        assert mh.pipeline_execution_id_from_event_prefix(
            "vams.prod.execution.E1.pipeline.P1") == "P1"

    def test_empty_prefix(self):
        assert mh.pipeline_execution_id_from_event_prefix("") == ""

    def test_unrecognized_prefix(self):
        assert mh.pipeline_execution_id_from_event_prefix("vams.prod.execution.E1") == ""


# ============================ vamsExecute: vamsExecuteGr00tFinetunePipeline ============================

@pytest.mark.unit
class TestVamsExecuteGr00tFinetunePipeline:
    def _load(self):
        if "vamsExecuteGr00tFinetunePipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteGr00tFinetunePipeline"])
        return importlib.import_module("vamsExecuteGr00tFinetunePipeline")

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputS3AssetFilePath": "s3://abkt/legacy/asset/",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/gr00t/",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "inputMetadata": {"VAMS": {"assetMetadata": {"GROOT_MAX_STEPS": 9999, "GROOT_BASE_MODEL_PATH": "/m/base"}}},
            "inputParameters": '{"maxSteps": 100, "batchSize": 8}',
            "executingUserName": "user@x",
            "assetId": "legacyAsset",
            "databaseId": "legacyDb",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/asset/", "assetId": "xidM", "databaseId": "dbM",
                            "assetRootS3Key": "xidM/", "auxPreviewPrefix": "dbM/xidM/asset/preview"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/gr00t/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _s3_for_manifest(self, metadata_envelope, config_obj):
        """An s3 mock that returns the manifest, then metadata envelope, then raw config, by key."""
        manifest = self._manifest()

        def get_object(Bucket, Key):  # noqa: N803 - boto3 kwarg names
            if Key.endswith("manifest.json"):
                body = json.dumps(manifest).encode("utf-8")
            elif Key.endswith("metadata.json"):
                body = json.dumps(metadata_envelope).encode("utf-8")
            elif Key.endswith("config.json"):
                body = json.dumps(config_obj).encode("utf-8")
            else:
                raise Exception(f"unexpected key {Key}")
            return {"Body": MagicMock(read=lambda b=body: b)}

        s3 = MagicMock()
        s3.get_object.side_effect = get_object
        return s3

    def test_forwards_locations_not_inline_content(self):
        mod = self._load()
        # metadata read from S3 (enveloped) + config read from S3 (raw).
        metadata_envelope = {"schemaVersion": 1,
                             "metadata": {"VAMS": {"assetMetadata": {"GROOT_MAX_STEPS": 9999,
                                                                     "GROOT_BASE_MODEL_PATH": "/m/base"}}}}
        config_obj = {"maxSteps": 100, "batchSize": 8}
        s3 = self._s3_for_manifest(metadata_envelope, config_obj)
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # Manifest-resolved input path + identity + outputs + aux.
        assert payload["inputS3AssetPath"] == "s3://abkt/xidM/asset/"
        assert payload["assetId"] == "xidM" and payload["databaseId"] == "dbM"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/p1/MJOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/gr00t/E1/"
        # The metadata + input-configuration S3 LOCATIONS travel, never the inline content.
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload
        # The task token + orchestration event prefix ride along.
        assert payload["sfnExternalTaskToken"] == "tok-123"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"

    def test_boundary_merge_same_value_from_s3_as_inline(self):
        """The boundary config merge must produce the SAME gr00tConfig whether metadata/config
        are read from S3 or supplied inline. Metadata (2nd priority) overrides inputParameters."""
        mod = self._load()
        metadata_envelope = {"schemaVersion": 1,
                             "metadata": {"VAMS": {"assetMetadata": {"GROOT_MAX_STEPS": 9999,
                                                                     "GROOT_BASE_MODEL_PATH": "/m/base"}}}}
        config_obj = {"maxSteps": 100, "batchSize": 8}

        # (a) read from S3
        s3_a = self._s3_for_manifest(metadata_envelope, config_obj)
        invoke_a = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3_a), patch.object(mod.lambda_client, "invoke", invoke_a):
            mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        cfg_from_s3 = json.loads(json.loads(invoke_a.call_args.kwargs["Payload"].decode("utf-8"))["gr00tConfig"])

        # (b) read from inline payload (no manifest, S3 raises so fetch_* return {} -> inline used)
        s3_b = MagicMock()
        s3_b.get_object.side_effect = Exception("no S3")
        body_inline = self._body()
        body_inline.pop("inputManifestS3Location")
        body_inline.pop("inputConfigurationS3Location")  # force inline inputParameters fallback
        invoke_b = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3_b), patch.object(mod.lambda_client, "invoke", invoke_b):
            mod.lambda_handler({"body": json.dumps(body_inline)}, MagicMock())
        cfg_from_inline = json.loads(json.loads(invoke_b.call_args.kwargs["Payload"].decode("utf-8"))["gr00tConfig"])

        # Same merged config either way. The input CONFIGURATION wins on maxSteps (100, not the
        # asset's 9999) because it is what the operator supplied at execute time; asset metadata still
        # supplies baseModelPath, which the configuration does not mention — the fallback layer.
        expected = {"maxSteps": 100, "batchSize": 8, "baseModelPath": "/m/base"}
        assert cfg_from_s3 == expected
        assert cfg_from_inline == expected
        assert cfg_from_s3 == cfg_from_inline

    def test_legacy_fallback_without_manifest(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest / no files")
        invoke = MagicMock(return_value={"StatusCode": 200})
        body = self._body()
        body.pop("inputManifestS3Location")
        body.pop("inputConfigurationS3Location")
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        # No manifest -> legacy input path (asset-level path gets a trailing slash). assetId /
        # databaseId no longer fall back to the SFN body; without a manifest they are empty.
        assert payload["inputS3AssetPath"] == "s3://abkt/legacy/asset/"
        assert payload["assetId"] == "" and payload["databaseId"] == ""
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/legacy/files/"
        # The merge still happens on the legacy fallback path, with the same precedence: the input
        # configuration overrides asset metadata, and metadata fills what it omits.
        merged = json.loads(payload["gr00tConfig"])
        assert merged["maxSteps"] == 100             # input configuration overrides metadata's 9999
        assert merged["baseModelPath"] == "/m/base"  # metadata supplies what config omits
        assert merged["batchSize"] == 8              # input configuration only
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_missing_task_token_errors(self):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        body = self._body()
        body.pop("TaskToken")
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", MagicMock()):
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
            "inputS3AssetPath": "s3://abkt/xidM/asset/",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/gr00t/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "gr00tConfig": '{"maxSteps": 9999}',
            "sfnExternalTaskToken": "tok-123",
            "assetId": "xidM",
            "databaseId": "dbM",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime
        return MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:Gr00tFinetune:gr00t-finetune-x",
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
        assert sfn_input["inputS3AssetPath"] == "s3://abkt/xidM/asset/"
        assert sfn_input["assetId"] == "xidM"
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
        assert detail["subExecution"]["executionArn"].endswith("gr00t-finetune-x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/Gr00tFinetune"

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


# ============================ constructPipeline: definition threads locations ============================

@pytest.mark.unit
class TestConstructPipeline:
    """gr00t has no consolidated_handler / metadataGenerationPipeline module; the analogous
    Stage-3 surface is constructPipeline, which must thread the S3 LOCATIONS into the container
    command definition and never embed inline metadata/config content."""

    def _load(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def test_definition_carries_locations_not_content(self):
        mod = self._load()
        event = {
            "inputS3AssetPath": "s3://abkt/xidM/asset/",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/gr00t/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "gr00tConfig": '{"maxSteps": 9999}',
            "assetId": "xidM",
            "databaseId": "dbM",
            "externalSfnTaskToken": "tok",
        }
        out = mod.lambda_handler(event, MagicMock())
        assert out["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert out["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert "inputMetadata" not in out
        assert "inputParameters" not in out
        # The container command embeds the definition JSON (["python", "__main__.py", "<json>"]),
        # which carries the locations only.
        definition = json.loads(out["definition"][2])
        assert definition["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert definition["inputConfigurationS3Location"] == "s3://abkt/.../config.json"
        assert definition["assetId"] == "xidM"
        assert "inputMetadata" not in definition
        assert "inputParameters" not in definition
