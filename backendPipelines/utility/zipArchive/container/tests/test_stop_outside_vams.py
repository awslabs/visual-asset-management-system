#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""#1125 in-dev D26: a container stopped outside VAMS ends its run instead of holding the job.

Seen on demo22: a Batch TerminateJob outside VAMS (the path an out-of-memory kill or a host loss also
takes) made the container record its stop and exit 143 without answering the zipArchive state
machine's own task token. The ZipArchiveBatchJob state then waited for its 11-hour timeout, and the
job stayed Preparing (holding one of the user's active slots) the whole time.

- Soft kill (SIGTERM) with no VAMS stopper recorded: the container answers its own token with
  STOPPED_ERROR, so the run ends FAILED; the read-time reconcile then shows the recorded stop reason.
- SIGTERM after a VAMS stopper recorded the stop (``stopRequestedAt``: the user's cancel, the
  read-time reconcile, a delete; or the job already ended): nothing is sent, as before (#1125 D24).
- Hard kill (no SIGTERM): the container heartbeats its own token as well as the VAMS workflow's, so
  the state's heartbeat timeout ends the run (States.HeartbeatTimeout).
"""

import pytest

from zip_archive_pipeline import runner
from test_runner import AUX
from test_terminal_races import _hook, _setup, zd

pytestmark = pytest.mark.unit


class FakeSfn:
    def __init__(self, fail=False):
        self.failures, self.heartbeats, self.fail = [], [], fail

    def send_task_failure(self, taskToken, error, cause):  # noqa: N803 - mirrors boto3
        if self.fail:
            raise RuntimeError("TaskTimedOut")
        self.failures.append((taskToken, error, cause))

    def send_task_heartbeat(self, taskToken):  # noqa: N803 - mirrors boto3
        if self.fail:
            raise RuntimeError("throttled")
        self.heartbeats.append(taskToken)


def _sigterm_while_writing(monkeypatch, job, sfn, token="internal-token"):
    """The SIGTERM handler, as main installs it, fired while the archive upload is open."""
    handler = runner.sigterm_handler(job, lambda: runner.answer_stop(job, sfn, token))
    _hook(monkeypatch, "upload_open", lambda: handler(15, None))
    with pytest.raises(SystemExit) as raised:
        job.run()
    assert raised.value.code == 143


class TestSoftKill:
    def test_a_terminate_outside_vams_answers_the_token_with_the_stop(self, monkeypatch):
        s3, table, job = _setup()
        sfn = FakeSfn()
        _sigterm_while_writing(monkeypatch, job, sfn)
        assert sfn.failures == [("internal-token", runner.STOPPED_ERROR,
                                 runner.REASON_PREFIX + runner.STOPPED)]
        assert runner.STOPPED_ERROR == "ZipArchiveContainerStopped"
        # The outcome is still left to the finalizer, with the stop recorded (#1125 D24).
        assert table.row["status"] == "RUNNING" and table.row["stopReason"] == runner.STOPPED
        assert not [k for (b, k) in s3.uploads if b == AUX], "the stop aborted the upload"

    def test_the_run_that_failed_then_finalizes_the_job_with_the_stop(self, monkeypatch):
        s3, table, job = _setup()
        _sigterm_while_writing(monkeypatch, job, FakeSfn())
        # pipelineEnd reports the failure to the VAMS workflow: the run's main row is FAILED with
        # "Pipeline Failure: ZipArchiveContainerStopped: ..." (handleExecutionError).
        error = f"Pipeline Failure: {runner.STOPPED_ERROR}: See AWS cloudwatch logs for error cause."
        status, changed = zd.finalize_ended_run(table, s3, AUX, dict(table.row), "FAILED", error)
        assert (status, changed) == (zd.STATUS_FAILED, True)
        assert table.row["errorMessage"] == runner.STOPPED

    @pytest.mark.parametrize("marker", ["stopRequestedAt", "cancelRequestedAt"])
    def test_a_stop_a_vams_stopper_recorded_sends_nothing(self, monkeypatch, marker):
        s3, table, job = _setup()
        sfn = FakeSfn()
        handler = runner.sigterm_handler(job, lambda: runner.answer_stop(job, sfn, "internal-token"))

        def vams_stop_then_sigterm():
            table.row["stopRequestedAt"] = "2026-11-10T00:00:00Z"
            if marker == "cancelRequestedAt":
                table.row["cancelRequestedAt"] = "2026-11-10T00:00:00Z"
            handler(15, None)
        _hook(monkeypatch, "upload_open", vams_stop_then_sigterm)
        with pytest.raises(SystemExit):
            job.run()
        assert sfn.failures == []
        assert table.row["stopReason"] == runner.STOPPED

    def test_a_job_another_writer_already_ended_sends_nothing(self, monkeypatch):
        s3, table, job = _setup()
        sfn = FakeSfn()
        handler = runner.sigterm_handler(job, lambda: runner.answer_stop(job, sfn, "internal-token"))

        def ended_then_sigterm():
            table.row["status"] = "CANCELLED"
            handler(15, None)
        _hook(monkeypatch, "upload_open", ended_then_sigterm)
        with pytest.raises(SystemExit):
            job.run()
        assert sfn.failures == []

    def test_a_stop_during_the_complete_answers_nothing_and_lets_it_finish(self, monkeypatch):
        s3, table, job = _setup()
        sfn = FakeSfn()
        handler = runner.sigterm_handler(job, lambda: runner.answer_stop(job, sfn, "internal-token"))

        class Completing(runner.MultipartSink):
            def close(self):
                self.completing = True
                handler(15, None)   # returns: stop() answers False while the Complete is in flight
                self.completing = False
                return super().close()
        monkeypatch.setattr(runner, "MultipartSink", Completing)
        # The handler returned instead of exiting (the Complete's own path decides; its outcomes are
        # pinned in test_abort_204.py), and the token was not answered from the handler.
        job.run()
        assert sfn.failures == []

    def test_mode_2_has_no_job_row_and_always_answers(self):
        job = runner.Job({"mode": "archiveRun"}, None, None, logger=lambda *a: None)
        job._stopped = True
        sfn = FakeSfn()
        assert runner.answer_stop(job, sfn, "t") is True
        assert sfn.failures[0][1] == runner.STOPPED_ERROR

    def test_no_token_and_a_failed_send_are_harmless(self):
        job = runner.Job({"mode": "archiveRun"}, None, None, logger=lambda *a: None)
        assert runner.answer_stop(job, FakeSfn(), "") is False
        assert runner.answer_stop(job, FakeSfn(fail=True), "t") is False

    def test_a_row_read_error_keeps_the_old_behaviour(self, monkeypatch):
        s3, table, job = _setup()
        job.status = runner.StatusWriter(table, "alice", "r1", "exec-1")
        monkeypatch.setattr(runner.StatusWriter, "must_stop",
                            lambda self: (_ for _ in ()).throw(RuntimeError("read failed")))
        sfn = FakeSfn()
        assert runner.answer_stop(job, sfn, "t") is False and sfn.failures == []


class TestHardKill:
    def test_both_tokens_are_heartbeated(self):
        sfn = FakeSfn()
        beat = runner.Heartbeat(sfn, ["external-token", "", "internal-token"])
        beat.beat()
        assert sfn.heartbeats == ["external-token", "internal-token"]

    def test_a_failed_heartbeat_is_retried_next_interval(self):
        runner.Heartbeat(FakeSfn(fail=True), ["t"]).beat()

    def test_a_single_token_still_works(self):
        sfn = FakeSfn()
        runner.Heartbeat(sfn, "only").beat()
        assert sfn.heartbeats == ["only"]

    def test_the_interval_is_well_inside_the_state_heartbeat_timeout(self):
        # infra zipArchive-construct.ts ZIP_ARCHIVE_BATCH_HEARTBEAT (pinned there to 30 minutes).
        assert runner.HEARTBEAT_SECONDS == 120
        assert runner.HEARTBEAT_SECONDS * 5 < 30 * 60

    def test_main_heartbeats_the_internal_token(self, monkeypatch):
        seen = {}

        class Recorder(runner.Heartbeat):
            def __init__(self, sfn, tokens, interval=runner.HEARTBEAT_SECONDS):
                seen["tokens"] = tokens
                super().__init__(sfn, tokens, interval)

        class StubJob:
            def __init__(self, *a, **k):
                pass

            def run(self):
                return {"status": "succeeded"}
        sfn = FakeSfn()
        sfn.send_task_success = lambda **kw: None
        monkeypatch.setattr(runner, "Heartbeat", Recorder)
        monkeypatch.setattr(runner, "Job", StubJob)
        monkeypatch.setattr(runner, "Clients", lambda *a: None)
        monkeypatch.setattr(runner, "install_sigterm", lambda job, on_stop=None: None)
        monkeypatch.setattr(runner.boto3, "client", lambda *a, **k: sfn)
        monkeypatch.setattr(runner.boto3, "resource", lambda *a, **k: None)
        definition = '{"mode": "archiveRun", "externalSfnTaskToken": "ext"}'
        assert runner.main(["x", definition], {"TASK_TOKEN": "int", "AWS_REGION": "us-east-1"}) == 0
        assert seen["tokens"] == ["ext", "int"]
