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
import datetime
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


# ================== output-target identity for a run with no input files ==================

@pytest.mark.unit
class TestFileLessRunResolvesTheOutputTargetIdentity:
    """A run with no input files takes its asset identity from the execution's OUTPUT target.

    That target reaches the reader in TWO shapes, and both must resolve. The manifest's
    `outputTarget` block is the one a manifest-driven run actually uses: the per-pipeline Step
    Functions task body carries only the manifest POINTER (workflowExecutionId, the manifest S3
    location, the task token), so a reader that consults only the legacy top-level
    outputAssetId/outputDatabaseId sees nothing — and the container then fails with "assetId is
    required in pipeline definition" AFTER the Batch job has been scheduled and the GPU paid for.

    Verified against a live cosmos3 execution whose manifest carried
    outputTarget.assetId = 'xddcc84a4-...' while the task body carried no output ids at all.
    """

    def test_the_manifest_output_target_supplies_the_identity(self):
        manifest = {"schemaVersion": 2, "inputFiles": [],
                    "outputTarget": {"locationType": "asset", "assetId": "xDest",
                                     "databaseId": "dbDest",
                                     "fileBaseExecutionPathExtension": "/"},
                    "outputs": {"bucket": "abkt", "files": "f/", "metadata": "m/",
                                "previews": "p/", "results": "r/"}}
        # The task body carries NO output ids — exactly what the SFN pipeline task sends.
        resolved = mh.resolve_inputs({"workflowExecutionId": "e1"}, manifest)
        assert (resolved["assetId"], resolved["databaseId"]) == ("xDest", "dbDest")

    def test_the_legacy_top_level_shape_still_resolves(self):
        # No manifest at all: the pre-manifest body shape must keep working.
        resolved = mh.resolve_inputs({"outputAssetId": "xLegacy", "outputDatabaseId": "dbLegacy"},
                                     None)
        assert (resolved["assetId"], resolved["databaseId"]) == ("xLegacy", "dbLegacy")

    def test_the_manifest_output_target_wins_over_the_legacy_keys(self):
        manifest = {"schemaVersion": 2, "inputFiles": [],
                    "outputTarget": {"assetId": "xManifest", "databaseId": "dbManifest"},
                    "outputs": {"bucket": "abkt", "files": "f/", "metadata": "m/",
                                "previews": "p/", "results": "r/"}}
        resolved = mh.resolve_inputs(
            {"outputAssetId": "xLegacy", "outputDatabaseId": "dbLegacy"}, manifest)
        assert (resolved["assetId"], resolved["databaseId"]) == ("xManifest", "dbManifest")

    def test_an_input_file_still_wins_over_the_output_target(self):
        # The output target is a FALLBACK. When the run has an input file, that file's asset is the
        # subject — otherwise a run reading asset A would report itself as running against output B.
        manifest = {"schemaVersion": 2,
                    "inputFiles": [{"relativePath": "/in.mp4", "databaseId": "dbIn",
                                    "assetId": "xIn", "bucket": "abkt",
                                    "key": "xIn/in.mp4"}],
                    "outputTarget": {"assetId": "xDest", "databaseId": "dbDest"},
                    "outputs": {"bucket": "abkt", "files": "f/", "metadata": "m/",
                                "previews": "p/", "results": "r/"}}
        resolved = mh.resolve_inputs({}, manifest)
        assert (resolved["assetId"], resolved["databaseId"]) == ("xIn", "dbIn")

    def test_no_identity_anywhere_leaves_it_empty_rather_than_raising(self):
        resolved = mh.resolve_inputs({}, {"schemaVersion": 2, "inputFiles": [], "outputs": {}})
        assert (resolved["assetId"], resolved["databaseId"]) == ("", "")


# ============== sub-state-machine execution name uniqueness / retry idempotence ==============

@pytest.mark.unit
class TestSubStateMachineExecutionName:
    """The name openPipeline runs its own state machine under must be unique across concurrent runs
    yet identical across retries of the same run.

    A workflow may carry several triggers of one type, so one upload fans out to simultaneous runs
    of the SAME variant; Step Functions rejects a repeated name with ExecutionAlreadyExists, which
    openPipeline turns into a generic 500. A random suffix would fix the collision but break SFN
    retry idempotence, so the name is derived from the pipeline execution id.
    """

    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _prefix(self, pipeline_execution_id):
        return f"vams.prod.execution.E1.pipeline.{pipeline_execution_id}"

    def test_two_runs_in_the_same_second_get_different_names(self):
        mod = self._load()
        first = mod.build_job_name("nano", self._prefix("P1"))
        second = mod.build_job_name("nano", self._prefix("P2"))
        assert first != second

    def test_the_same_run_always_derives_the_same_name(self):
        # An SFN retry re-invokes this lambda with the same body; a second start_execution under a
        # NEW name would launch a duplicate sub-execution.
        mod = self._load()
        prefix = self._prefix("P1")
        assert mod.build_job_name("nano", prefix) == mod.build_job_name("nano", prefix)

    def test_a_direct_invocation_without_a_prefix_is_still_unique(self):
        mod = self._load()
        names = {mod.build_job_name("nano", "") for _ in range(20)}
        assert len(names) == 20

    def test_the_name_obeys_the_step_functions_constraints(self):
        mod = self._load()
        for prefix in (self._prefix("P1"), ""):
            name = mod.build_job_name("super-image2video", prefix)
            assert len(name) <= 80
            assert ":" not in name and "/" not in name


# ============== launch-time gating of the container's hard requirements ==============

@pytest.mark.unit
class TestBlankIdentityFailsBeforeTheGpuIsProvisioned:
    """An unreadable manifest resolves to blank assetId/output paths (resolve_inputs is
    best-effort). The container treats both as hard requirements but only checks them once the
    Batch job has provisioned a GPU instance, so the run must be rejected here instead."""

    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "modelVariant": "nano",
            "taskMode": "text2video",
            "cosmosPrompt": "A drone shot.",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/cosmos3/E1/",
            "assetId": "xidM",
            "databaseId": "dbM",
            "sfnExternalTaskToken": "tok-123",
        }

    def test_a_blank_asset_id_fails_fast_and_reports_the_task_failure(self):
        mod = self._load()
        start = MagicMock()
        send_failure = MagicMock()
        event = self._event()
        event["assetId"] = ""
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_failure", send_failure):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 400
        start.assert_not_called()
        # The parent workflow must be failed rather than left to its multi-hour taskTimeout.
        assert send_failure.call_count == 1
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_a_blank_output_files_path_fails_fast(self):
        mod = self._load()
        start = MagicMock()
        send_failure = MagicMock()
        event = self._event()
        event["outputS3AssetFilesPath"] = ""
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_failure", send_failure):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 400
        start.assert_not_called()
        assert send_failure.call_count == 1

    def test_a_fully_resolved_run_still_starts(self):
        mod = self._load()
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:Cosmos3:x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200
        start.assert_called_once()
