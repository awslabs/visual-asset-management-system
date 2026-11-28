# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The two remaining terminal paths release an execution's perInputFileVersion locks: the error handler
after it finalizes the main row FAILED, and the abort API after it marks the main row ABORTED. Both
derive the keys from the workflow's stored restriction and the execution's input rows, and neither can
fail its own reconciliation on the lock table."""

import contextlib
import json
import os

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

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

from backend.backend.handlers.workflows.sfn import handleExecutionError as heh  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

ES = "backend.backend.handlers.workflows.executionService"
el = heh.el
WF_DB, WF_ID, EXEC = "wdb", "wf", "e1000000000000000000000000000001"
ROWS = [
    {"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1",
     "inputAssetFileKey": "/a1/f.glb", "versionId": "v1"},
    {"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1",
     "inputAssetFileKey": "/a1/g.glb", "versionId": "v2"},
]
KEYS = [el.build_lock_key(WF_DB, WF_ID, "db", "a1", "/a1/f.glb", "v1"),
        el.build_lock_key(WF_DB, WF_ID, "db", "a1", "/a1/g.glb", "v2")]


def _tables(restriction, workflow_get_error=None):
    workflow_table = MagicMock()
    if workflow_get_error is not None:
        workflow_table.get_item.side_effect = workflow_get_error
    else:
        workflow_table.get_item.return_value = {"Item": {
            "databaseId": WF_DB, "workflowId": WF_ID,
            "systemConfig": {"concurrencyRestriction": restriction}}}
    inputs_table = MagicMock()
    inputs_table.query.return_value = {"Items": list(ROWS)}
    locks_table = MagicMock()
    return workflow_table, inputs_table, locks_table


def _released_keys(locks_table):
    return [c.kwargs["Key"]["lockKey"] for c in locks_table.delete_item.call_args_list]


# ---------------------------------------------------------------------------------------------------
# handleExecutionError
# ---------------------------------------------------------------------------------------------------

BODY = {"workflowExecutionId": EXEC, "workflowDatabaseId": WF_DB, "workflowId": WF_ID}
ERROR_INFO = {"Error": "States.Timeout", "Cause": json.dumps({"errorMessage": "step timed out"})}


def _reconcile(restriction="perInputFileVersion", workflow_get_error=None):
    workflow_table, inputs_table, locks_table = _tables(restriction, workflow_get_error)
    main_table = MagicMock(query=MagicMock(return_value={"Items": [{"executionStatus": "RUNNING"}]}))
    finalize = MagicMock()

    def _table(name):
        return {heh.workflow_table: workflow_table, heh.workflow_execution_inputs_table: inputs_table,
                heh.workflow_execution_locks_table: locks_table}.get(name, main_table)

    with contextlib.ExitStack() as patches:
        for context in (
            patch.object(heh, "_get_pipeline_rows", return_value=[]),
            patch.object(heh.eo, "finalize_main_row", finalize),
            patch.object(heh, "_fetch_execution_log", return_value=""),
            patch.object(heh.dynamodb, "Table", side_effect=_table),
        ):
            patches.enter_context(context)
        response = heh.lambda_handler({"body": BODY, "errorInfo": ERROR_INFO}, MagicMock())
    assert response == {"handled": True}
    return finalize, inputs_table, locks_table


@pytest.mark.unit
class TestErrorHandlerReleasesLocks:
    def test_the_failed_run_releases_every_input_row_version_after_finalizing(self):
        finalize, _inputs, locks_table = _reconcile()
        finalize.assert_called_once()
        assert _released_keys(locks_table) == KEYS
        assert {c.kwargs["ExpressionAttributeValues"][":e"]
                for c in locks_table.delete_item.call_args_list} == {EXEC}

    def test_the_none_restriction_reads_no_input_rows_and_deletes_nothing(self):
        for restriction in ("none",):
            _finalize, inputs_table, locks_table = _reconcile(restriction)
            # The one input read is the failed-end audit record's asset lookup, never a lock rebuild.
            assert [c.kwargs.get("ProjectionExpression") for c in inputs_table.query.call_args_list] == [
                "databaseId, assetId"]
            locks_table.delete_item.assert_not_called()

    def test_a_failing_release_still_answers_handled(self):
        finalize, _inputs, locks_table = _reconcile(workflow_get_error=ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}}, "GetItem"))
        finalize.assert_called_once()
        locks_table.delete_item.assert_not_called()

    def test_a_body_without_an_execution_id_touches_no_table(self):
        with patch.object(heh.dynamodb, "Table") as table_factory:
            assert heh.lambda_handler({"body": {}, "errorInfo": ERROR_INFO}, MagicMock()) == {"handled": True}
        table_factory.assert_not_called()


# ---------------------------------------------------------------------------------------------------
# executionService.abort_execution
# ---------------------------------------------------------------------------------------------------

def _main_row():
    return {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}",
            "workflowDatabaseId": WF_DB, "workflowId": WF_ID,
            "workflow_execution_arn": "arn:ex:main", "executionStatus": "RUNNING"}


def _abort(restriction="perInputFileVersion", workflow_get_error=None, stop_error=None, describe=None):
    """Drive abort_execution; StopExecution raises `stop_error` when given, and DescribeExecution answers
    (or raises) `describe`."""
    workflow_table, inputs_table, locks_table = _tables(restriction, workflow_get_error)

    def _table(name):
        return {le.workflow_database: workflow_table, le.workflow_execution_inputs_table: inputs_table,
                le.workflow_execution_locks_table: locks_table}.get(name, MagicMock())

    stop = MagicMock(return_value={}, side_effect=stop_error)
    describe_mock = MagicMock(side_effect=describe) if isinstance(describe, Exception) \
        else MagicMock(return_value=describe or {"status": "RUNNING"})
    persist = MagicMock()
    with patch(f"{ES}.get_execution_main_row", return_value=_main_row()), \
            patch(f"{ES}.authorize_abort", return_value=(True, "")), \
            patch(f"{ES}.get_pipeline_execution_rows", return_value=[]), \
            patch.object(le.sfn, "stop_execution", stop), \
            patch.object(le.sfn, "describe_execution", describe_mock), \
            patch(f"{ES}._persist_reconciled_main_row", persist), \
            patch(f"{ES}.log_actions"), \
            patch.object(le.dynamodb, "Table", side_effect=_table):
        response = le.abort_execution({"requestContext": {"http": {}}}, EXEC)
    return response, persist, inputs_table, locks_table


THROTTLED_STOP = ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                             "StopExecution")


@pytest.mark.unit
class TestAbortReleasesLocks:
    def test_the_aborted_run_releases_every_input_row_version_after_marking_the_main_row(self):
        response, persist, _inputs, locks_table = _abort()
        assert response["statusCode"] == 200
        persist.assert_called_once()
        assert _released_keys(locks_table) == KEYS
        assert {c.kwargs["ExpressionAttributeValues"][":e"]
                for c in locks_table.delete_item.call_args_list} == {EXEC}

    def test_the_none_restriction_reads_no_input_rows_and_deletes_nothing(self):
        for restriction in ("none",):
            response, _persist, inputs_table, locks_table = _abort(restriction)
            assert response["statusCode"] == 200
            inputs_table.query.assert_not_called()
            locks_table.delete_item.assert_not_called()

    def test_a_failing_release_does_not_fail_the_abort(self):
        response, persist, _inputs, locks_table = _abort(workflow_get_error=ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}}, "GetItem"))
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Execution aborted"
        persist.assert_called_once()
        locks_table.delete_item.assert_not_called()


@pytest.mark.unit
class TestAbortReleasesOnlyAStoppedMachine:
    """The abort releases the run's locks only when its state machine runs no further: the stop
    succeeded, there was no execution to stop, or a describe after a failed stop shows it ended. One that
    may still be running keeps them, and the response says so in its `warnings`."""

    def _body(self, response):
        assert response["statusCode"] == 200
        return json.loads(response["body"])

    def test_a_stop_that_succeeds_releases_the_locks_and_warns_of_nothing(self):
        response, _persist, _inputs, locks_table = _abort()
        assert "warnings" not in self._body(response)
        assert _released_keys(locks_table) == KEYS

    def test_a_throttled_stop_of_a_still_running_machine_keeps_the_locks_and_says_so(self):
        response, persist, _inputs, locks_table = _abort(stop_error=THROTTLED_STOP,
                                                        describe={"status": "RUNNING"})
        body = self._body(response)
        assert body["message"] == "Execution aborted"
        assert body["warnings"] == [le.MAIN_EXECUTION_STOP_UNCONFIRMED_WARNING]
        persist.assert_called_once()
        locks_table.delete_item.assert_not_called()

    def test_the_warning_names_no_arn(self):
        response, _persist, _inputs, _locks = _abort(stop_error=THROTTLED_STOP)
        assert "arn:" not in json.dumps(self._body(response)["warnings"])

    @pytest.mark.parametrize("status", sorted(el.SFN_ENDED_STATUSES))
    def test_a_failed_stop_of_a_machine_that_already_ended_releases_the_locks(self, status):
        response, _persist, _inputs, locks_table = _abort(stop_error=THROTTLED_STOP,
                                                         describe={"status": status})
        assert "warnings" not in self._body(response)
        assert _released_keys(locks_table) == KEYS

    def test_a_failed_stop_whose_describe_also_fails_keeps_the_locks(self):
        response, _persist, _inputs, locks_table = _abort(
            stop_error=THROTTLED_STOP, describe=ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "DescribeExecution"))
        assert self._body(response)["warnings"] == [le.MAIN_EXECUTION_STOP_UNCONFIRMED_WARNING]
        locks_table.delete_item.assert_not_called()

    def test_an_execution_that_no_longer_exists_releases_the_locks(self):
        gone = le.sfn.exceptions.ExecutionDoesNotExist(
            {"Error": {"Code": "ExecutionDoesNotExist", "Message": "gone"}}, "StopExecution")
        response, _persist, _inputs, locks_table = _abort(stop_error=gone)
        assert "warnings" not in self._body(response)
        assert _released_keys(locks_table) == KEYS
