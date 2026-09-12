#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""pipelineEnd: the last state of the sub-state-machine, and the only one that releases the
external token after the container ran.

Success needs the container's reporter marker in $.batchResult — a container that exited 0 without
calling back must not read as success. A failure forwards the inner callback's cause verbatim under
its code; Batch's job description (a .sync failure with no callback) is lifted into a readable
sentence, with the attempt-timeout text mapped to the 6 h bound. An already-reported token is not an
error. The event is returned unchanged so $.error survives for the Choice."""

import copy
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_MODULE = "pipelineEnd"
_TOKEN = "tok-123"
_CODES = ("VideoSopBomInputRejected", "VideoSopBomLimitExceeded", "VideoSopBomTranscribeFailed",
          "VideoSopBomModelOutputInvalid", "VideoSopBomConnectivityError", "VideoSopBomPipelineError")


def _load():
    if _MODULE in sys.modules:
        return importlib.reload(sys.modules[_MODULE])
    return importlib.import_module(_MODULE)


def _event(**overrides):
    event = {"batchJobName": "VideoSopBom_9e8d7c6b5a4f_20260909_120000_a1b2c3",
             "definitionCommand": ["--definition-s3-uri",
                                   "s3://aux/pipelines/genai-video-sop-bom/E1/definition.json"],
             "externalSfnTaskToken": _TOKEN, "status": "STARTING"}
    event.update(overrides)
    return event


def _marker():
    return {"reporter": "video_sop_bom_pipeline", "status": "SUCCEEDED", "fileCount": 14,
            "summaryKey": "pipelines/genai-video-sop-bom/JOB/output/E1/results/summary.json"}


def _batch_job(status="FAILED", exit_code=137, reason="Essential container in task exited",
               status_reason=None):
    """A Batch job description in the PascalCase shape Step Functions returns for a .sync task."""
    container = {"ExitCode": exit_code, "LogStreamName": "VideoSopBom/default/abc"}
    if reason is not None:
        container["Reason"] = reason
    job = {"JobName": "VideoSopBom_9e8d7c6b5a4f_20260909_120000_a1b2c3", "JobId": "9b1e-job",
           "Status": status, "Container": container,
           "Attempts": [{"Container": dict(container), "StatusReason": status_reason or ""}]}
    if status_reason:
        job["StatusReason"] = status_reason
    return job


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "SendTaskFailure")


def _run(mod, event):
    """(result, send_task_success mock, send_task_failure mock)."""
    success = MagicMock()
    failure = MagicMock()
    with patch.object(mod.sfn, "send_task_success", success), \
            patch.object(mod.sfn, "send_task_failure", failure):
        result = mod.lambda_handler(event, MagicMock())
    return result, success, failure


def _failure(failure):
    """The single send_task_failure call's (error, cause), with the contract asserted; the budget is
    read from the loaded module and its value pinned once, in TestFailureCauses."""
    assert failure.call_count == 1
    kwargs = failure.call_args.kwargs
    assert kwargs["taskToken"] == _TOKEN
    assert kwargs["error"] in _CODES
    assert not kwargs["cause"].startswith(kwargs["error"])
    assert len(kwargs["cause"]) <= sys.modules[_MODULE].MAX_CAUSE_CHARS
    return kwargs["error"], kwargs["cause"]


@pytest.mark.unit
class TestSuccessNeedsTheReporterMarker:
    def test_the_reporter_marker_releases_the_token(self):
        mod = _load()
        event = _event(batchResult=_marker())
        result, success, failure = _run(mod, event)
        failure.assert_not_called()
        success.assert_called_once()
        assert success.call_args.kwargs["taskToken"] == _TOKEN
        assert json.loads(success.call_args.kwargs["output"]) == {"status": "Pipeline Success"}
        assert result is event

    def test_a_job_that_exited_zero_without_reporting_is_a_failure(self):
        mod = _load()
        result, success, failure = _run(
            mod, _event(batchResult=_batch_job(status="SUCCEEDED", exit_code=0, reason=None)))
        success.assert_not_called()
        code, cause = _failure(failure)
        assert code == "VideoSopBomPipelineError"
        assert cause == "container exited without reporting (Batch status SUCCEEDED, exit 0)."

    def test_a_missing_batch_result_is_a_failure(self):
        mod = _load()
        _, success, failure = _run(mod, _event())
        success.assert_not_called()
        code, cause = _failure(failure)
        assert code == "VideoSopBomPipelineError" and "without reporting" in cause

    def test_a_foreign_reporter_is_a_failure(self):
        mod = _load()
        _, success, failure = _run(mod, _event(batchResult={"reporter": "someone_else"}))
        success.assert_not_called()
        _failure(failure)


@pytest.mark.unit
class TestFailureCauses:
    @pytest.mark.parametrize("code", _CODES)
    def test_an_inner_callback_cause_is_forwarded_verbatim_under_its_code(self, code):
        mod = _load()
        sentence = "part3.mov is 5.2 GB; this deployment allows at most 4.0 GB per video."
        _, success, failure = _run(mod, _event(error={"Error": code, "Cause": sentence}))
        success.assert_not_called()
        assert _failure(failure) == (code, sentence)

    def test_batch_json_without_a_callback_is_lifted(self):
        mod = _load()
        error = {"Error": "States.TaskFailed", "Cause": json.dumps(_batch_job())}
        _, _, failure = _run(mod, _event(error=error))
        code, cause = _failure(failure)
        assert code == "VideoSopBomPipelineError"
        assert cause == ("container exited without reporting (Batch status FAILED, exit 137, "
                         "Essential container in task exited).")

    def test_the_attempt_timeout_text_is_mapped_to_the_six_hour_bound(self):
        mod = _load()
        job = _batch_job(status_reason="Job attempt duration exceeded timeout")
        _, _, failure = _run(mod, _event(error={"Error": "States.TaskFailed", "Cause": json.dumps(job)}))
        _, cause = _failure(failure)
        assert cause == mod.ATTEMPT_TIMEOUT_CAUSE
        assert cause == "the container did not finish within the 6 h bound; last stage per the container log"

    def test_camel_case_job_keys_are_read_too(self):
        mod = _load()
        job = {"jobId": "x", "status": "FAILED", "statusReason": "Task failed to start",
               "container": {"exitCode": 1, "reason": "CannotPullContainerError"}}
        _, _, failure = _run(mod, _event(error={"Error": "States.TaskFailed", "Cause": json.dumps(job)}))
        _, cause = _failure(failure)
        assert "exit 1" in cause and "CannotPullContainerError" in cause and "Task failed to start" in cause

    def test_a_lambda_error_json_cause_uses_its_error_message(self):
        # The ConstructPipelineTask catch: Step Functions wraps a Lambda raise as JSON.
        mod = _load()
        message = "the state machine input carries no inputManifestS3Location; the pipeline cannot locate its outputs."
        cause_json = json.dumps({"errorMessage": message, "errorType": "ValueError", "stackTrace": []})
        _, _, failure = _run(mod, _event(error={"Error": "ValueError", "Cause": cause_json}))
        assert _failure(failure) == ("VideoSopBomPipelineError", message)

    def test_an_unknown_error_with_a_plain_cause_is_forwarded_as_a_pipeline_error(self):
        mod = _load()
        _, _, failure = _run(mod, _event(error={"Error": "States.Runtime", "Cause": "some text"}))
        assert _failure(failure) == ("VideoSopBomPipelineError", "some text")

    def test_an_error_block_without_a_cause_still_reports(self):
        mod = _load()
        _, _, failure = _run(mod, _event(error={"Error": "States.Runtime"}))
        assert _failure(failure) == ("VideoSopBomPipelineError", "States.Runtime")

    def test_an_overlong_cause_is_cut_to_the_stored_budget(self):
        # A Cause over 32768 characters (a Lambda stack-trace JSON from the ConstructPipelineTask
        # catch, or a long container sentence) makes SendTaskFailure raise ValidationException —
        # not an already-reported code, so _send would re-raise and the external token would never
        # be failed: the taskTimeout hang this Lambda exists to prevent.
        mod = _load()
        assert mod.MAX_CAUSE_CHARS == 16384
        _, _, failure = _run(mod, _event(error={"Error": "VideoSopBomPipelineError", "Cause": "x" * 40000}))
        assert len(_failure(failure)[1]) == 16384
        # The unknown-code text path is cut the same way.
        _, _, failure = _run(mod, _event(error={"Error": "States.Runtime", "Cause": "y" * 40000}))
        assert len(_failure(failure)[1]) == 16384


@pytest.mark.unit
class TestCallbackWrapping:
    @pytest.mark.parametrize("code", ["TaskDoesNotExist", "TaskTimedOut", "InvalidToken"])
    def test_an_already_reported_failure_token_is_not_an_error(self, code):
        mod = _load()
        event = _event(error={"Error": "VideoSopBomInputRejected", "Cause": "x is y."})
        with patch.object(mod.sfn, "send_task_failure", MagicMock(side_effect=_client_error(code))):
            assert mod.lambda_handler(event, MagicMock()) is event

    def test_an_already_reported_success_token_is_not_an_error(self):
        mod = _load()
        event = _event(batchResult=_marker())
        with patch.object(mod.sfn, "send_task_success", MagicMock(side_effect=_client_error("TaskTimedOut"))):
            assert mod.lambda_handler(event, MagicMock()) is event

    def test_other_client_errors_raise(self):
        # An AccessDeniedException means the grant is missing; the sub-state-machine should fail
        # loudly rather than record a success the workflow never heard about.
        mod = _load()
        event = _event(error={"Error": "VideoSopBomInputRejected", "Cause": "x is y."})
        with patch.object(mod.sfn, "send_task_failure", MagicMock(side_effect=_client_error("AccessDeniedException"))), \
                pytest.raises(ClientError):
            mod.lambda_handler(event, MagicMock())

    def test_no_token_sends_nothing_and_returns_the_event(self):
        mod = _load()
        event = _event(externalSfnTaskToken="", error={"Error": "VideoSopBomInputRejected", "Cause": "x."})
        result, success, failure = _run(mod, event)
        success.assert_not_called()
        failure.assert_not_called()
        assert result is event

    @pytest.mark.parametrize("event", [
        _event(batchResult=_marker()),
        _event(error={"Error": "VideoSopBomLimitExceeded", "Cause": "total video duration 5h12m exceeds 4h00m."}),
        _event(error={"Error": "States.TaskFailed", "Cause": json.dumps(_batch_job())}),
    ])
    def test_the_event_is_returned_unchanged(self, event):
        mod = _load()
        snapshot = copy.deepcopy(event)
        result, _, _ = _run(mod, event)
        assert result is event and result == snapshot
