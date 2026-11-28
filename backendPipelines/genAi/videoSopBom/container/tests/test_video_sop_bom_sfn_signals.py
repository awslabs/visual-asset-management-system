# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The inner task-token callbacks and the SIGTERM handler.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_sfn_signals.py -q

`error` is a short code capped at 256 and `cause` a readable sentence capped at 32768 (the SendTaskFailure
API limits); a ClientError on the final act exits 1 so the .sync task still fails. The SIGTERM handler
must never call DeleteTranscriptionJob on a non-terminal job (the API rejects it), must tolerate no job
having started, and must end with os._exit(143).
"""

import json
import logging
import os
import signal
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import FakeSfn, FakeTranscribe, client_error, make_clients  # noqa: E402


class TestSendTaskFailure:
    def test_code_in_error_and_sentence_in_cause(self, vsb_task_token):
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn()
        sent = sfn.send_task_failure(make_clients(sfn=fake), "VideoSopBomLimitExceeded",
                                     "total video duration 5h12m exceeds this deployment's limit of 4h00m (240 minutes).")
        assert sent is True
        assert fake.failures == [{
            "taskToken": vsb_task_token,
            "error": "VideoSopBomLimitExceeded",
            "cause": "total video duration 5h12m exceeds this deployment's limit of 4h00m (240 minutes).",
        }]

    def test_error_and_cause_are_truncated_to_the_api_limits(self, vsb_task_token):
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn()
        sfn.send_task_failure(make_clients(sfn=fake), "VideoSopBomPipelineError", "x" * 40000)
        assert len(fake.failures[0]["cause"]) == 32768
        assert len(fake.failures[0]["error"]) <= 256
        assert sfn.ERROR_MAX_CHARS == 256 and sfn.CAUSE_MAX_CHARS == 32768

    def test_a_cause_that_starts_with_the_code_is_stripped(self, vsb_task_token):
        """The platform renders '<code>: <cause>'; a cause carrying the code would render it twice."""
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn()
        sfn.send_task_failure(make_clients(sfn=fake), "VideoSopBomInputRejected", "VideoSopBomInputRejected: part3.mov has no audio stream.")
        assert fake.failures[0]["cause"] == "part3.mov has no audio stream."

    def test_an_unknown_code_becomes_the_pipeline_error_code(self, vsb_task_token):
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn()
        sfn.send_task_failure(make_clients(sfn=fake), "SomethingElse", "boom")
        assert fake.failures[0]["error"] == "VideoSopBomPipelineError"

    def test_client_error_on_the_final_act_exits_1(self, vsb_task_token):
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn(fail_with=client_error("TaskTimedOut", "expired", "SendTaskFailure"))
        with pytest.raises(SystemExit) as raised:
            sfn.send_task_failure(make_clients(sfn=fake), "VideoSopBomPipelineError", "late")
        assert raised.value.code == 1

    def test_without_a_token_nothing_is_sent(self, monkeypatch):
        from video_sop_bom_pipeline import sfn

        monkeypatch.delenv("TASK_TOKEN", raising=False)
        fake = FakeSfn()
        assert sfn.send_task_failure(make_clients(sfn=fake), "VideoSopBomPipelineError", "local run") is False
        assert fake.failures == []

    def test_the_token_value_is_never_logged(self, vsb_task_token, caplog):
        from video_sop_bom_pipeline import sfn

        with caplog.at_level(logging.DEBUG):
            sfn.send_task_failure(make_clients(sfn=FakeSfn()), "VideoSopBomPipelineError", "x")
        assert vsb_task_token not in caplog.text
        assert "TASK_TOKEN present=True" in caplog.text


class TestSendTaskSuccess:
    def test_payload_carries_the_reporter_marker(self, vsb_task_token):
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn()
        payload = sfn.success_payload(14, "pipelines/x/output/exec-0001/results/summary.json")
        assert sfn.send_task_success(make_clients(sfn=fake), payload) is True
        output = json.loads(fake.successes[0]["output"])
        assert output == {
            "reporter": "video_sop_bom_pipeline",
            "status": "SUCCEEDED",
            "fileCount": 14,
            "summaryKey": "pipelines/x/output/exec-0001/results/summary.json",
        }

    def test_a_payload_without_the_marker_is_refused_before_the_call(self, vsb_task_token):
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn()
        with pytest.raises(ValueError):
            sfn.send_task_success(make_clients(sfn=fake), {"status": "SUCCEEDED"})
        assert fake.successes == []

    def test_client_error_on_success_exits_1(self, vsb_task_token):
        from video_sop_bom_pipeline import sfn

        fake = FakeSfn(fail_with=client_error("InvalidToken", "bad", "SendTaskSuccess"))
        with pytest.raises(SystemExit) as raised:
            sfn.send_task_success(make_clients(sfn=fake), sfn.success_payload(1, "k"))
        assert raised.value.code == 1


@pytest.fixture
def restore_sigterm():
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


@pytest.fixture
def exit_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "_exit", lambda code: calls.append(code))
    return calls


class TestSigtermHandler:
    def _state(self, transcribe, sfn_signal, job_name):
        from video_sop_bom_pipeline.signals import RunState

        state = RunState(clients=make_clients(transcribe=transcribe, sfn_signal=sfn_signal))
        state.enter(6, "transcribe")
        state.transcribe_job_name = job_name
        return state

    def test_logs_the_orphan_marker_sends_best_effort_failure_and_exits_143(self, vsb_task_token, restore_sigterm, exit_recorder, caplog):
        from video_sop_bom_pipeline import signals

        transcribe, sfn_signal = FakeTranscribe(), FakeSfn()
        # Positive control for the negative below: the fake does record a DeleteTranscriptionJob call.
        transcribe.delete_transcription_job(TranscriptionJobName="control")
        assert transcribe.delete_calls == ["control"]
        transcribe.delete_calls.clear()
        state = self._state(transcribe, sfn_signal, "vams-video-sop-bom-pexid-abcd1234")
        handler = signals.install_sigterm_handler(state)
        assert signal.getsignal(signal.SIGTERM) is handler

        with caplog.at_level(logging.INFO):
            handler(signal.SIGTERM, None)

        assert "TRANSCRIBE_ORPHANED job=vams-video-sop-bom-pexid-abcd1234 stage=6" in caplog.text
        assert exit_recorder == [143]
        assert transcribe.delete_calls == [], "DeleteTranscriptionJob must never be called on a non-terminal job"
        assert len(sfn_signal.failures) == 1
        failure = sfn_signal.failures[0]
        assert failure["error"] == "VideoSopBomPipelineError"
        assert failure["cause"].startswith("terminated at stage 6 (transcribe) after ")
        assert failure["cause"].endswith("; Transcribe job vams-video-sop-bom-pexid-abcd1234 left running")

    def test_tolerates_no_job_yet(self, vsb_task_token, restore_sigterm, exit_recorder, caplog):
        from video_sop_bom_pipeline import signals

        transcribe, sfn_signal = FakeTranscribe(), FakeSfn()
        state = self._state(transcribe, sfn_signal, None)
        state.enter(2, "download")
        handler = signals.install_sigterm_handler(state)

        with caplog.at_level(logging.INFO):
            handler(signal.SIGTERM, None)

        assert "TRANSCRIBE_ORPHANED" not in caplog.text
        assert exit_recorder == [143]
        assert sfn_signal.failures[0]["cause"].endswith("; no Transcribe job had been started")
        assert "stage 2 (download)" in sfn_signal.failures[0]["cause"]

    def test_an_invalid_token_is_swallowed_and_the_process_still_exits(self, vsb_task_token, restore_sigterm, exit_recorder):
        from video_sop_bom_pipeline import signals

        sfn_signal = FakeSfn(fail_with=client_error("InvalidToken", "consumed", "SendTaskFailure"))
        handler = signals.install_sigterm_handler(self._state(FakeTranscribe(), sfn_signal, "job"))
        handler(signal.SIGTERM, None)
        assert exit_recorder == [143]

    def test_without_a_token_no_callback_is_attempted(self, monkeypatch, restore_sigterm, exit_recorder):
        from video_sop_bom_pipeline import signals

        monkeypatch.delenv("TASK_TOKEN", raising=False)
        sfn_signal = FakeSfn()
        handler = signals.install_sigterm_handler(self._state(FakeTranscribe(), sfn_signal, "job"))
        handler(signal.SIGTERM, None)
        assert sfn_signal.failures == []
        assert exit_recorder == [143]
