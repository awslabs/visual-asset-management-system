#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""openPipeline: the nested RequestResponse target that starts the sub-state-machine.

It tolerates the legacy single-path event the cross-pipeline extension-gate harness sends, refuses
the first entry the extension gate rejects (one send_task_failure, no start_execution), mints
batchJobName and runs the sub-state-machine under that exact name, and registers the execution on
the orchestration bus. Its abort helper is deliberately NOT wrapped (nested invoke: a raise sets
FunctionError for vamsExecute) and it has no accumulate-then-scan `responses` loop."""

import datetime
import importlib
import json
import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE = "openPipeline"
_TOKEN = "tok-123"
_EXECUTION_ID = "3f7c1e9a2b4d48c6a1f05e8d7c9b0a12"
_PIPELINE_EXECUTION_ID = "9e8d7c6b5a4f43e2b1a0f9e8d7c6b5a4"
_PREFIX = f"vams.prod-us-east-1.execution.{_EXECUTION_ID}.pipeline.{_PIPELINE_EXECUTION_ID}"
# The container's definition_schema.json `batchJobName` pattern: the id segment is 0-12 characters (empty for
# a direct invocation without an orchestration prefix).
_NAME_SHAPE = re.compile(r"^VideoSopBom_[0-9A-Za-z-]{0,12}_\d{8}_\d{6}_[0-9a-f]{6}$")


def _load():
    if _MODULE in sys.modules:
        return importlib.reload(sys.modules[_MODULE])
    return importlib.import_module(_MODULE)


def _entry(name, key=None):
    return {"bucket": "abkt", "key": key or f"xidV/{name}", "versionId": "",
            "relativePath": f"/{name}", "assetId": "xidV", "databaseId": "dbV"}


def _event(**overrides):
    event = {
        "inputFiles": [_entry("part1.mp4"), _entry("part2.MOV")],
        "inputS3AssetFilePath": "s3://abkt/xidV/part1.mp4",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/genai-video-sop-bom/JOB/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/genai-video-sop-bom/JOB/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/genai-video-sop-bom/JOB/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/genai-video-sop-bom/E1/",
        "assetId": "xidV", "databaseId": "dbV",
        "inputManifestS3Location":
            "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "inputConfigurationS3Location":
            "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
        "sfnExternalTaskToken": _TOKEN,
        "executingUserName": "smoke", "executingRequestContext": "smoke",
        "orchestrationEventPrefix": _PREFIX,
    }
    event.update(overrides)
    return event


def _invoke(mod, event):
    """(response, start_execution mock, send_task_failure mock, put_events mock)."""
    start = MagicMock(return_value={
        "executionArn": "arn:aws:states:us-east-1:1:execution:VideoSopBom:VideoSopBom_x",
        "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
    })
    send_failure = MagicMock()
    put_events = MagicMock()
    with patch.object(mod.sfn, "start_execution", start), \
            patch.object(mod.sfn, "send_task_failure", send_failure), \
            patch.object(mod.events_client, "put_events", put_events):
        resp = mod.lambda_handler(event, MagicMock())
    return resp, start, send_failure, put_events


def _rejection(send_failure):
    assert send_failure.call_count == 1
    kwargs = send_failure.call_args.kwargs
    assert kwargs["taskToken"] == _TOKEN
    assert not kwargs["cause"].startswith(kwargs["error"])
    return kwargs["error"], kwargs["cause"]


@pytest.mark.unit
class TestStartsTheSubStateMachine:
    def test_the_input_list_locations_and_ids_travel_in_the_state_machine_input(self):
        mod = _load()
        event = _event()
        resp, start, send_failure, _ = _invoke(mod, event)
        assert resp["statusCode"] == 200, resp
        send_failure.assert_not_called()
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["inputFiles"] == event["inputFiles"]
        for key in ("inputManifestS3Location", "inputMetadataS3Location",
                    "inputConfigurationS3Location", "orchestrationEventPrefix",
                    "outputS3AssetFilesPath", "inputOutputS3AssetAuxiliaryFilesPath",
                    "assetId", "databaseId"):
            assert sfn_input[key] == event[key], key
        assert sfn_input["externalSfnTaskToken"] == _TOKEN
        assert sfn_input["executionId"] == _EXECUTION_ID
        assert sfn_input["pipelineExecutionId"] == _PIPELINE_EXECUTION_ID
        assert "inputParameters" not in sfn_input and "inputMetadata" not in sfn_input
        assert resp["body"]["batchJobName"] == sfn_input["batchJobName"]

    def test_the_execution_name_is_the_batch_job_name(self):
        mod = _load()
        _, start, _, _ = _invoke(mod, _event())
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert start.call_args.kwargs["name"] == sfn_input["batchJobName"]
        assert start.call_args.kwargs["stateMachineArn"] == mod.STATE_MACHINE_ARN

    def test_the_batch_job_name_shape(self):
        mod = _load()
        _, start, _, _ = _invoke(mod, _event())
        name = start.call_args.kwargs["name"]
        assert _NAME_SHAPE.match(name), name
        assert name.startswith(f"VideoSopBom_{_PIPELINE_EXECUTION_ID[:12]}_")
        assert len(name) <= 80 and ":" not in name and "/" not in name

    def test_names_differ_across_runs_of_the_same_pipeline_execution(self):
        # An SFN retry of the parent task re-invokes with the same prefix; the random suffix keeps
        # start_execution from answering ExecutionAlreadyExists.
        mod = _load()
        names = {_invoke(mod, _event())[1].call_args.kwargs["name"] for _ in range(20)}
        assert len(names) == 20

    def test_a_direct_invocation_without_a_prefix_still_names_the_run(self):
        # No orchestration prefix means no pipeline execution id: the registry formula yields an
        # empty middle segment, which the definition_schema.json pattern ({0,12}) admits, and the
        # two ids travel as "" (the constructPipeline conformance test validates that document too).
        mod = _load()
        _, start, _, _ = _invoke(mod, _event(orchestrationEventPrefix=""))
        name = start.call_args.kwargs["name"]
        assert re.match(r"^VideoSopBom__\d{8}_\d{6}_[0-9a-f]{6}$", name), name
        assert _NAME_SHAPE.match(name), name
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["executionId"] == "" and sfn_input["pipelineExecutionId"] == ""

    def test_registers_the_sub_execution_on_the_orchestration_bus(self):
        mod = _load()
        _, _, _, put_events = _invoke(mod, _event())
        entry = put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["DetailType"] == "pipeline.execution.register"
        assert entry["Source"] == _PREFIX
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == _PIPELINE_EXECUTION_ID
        assert detail["subExecution"]["executionArn"].endswith("VideoSopBom_x")
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/VAMSStateMachine-VideoSopBom"

    def test_a_registration_failure_never_fails_the_pipeline(self):
        mod = _load()
        start = MagicMock(return_value={"executionArn": "arn:x", "startDate": datetime.datetime(2026, 1, 1)})
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock(side_effect=Exception("denied"))):
            resp = mod.lambda_handler(_event(), MagicMock())
        assert resp["statusCode"] == 200

    def test_a_start_execution_failure_reports_the_token(self):
        mod = _load()
        send_failure = MagicMock()
        with patch.object(mod.sfn, "start_execution", MagicMock(side_effect=Exception("boom"))), \
                patch.object(mod.sfn, "send_task_failure", send_failure), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(_event(), MagicMock())
        assert resp["statusCode"] == 500
        code, cause = _rejection(send_failure)
        assert code == "VideoSopBomPipelineError"
        assert "could not be started" in cause and "boom" in cause


@pytest.mark.unit
class TestLegacyEventTolerance:
    """The cross-pipeline extension-gate harness sends ONE inputS3AssetFilePath and no inputFiles."""

    def test_a_single_legacy_path_becomes_one_input_entry(self):
        mod = _load()
        event = _event()
        del event["inputFiles"]
        resp, start, _, _ = _invoke(mod, event)
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["inputFiles"] == [
            {"bucket": "abkt", "key": "xidV/part1.mp4", "versionId": "", "relativePath": "/part1.mp4"}]

    def test_a_legacy_folder_uri_is_rejected(self):
        mod = _load()
        event = _event(inputS3AssetFilePath="s3://abkt/xidV/videos/")
        del event["inputFiles"]
        resp, start, send_failure, _ = _invoke(mod, event)
        assert resp["statusCode"] == 400
        start.assert_not_called()
        code, cause = _rejection(send_failure)
        assert code == "VideoSopBomInputRejected"
        assert "xidV/videos/" in cause and "whole-asset or folder" in cause

    def test_no_inputs_at_all_is_rejected(self):
        mod = _load()
        event = _event(inputS3AssetFilePath="")
        del event["inputFiles"]
        resp, start, send_failure, _ = _invoke(mod, event)
        assert resp["statusCode"] == 400
        start.assert_not_called()
        code, cause = _rejection(send_failure)
        assert code == "VideoSopBomInputRejected" and "at least 1" in cause


@pytest.mark.unit
class TestExtensionGate:
    def test_the_first_offender_is_named(self):
        mod = _load()
        event = _event(inputFiles=[_entry("part1.mp4"), _entry("notes.pdf"), _entry("clip.avi")])
        resp, start, send_failure, _ = _invoke(mod, event)
        assert resp["statusCode"] == 400
        start.assert_not_called()
        code, cause = _rejection(send_failure)
        assert code == "VideoSopBomInputRejected"
        assert "notes.pdf" in cause and ".pdf" in cause and "clip.avi" not in cause
        assert "accepts .mp4, .mov, .m4v, .webm, .mkv" in cause

    @pytest.mark.parametrize("extension", [".m", ".mp", ".mo", ".we", ".mk", ".mp4,"])
    def test_a_substring_of_the_joined_allow_list_is_rejected(self, extension):
        mod = _load()
        resp, start, send_failure, _ = _invoke(mod, _event(inputFiles=[_entry(f"capture{extension}")]))
        assert resp["statusCode"] == 400
        start.assert_not_called()
        assert send_failure.call_count == 1

    @pytest.mark.parametrize("extension", [".mp4", ".mov", ".m4v", ".webm", ".mkv", ".MP4", ".Mkv"])
    def test_a_listed_extension_is_accepted_in_either_case(self, extension):
        mod = _load()
        resp, start, send_failure, _ = _invoke(mod, _event(inputFiles=[_entry(f"capture{extension}")]))
        assert resp["statusCode"] == 200
        start.assert_called_once()
        send_failure.assert_not_called()

    def test_a_container_entry_is_rejected(self):
        mod = _load()
        folder = _entry("videos", key="xidV/videos/")
        resp, start, send_failure, _ = _invoke(mod, _event(inputFiles=[folder]))
        assert resp["statusCode"] == 400
        start.assert_not_called()
        _, cause = _rejection(send_failure)
        assert "xidV/videos/" in cause

    def test_a_rejection_body_carries_message_not_error(self):
        # backendPipelines/tests/test_open_pipeline_failure_route_returns.py forbids an "error" key
        # in a 4xx body; the readable text rides in "message" and on the task token.
        mod = _load()
        resp, _, _, _ = _invoke(mod, _event(inputFiles=[_entry("notes.pdf")]))
        assert "message" in resp["body"] and "error" not in resp["body"]


@pytest.mark.unit
class TestModuleContract:
    def _source(self):
        return open(os.path.join(_LAMBDA_DIR, f"{_MODULE}.py"), encoding="utf-8").read()

    def test_the_five_extension_default_applies_when_the_variable_is_unset(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_INPUT_FILEEXTENSIONS", raising=False)
        try:
            mod = _load()
            assert mod.ALLOWED_INPUT_FILEEXTENSIONS == ".mp4,.mov,.m4v,.webm,.mkv"
        finally:
            monkeypatch.undo()
            _load()

    def test_module_level_clients_and_env_are_read_at_import(self):
        mod = _load()
        assert hasattr(mod, "sfn") and hasattr(mod, "events_client")
        assert mod.STATE_MACHINE_ARN == os.environ["STATE_MACHINE_ARN"]
        assert mod.BATCH_JOB_NAME_PREFIX == "VideoSopBom_"

    def test_a_missing_state_machine_arn_fails_at_import(self, monkeypatch):
        # The registry rule: the required variable is read with os.environ[...] and no default, so a
        # builder that omits it fails at import rather than at the first start_execution.
        monkeypatch.delenv("STATE_MACHINE_ARN")
        try:
            with pytest.raises(KeyError, match="STATE_MACHINE_ARN"):
                _load()
        finally:
            monkeypatch.undo()
            _load()

    def test_the_abort_helper_cuts_an_overlong_cause_to_the_stored_budget(self):
        # SendTaskFailure rejects a cause over 32768 characters; the stored budget is 16384.
        mod = _load()
        assert mod.MAX_CAUSE_CHARS == 16384
        send_failure = MagicMock()
        with patch.object(mod.sfn, "send_task_failure", send_failure):
            mod.abort_external_workflow("VideoSopBomPipelineError", "w" * 40000, _TOKEN)
        kwargs = send_failure.call_args.kwargs
        assert kwargs["taskToken"] == _TOKEN and kwargs["error"] == "VideoSopBomPipelineError"
        assert len(kwargs["cause"]) == 16384

    def test_the_abort_helper_is_not_wrapped(self):
        # Nested-invoke rule (backendPipelines/CLAUDE.md): a raise here sets FunctionError, which
        # makes vamsExecute report the token under its own role. Wrapping it hides the failure.
        source = self._source()
        start = source.index("def abort_external_workflow")
        end = source.find("\ndef ", start + 1)
        assert "try:" not in source[start:end]

    def test_no_accumulate_then_scan_responses_loop(self):
        # Keeps _EXPECTED_WITH_SCAN at 10 in test_open_pipeline_failure_route_returns.py.
        source = self._source()
        # Positive control: the file read is the handler (an empty file passes the negatives alone).
        assert "def lambda_handler" in source and "start_execution(" in source
        assert "in responses" not in source
        assert '"error" in response' not in source and "'error' in response" not in source

    @pytest.mark.parametrize("prefix,expected", [
        (_PREFIX, (_EXECUTION_ID, _PIPELINE_EXECUTION_ID)),
        ("garbage", ("", "")),
        ("x.pipeline.P1", ("", "P1")),
        ("", ("", "")),
    ])
    def test_ids_from_event_prefix(self, prefix, expected):
        mod = _load()
        assert mod.ids_from_event_prefix(prefix) == expected
