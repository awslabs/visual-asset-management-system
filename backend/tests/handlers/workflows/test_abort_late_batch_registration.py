# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The abort's late-registration sweep, followed all the way to the AWS Batch call.

S2-BACKEND-044 — a pipeline that submits its OWN Batch job registers it asynchronously (the pipeline
emits an EventBridge event and registerPipelineExecution writes the row), so the registration can land
after the abort has read the row. The abort therefore stops the parent state machine first, then reads
the pipeline rows, then re-reads them and stops anything that appeared in between. A row it has already
stamped ABORTED is no longer a candidate for this API, so a registration missed by that sweep leaves a
GPU job running and billing with no in-product remedy.

The abort-window tests in test_reconcile_and_abort_races.py stub _abort_registered_sub_process, which
proves the sweep reaches the dispatcher but not that the dispatcher reaches Batch for a job it first
saw on the second read. Here the dispatcher is real and the boto3 Batch client is the spy, so the
assertion is the one the orphaned job cares about: TerminateJob was called with its id.

executionService resolves its table names at import (mirrors test_executionService_wb53.py)."""

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

MOD = "backend.backend.handlers.workflows.executionService"

EXEC_ID = "e1000000000000000000000000000001"
COMPOSITE = "wf-db:wf"
LATE_JOB = {"resourceType": "batchJob", "jobId": "job-late"}
EARLY_JOB = {"resourceType": "batchJob", "jobId": "job-early"}


def _main_row():
    return {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": COMPOSITE,
            "workflowDatabaseId": "wf-db", "workflowId": "wf",
            "workflow_execution_arn": "arn:ex:main",
            "executionStatus": "RUNNING", "executionStopDate": ""}


def _pipeline_row(subs, status="RUNNING", pipeline_id="P1"):
    return {"pipelineExecutionId": pipeline_id, "workflowExecutionId": EXEC_ID,
            "executionStatus": status, "registeredSubExecutions": subs,
            "executionStopDate": "", "pipelineExecutionType": "Lambda"}


def _abort(row_reads, terminate=None):
    """Run abort_execution over scripted pipeline-row reads; returns (response, terminate spy).

    Only the row reads, the state-machine stop and the row writes are stubbed — the sub-process
    dispatcher and the Batch termination helper are the code under test.
    """
    reads = list(row_reads)
    terminate_spy = MagicMock(side_effect=terminate) if terminate else MagicMock(return_value={})
    with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
            patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
            patch(f"{MOD}.get_pipeline_execution_rows",
                  side_effect=lambda execution_id: reads.pop(0) if reads else []), \
            patch.object(le.sfn, "stop_execution", return_value={}), \
            patch.object(le.batch_client, "terminate_job", terminate_spy), \
            patch(f"{MOD}._persist_reconciled_main_row"), \
            patch(f"{MOD}.log_actions"), \
            patch.object(le.dynamodb, "Table", return_value=MagicMock()), \
            patch.object(le.eo, "set_pipeline_status"):
        response = le.abort_execution({}, EXEC_ID)
    return response, terminate_spy


@pytest.mark.unit
class TestALateRegisteredBatchJobIsTerminated:
    """The whole path: second read -> dispatcher -> _terminate_batch_job_reporting -> TerminateJob."""

    def test_a_job_registered_after_the_first_read_reaches_terminate_job(self):
        # First read: registeredSubExecutions is still empty, because the EventBridge registration has
        # not landed. Second read: the job is there, on the row this request has meanwhile stamped
        # ABORTED — the state in which nothing else can ever stop it.
        response, terminate = _abort(
            [[_pipeline_row([])], [_pipeline_row([LATE_JOB], status="ABORTED")]])
        assert response["statusCode"] == 200
        terminated = [c.kwargs.get("jobId") for c in terminate.call_args_list]
        assert "job-late" in terminated, (
            f"the late-registered Batch job keeps running and billing: {terminated}")

    def test_a_job_seen_on_the_first_read_is_terminated_exactly_once(self):
        # Control on the second pass: it must catch up, not repeat. Every sub-process the first pass
        # attempted is remembered, so a job present on both reads is stopped once.
        response, terminate = _abort(
            [[_pipeline_row([EARLY_JOB])], [_pipeline_row([EARLY_JOB], status="ABORTED")]])
        assert response["statusCode"] == 200
        terminated = [c.kwargs.get("jobId") for c in terminate.call_args_list]
        assert terminated == ["job-early"], f"expected one TerminateJob call: {terminated}"

    def test_a_job_on_a_row_that_finished_before_the_abort_is_left_alone(self):
        # Control the other way: a step already terminal when the abort began released its own work, so
        # the sweep must not call TerminateJob on it — that would report "may still be running" for a
        # step that completed normally.
        response, terminate = _abort(
            [[_pipeline_row([EARLY_JOB], status="SUCCEEDED")],
             [_pipeline_row([EARLY_JOB], status="SUCCEEDED")]])
        assert response["statusCode"] == 200
        terminate.assert_not_called()
        assert "warnings" not in json.loads(response["body"])

    def test_a_failed_termination_of_a_late_job_is_named_in_the_response(self):
        # The abort answers 200, so the warnings list is the only signal that something was left
        # running. A late job whose TerminateJob is refused has to appear in it by id.
        error = ClientError({"Error": {"Code": "AccessDeniedException"}}, "TerminateJob")
        response, terminate = _abort(
            [[_pipeline_row([])], [_pipeline_row([LATE_JOB], status="ABORTED")]],
            terminate=error)
        assert response["statusCode"] == 200
        terminate.assert_called()
        warnings = json.loads(response["body"]).get("warnings", [])
        assert any("job-late" in w for w in warnings), warnings

    def test_the_parent_state_machine_is_stopped_before_the_rows_are_read(self):
        # The ordering the sweep rests on: a running parent is what schedules the next task, so reading
        # the rows while it still runs leaves a window the second pass then has to cover twice over.
        order = []
        reads = [[_pipeline_row([])], [_pipeline_row([LATE_JOB], status="ABORTED")]]

        def _read(execution_id):
            order.append("read")
            return reads.pop(0) if reads else []

        with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
                patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
                patch(f"{MOD}.get_pipeline_execution_rows", side_effect=_read), \
                patch.object(le.sfn, "stop_execution",
                             side_effect=lambda **kwargs: order.append("stop-parent") or {}), \
                patch.object(le.batch_client, "terminate_job", return_value={}), \
                patch(f"{MOD}._persist_reconciled_main_row"), \
                patch(f"{MOD}.log_actions"), \
                patch.object(le.dynamodb, "Table", return_value=MagicMock()), \
                patch.object(le.eo, "set_pipeline_status"):
            response = le.abort_execution({}, EXEC_ID)
        assert response["statusCode"] == 200
        assert order.index("stop-parent") < order.index("read"), order
        assert order.count("read") >= 2, f"the rows must be re-read after the stop: {order}"
