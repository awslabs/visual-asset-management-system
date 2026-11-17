# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Round 6 of #1123 (M11): the cancel path hands the copies it stopped to the copy engine itself.

Every caller of cancel_execution_copy_jobs acts on a run that is ending or has ended (the abort
route, the error handler, the workflow status change handler, the output step of a run that ended
while it ran), and the execution details read never checks the copies of an ended run. So when the
cancel path left a job to the engine (it could not read the asset object) or finalized it SUCCEEDED
itself (the post-landing work is the engine's), the only remaining trigger used to be the copy
state machine's status change event. With that event lost, the job stayed open, or the staged
source, the audit entry, the asset type and the email were never done.

Now each such stop is also handed to the engine's reconcile with an asynchronous invoke. These tests
drive the real callers with the status change event lost, and fail on bed9b077c: the job stays
RUNNING (deferred) or the post-landing work is missing (SUCCEEDED by the cancel path). The reconcile
stays idempotent, so the hand-over and a late status event, in either order, or a duplicate
hand-over, finish the job once. A failed hand-over does not fail the cancel."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, _described, machine)
from backend.tests.handlers.assets.test_largeObjectCopy_race_prior_object import (
    UNVERSIONED, VERSIONED, _DeniedHead, _Run)
from backend.tests.handlers.workflows import test_system_pipeline_hooks as hooks

le = hooks.le  # executionService: the abort route and the workflow status change handler
heh = hooks.heh  # handleExecutionError: the error handler

ENGINE_FUNCTION = "vams-largeObjectCopy-engine"
BUCKETS = pytest.mark.parametrize("bucket", [VERSIONED, UNVERSIONED])


class _HandOff:
    """The cancel path's Lambda client. Records each asynchronous invoke; deliver() runs the
    requests on the engine (what Lambda does with an Event invoke)."""

    def __init__(self, fail=False):
        self.client = MagicMock()
        if fail:
            self.client.invoke.side_effect = ClientError(
                {"Error": {"Code": "TooManyRequestsException", "Message": "x"}}, "Invoke")

    def payloads(self):
        return [json.loads(c.kwargs["Payload"]) for c in self.client.invoke.call_args_list]

    def assert_handed(self, times=1):
        assert times >= 1
        assert self.client.invoke.call_args_list
        assert [(c.kwargs["FunctionName"], c.kwargs["InvocationType"])
                for c in self.client.invoke.call_args_list] == [(ENGINE_FUNCTION, "Event")] * times
        assert self.payloads() == [{"action": "reconcile", "executionArn": EXEC_ARN}] * times

    def deliver(self):
        return [engine.lambda_handler(payload, None) for payload in self.payloads()]


def _status_event():
    """The copy state machine's status change event for the stop (when it is not lost)."""
    return {"detail-type": engine.STATUS_CHANGE_DETAIL_TYPE,
            "detail": {"executionArn": EXEC_ARN, "status": "ABORTED"}}


def _stopped(run):
    """What DescribeExecution answers once the cancel path stopped the copy execution."""
    run.env["sfn"].describe_execution.return_value = _described(
        run.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)


def _executions_table(env):
    return MagicMock(**{"Table.return_value": env["jobs"]})


def _abort_route(env, s3_client, handoff):
    """The abort route's (and group abort's) cancel: executionService._cancel_output_copies."""
    with patch.object(le, "dynamodb", _executions_table(env)), \
            patch.object(le, "get_table_name", return_value="t-copy-jobs"), \
            patch.object(le, "sfn", env["sfn"]), \
            patch.object(le, "_copy_s3_client", return_value=s3_client), \
            patch.object(le, "lambda_client", handoff.client):
        return le._cancel_output_copies("e2")


def _error_handler(env, s3_client, handoff):
    """The error handler's cancel of a FAILED run: handleExecutionError._cancel_output_copies."""
    with patch.object(heh, "dynamodb", _executions_table(env)), \
            patch.object(heh, "get_table_name", return_value="t-copy-jobs"), \
            patch.object(heh, "sfn_client", env["sfn"]), \
            patch.object(heh, "s3_client", s3_client), \
            patch.object(heh, "lambda_client", handoff.client, create=True):
        return heh._cancel_output_copies("e2")


def _status_change_handler(env, s3_client, handoff):
    """The workflow status change handler for a run that TIMED_OUT outside VAMS while it waited on
    its copies (handle_sfn_status_change -> _cancel_output_copies)."""
    main_arn = "arn:aws:states:us-east-1:111122223333:execution:vams-wf:e2"
    execution_id = "a7d1c2b4-0e5f-4a6b-9c8d-7e6f5a4b3c2d"
    row = {"workflowExecutionId": execution_id, "workflow_execution_arn": main_arn,
           "executionStatus": "RUNNING", "executionPhase": loc.EXECUTION_PHASE_INGESTING_OUTPUTS,
           "outputIngestionStartedDate": "2026-11-06T10:00:00Z"}
    event = {"detail": {"executionArn": main_arn, "name": execution_id, "status": "TIMED_OUT"}}
    real_cancel = le._cancel_output_copies
    with patch.object(le, "get_execution_main_row", return_value=row), \
            patch.object(le, "_reconcile_main_status"), \
            patch.object(le, "_cancel_output_copies", side_effect=lambda _id: real_cancel("e2")) as cancel:
        with patch.object(le, "dynamodb", _executions_table(env)), \
                patch.object(le, "get_table_name", return_value="t-copy-jobs"), \
                patch.object(le, "sfn", env["sfn"]), \
                patch.object(le, "_copy_s3_client", return_value=s3_client), \
                patch.object(le, "lambda_client", handoff.client):
            result = le.handle_sfn_status_change(event)
    assert result == {"handled": True, "reconciled": True}
    cancel.assert_called_once_with(execution_id)
    return []


CALLERS = {"ABORTED": _abort_route, "FAILED": _error_handler, "TIMED_OUT": _status_change_handler}


@pytest.fixture
def engine_name():
    with patch.dict(os.environ, {loc.ENV_ENGINE_FUNCTION_NAME: ENGINE_FUNCTION}):
        yield


def _cancel_client(run, outcome):
    """deferred: the caller cannot read the asset object (the abort route and the error handler
    hold only AbortMultipartUpload), so the job is left to the engine. succeeded: the caller can, and
    finalizes the landed job SUCCEEDED itself, leaving the post-landing work to the engine."""
    return _DeniedHead(run.env["s3"]) if outcome == "deferred" else run.env["s3"]


# --------------------------------------------------------------------------------------------- #
# The reviewer's six cases (rv6): every caller, both outcomes, the status change event lost
# --------------------------------------------------------------------------------------------- #

@BUCKETS
@pytest.mark.parametrize("run_status", sorted(CALLERS))
@pytest.mark.parametrize("outcome", ["deferred", "succeeded"])
def test_cancel_hands_the_stop_to_the_engine_with_the_event_lost(env, machine, engine_name,
                                                                  bucket, run_status, outcome):
    run = _Run(env, bucket)
    run.complete_by_hand()
    _stopped(run)
    handoff = _HandOff()
    assert CALLERS[run_status](env, _cancel_client(run, outcome), handoff) == []
    env["sfn"].stop_execution.assert_called_once()
    expected = loc.STATUS_RUNNING if outcome == "deferred" else loc.STATUS_SUCCEEDED
    assert run.row()["status"] == expected
    # Lambda runs the hand-over; no status change event arrives. Before round 6 nothing was handed
    # over: a deferred job stayed RUNNING, and a job the cancel path finalized kept its staged source
    # with no audit entry and no email.
    handoff.deliver()
    run.assert_landed()
    handoff.assert_handed()


# --------------------------------------------------------------------------------------------- #
# Idempotency: the hand-over and a late status event, in either order, finish the job once
# --------------------------------------------------------------------------------------------- #

@BUCKETS
@pytest.mark.parametrize("outcome", ["deferred", "succeeded"])
@pytest.mark.parametrize("order", ["handoff-then-event", "event-then-handoff", "handoff-twice"])
def test_the_hand_over_and_a_late_status_event_finish_the_job_once(env, machine, engine_name,
                                                                    bucket, outcome, order):
    run = _Run(env, bucket)
    run.complete_by_hand()
    _stopped(run)
    handoff = _HandOff()
    _abort_route(env, _cancel_client(run, outcome), handoff)
    handoff.assert_handed()
    if order == "handoff-then-event":
        handoff.deliver()
        engine.lambda_handler(_status_event(), None)
    elif order == "event-then-handoff":
        engine.lambda_handler(_status_event(), None)
        handoff.deliver()
    else:  # an asynchronous invoke is delivered at least once
        handoff.deliver()
        handoff.deliver()
    run.assert_landed()
    assert run.row().get("counted") is True


@BUCKETS
def test_an_open_upload_the_abort_route_cannot_check_is_decided_by_the_engine(env, machine,
                                                                              engine_name, bucket):
    """The multipart upload was still open, and the abort route cannot read the asset object. Its
    abort answers 204, which S3 also answers for an upload a Complete completed (#1123 D1), so the
    route cannot tell whether anything landed: it hands the stop to the engine, whose reconcile
    checks the object (nothing landed) and writes CANCELLED. Before D1 the route wrote CANCELLED on
    the 204 alone, which was wrong whenever a Complete had landed the file."""
    run = _Run(env, bucket)
    _stopped(run)
    handoff = _HandOff()
    _abort_route(env, _DeniedHead(env["s3"]), handoff)
    assert run.row()["status"] == loc.STATUS_RUNNING
    handoff.assert_handed()
    handoff.deliver()
    run.assert_not_landed(loc.STATUS_CANCELLED)


@BUCKETS
def test_an_open_upload_the_cancel_path_can_check_is_cancelled_without_a_hand_over(
        env, machine, engine_name, bucket):
    """With read access (the status change handler, the output step) the cancel path checks the
    object itself after the abort: nothing landed, so it writes CANCELLED and hands nothing over."""
    run = _Run(env, bucket)
    _stopped(run)
    handoff = _HandOff()
    _abort_route(env, env["s3"], handoff)
    run.assert_not_landed(loc.STATUS_CANCELLED)
    handoff.client.invoke.assert_not_called()


@BUCKETS
@pytest.mark.parametrize("outcome", ["deferred", "succeeded"])
def test_no_hand_over_without_a_stop(env, machine, engine_name, bucket, outcome):
    """The execution no longer exists: no stop was made and there is nothing to reconcile, so
    nothing is handed over (it would only dead-letter and raise the alarm). An undecided job is
    CANCELLED; a job whose landing the cancel path can read is kept SUCCEEDED (both unchanged)."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    env["sfn"].stop_execution.side_effect = ClientError(
        {"Error": {"Code": "ExecutionDoesNotExist", "Message": "x"}}, "StopExecution")
    handoff = _HandOff()
    _abort_route(env, _cancel_client(run, outcome), handoff)
    expected = loc.STATUS_CANCELLED if outcome == "deferred" else loc.STATUS_SUCCEEDED
    assert run.row()["status"] == expected
    handoff.client.invoke.assert_not_called()


# --------------------------------------------------------------------------------------------- #
# A failed hand-over never fails the cancel; the status change event still reconciles
# --------------------------------------------------------------------------------------------- #

@BUCKETS
@pytest.mark.parametrize("run_status", ["ABORTED", "FAILED"])
def test_a_failed_hand_over_does_not_fail_the_cancel(env, machine, engine_name, bucket, run_status):
    run = _Run(env, bucket)
    run.complete_by_hand()
    _stopped(run)
    handoff = _HandOff(fail=True)
    assert CALLERS[run_status](env, _DeniedHead(env["s3"]), handoff) == []
    assert handoff.client.invoke.call_count == 1
    assert run.row()["status"] == loc.STATUS_RUNNING
    engine.lambda_handler(_status_event(), None)
    run.assert_landed()


@BUCKETS
def test_no_engine_function_logs_and_leaves_it_to_the_event(env, machine, bucket):
    run = _Run(env, bucket)
    run.complete_by_hand()
    _stopped(run)
    handoff = _HandOff()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(loc.ENV_ENGINE_FUNCTION_NAME, None)
        os.environ.pop(loc.ENV_ENGINE_FUNCTION_NAME_PARAMETER, None)
        assert _abort_route(env, _DeniedHead(env["s3"]), handoff) == []
    handoff.client.invoke.assert_not_called()
    assert run.row()["status"] == loc.STATUS_RUNNING
    engine.lambda_handler(_status_event(), None)
    run.assert_landed()


# --------------------------------------------------------------------------------------------- #
# request_reconcile and engine_function_name
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestRequestReconcile:
    def test_hands_each_execution_over_asynchronously(self):
        client = MagicMock()
        assert loc.request_reconcile(client, "fn", ["a1", "a2"]) == 2
        assert [json.loads(c.kwargs["Payload"]) for c in client.invoke.call_args_list] == [
            {"action": "reconcile", "executionArn": "a1"}, {"action": "reconcile", "executionArn": "a2"}]
        assert {c.kwargs["InvocationType"] for c in client.invoke.call_args_list} == {"Event"}

    def test_one_failure_does_not_stop_the_others_and_never_raises(self):
        client = MagicMock()
        client.invoke.side_effect = [RuntimeError("boom"), {}]
        assert loc.request_reconcile(client, "fn", ["a1", "a2"]) == 1
        assert client.invoke.call_count == 2

    def test_nothing_to_hand_or_no_function(self):
        client = MagicMock()
        assert loc.request_reconcile(client, "fn", []) == 0
        assert loc.request_reconcile(client, "", ["a1"]) == 0
        assert loc.request_reconcile(None, "fn", ["a1"]) == 0
        client.invoke.assert_not_called()


@pytest.mark.unit
class TestEngineFunctionName:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        with patch.dict(loc._engine_function_names, {}, clear=True):
            yield

    def test_the_environment_variable_wins(self):
        ssm = MagicMock()
        environ = {loc.ENV_ENGINE_FUNCTION_NAME: "fn", loc.ENV_ENGINE_FUNCTION_NAME_PARAMETER: "/p"}
        assert loc.engine_function_name(environ, ssm) == "fn"
        ssm.get_parameter.assert_not_called()

    def test_the_parameter_is_read_once_per_container(self):
        ssm = MagicMock(**{"get_parameter.return_value": {"Parameter": {"Value": "fn"}}})
        environ = {loc.ENV_ENGINE_FUNCTION_NAME_PARAMETER: "/p"}
        assert loc.engine_function_name(environ, ssm) == "fn"
        assert loc.engine_function_name(environ, ssm) == "fn"
        ssm.get_parameter.assert_called_once_with(Name="/p")

    def test_a_failed_read_returns_empty_and_is_retried(self):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = [RuntimeError("denied"), {"Parameter": {"Value": "fn"}}]
        environ = {loc.ENV_ENGINE_FUNCTION_NAME_PARAMETER: "/p"}
        assert loc.engine_function_name(environ, ssm) == ""
        assert loc.engine_function_name(environ, ssm) == "fn"

    def test_none_configured(self):
        assert loc.engine_function_name({}, MagicMock()) == ""
        assert loc.engine_function_name({loc.ENV_ENGINE_FUNCTION_NAME_PARAMETER: "/p"}, None) == ""
