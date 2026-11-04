#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""pipelineEnd: the last state of the sub-state-machine, and the only one that releases the
external token after the container ran.

Success needs the container's reporter marker in $.batchResult — a container that exited 0 without
calling back must not read as success. A failure forwards the inner callback's cause verbatim under
its code; Batch's job description (a .sync failure with no callback) is lifted into a readable
sentence, with the attempt-timeout text mapped to the 6 h bound. An already-reported token is not an
error. The event is returned unchanged so $.error survives for the Choice.

On a failed outcome the handler also deletes the run's scratch prefix on the auxiliary bucket — the
container does that itself on success, so a rejection, handled failure, unreported exit or abort was
what left objects behind until the lifecycle rule expired them. The cleanup runs AFTER the token
release and never raises: the token is this handler's one job."""

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
_AUX_BUCKET = "aux"
_AUX_PREFIX = "pipelines/genai-video-sop-bom/E1/"


def _load():
    if _MODULE in sys.modules:
        return importlib.reload(sys.modules[_MODULE])
    return importlib.import_module(_MODULE)


def _event(**overrides):
    event = {"batchJobName": "VideoSopBom_9e8d7c6b5a4f_20260909_120000_a1b2c3",
             "definitionCommand": ["--definition-s3-uri",
                                   f"s3://{_AUX_BUCKET}/{_AUX_PREFIX}definition.json"],
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


class _S3Stub:
    """An auxiliary bucket holding `keys`, paged `page_size` at a time, that records every call in
    `calls` alongside the token callbacks so the order between release and cleanup is assertable.
    `fail_on` names a method ("list_objects_v2" / "delete_objects") that raises a ClientError."""

    def __init__(self, keys=(), page_size=1000, fail_on=None, calls=None):
        self.bucket = _AUX_BUCKET
        self.objects = {k: True for k in keys}
        self.page_size = page_size
        self.fail_on = fail_on
        self.calls = calls if calls is not None else []
        self.deleted = []

    def _check(self, name, Bucket):
        self.calls.append(name)
        assert Bucket == self.bucket
        if self.fail_on == name:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": name}}, name)

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        self._check("list_objects_v2", Bucket)
        # S3 continues after the last key of the previous page, so a page deleted in between does
        # not shift the next one — an offset token would, and would hide a real skipped page.
        keys = sorted(k for k in self.objects if k.startswith(Prefix) and k > (ContinuationToken or ""))
        page = keys[:self.page_size]
        truncated = len(keys) > self.page_size
        out = {"Contents": [{"Key": k} for k in page], "IsTruncated": truncated}
        if truncated:
            out["NextContinuationToken"] = page[-1]
        return out

    def delete_objects(self, Bucket, Delete):
        self._check("delete_objects", Bucket)
        assert Delete.get("Quiet") is True
        assert 1 <= len(Delete["Objects"]) <= 1000
        for obj in Delete["Objects"]:
            self.deleted.append(obj["Key"])
            self.objects.pop(obj["Key"], None)
        return {}


def _recording(mock, name, calls):
    """A MagicMock whose every call is appended to `calls` under `name` (then behaves as `mock`)."""
    def _call(**kwargs):
        calls.append(name)
        return mock(**kwargs)
    return MagicMock(side_effect=_call)


def _run(mod, event, s3=None):
    """(result, send_task_success mock, send_task_failure mock); the S3 client is always stubbed so no
    test reaches boto3, and the stub (default: an empty bucket) is reachable as result.s3 via `s3`."""
    s3 = s3 if s3 is not None else _S3Stub()
    success = MagicMock()
    failure = MagicMock()
    with patch.object(mod.sfn, "send_task_success", _recording(success, "send_task_success", s3.calls)), \
            patch.object(mod.sfn, "send_task_failure", _recording(failure, "send_task_failure", s3.calls)), \
            patch.object(mod, "s3", s3):
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
        with patch.object(mod.sfn, "send_task_failure", MagicMock(side_effect=_client_error(code))), \
                patch.object(mod, "s3", _S3Stub()):
            assert mod.lambda_handler(event, MagicMock()) is event

    def test_an_already_reported_success_token_is_not_an_error(self):
        mod = _load()
        event = _event(batchResult=_marker())
        with patch.object(mod.sfn, "send_task_success", MagicMock(side_effect=_client_error("TaskTimedOut"))), \
                patch.object(mod, "s3", _S3Stub()):
            assert mod.lambda_handler(event, MagicMock()) is event

    def test_other_client_errors_raise(self):
        # An AccessDeniedException means the grant is missing; the sub-state-machine should fail
        # loudly rather than record a success the workflow never heard about. The cleanup runs after
        # the release, so it must not get a chance to swallow this either.
        mod = _load()
        s3 = _S3Stub(keys=[f"{_AUX_PREFIX}definition.json"])
        event = _event(error={"Error": "VideoSopBomInputRejected", "Cause": "x is y."})
        with patch.object(mod.sfn, "send_task_failure", MagicMock(side_effect=_client_error("AccessDeniedException"))), \
                patch.object(mod, "s3", s3), pytest.raises(ClientError):
            mod.lambda_handler(event, MagicMock())
        assert s3.calls == []

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


@pytest.mark.unit
class TestAuxScratchCleanup:
    """The run's objects under pipelines/genai-video-sop-bom/<executionId>/ after pipelineEnd."""

    _SCRATCH = (f"{_AUX_PREFIX}definition.json", f"{_AUX_PREFIX}audio/combined.flac",
                f"{_AUX_PREFIX}transcribe/combined.json", f"{_AUX_PREFIX}analysis/segments.json")
    # Another run's folder under the same root, and a sibling pipeline's prefix: neither is this run's.
    _OTHERS = ("pipelines/genai-video-sop-bom/E2/definition.json",
               "pipelines/other-pipeline/E1/definition.json")

    def _bucket(self, **kwargs):
        return _S3Stub(keys=self._SCRATCH + self._OTHERS, **kwargs)

    @pytest.mark.parametrize("event", [
        # ConstructPipelineTask raised (a pre-invoke rejection or a fault): the definition is the
        # one object written so far.
        _event(error={"Error": "VideoSopBomLimitExceeded", "Cause": "total video duration 00h03m20s exceeds 3 minutes."}),
        # The container reported a handled failure on the inner token (silent clip, bad model output).
        _event(error={"Error": "VideoSopBomTranscribeFailed", "Cause": "no speech was detected."}),
        # A .sync failure with no callback (OOM, SIGKILL, attempt timeout).
        _event(error={"Error": "States.TaskFailed", "Cause": json.dumps(_batch_job())}),
        # A job that exited without reporting, and a foreign reporter.
        _event(batchResult=_batch_job(status="SUCCEEDED", exit_code=0, reason=None)),
        _event(batchResult={"reporter": "someone_else"}),
        _event(),
    ], ids=["construct-rejection", "handled-container-failure", "sync-failure", "unreported-exit",
            "foreign-reporter", "no-batch-result"])
    def test_every_failed_outcome_deletes_this_runs_prefix_and_nothing_else(self, event):
        mod = _load()
        s3 = self._bucket()
        _, _, failure = _run(mod, event, s3=s3)
        _failure(failure)
        assert sorted(s3.deleted) == sorted(self._SCRATCH)
        assert set(s3.objects) == set(self._OTHERS)

    def test_success_deletes_nothing(self):
        # The container's upload stage deleted the prefix itself before it reported; a second pass
        # here would only add delete markers on a versioned bucket.
        mod = _load()
        s3 = self._bucket()
        _run(mod, _event(batchResult=_marker()), s3=s3)
        assert s3.deleted == [] and "list_objects_v2" not in s3.calls

    def test_the_token_is_released_before_any_s3_call(self):
        # The release is this handler's one job; the cleanup must never sit between a failure and
        # the parent workflow learning of it.
        mod = _load()
        s3 = self._bucket()
        _run(mod, _event(error={"Error": "VideoSopBomInputRejected", "Cause": "x is y."}), s3=s3)
        assert s3.calls[0] == "send_task_failure"
        assert s3.calls.index("send_task_failure") < s3.calls.index("list_objects_v2")

    def test_no_token_still_cleans_up(self):
        # A direct invoke carries no token; its scratch is no more wanted.
        mod = _load()
        s3 = self._bucket()
        _run(mod, _event(externalSfnTaskToken="", error={"Error": "VideoSopBomInputRejected", "Cause": "x."}), s3=s3)
        assert sorted(s3.deleted) == sorted(self._SCRATCH)

    def test_a_prefix_wider_than_one_list_page_is_walked_to_exhaustion(self):
        mod = _load()
        keys = [f"{_AUX_PREFIX}frames/frame-{i:04d}.jpg" for i in range(2500)]
        s3 = _S3Stub(keys=keys, page_size=1000)
        _run(mod, _event(error={"Error": "VideoSopBomPipelineError", "Cause": "x."}), s3=s3)
        assert s3.objects == {}
        assert s3.calls.count("list_objects_v2") == 3
        assert s3.calls.count("delete_objects") == 3

    @pytest.mark.parametrize("failing", ["list_objects_v2", "delete_objects"])
    def test_an_s3_error_is_logged_and_the_event_is_still_returned(self, failing):
        # Best-effort: a missing grant or a throttled delete must not turn a reported failure into a
        # Lambda error that fails the sub-state-machine a second way.
        mod = _load()
        s3 = self._bucket(fail_on=failing)
        event = _event(error={"Error": "VideoSopBomInputRejected", "Cause": "x is y."})
        result, _, failure = _run(mod, event, s3=s3)
        _failure(failure)
        assert result is event

    def test_the_locator_is_the_definition_uri_the_state_carries(self):
        mod = _load()
        assert mod.aux_prefix_location(_event()) == (_AUX_BUCKET, _AUX_PREFIX)

    @pytest.mark.parametrize("command", [
        None, [], ["--definition-s3-uri"], ["--other-flag", f"s3://{_AUX_BUCKET}/{_AUX_PREFIX}definition.json"],
        ["--definition-s3-uri", f"https://{_AUX_BUCKET}/{_AUX_PREFIX}definition.json"],
        ["--definition-s3-uri", f"s3://{_AUX_BUCKET}/{_AUX_PREFIX}other.json"],
        # Outside this pipeline's root, the root itself, and a folder deeper than one execution.
        ["--definition-s3-uri", f"s3://{_AUX_BUCKET}/pipelines/other-pipeline/E1/definition.json"],
        ["--definition-s3-uri", f"s3://{_AUX_BUCKET}/pipelines/genai-video-sop-bom/definition.json"],
        ["--definition-s3-uri", f"s3://{_AUX_BUCKET}/pipelines/genai-video-sop-bom//definition.json"],
        ["--definition-s3-uri", f"s3://{_AUX_BUCKET}/pipelines/genai-video-sop-bom/E1/sub/definition.json"],
        ["--definition-s3-uri", f"s3://{_AUX_BUCKET}/genai-video-sop-bom/E1/definition.json"],
        ["--definition-s3-uri", "s3:///pipelines/genai-video-sop-bom/E1/definition.json"],
    ])
    def test_a_locator_outside_one_execution_folder_of_this_pipeline_deletes_nothing(self, command):
        # The delete is aimed by the event. A malformed or foreign locator yields no location, so a
        # failed run can never clear another run's folder, another pipeline's prefix, or the root.
        mod = _load()
        event = _event(error={"Error": "VideoSopBomInputRejected", "Cause": "x."})
        if command is None:
            del event["definitionCommand"]
        else:
            event["definitionCommand"] = command
        assert mod.aux_prefix_location(event) is None
        s3 = self._bucket()
        result, _, failure = _run(mod, event, s3=s3)
        _failure(failure)
        assert result is event and s3.calls == ["send_task_failure"]

    def test_the_root_the_handler_refuses_outside_of_is_the_lifecycle_rules_prefix(self):
        # The storage stack's lifecycle rule and the pipelineEnd role's s3:prefix condition both name
        # this root; the handler's own guard must be the same string or one of the three drifts.
        mod = _load()
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.abspath(os.path.join(here, "..", "..", "..", "..", ".."))
        storage = open(os.path.join(repo, "infra", "lib", "nestedStacks", "storage",
                                    "storageBuilder-nestedStack.ts"), encoding="utf-8").read()
        builder = open(os.path.join(repo, "infra", "lib", "nestedStacks", "pipelines", "genAi", "videoSopBom",
                                    "lambdaBuilder", "videoSopBomFunctions.ts"), encoding="utf-8").read()
        assert mod.AUX_PREFIX_ROOT == "pipelines/genai-video-sop-bom/"
        assert f'prefix: "{mod.AUX_PREFIX_ROOT}"' in storage
        assert f'VIDEO_SOP_BOM_AUX_PREFIX_ROOT = "{mod.AUX_PREFIX_ROOT}"' in builder
