# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A bound that was reported as a violation of the thing it bounds.

The abort's second pass (executionService) re-reads the pipeline rows to catch a sub-process
registered inside the abort window. Re-reading rows that were ALREADY terminal when the abort started
issues stop calls against steps that finished normally, which can attach a "may still be running"
warning to an abort that left nothing running.

The stubs here hand back REAL dicts and TERMINATE: a MagicMock answers `.get('LastEvaluatedKey')`
truthily forever, so a paging loop built on one never exits. Every fake counts its queries and fails
the test rather than hanging the suite.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# The handler resolves table names at import (mirrors test_executeWorkflow.py / test_executionService_wb53.py).
for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
    ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates"),
    ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema"),
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
    ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
    ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows"),
    ("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
]:
    os.environ.setdefault(_name, _value)

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

ES = "backend.backend.handlers.workflows.executionService"

WF_DB, WF_ID = "wfdb", "wf1"
def _pipeline_row(subs, status="RUNNING", pipeline_id="P1"):
    return {"pipelineExecutionId": pipeline_id, "workflowExecutionId": "x-exec",
            "executionStatus": status, "registeredSubExecutions": subs,
            "executionStopDate": ""}


@pytest.mark.unit
class TestTheAbortSecondPassLeavesFinishedStepsAlone:
    """The second pass exists to catch a sub-process registered inside the abort window. A row that was
    already terminal when the abort started is not that case: its step finished on its own and released
    its own sub-processes, so stopping them again can only misreport a clean abort."""

    def _abort(self, row_reads, sub_spy=None):
        main_table, pexec_table = MagicMock(), MagicMock()
        reads = list(row_reads)

        def _table(name):
            return pexec_table if name == le.pipeline_executions_table else main_table

        with patch(f"{ES}.get_execution_main_row",
                   return_value={"workflowExecutionId": "x-exec", "workflowId": WF_ID,
                                 "workflowDatabaseId": WF_DB,
                                 "workflow_execution_arn": "arn:ex:main",
                                 "executionStatus": "RUNNING", "executionStopDate": ""}), \
                patch(f"{ES}.authorize_abort", return_value=(True, "")), \
                patch(f"{ES}.get_pipeline_execution_rows",
                      side_effect=lambda execution_id: reads.pop(0) if reads else []), \
                patch(f"{ES}._stop_sfn_execution"), \
                patch(f"{ES}._abort_registered_sub_process",
                      side_effect=(sub_spy or (lambda sub: ""))), \
                patch(f"{ES}._persist_reconciled_main_row"), \
                patch(f"{ES}.log_actions"), \
                patch.object(le.dynamodb, "Table", side_effect=_table), \
                patch.object(le.eo, "set_pipeline_status"):
            resp = le.abort_execution({}, "x-exec")
        return resp

    def test_a_step_that_finished_before_the_abort_is_not_stopped(self):
        finished = {"resourceType": "ecsTask", "taskArn": "arn:task:finished"}
        attempted = []
        resp = self._abort(
            [[_pipeline_row([finished], status="SUCCEEDED")],
             [_pipeline_row([finished], status="SUCCEEDED")]],
            sub_spy=lambda sub: attempted.append(sub.get("taskArn")) or
            "could not be aborted: arn:task:finished; it may still be running.")
        assert resp["statusCode"] == 200
        assert attempted == [], (
            f"the sub-processes of a step that finished normally were stopped again: {attempted}")
        body = json.loads(resp["body"])
        assert "warnings" not in body, (
            f"a clean abort must not warn about work that finished on its own: {body}")

    def test_a_sub_process_registered_inside_the_window_is_still_stopped(self):
        # Positive control: the skip must not have disabled the second pass. This row was RUNNING at
        # the first read, so it IS the late-registration case the pass exists for.
        late = {"resourceType": "batchJob", "jobId": "job-late"}
        stopped = []
        resp = self._abort(
            [[_pipeline_row([])], [_pipeline_row([late], status="ABORTED")]],
            sub_spy=lambda sub: stopped.append(sub.get("jobId")) or "")
        assert resp["statusCode"] == 200
        assert "job-late" in stopped

    def test_a_row_that_appeared_inside_the_window_is_still_stopped(self):
        # A pipeline row absent from the first read is likewise not a pre-terminal row, so the skip
        # must not swallow it.
        late = {"resourceType": "batchJob", "jobId": "job-new-row"}
        stopped = []
        resp = self._abort(
            [[], [_pipeline_row([late], status="RUNNING", pipeline_id="P2")]],
            sub_spy=lambda sub: stopped.append(sub.get("jobId")) or "")
        assert resp["statusCode"] == 200
        assert "job-new-row" in stopped

    def test_a_late_registration_on_a_pre_terminal_row_is_reported_when_that_row_was_running(self):
        # The distinguishing case: the SAME pipeline id, RUNNING at the first read and terminal at the
        # second, carries a job registered in between. The skip keys on the FIRST read's status, so
        # this one is still caught.
        late = {"resourceType": "deadlineCloudJob", "farmId": "f", "queueId": "q", "jobId": "job-x"}
        stopped = []
        resp = self._abort(
            [[_pipeline_row([], status="RUNNING")],
             [_pipeline_row([late], status="SUCCEEDED")]],
            sub_spy=lambda sub: stopped.append(sub.get("jobId")) or "")
        assert resp["statusCode"] == 200
        assert "job-x" in stopped


@pytest.mark.unit
class TestAPartialDeadlineRegistrationIsNamed:
    """A registration naming a job but no farm or queue cannot be cancelled. Returning "" reports a
    clean abort while a farm job may still be running — the one outcome the return value exists to
    prevent."""

    def test_a_registration_without_a_farm_or_queue_is_reported(self):
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "jobId": "job-1"})
        assert "job-1" in warning
        assert "may still be running" in warning
        client.update_job.assert_not_called()

    def test_a_registration_missing_only_the_queue_is_reported(self):
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "farmId": "farm-1", "jobId": "job-2"})
        assert "job-2" in warning
        client.update_job.assert_not_called()

    def test_a_registration_with_no_job_id_stays_silent(self):
        # Control: with no job named there is nothing to report and nothing was left running — the
        # same conclusion the Step Functions and Batch arms reach for an empty locator.
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            assert le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "farmId": "f", "queueId": "q"}) == ""
        client.update_job.assert_not_called()

    def test_a_complete_registration_is_cancelled_and_reports_nothing(self):
        # Control: the partial-row arm must not have swallowed the working path.
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "farmId": "farm-1", "queueId": "queue-1",
                 "jobId": "job-3"})
        assert warning == ""
        assert client.update_job.call_args.kwargs == {
            "farmId": "farm-1", "queueId": "queue-1", "jobId": "job-3",
            "targetTaskRunStatus": "CANCELED"}
