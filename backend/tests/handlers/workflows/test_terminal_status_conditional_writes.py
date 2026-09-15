# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-029: both writers of a MAIN execution row's terminal status must be atomic.

`set_pipeline_status` already conditions its write on the row not already holding a terminal status,
so a still-in-flight interim lambda cannot regress an ABORTED pipeline row. The MAIN row has no such
guard: `executionOutputs.finalize_main_row` writes the terminal status unconditionally, and the abort
path decides whether to write by READING the status first and then writing (`abort_execution` ->
`_persist_reconciled_main_row`). Two writers racing on the same main row therefore overwrite each
other's terminal status — an abort issued while the error handler is finalizing can leave the row
reporting SUCCEEDED, or an abort can be silently reverted to FAILED. The stored status is what every
list, detail view and re-run decision reads, so the wrong terminal status is not self-correcting.

The fix is the SAME condition set `set_pipeline_status` uses
(`attribute_not_exists(executionStatus) OR NOT executionStatus IN (...terminal)`), swallowing ONLY
ConditionalCheckFailedException via the existing `_is_conditional_check_failure` helper, applied to
`finalize_main_row` and to the abort path's main-row write.

executionService resolves its table names at import (mirrors
test_execution_logs_scope_and_abort_pipeline_write.py)."""

import os

import pytest
from unittest.mock import MagicMock, patch

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

from backend.backend.common.workflows import executionOutputs as eo
from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"
EXEC_ID = "e1000000000000000000000000000001"


def _client_error(code):
    """A botocore ClientError carrying `code`, which is what DynamoDB raises for a failed condition."""
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": code}}, "UpdateItem")


@pytest.mark.unit
class TestFinalizeMainRowIsConditional:
    """finalize_main_row must not be a blind write: a row that already holds a terminal status was
    finished by another writer and must be left alone."""

    def test_conditions_on_non_terminal_status(self):
        """FIX-029: the same condition set_pipeline_status uses, on the main row."""
        table = MagicMock()
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        eo.finalize_main_row(dynamo, "main-tbl", EXEC_ID, "db", "wf", "SUCCEEDED",
                             "2026-01-01T00:00:00Z")
        kwargs = table.update_item.call_args.kwargs
        condition = kwargs.get("ConditionExpression", "")
        values = kwargs["ExpressionAttributeValues"]
        assert "attribute_not_exists(executionStatus)" in condition
        assert "NOT executionStatus IN (" in condition
        assert set(eo.TERMINAL_STATUSES).issubset(set(values.values()))

    def test_swallows_only_a_conditional_check_failure(self):
        """FIX-029: losing the race is the expected outcome, not an error the caller must handle.

        The error handler and the abort path both call this on a failure path; raising there would
        mask the original outcome."""
        table = MagicMock()
        table.update_item.side_effect = _client_error("ConditionalCheckFailedException")
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        eo.finalize_main_row(dynamo, "main-tbl", EXEC_ID, "db", "wf", "FAILED",
                             "2026-01-01T00:00:00Z")

    def test_a_real_write_failure_still_propagates(self):
        """FIX-029 control: only ConditionalCheckFailedException may be swallowed.

        Without this, the previous test is satisfied by a blanket `except Exception: pass`, which
        would hide a throttle or a permissions failure and leave the row reporting RUNNING forever.
        This passes today (there is no try/except at all) and must keep passing after the fix."""
        table = MagicMock()
        table.update_item.side_effect = _client_error("ProvisionedThroughputExceededException")
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        with pytest.raises(Exception, match="ProvisionedThroughput"):
            eo.finalize_main_row(dynamo, "main-tbl", EXEC_ID, "db", "wf", "FAILED",
                                 "2026-01-01T00:00:00Z")


def _main_rows_table():
    """A moto-backed V2 main execution table (PK workflowExecutionId, SK workflowDatabaseId:workflowId)."""
    import boto3
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName="main-cond",
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    return ddb, ddb.Table("main-cond")


@pytest.mark.unit
class TestFinalizeMainRowAgainstDynamoDB:
    """Against a real DynamoDB expression parser, so the condition is exercised rather than pattern
    matched. The pipeline-row equivalent of this lives in test_stage2_interim_outputs.py."""

    KEY = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": "db:wf"}

    @pytest.mark.aws
    def test_an_already_terminal_row_is_not_regressed(self):
        """FIX-029: an ABORTED main row keeps its status when a late finalize reports SUCCEEDED."""
        from moto import mock_aws
        with mock_aws():
            ddb, table = _main_rows_table()
            table.put_item(Item=dict(self.KEY, executionStatus="ABORTED",
                                     executionStopDate="2026-01-01T00:00:00Z"))
            eo.finalize_main_row(ddb, "main-cond", EXEC_ID, "db", "wf", "SUCCEEDED",
                                 "2026-02-02T00:00:00Z")
            item = table.get_item(Key=self.KEY)["Item"]
            assert item["executionStatus"] == "ABORTED"
            assert item["executionStopDate"] == "2026-01-01T00:00:00Z"

    @pytest.mark.aws
    def test_a_first_terminal_write_still_succeeds(self):
        """FIX-029 control: the condition must not block the normal case.

        Without this, the test above is satisfied by a condition that rejects every write — which
        would leave every execution stuck RUNNING. A row with no status yet and a RUNNING row both
        advance. Passes today and must keep passing after the fix."""
        from moto import mock_aws
        with mock_aws():
            ddb, table = _main_rows_table()
            no_status = dict(self.KEY)
            running = {"workflowExecutionId": "e2", "workflowDatabaseId:workflowId": "db:wf",
                       "executionStatus": "RUNNING"}
            table.put_item(Item=no_status)
            table.put_item(Item=running)
            eo.finalize_main_row(ddb, "main-cond", EXEC_ID, "db", "wf", "SUCCEEDED",
                                 "2026-02-02T00:00:00Z")
            eo.finalize_main_row(ddb, "main-cond", "e2", "db", "wf", "FAILED",
                                 "2026-02-02T00:00:00Z", execution_error="boom")
            assert table.get_item(Key=no_status)["Item"]["executionStatus"] == "SUCCEEDED"
            second = table.get_item(
                Key={"workflowExecutionId": "e2",
                     "workflowDatabaseId:workflowId": "db:wf"})["Item"]
            assert second["executionStatus"] == "FAILED"
            assert second["executionError"] == "boom"


@pytest.mark.unit
class TestAbortMainRowWriteIsConditional:
    """The abort path decides whether to write the main row by reading `executionStatus` and then
    writing. The read and the write are separate calls, so the write carries the same terminal guard —
    an end-state lambda that finishes inside that window keeps its status instead of being reverted."""

    def _main_row(self):
        return {"workflowExecutionId": EXEC_ID, "workflowId": "wfx", "workflowDatabaseId": "dbx",
                "workflowDatabaseId:workflowId": "dbx:wfx",
                "workflow_execution_arn": "arn:ex:main", "executionStatus": "RUNNING",
                "executionStopDate": ""}

    def _run_abort(self, main_update_side_effect=None):
        """Abort one execution with no pipeline rows, returning the MAIN table mock."""
        main_table = MagicMock()
        if main_update_side_effect is not None:
            main_table.update_item.side_effect = main_update_side_effect
        pexec_table = MagicMock()

        def _table(name):
            return pexec_table if name == le.pipeline_executions_table else main_table

        with patch(f"{MOD}.get_execution_main_row", return_value=self._main_row()), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]), \
             patch(f"{MOD}._stop_sfn_execution"), \
             patch(f"{MOD}.log_actions"), \
             patch.object(le.dynamodb, "Table", side_effect=_table):
            resp = le.abort_execution({}, EXEC_ID)
        return resp, main_table

    def test_the_abort_main_row_write_is_condition_guarded(self):
        """FIX-029: the abort's main-row write carries the same terminal guard."""
        resp, main_table = self._run_abort()
        assert resp["statusCode"] == 200
        update = main_table.update_item.call_args.kwargs
        condition = update.get("ConditionExpression", "")
        assert "attribute_not_exists(executionStatus)" in condition
        assert "NOT executionStatus IN (" in condition
        assert set(le.TERMINAL_STATUSES).issubset(set(update["ExpressionAttributeValues"].values()))

    def test_the_abort_still_writes_the_aborted_status(self):
        """FIX-029 control: the guard may not turn a legitimate abort into a no-op.

        Without this, a condition that rejects everything satisfies the test above while making abort
        stop reconciling the main row at all. Passes today and must keep passing after the fix."""
        resp, main_table = self._run_abort()
        assert resp["statusCode"] == 200
        main_table.put_item.assert_not_called()
        update = main_table.update_item.call_args.kwargs
        assert update["Key"] == {"workflowExecutionId": EXEC_ID,
                                 "workflowDatabaseId:workflowId": "dbx:wfx"}
        assert le.ABORTED_STATUS in update["ExpressionAttributeValues"].values()
        assert "executionStatus" in update["ExpressionAttributeNames"].values()

    def test_losing_the_race_is_reported_as_a_successful_abort(self):
        """FIX-029: the second writer losing the condition is the expected outcome, not an error.

        The execution really is finished — by the other writer — so the caller is told the abort
        succeeded rather than getting a 500 for a row that reached a terminal state on its own."""
        resp, _ = self._run_abort(_client_error("ConditionalCheckFailedException"))
        assert resp["statusCode"] == 200

    def test_a_real_abort_write_failure_still_propagates(self):
        """FIX-029 control: only ConditionalCheckFailedException may be swallowed on the abort path.

        Without this, the test above is satisfied by a blanket `except Exception: pass`, which would
        report a successful abort while the row still reads RUNNING. `abort_execution` has no
        try/except of its own, so a throttle or permissions failure reaches `lambda_handler` and
        becomes a 500 — which is the correct outcome, and the opposite of the swallowed case."""
        with pytest.raises(Exception, match="ProvisionedThroughput"):
            self._run_abort(_client_error("ProvisionedThroughputExceededException"))


@pytest.mark.unit
class TestReconcileReadPathsStayUnconditional:
    """FIX-029 scope control: the terminal guard is OPT-IN, and the shared writer defaults to off.

    `_persist_reconciled_main_row` is shared with the listing and details lazy-reconcile paths. What
    FIX-029 relied on — that those paths skip a terminal row before calling it — holds at READ time
    only, and S2-BACKEND-105 is exactly the window between that read and this write: the read paths now
    pass `only_if_not_terminal=True` for any write that touches an attribute a completing writer owns
    (see test_reconcile_and_abort_races.py). What stays pinned here is the WRITER's default, so a caller
    that did not ask for the guard is neither given one nor silently no-op'd by one."""

    KEY_ITEM = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": "dbx:wfx",
                "executionStatus": "SUCCEEDED", "executionStopDate": "2026-01-01T00:00:00Z",
                "lastSfnSyncCheckDate": "2026-01-01T00:00:00Z"}

    def test_the_default_write_carries_no_condition(self):
        table = MagicMock()
        le._persist_reconciled_main_row(table, dict(self.KEY_ITEM),
                                        le.DETAIL_RECONCILED_MAIN_ROW_ATTRIBUTES)
        kwargs = table.update_item.call_args.kwargs
        assert "ConditionExpression" not in kwargs
        assert set(le.TERMINAL_STATUSES).isdisjoint(
            set(kwargs["ExpressionAttributeValues"].values()) - {"SUCCEEDED"})

    def test_the_default_write_does_not_swallow_a_conditional_failure(self):
        """A caller that did not ask for the guard cannot be silently no-op'd by one.

        Without this, `only_if_not_terminal=False` could still absorb a ConditionalCheckFailed raised
        for some other reason, and the reconcile would report success having written nothing."""
        table = MagicMock()
        table.update_item.side_effect = _client_error("ConditionalCheckFailedException")
        with pytest.raises(Exception, match="ConditionalCheckFailed"):
            le._persist_reconciled_main_row(table, dict(self.KEY_ITEM),
                                            le.DETAIL_RECONCILED_MAIN_ROW_ATTRIBUTES)
