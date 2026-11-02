# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An execution stopped OUTSIDE VAMS releases its concurrency locks the first time a read observes it.

A direct Step Functions StopExecution (console, CLI, or a deleted state machine) ends the run without the
end-state lambda, the error handler, or the abort route running, so none of the three release sites is
reached. The read paths' lazy reconcile is the first place VAMS learns the run is terminal, and it releases
the run's lock rows there; without that, a `perAsset` row sits until the table's TTL and refuses every
launch on the asset for up to a day. Observed live in the round-5 smoke (execution stopped with the
`aws stepfunctions stop-execution` CLI; its `|asset|` row survived the ABORTED reconcile).

Both reconciles are covered: the executions LIST (`build_execution_items`, through the `on_terminal_observed`
callback) and the execution DETAILS view (`_reconcile_main_status`). A still-running poll must release
nothing, and a reconcile that reaches a `none` workflow must read no input rows.
"""

import contextlib
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

el = le.el
WF_DB, WF_ID, EXEC = "wdb", "wf", "e2000000000000000000000000000002"
COMPOSITE = f"{WF_DB}:{WF_ID}"
ROWS = [{"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1",
         "inputAssetFileKey": "/a1/one.glb", "versionId": "v1"}]
STOP = datetime(2026, 9, 18, 7, 22, 43, tzinfo=timezone.utc)


def _tables(restriction):
    workflow_table = MagicMock()
    workflow_table.get_item.return_value = {"Item": {
        "databaseId": WF_DB, "workflowId": WF_ID, "systemConfig": {"concurrencyRestriction": restriction}}}
    inputs_table = MagicMock()
    inputs_table.query.return_value = {"Items": list(ROWS)}
    locks_table = MagicMock()
    return workflow_table, inputs_table, locks_table


def _released_keys(locks_table):
    return [c.kwargs["Key"]["lockKey"] for c in locks_table.delete_item.call_args_list]


def _main_item(status="RUNNING"):
    return {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": COMPOSITE,
            "workflowDatabaseId": WF_DB, "workflowId": WF_ID, "executionStatus": status,
            "executionStopDate": "", "lastSfnSyncCheckDate": "",
            "workflow_execution_arn": "arn:aws:states:us-east-1:1:execution:sm:" + EXEC}


def _with_tables(restriction):
    workflow_table, inputs_table, locks_table = _tables(restriction)

    def _table(name):
        return {le.workflow_database: workflow_table, le.workflow_execution_inputs_table: inputs_table,
                le.workflow_execution_locks_table: locks_table}.get(name, MagicMock())
    return _table, inputs_table, locks_table


# ---------------------------------------------------------------------------------------------------
# Details view: _reconcile_main_status
# ---------------------------------------------------------------------------------------------------

def _reconcile_details(described, restriction="perAsset"):
    _table, inputs_table, locks_table = _with_tables(restriction)
    item = _main_item()
    with contextlib.ExitStack() as patches:
        patches.enter_context(patch.object(le.dynamodb, "Table", side_effect=_table))
        patches.enter_context(patch.object(le.sfn, "describe_execution", return_value=described))
        le._reconcile_main_status(EXEC, item)
    return item, inputs_table, locks_table


@pytest.mark.unit
class TestDetailsReconcileReleasesAnOutOfBandTermination:
    def test_an_aborted_poll_releases_the_runs_lock_rows(self):
        item, inputs_table, locks_table = _reconcile_details({"status": "ABORTED", "stopDate": STOP})
        assert item["executionStatus"] == "ABORTED"
        inputs_table.query.assert_called()
        assert _released_keys(locks_table) == [
            el.build_lock_key(WF_DB, WF_ID, "db", "a1", "/a1/one.glb", "v1", scope=el.LOCK_SCOPE_ASSET)]
        assert {c.kwargs["ExpressionAttributeValues"][":e"]
                for c in locks_table.delete_item.call_args_list} == {EXEC}

    def test_every_terminal_status_releases(self):
        for status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            _item, _inputs, locks_table = _reconcile_details({"status": status, "stopDate": STOP})
            assert len(_released_keys(locks_table)) == 1, status

    def test_a_still_running_poll_releases_nothing(self):
        # No stopDate: the run is live, and releasing here would let a competing launch in mid-run.
        item, inputs_table, locks_table = _reconcile_details({"status": "RUNNING"})
        assert item["executionStatus"] == "RUNNING"
        inputs_table.query.assert_not_called()
        locks_table.delete_item.assert_not_called()

    def test_the_none_restriction_reads_no_input_rows(self):
        _item, inputs_table, locks_table = _reconcile_details(
            {"status": "ABORTED", "stopDate": STOP}, restriction="none")
        inputs_table.query.assert_not_called()
        locks_table.delete_item.assert_not_called()

    def test_an_already_terminal_row_is_not_polled_and_releases_nothing(self):
        _table, inputs_table, locks_table = _with_tables("perAsset")
        item = _main_item(status="SUCCEEDED")
        item["executionStopDate"] = "2026-09-18T07:00:00Z"
        with patch.object(le.dynamodb, "Table", side_effect=_table), \
                patch.object(le.sfn, "describe_execution") as describe:
            le._reconcile_main_status(EXEC, item)
        describe.assert_not_called()
        locks_table.delete_item.assert_not_called()


# ---------------------------------------------------------------------------------------------------
# List view: build_execution_items -> on_terminal_observed
# ---------------------------------------------------------------------------------------------------

def _build(described, on_terminal_observed):
    input_row = {"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1",
                 "workflowDatabaseId": WF_DB, "workflowId": WF_ID}
    return le.build_execution_items(
        input_items=[input_row],
        fetch_main_row=lambda _id: _main_item(),
        describe_execution=lambda _arn: described,
        persist_main_row=MagicMock(),
        workflow_id_filter="", workflow_database_id="",
        on_terminal_observed=on_terminal_observed)


@pytest.mark.unit
class TestListReconcileReportsAnOutOfBandTermination:
    def test_a_terminal_poll_invokes_the_callback_with_the_execution_and_status(self):
        observed = MagicMock()
        items = _build({"status": "ABORTED", "stopDate": STOP}, observed)
        assert items[0]["executionStatus"] == "ABORTED"
        observed.assert_called_once()
        execution_id, main_item, status = observed.call_args.args
        assert (execution_id, status) == (EXEC, "ABORTED")
        assert main_item["workflowDatabaseId:workflowId"] == COMPOSITE

    def test_a_running_poll_does_not_invoke_the_callback(self):
        observed = MagicMock()
        _build({"status": "RUNNING"}, observed)
        observed.assert_not_called()

    def test_the_callback_is_optional(self):
        # Every other caller of build_execution_items keeps working without wiring one.
        items = _build({"status": "ABORTED", "stopDate": STOP}, None)
        assert items[0]["executionStatus"] == "ABORTED"

    def test_the_list_route_wires_the_release_helper(self):
        # The handler passes the real release helper, not a placeholder: an out-of-band stop observed
        # by the executions list releases the same rows the details view would.
        import inspect
        source = inspect.getsource(le)
        assert "on_terminal_observed=_release_locks_for_reconciled_terminal" in source


@pytest.mark.unit
class TestTheReleaseHelper:
    def test_releases_under_the_workflows_restriction_and_logs_the_count(self):
        _table, inputs_table, locks_table = _with_tables("perInputFile")
        with patch.object(le.dynamodb, "Table", side_effect=_table), patch.object(le, "logger") as log:
            le._release_locks_for_reconciled_terminal(EXEC, _main_item("FAILED"), "FAILED")
        assert _released_keys(locks_table) == [
            el.build_lock_key(WF_DB, WF_ID, "db", "a1", "/a1/one.glb", "v1", scope=el.LOCK_SCOPE_ASSET_FILE)]
        assert any("outside VAMS" in str(c) for c in log.info.call_args_list)

    def test_the_workflow_identity_falls_back_to_the_composite_key(self):
        # A migrated main row can carry only the composite sort key.
        _table, _inputs, locks_table = _with_tables("perAsset")
        item = _main_item("ABORTED")
        del item["workflowDatabaseId"]
        del item["workflowId"]
        with patch.object(le.dynamodb, "Table", side_effect=_table):
            le._release_locks_for_reconciled_terminal(EXEC, item, "ABORTED")
        assert len(_released_keys(locks_table)) == 1

    def test_a_failing_release_is_swallowed(self):
        # Best-effort by the same contract as the other release sites: a read path never fails on the
        # lock table, and an unreleased row still expires through TTL.
        workflow_table = MagicMock()
        workflow_table.get_item.side_effect = RuntimeError("dynamo down")
        with patch.object(le.dynamodb, "Table", return_value=workflow_table):
            le._release_locks_for_reconciled_terminal(EXEC, _main_item("ABORTED"), "ABORTED")
