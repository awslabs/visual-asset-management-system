# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A throttle slows a copy-engine caller; it never fails a run whose copies are alive (#1123 H1),
and the deploy window's AccessDenied is waited out by every caller of the copy grants (#1123 L1).

- call_engine: a throttle (TooManyRequestsException / 429 / a transient refusal, or an engine error
  whose type is one) is retried with jittered backoff within the invocation's remaining time minus a
  safety margin; when that runs out it raises LargeObjectCopyEngineBusy, which the workflow
  definition retries on the output step states. Any other error is unchanged.
- call_with_transient_retry (start_copy_job, cancel_execution_copy_jobs): throttles and AccessDenied
  are waited out within the caller's bound; other errors are raised at once.
- The generated definition retries LargeObjectCopyEngineBusy on process-output, the wait and
  finalize, bounded, and keeps the wait's 48 h timeout.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.common import largeObjectCopy as loc
from backend.backend.common.workflows import workflowAslBuilder as wab


def _client_error(code, status=400):
    return ClientError({"Error": {"Code": code, "Message": "m"},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "Invoke")


def _answer(payload, function_error=None):
    response = {"Payload": MagicMock(read=MagicMock(return_value=json.dumps(payload).encode()))}
    if function_error:
        response["FunctionError"] = function_error
    return response


def _rng():
    return 1.0  # the full backoff ceiling, so the bounds are exercised deterministically


class _Clock:
    """A remaining-time counter that the test's sleep advances."""

    def __init__(self, remaining_ms):
        self.ms = remaining_ms
        self.slept = []

    def remaining(self):
        return self.ms

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.ms -= seconds * 1000


@pytest.mark.unit
class TestCallEngineThrottle:
    @pytest.mark.parametrize("code,status", [("TooManyRequestsException", 429), ("ServiceException", 500),
                                             ("ResourceConflictException", 409), ("EC2ThrottledException", 502),
                                             ("SomethingNew", 429)])
    def test_a_throttle_then_success_returns_the_answer(self, code, status):
        client = MagicMock()
        client.invoke.side_effect = [_client_error(code, status), _client_error(code, status),
                                     _answer({"registered": True})]
        clock = _Clock(10 * 60 * 1000)
        assert loc.call_engine(client, "fn", loc.OUTPUT_STEP_REGISTER_TOKEN, sleep=clock.sleep,
                               remaining_ms=clock.remaining, rng=_rng, batchId="E1",
                               taskToken="t") == {"registered": True}
        assert client.invoke.call_count == 3
        assert len(clock.slept) == 2 and clock.slept[0] < clock.slept[1]

    def test_an_engine_error_of_a_throttle_type_is_retried(self):
        client = MagicMock()
        client.invoke.side_effect = [
            _answer({"errorType": "ProvisionedThroughputExceededException"}, "Unhandled"),
            _answer({"jobs": {}})]
        clock = _Clock(10 * 60 * 1000)
        assert loc.call_engine(client, "fn", loc.OUTPUT_STEP_RECORDED_JOBS, sleep=clock.sleep,
                               remaining_ms=clock.remaining, rng=_rng,
                               workflowExecutionId="E1") == {"jobs": {}}

    def test_a_throttle_until_the_time_budget_ends_raises_the_retriable_error(self):
        client = MagicMock(**{"invoke.side_effect": _client_error("TooManyRequestsException", 429)})
        clock = _Clock(3 * 60 * 1000)
        with pytest.raises(loc.LargeObjectCopyEngineBusy):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_RECORD_BATCH, sleep=clock.sleep,
                            remaining_ms=clock.remaining, rng=_rng, batchId="E1")
        assert clock.slept, "the call never waited"
        # It stops before the safety margin: never sleeps into the function's timeout.
        assert clock.ms >= loc.INVOCATION_SAFETY_MARGIN_MS
        assert max(clock.slept) <= loc.TRANSIENT_MAX_DELAY_SECONDS
        # The retriable error is not the one that fails the run.
        assert not issubclass(loc.LargeObjectCopyEngineBusy, loc.EngineCallFailed)

    def test_no_time_left_raises_the_retriable_error_without_waiting(self):
        client = MagicMock(**{"invoke.side_effect": _client_error("TooManyRequestsException", 429)})
        clock = _Clock(loc.INVOCATION_SAFETY_MARGIN_MS)
        with pytest.raises(loc.LargeObjectCopyEngineBusy):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_BATCH_OUTCOMES, sleep=clock.sleep,
                            remaining_ms=clock.remaining, rng=_rng, batchId="E1",
                            workflowExecutionId="E1")
        assert clock.slept == []

    @pytest.mark.parametrize("code", ["ResourceNotFoundException", "InvalidParameterValueException"])
    def test_other_errors_are_unchanged(self, code):
        client = MagicMock(**{"invoke.side_effect": _client_error(code)})
        clock = _Clock(10 * 60 * 1000)
        with pytest.raises(loc.EngineCallFailed):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_CANCEL, sleep=clock.sleep,
                            remaining_ms=clock.remaining, workflowExecutionId="E1")
        assert client.invoke.call_count == 1
        assert clock.slept == []

    def test_an_engine_error_of_another_type_still_fails(self):
        client = MagicMock(**{"invoke.return_value": _answer({"errorType": "ValueError"}, "Unhandled")})
        with pytest.raises(loc.EngineCallFailed):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_CANCEL, sleep=lambda s: None,
                            workflowExecutionId="E1")
        assert client.invoke.call_count == 1


@pytest.mark.unit
class TestTransientRetryHelper:
    def test_a_throttle_then_success(self):
        call = MagicMock(side_effect=[_client_error("ThrottlingException"), "ok"])
        clock = _Clock(10 * 60 * 1000)
        assert loc.call_with_transient_retry(call, "x", 120, clock.remaining, clock.sleep, _rng) == "ok"
        assert call.call_count == 2

    def test_access_denied_during_the_deploy_window_then_success(self):
        call = MagicMock(side_effect=[_client_error("AccessDeniedException"),
                                      _client_error("AccessDeniedException"), "ok"])
        clock = _Clock(10 * 60 * 1000)
        assert loc.call_with_transient_retry(call, "x", 120, clock.remaining, clock.sleep, _rng) == "ok"

    def test_access_denied_past_the_bound_raises_it(self):
        call = MagicMock(side_effect=_client_error("AccessDeniedException"))
        clock = _Clock(10 * 60 * 1000)
        with pytest.raises(ClientError) as raised:
            loc.call_with_transient_retry(call, "x", 15, clock.remaining, clock.sleep, _rng)
        assert loc.error_code(raised.value) == "AccessDeniedException"
        assert clock.slept, "the call never waited"
        assert sum(clock.slept) <= 15

    def test_a_throttle_past_the_bound_raises_it_or_the_busy_error(self):
        call = MagicMock(side_effect=_client_error("ThrottlingException"))
        with pytest.raises(ClientError):
            loc.call_with_transient_retry(call, "x", 5, None, lambda s: None, _rng)
        with pytest.raises(loc.LargeObjectCopyEngineBusy):
            loc.call_with_transient_retry(call, "x", 5, None, lambda s: None, _rng,
                                          busy_error=loc.LargeObjectCopyEngineBusy)

    def test_another_error_is_raised_at_once(self):
        call = MagicMock(side_effect=_client_error("ValidationException"))
        sleeps = []
        with pytest.raises(ClientError):
            loc.call_with_transient_retry(call, "x", 120, None, sleeps.append, _rng)
        assert call.call_count == 1 and sleeps == []


@pytest.mark.unit
class TestCallersWaitOutTheDeployWindow:
    def _job(self):
        return loc.build_job(
            kind=loc.KIND_UPLOAD, source={"bucket": "s", "key": "k", "size": 6 * loc.GIB},
            dest={"bucket": "d", "key": "a/k"}, metadata={}, content_headers={}, database_id="db",
            asset_id="a", upload_id="u", upload_type="assetFile", relative_key="k",
            change_user_id="u1", scope_id="u")

    def test_start_copy_job_waits_out_access_denied_and_throttles(self):
        table = MagicMock()
        table.put_item.side_effect = [_client_error("AccessDeniedException"), {}]
        sfn = MagicMock()
        sfn.start_execution.side_effect = [_client_error("ThrottlingException"),
                                           {"executionArn": "arn:x"}]
        sleeps = []
        assert loc.start_copy_job(table, sfn, "arn:sm", self._job(), max_wait_seconds=15,
                                  sleep=sleeps.append) == "arn:x"
        assert table.put_item.call_count == 2 and sfn.start_execution.call_count == 2
        assert len(sleeps) == 2

    def test_start_copy_job_without_a_bound_is_unchanged(self):
        table = MagicMock(**{"put_item.side_effect": _client_error("AccessDeniedException")})
        with pytest.raises(ClientError):
            loc.start_copy_job(table, MagicMock(), "arn:sm", self._job())
        assert table.put_item.call_count == 1

    def test_the_cancel_path_waits_out_access_denied_on_its_jobs_read(self):
        table = MagicMock()
        with patch.object(loc, "jobs_for_execution",
                          side_effect=[_client_error("AccessDeniedException"), []]) as read:
            sleeps = []
            assert loc.cancel_execution_copy_jobs(table, MagicMock(), MagicMock(), "E1",
                                                  max_wait_seconds=15, sleep=sleeps.append) == []
        assert read.call_count == 2 and len(sleeps) == 1

    def test_the_cancel_path_waits_out_a_throttled_stop(self):
        job = {"jobId": "j1", "status": loc.STATUS_RUNNING, "executionArn": "arn:e",
               "relativeKey": "k"}
        sfn = MagicMock()
        sfn.stop_execution.side_effect = [_client_error("ThrottlingException"), {}]
        with patch.object(loc, "jobs_for_execution", return_value=[job]), \
                patch.object(loc, "request_stop"), patch.object(loc, "mark_cancelled") as cancelled:
            warnings = loc.cancel_execution_copy_jobs(MagicMock(), sfn, MagicMock(), "E1",
                                                      max_wait_seconds=15, sleep=lambda s: None)
        assert warnings == []
        assert sfn.stop_execution.call_count == 2
        cancelled.assert_called_once()


@pytest.mark.unit
class TestTheDefinitionRetriesABusyEngine:
    def _states(self):
        from backend.tests.common.workflows import test_workflowAsl_output_ingestion as asl_tests
        return asl_tests._generate()["States"]

    def test_each_output_state_retries_the_busy_error_bounded(self):
        states = self._states()
        process_output = next(s for sid, s in states.items() if sid.startswith("process-outputs-"))
        for state in (process_output, states[wab.AWAIT_INGESTION_STATE_ID],
                      states[wab.FINALIZE_OUTPUTS_STATE_ID]):
            busy = [r for r in state["Retry"] if r["ErrorEquals"] == [wab.ENGINE_BUSY_ERROR]]
            assert busy == [wab.ENGINE_BUSY_RETRIER]
            assert busy[0]["MaxAttempts"] <= 10 and busy[0]["JitterStrategy"] == "FULL"
            # Retried before the error path: the catch-all still follows an exhausted retry.
            assert state["Catch"][-1]["ErrorEquals"] == ["States.ALL"]
        assert wab.ENGINE_BUSY_ERROR == loc.LargeObjectCopyEngineBusy.__name__

    def test_the_maximum_wait_is_unchanged(self):
        wait = self._states()[wab.AWAIT_INGESTION_STATE_ID]
        assert wait["TimeoutSeconds"] == loc.MAX_OUTPUT_INGESTION_WAIT_HOURS * 3600
        assert wait["Catch"][0] == {"ErrorEquals": ["States.Timeout"],
                                    "Next": wab.INGESTION_TIMEOUT_STATE_ID, "ResultPath": "$.errorInfo"}
