# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Repeating an abort is the remedy for a sub-process registered after the last one (S2-BACKEND-044).

Registration is asynchronous: the pipeline puts an EventBridge event and
``registerPipelineExecution`` writes the row, so a self-submitted Batch job can be registered
after the abort's second read. The abort stamps every row it touched ABORTED, and ABORTED is a
terminal status, so a sweep that skips terminal rows leaves that job with nothing in the product
able to stop it — it keeps running and billing behind a 200 "Execution aborted".

The distinction the sweep has to draw is between the two ways a row becomes terminal:

* it finished ON ITS OWN (SUCCEEDED / FAILED / TIMED_OUT) — its sub-processes ended with it, and
  issuing stop calls would attach "may still be running" warnings to work that completed;
* a previous abort stamped it ABORTED — its sub-processes were meant to be stopped, so anything
  now on the row is either already stopped (the stop calls are no-ops) or an orphan.

So ABORTED rows are swept on a retry and the others are not. The stop calls are idempotent by
construction: StopExecution on a finished execution, TerminateJob on a finished job and
UpdateJob(CANCELED) on a finished Deadline job are all accepted rather than reported.

``registerPipelineExecution`` is the other half: it still records a sub-process reported onto an
ABORTED row — being on the row is what lets the retried abort find it — and names it at warning
level, which is the only signal that the orphan exists.

executionService resolves its table names at import (mirrors test_abort_late_batch_registration.py).
"""

import json
import os

import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le  # noqa: E402
from backend.backend.handlers.workflows.sfn import registerPipelineExecution as reg  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

EXEC_ID = "e1000000000000000000000000000001"
COMPOSITE = "wf-db:wf"
EARLIER_STOP_DATE = "2026-01-01T00:00:00.000000Z"
ORPHAN_JOB = {"resourceType": "batchJob", "jobId": "job-orphan"}


def _main_row(status="ABORTED"):
    return {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": COMPOSITE,
            "workflowDatabaseId": "wf-db", "workflowId": "wf",
            "workflow_execution_arn": "arn:ex:main",
            "executionStatus": status, "executionStopDate": EARLIER_STOP_DATE}


def _pipeline_row(subs, status="ABORTED", pipeline_id="P1", stop_date=EARLIER_STOP_DATE):
    return {"pipelineExecutionId": pipeline_id, "workflowExecutionId": EXEC_ID,
            "executionStatus": status, "registeredSubExecutions": subs,
            "executionStopDate": stop_date, "pipelineExecutionType": "Lambda"}


def _abort(row_reads, terminate=None, main_status="ABORTED"):
    """Run abort_execution over scripted pipeline-row reads.

    Returns (response, terminate spy, set_pipeline_status spy). Only the row reads, the
    state-machine stop and the row writes are stubbed — the sub-process dispatcher and the Batch
    termination helper are the code under test.
    """
    reads = list(row_reads)
    terminate_spy = MagicMock(side_effect=terminate) if terminate else MagicMock(return_value={})
    status_spy = MagicMock()
    with patch(f"{MOD}.get_execution_main_row", return_value=_main_row(main_status)), \
            patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
            patch(f"{MOD}.get_pipeline_execution_rows",
                  side_effect=lambda execution_id: reads.pop(0) if reads else []), \
            patch.object(le.sfn, "stop_execution", return_value={}), \
            patch.object(le.batch_client, "terminate_job", terminate_spy), \
            patch(f"{MOD}._persist_reconciled_main_row"), \
            patch(f"{MOD}.log_actions"), \
            patch.object(le.dynamodb, "Table", return_value=MagicMock()), \
            patch.object(le.eo, "set_pipeline_status", status_spy):
        response = le.abort_execution({}, EXEC_ID)
    return response, terminate_spy, status_spy


def _terminated(spy):
    return [call.kwargs.get("jobId") for call in spy.call_args_list]


@pytest.mark.unit
class TestARetriedAbortReachesAnOrphanOnAnAbortedRow:
    """The row a previous abort stamped ABORTED is the only place a missed registration can be."""

    def test_a_job_registered_after_the_previous_abort_is_terminated(self):
        # Both reads see the row as a previous abort left it: ABORTED, now carrying a job that
        # registered after that abort had finished looking.
        response, terminate, _status = _abort(
            [[_pipeline_row([ORPHAN_JOB])], [_pipeline_row([ORPHAN_JOB])]])

        assert response["statusCode"] == 200
        assert _terminated(terminate) == ["job-orphan"], (
            "a Batch job left on an ABORTED row keeps running and billing with no in-product "
            f"remedy: {_terminated(terminate)}")

    def test_the_retry_does_not_move_the_rows_stop_date(self):
        """The row already stopped; a retry catches up on sub-processes, it does not re-date the run."""
        _response, _terminate, status = _abort(
            [[_pipeline_row([ORPHAN_JOB])], [_pipeline_row([ORPHAN_JOB])]])

        assert status.call_args_list, (
            "the row was never reached, so this says nothing about its stop date")
        stop_dates = {call.kwargs.get("stop_date") for call in status.call_args_list}
        assert stop_dates == {EARLIER_STOP_DATE}, (
            f"the retried abort rewrote the pipeline row's stop date: {stop_dates}")

    def test_a_refused_termination_on_a_retry_is_named_in_the_response(self):
        # The abort answers 200, so the warnings list is the only signal that the orphan survived.
        response, _terminate, _status = _abort(
            [[_pipeline_row([ORPHAN_JOB])], [_pipeline_row([ORPHAN_JOB])]],
            terminate=ClientError({"Error": {"Code": "AccessDeniedException"}}, "TerminateJob"))

        warnings = json.loads(response["body"]).get("warnings", [])
        assert any("job-orphan" in w for w in warnings), (
            f"the orphan the retry could not stop was not named: {warnings}")


@pytest.mark.unit
class TestARowThatFinishedOnItsOwnIsStillLeftAlone:
    """Control. Sweeping these would report "may still be running" for completed work."""

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT"])
    def test_no_stop_is_issued_and_no_warning_is_produced(self, status):
        response, terminate, _status = _abort(
            [[_pipeline_row([ORPHAN_JOB], status=status)],
             [_pipeline_row([ORPHAN_JOB], status=status)]])

        assert response["statusCode"] == 200
        terminate.assert_not_called()
        assert "warnings" not in json.loads(response["body"])


@pytest.mark.unit
class TestTheOrdinaryAbortPathIsUnchanged:
    """Control. A first abort of a running row must still behave exactly as before."""

    def test_a_running_row_is_swept_and_stamped(self):
        response, terminate, status = _abort(
            [[_pipeline_row([ORPHAN_JOB], status="RUNNING", stop_date="")],
             [_pipeline_row([ORPHAN_JOB], status="ABORTED")]],
            main_status="RUNNING")

        assert response["statusCode"] == 200
        assert _terminated(terminate) == ["job-orphan"], (
            "the job was stopped more than once, or not at all: "
            f"{_terminated(terminate)}")
        assert status.call_args_list, "the running pipeline row was never stamped"
        assert status.call_args_list[0].args[4] == "ABORTED"

    def test_a_job_registered_between_the_two_reads_is_still_stopped(self):
        response, terminate, _status = _abort(
            [[_pipeline_row([], status="RUNNING", stop_date="")],
             [_pipeline_row([ORPHAN_JOB])]],
            main_status="RUNNING")

        assert response["statusCode"] == 200
        assert _terminated(terminate) == ["job-orphan"]


def _register_event(detail):
    return {"detail-type": reg.REGISTER_DETAIL_TYPE, "detail": detail}


def _register(row_status, sub=None):
    """Run the registration lambda against a row in a given status. Returns (table, logger)."""
    row = {"pipelineExecutionId": "P1", "workflowExecutionId": EXEC_ID,
           "executionStatus": row_status,
           "registeredSubExecutions": [], "registeredLogs": []}
    table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
    logger = MagicMock()
    with patch.object(reg.dynamodb, "Table", return_value=table), \
            patch.object(reg, "logger", logger):
        reg.lambda_handler(_register_event({
            "pipelineExecutionId": "P1",
            "subExecution": sub if sub is not None else dict(ORPHAN_JOB),
        }), MagicMock())
    return table, logger


@pytest.mark.unit
class TestRegistrationOntoAnAbortedRowIsRecordedAndNamed:
    """The record is what makes the retried abort possible; the warning is what prompts it."""

    def test_the_sub_process_is_still_appended(self):
        table, _logger = _register("ABORTED")

        assert table.update_item.called, (
            "the sub-process was dropped, so a retried abort has nothing to find and the job "
            "can never be stopped through the API")
        appended = table.update_item.call_args.kwargs["ExpressionAttributeValues"][":s"]
        assert [entry.get("jobId") for entry in appended] == ["job-orphan"]

    def test_the_late_registration_is_logged_at_warning_level(self):
        _table, logger = _register("ABORTED")

        assert logger.warning.called, (
            "a sub-process registered onto an ABORTED row is an orphan the abort never saw, and "
            "nothing in the logs says so")
        message = " ".join(str(call.args[0]) for call in logger.warning.call_args_list)
        assert "P1" in message and "batchJob" in message, (
            f"the warning does not name the pipeline execution or the resource: {message}")

    def test_a_registration_onto_a_running_row_is_not_warned_about(self):
        """Control: the ordinary case must stay quiet, or the warning means nothing."""
        table, logger = _register("RUNNING")

        assert table.update_item.called
        assert not logger.warning.called

    def test_a_registration_onto_a_row_that_finished_on_its_own_is_not_warned_about(self):
        """Control: a job on a SUCCEEDED row ended with the step, so it is not an orphan."""
        table, logger = _register("SUCCEEDED")

        assert table.update_item.called
        assert not logger.warning.called
