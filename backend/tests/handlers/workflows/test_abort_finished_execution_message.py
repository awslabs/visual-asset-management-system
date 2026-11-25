# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The abort answer says whether the abort stopped the execution (issue #344).

``DELETE /workflows/executions/{executionId}`` on a run that had already finished left its status
alone, which is right, but answered "Execution aborted". The answer now says "Execution aborted" only
when the call wrote ABORTED to the main row. A run that was already terminal, or that another writer
finished between the abort's read and its guarded write, answers a 200 whose message says it was not
running and names its status.

A run that was RUNNING when the abort read it and that another writer recorded ABORTED before the
abort's own guarded write (the Step Functions status-change push for the abort's own StopExecution, a
concurrent abort, a read-path reconcile) was still stopped by this call, so it answers "Execution
aborted" (review r1 1962-1).

Only the row reads, the state-machine stop and the DynamoDB tables are stubbed; the main-row write and
its terminal guard (_persist_reconciled_main_row) are the code under test.

executionService resolves its table names at import (mirrors test_abort_retry_sweeps_aborted_rows.py).
"""

import json
import os

import pytest
from unittest.mock import MagicMock, call, patch
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

MOD = "backend.backend.handlers.workflows.executionService"

EXEC_ID = "e3440000000000000000000000000001"
ORPHAN_JOB = {"resourceType": "batchJob", "jobId": "job-orphan"}
CONDITIONAL_CHECK_FAILED = ClientError(
    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "The conditional request failed"}},
    "UpdateItem")


def _main_row(status):
    return {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": "wf-db:wf",
            "workflowDatabaseId": "wf-db", "workflowId": "wf",
            "workflow_execution_arn": "arn:ex:main", "executionStatus": status,
            "executionStopDate": "" if status == "RUNNING" else "2026-11-25T10:00:00.000000Z"}


def _pipeline_row(status, subs=()):
    return {"pipelineExecutionId": "P1", "workflowExecutionId": EXEC_ID, "executionStatus": status,
            "registeredSubExecutions": [dict(s) for s in subs], "executionStopDate": "",
            "pipelineExecutionType": "Lambda"}


def _abort(main_reads, pipeline_rows=(), update_error=None):
    """Run abort_execution. `main_reads` are the successive main-row reads (the first is the abort's
    own read; a second is the read-back after a rejected write). Returns (response, main table,
    main-row read spy, completion-event spy, Batch terminate spy)."""
    main_table = MagicMock()
    if update_error is not None:
        main_table.update_item.side_effect = update_error
    reads = MagicMock(side_effect=[dict(r) if r else r for r in main_reads])
    emit = MagicMock()
    terminate = MagicMock(return_value={})
    rows = [dict(r) for r in pipeline_rows]
    with patch(f"{MOD}.get_execution_main_row", reads), \
            patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
            patch(f"{MOD}.get_pipeline_execution_rows", side_effect=lambda execution_id: [dict(r) for r in rows]), \
            patch(f"{MOD}._cancel_output_copies", return_value=[]), \
            patch.object(le.sfn, "stop_execution", return_value={}), \
            patch.object(le.batch_client, "terminate_job", terminate), \
            patch.object(le.el, "release_locks_for_execution"), \
            patch(f"{MOD}.emit_workflow_execution_completed", emit), \
            patch(f"{MOD}.log_actions"), \
            patch.object(le.eo, "set_pipeline_status"), \
            patch.object(le.dynamodb, "Table", return_value=main_table):
        response = le.abort_execution({}, EXEC_ID)
    return response, main_table, reads, emit, terminate


def _body(response):
    assert response["statusCode"] == 200
    return json.loads(response["body"])


@pytest.mark.unit
class TestAnAbortOfAFinishedRunSaysItWasNotRunning:
    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"])
    def test_the_message_names_the_terminal_status_and_claims_no_abort(self, status):
        response, main_table, _reads, emit, _terminate = _abort([_main_row(status)])
        body = _body(response)
        assert body == {"message": f"Execution was not running (status {status}), so it was not aborted."}
        assert "Execution aborted" not in body["message"]
        main_table.update_item.assert_not_called()
        emit.assert_not_called()

    def test_a_succeeded_run_whose_pipelines_finished_on_their_own_gets_no_sweep_sentence(self):
        response, _table, _reads, _emit, terminate = _abort(
            [_main_row("SUCCEEDED")], [_pipeline_row("SUCCEEDED", [ORPHAN_JOB])])
        assert _body(response)["message"] == (
            "Execution was not running (status SUCCEEDED), so it was not aborted.")
        terminate.assert_not_called()

    def test_a_repeated_abort_says_the_stopped_pipelines_were_swept(self):
        """Repeating an abort is the remedy for a sub-process registered after the previous abort's
        last read, so the answer says the sweep ran rather than only that nothing was aborted."""
        response, _table, _reads, _emit, terminate = _abort(
            [_main_row("ABORTED")], [_pipeline_row("ABORTED", [ORPHAN_JOB])])
        message = _body(response)["message"]
        assert message.startswith("Execution was not running (status ABORTED), so it was not aborted.")
        assert message.endswith(le.EXECUTION_NOT_RUNNING_RESWEPT_SUFFIX)
        assert [c.kwargs.get("jobId") for c in terminate.call_args_list] == ["job-orphan"]

    def test_warnings_still_accompany_the_not_running_message(self):
        """A sub-process VAMS cannot stop gets no stop request, so the sweep sentence is left off."""
        response, _table, _reads, _emit, _terminate = _abort(
            [_main_row("ABORTED")],
            [_pipeline_row("ABORTED", [{"resourceType": "slurmJob", "jobId": "slurm-1"}])])
        body = _body(response)
        assert body["message"] == "Execution was not running (status ABORTED), so it was not aborted."
        assert body["warnings"] and body["notAbortable"]

    def test_a_repeated_abort_with_no_registered_sub_process_gets_no_sweep_sentence(self):
        """The ABORTED pipeline row is swept, but it registered nothing, so no stop request went out."""
        response, _table, _reads, _emit, terminate = _abort(
            [_main_row("ABORTED")], [_pipeline_row("ABORTED")])
        assert _body(response) == {
            "message": "Execution was not running (status ABORTED), so it was not aborted."}
        terminate.assert_not_called()


@pytest.mark.unit
class TestAnAbortOfARunningRunStillSaysAborted:
    def test_the_running_run_is_aborted_and_says_so(self):
        response, main_table, reads, emit, _terminate = _abort([_main_row("RUNNING")])
        assert _body(response) == {"message": "Execution aborted"}
        main_table.update_item.assert_called_once()
        assert "ConditionExpression" in main_table.update_item.call_args.kwargs
        assert reads.call_count == 1
        emit.assert_called_once()


@pytest.mark.unit
class TestARunFinishedByAnotherWriterDuringTheAbort:
    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT"])
    def test_the_message_names_the_status_the_run_finished_with_on_its_own(self, status):
        """The row read RUNNING, the guarded ABORTED write was rejected because the run finished on its
        own in between, so the status named is the one read back (strongly consistent), not ABORTED."""
        response, _table, reads, emit, _terminate = _abort(
            [_main_row("RUNNING"), _main_row(status)], update_error=CONDITIONAL_CHECK_FAILED)
        assert _body(response) == {
            "message": f"Execution was not running (status {status}), so it was not aborted."}
        assert reads.call_args_list == [call(EXEC_ID), call(EXEC_ID, consistent_read=True)]
        emit.assert_not_called()

    def test_aborted_recorded_first_by_another_writer_still_answers_aborted(self):
        """Review r1 1962-1. This call stopped the RUNNING execution, and the Step Functions push for
        that stop recorded ABORTED before the call's own guarded write. The run was aborted, so the
        answer is "Execution aborted"; the writer that recorded it announced the completion."""
        response, _table, reads, emit, _terminate = _abort(
            [_main_row("RUNNING"), _main_row("ABORTED")], update_error=CONDITIONAL_CHECK_FAILED)
        assert _body(response) == {"message": "Execution aborted"}
        assert reads.call_args_list == [call(EXEC_ID), call(EXEC_ID, consistent_read=True)]
        emit.assert_not_called()

    def test_a_row_gone_on_the_read_back_is_named_unknown(self):
        response, _table, _reads, _emit, _terminate = _abort(
            [_main_row("RUNNING"), None], update_error=CONDITIONAL_CHECK_FAILED)
        assert _body(response)["message"] == (
            "Execution was not running (status unknown), so it was not aborted.")


STOP_REQUEST_CASES = [
    ({"resourceType": "stepFunctionsExecution",
      "executionArn": "arn:aws:states:us-east-1:123456789012:execution:sm:run-1"}, True),
    ({"executionArn": "arn:aws:states:us-east-1:123456789012:execution:sm:run-1"}, True),
    ({"resourceType": "stepFunctionsExecution",
      "stateMachineArn": "arn:aws:states:us-east-1:123456789012:stateMachine:sm"}, False),
    ({"resourceType": "batchJob", "jobId": "job-1"}, True),
    ({"resourceType": "batchJob", "jobArn": "arn:aws:batch:us-east-1:123456789012:job/job-1"}, True),
    ({"resourceType": "batchJob", "label": "no locator"}, False),
    ({"resourceType": "deadlineCloudJob", "jobId": "job-1", "farmId": "farm-1", "queueId": "queue-1"}, True),
    ({"resourceType": "deadlineCloudJob", "jobId": "job-1"}, False),
    ({"resourceType": "sageMakerTrainingJob",
      "trainingJobArn": "arn:aws:sagemaker:us-east-1:123456789012:training-job/train-1"}, True),
    ({"resourceType": "sageMakerTrainingJob", "label": "no locator"}, False),
    ({"resourceType": "sageMakerPipelineExecution",
      "pipelineExecutionArn": "arn:aws:sagemaker:us-east-1:123456789012:pipeline/p1/execution/exec1"}, True),
    ({"resourceType": "sageMakerPipelineExecution", "label": "no locator"}, False),
    ({"resourceType": "slurmJob", "jobId": "slurm-1"}, False),
]


@pytest.mark.unit
class TestTheSweepSentenceCountsOnlyStopRequestsSent:
    """Review r1 1962-3. The sentence says a stop request was sent, so it is counted where the stop
    dispatcher actually calls a stop API. `_sends_stop_request` mirrors the dispatcher's locator checks;
    this pins the two together for every abortable type."""

    @pytest.mark.parametrize("sub,expected", STOP_REQUEST_CASES)
    def test_the_predicate_matches_whether_the_dispatcher_calls_a_stop_api(self, sub, expected):
        stop_apis = {"sfn": MagicMock(return_value={}), "batch": MagicMock(return_value={}),
                     "deadline": MagicMock(), "sagemaker": MagicMock()}
        with patch.object(le.sfn, "stop_execution", stop_apis["sfn"]), \
                patch.object(le.batch_client, "terminate_job", stop_apis["batch"]), \
                patch.object(le, "deadline_client", stop_apis["deadline"]), \
                patch.object(le, "sagemaker_client", stop_apis["sagemaker"]):
            le._abort_registered_sub_process(dict(sub))
        called = (stop_apis["sfn"].called or stop_apis["batch"].called
                  or stop_apis["deadline"].update_job.called
                  or stop_apis["sagemaker"].stop_training_job.called
                  or stop_apis["sagemaker"].stop_pipeline_execution.called)
        assert le._sends_stop_request(dict(sub)) is expected
        assert called is expected

    def test_a_deadline_job_found_by_discovery_counts_as_a_stop_request(self):
        """A Deadline Cloud step that never registered its job is cancelled after discovery."""
        row = dict(_pipeline_row("ABORTED"), pipelineExecutionType="DeadlineCloud")
        with patch(f"{MOD}._deadline_farm_queue_for_pipeline", return_value=("farm-1", "queue-1")), \
                patch(f"{MOD}._discover_deadline_job_id", return_value="job-found"), \
                patch(f"{MOD}._cancel_deadline_job_reporting", return_value=(True, "")) as cancel:
            response, _table, _reads, _emit, _terminate = _abort([_main_row("ABORTED")], [row])
        assert _body(response)["message"].endswith(le.EXECUTION_NOT_RUNNING_RESWEPT_SUFFIX)
        cancel.assert_called_once_with("farm-1", "queue-1", "job-found")
