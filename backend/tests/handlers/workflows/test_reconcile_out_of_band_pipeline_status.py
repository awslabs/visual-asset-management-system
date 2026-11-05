# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An execution stopped OUTSIDE VAMS shows its unfinished pipeline steps stopped, not RUNNING (#1020).

A direct Step Functions StopExecution ends the outer run without the abort route, the end-state lambda
or the error handler, so the only VAMS code that learns about it is the lazy reconcile on the read paths.
That reconcile marks the main row; it must also mark each non-terminal pipeline row with the observed
stop (status + stop date, conditioned on the row not being terminal), and nothing else: a GET stops no
sub-process. Both reconciles are covered: the executions LIST (`build_execution_items` through
`on_terminal_observed`) and the execution DETAILS view (`_reconcile_main_status`).
"""

import contextlib
import inspect
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
    ("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows"),
    ("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
    ("WORKFLOW_EXECUTION_LOG_GROUP_ARN", "arn:aws:logs:us-east-1:1:log-group:vams-wf:*"),
    ("WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME", "t-wf-locks"),
]:
    os.environ.setdefault(_name, _value)

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

WF_DB, WF_ID, EXEC = "wdb", "wf", "e3000000000000000000000000000003"
COMPOSITE = f"{WF_DB}:{WF_ID}"
STOP = datetime(2026, 11, 3, 9, 15, 0, tzinfo=timezone.utc)
STOP_ISO = "2026-11-03T09:15:00Z"


def _main_item(status="RUNNING"):
    return {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": COMPOSITE,
            "workflowDatabaseId": WF_DB, "workflowId": WF_ID, "executionStatus": status,
            "executionStopDate": "", "lastSfnSyncCheckDate": "",
            "workflow_execution_arn": "arn:aws:states:us-east-1:1:execution:sm:" + EXEC}


def _pipeline_rows():
    return [
        {"pipelineExecutionId": "p-running", "workflowExecutionId": EXEC, "executionStatus": "RUNNING",
         "registeredSubExecutions": [{"resourceType": "batchJob", "jobId": "job-1"}]},
        {"pipelineExecutionId": "p-new", "workflowExecutionId": EXEC, "executionStatus": "NEW"},
        {"pipelineExecutionId": "p-done", "workflowExecutionId": EXEC, "executionStatus": "SUCCEEDED",
         "executionStopDate": "2026-11-03T09:00:00Z"},
    ]


class _Tables:
    """The tables the reconcile reaches, with the pipeline table answering the execution's rows and
    recording the conditional status writes."""

    def __init__(self, rows):
        self.pipeline_table = MagicMock()
        self.pipeline_table.query.return_value = {"Items": rows}
        self.workflow_table = MagicMock()
        self.workflow_table.get_item.return_value = {"Item": {
            "databaseId": WF_DB, "workflowId": WF_ID, "systemConfig": {"concurrencyRestriction": "none"}}}

    def __call__(self, name):
        return {le.pipeline_executions_table: self.pipeline_table,
                le.workflow_database: self.workflow_table}.get(name, MagicMock())

    def writes(self):
        out = {}
        for c in self.pipeline_table.update_item.call_args_list:
            values = c.kwargs["ExpressionAttributeValues"]
            out[c.kwargs["Key"]["pipelineExecutionId"]] = (values[":st"], values.get(":s"))
            assert "ConditionExpression" in c.kwargs
        return out


def _reconcile_details(described, rows=None):
    tables = _Tables(_pipeline_rows() if rows is None else rows)
    item = _main_item()
    stops = MagicMock()
    with contextlib.ExitStack() as patches:
        patches.enter_context(patch.object(le.dynamodb, "Table", side_effect=tables))
        patches.enter_context(patch.object(le.sfn, "describe_execution", return_value=described))
        patches.enter_context(patch.object(le, "_abort_registered_sub_process", stops))
        patches.enter_context(patch.object(le, "_stop_sfn_execution", stops))
        le._reconcile_main_status(EXEC, item)
    return item, tables, stops


@pytest.mark.unit
class TestDetailsReconcileMarksUnfinishedPipelines:
    def test_an_external_abort_marks_running_and_new_rows_aborted_with_the_stop_date(self):
        item, tables, _stops = _reconcile_details({"status": "ABORTED", "stopDate": STOP})
        assert item["executionStatus"] == "ABORTED"
        assert tables.writes() == {"p-running": ("ABORTED", STOP_ISO), "p-new": ("ABORTED", STOP_ISO)}

    def test_a_finished_pipeline_row_is_left_alone(self):
        _item, tables, _stops = _reconcile_details({"status": "ABORTED", "stopDate": STOP})
        assert "p-done" not in tables.writes()

    def test_a_timed_out_run_marks_rows_timed_out(self):
        _item, tables, _stops = _reconcile_details({"status": "TIMED_OUT", "stopDate": STOP})
        assert set(v[0] for v in tables.writes().values()) == {"TIMED_OUT"}

    def test_a_failed_run_marks_rows_aborted(self):
        # The step was cut off by the outer failure; it did not fail itself.
        _item, tables, _stops = _reconcile_details({"status": "FAILED", "stopDate": STOP})
        assert set(v[0] for v in tables.writes().values()) == {"ABORTED"}

    def test_a_succeeded_run_marks_nothing(self):
        _item, tables, _stops = _reconcile_details({"status": "SUCCEEDED", "stopDate": STOP})
        tables.pipeline_table.update_item.assert_not_called()

    def test_a_still_running_poll_marks_nothing(self):
        _item, tables, _stops = _reconcile_details({"status": "RUNNING"})
        tables.pipeline_table.update_item.assert_not_called()

    def test_the_read_stops_no_sub_process(self):
        _item, _tables, stops = _reconcile_details({"status": "ABORTED", "stopDate": STOP})
        stops.assert_not_called()

    def test_a_pipeline_that_finished_inside_the_window_keeps_its_state(self):
        # The write is conditioned on the row not being terminal; DynamoDB's refusal is swallowed so a
        # concurrent end-state write is neither reverted nor turned into a failed read.
        from botocore.exceptions import ClientError
        tables = _Tables(_pipeline_rows())
        tables.pipeline_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "terminal"}}, "UpdateItem")
        item = _main_item()
        with patch.object(le.dynamodb, "Table", side_effect=tables), \
                patch.object(le.sfn, "describe_execution",
                             return_value={"status": "ABORTED", "stopDate": STOP}):
            le._reconcile_main_status(EXEC, item)
        assert item["executionStatus"] == "ABORTED"
        assert tables.pipeline_table.update_item.call_count == 2

    def test_a_failing_pipeline_read_does_not_fail_the_reconcile(self):
        tables = _Tables([])
        tables.pipeline_table.query.side_effect = RuntimeError("dynamo down")
        item = _main_item()
        with patch.object(le.dynamodb, "Table", side_effect=tables), \
                patch.object(le.sfn, "describe_execution",
                             return_value={"status": "ABORTED", "stopDate": STOP}):
            le._reconcile_main_status(EXEC, item)
        assert item["executionStatus"] == "ABORTED"


@pytest.mark.unit
class TestListReconcileMarksUnfinishedPipelines:
    def test_the_list_route_wires_the_settle_helper(self):
        assert "on_terminal_observed=_settle_reconciled_terminal" in inspect.getsource(le)

    def test_the_settle_helper_marks_pipelines_and_releases_locks(self):
        tables = _Tables(_pipeline_rows())
        item = _main_item("ABORTED")
        item["executionStopDate"] = STOP_ISO
        with patch.object(le.dynamodb, "Table", side_effect=tables), \
                patch.object(le, "_release_locks_for_reconciled_terminal") as release:
            le._settle_reconciled_terminal(EXEC, item, "ABORTED")
        assert tables.writes() == {"p-running": ("ABORTED", STOP_ISO), "p-new": ("ABORTED", STOP_ISO)}
        release.assert_called_once_with(EXEC, item, "ABORTED")

    def test_an_external_abort_seen_by_the_list_marks_the_rows(self):
        tables = _Tables(_pipeline_rows())
        input_row = {"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1"}
        with patch.object(le.dynamodb, "Table", side_effect=tables):
            items = le.build_execution_items(
                input_items=[input_row],
                fetch_main_row=lambda _id: _main_item(),
                describe_execution=lambda _arn: {"status": "ABORTED", "stopDate": STOP},
                persist_main_row=MagicMock(),
                workflow_id_filter="", workflow_database_id="",
                on_terminal_observed=le._settle_reconciled_terminal)
        assert items[0]["executionStatus"] == "ABORTED"
        assert tables.writes() == {"p-running": ("ABORTED", STOP_ISO), "p-new": ("ABORTED", STOP_ISO)}


def _abort_over(rows):
    """Run the abort route over stored pipeline rows (both of its reads answer the same rows). Returns
    the sub-process stop spy."""
    stops = MagicMock(return_value="")
    with contextlib.ExitStack() as patches:
        patches.enter_context(patch.object(le, "get_execution_main_row",
                                           return_value={**_main_item("ABORTED"),
                                                         "executionStopDate": STOP_ISO}))
        patches.enter_context(patch.object(le, "authorize_abort", return_value=(True, "")))
        patches.enter_context(patch.object(le, "get_pipeline_execution_rows",
                                           side_effect=lambda _id: [dict(r) for r in rows]))
        patches.enter_context(patch.object(le, "_stop_sfn_execution", return_value=True))
        patches.enter_context(patch.object(le, "_abort_registered_sub_process", stops))
        patches.enter_context(patch.object(le, "_persist_reconciled_main_row"))
        patches.enter_context(patch.object(le, "log_actions"))
        patches.enter_context(patch.object(le.dynamodb, "Table", return_value=MagicMock()))
        patches.enter_context(patch.object(le.eo, "set_pipeline_status"))
        patches.enter_context(patch.object(le.el, "release_locks_for_execution"))
        response = le.abort_execution({}, EXEC)
    assert response["statusCode"] == 200
    return stops


@pytest.mark.unit
class TestAbortAfterTheReconcileStopsTheSubProcesses:
    """The hint on a stopped run says Abort stops the sub-processes still running. The reconcile marks
    the unfinished rows with what it observed, so the abort must sweep every status that write can
    leave, or the hint promises a stop that never happens."""

    @pytest.mark.parametrize("observed", le.NON_SUCCESS_TERMINAL_STATUSES)
    def test_every_row_the_read_marked_is_swept_by_the_abort(self, observed):
        _item, tables, _stops = _reconcile_details({"status": observed, "stopDate": STOP})
        written = tables.writes()
        assert written, "the reconcile marked no row, so this says nothing about the abort"
        rows = [{**row, "executionStatus": written[row["pipelineExecutionId"]][0]}
                if row["pipelineExecutionId"] in written else row
                for row in _pipeline_rows()]

        stops = _abort_over(rows)

        stopped = [c.args[0] for c in stops.call_args_list]
        assert {"resourceType": "batchJob", "jobId": "job-1"} in stopped, (
            f"a {observed} run's step marked {written['p-running'][0]} by the read kept its "
            f"sub-process running through the abort: {stopped}")

    def test_a_step_that_succeeded_on_its_own_is_still_left_alone(self):
        rows = [{"pipelineExecutionId": "p-done", "workflowExecutionId": EXEC,
                 "executionStatus": "SUCCEEDED",
                 "registeredSubExecutions": [{"resourceType": "batchJob", "jobId": "job-done"}]}]
        _abort_over(rows).assert_not_called()
