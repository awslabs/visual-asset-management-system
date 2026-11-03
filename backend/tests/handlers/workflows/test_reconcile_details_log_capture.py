# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The execution DETAILS reconcile stores the log of a run it finds stopped outside VAMS.

A run stopped directly in Step Functions reaches neither the end-state lambda nor the error handler,
so the first read path that records its terminal status is the only place its log can be captured.
Once the details view has written that status, no listing reconciles the run again, so the details
reconcile captures the log the same way the listing's reconcile does (#967).
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

EXEC = "e3000000000000000000000000000003"
STOP = datetime(2026, 11, 3, 17, 44, 13, tzinfo=timezone.utc)
LOG_GROUP_ARN = "arn:aws:logs:us-east-1:111122223333:log-group:vams-wf:*"
EVENTS = [{"message": f"event {i} {EXEC}", "logStreamName": "s", "timestamp": i} for i in range(12)]


def _main_item():
    return {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": "wdb:wf",
            "workflowDatabaseId": "wdb", "workflowId": "wf", "executionStatus": "RUNNING",
            "executionStopDate": "", "lastSfnSyncCheckDate": "",
            "executionStartDate": "2026-11-03T17:44:00Z", "executionLogGroupArn": LOG_GROUP_ARN,
            "workflow_execution_arn": "arn:aws:states:us-east-1:111122223333:execution:sm:" + EXEC}


def _reconcile(described, filter_log_events=None):
    main_table = MagicMock()
    filter_log_events = filter_log_events or MagicMock(return_value={"events": EVENTS})
    item = _main_item()
    with contextlib.ExitStack() as patches:
        patches.enter_context(patch.object(
            le.dynamodb, "Table",
            side_effect=lambda name: main_table if name == le.workflow_execution_database_v2 else MagicMock()))
        patches.enter_context(patch.object(le.sfn, "describe_execution", return_value=described))
        patches.enter_context(patch.object(le.logs_client, "filter_log_events", filter_log_events))
        le._reconcile_main_status(EXEC, item)
    return item, main_table, filter_log_events


def _written(main_table):
    """The attribute -> value map of the reconcile's main-row update."""
    assert main_table.update_item.called
    kwargs = main_table.update_item.call_args.kwargs
    names, values = kwargs["ExpressionAttributeNames"], kwargs["ExpressionAttributeValues"]
    return {names[f"#a{i}"]: values[f":v{i}"] for i in range(len(names))}, kwargs


@pytest.mark.unit
class TestTheDetailsReconcileStoresTheLog:
    def test_an_aborted_run_stores_its_log_and_error_under_the_terminal_guard(self):
        item, main_table, read = _reconcile(
            {"status": "ABORTED", "stopDate": STOP, "error": "States.Aborted", "cause": "stopped"})

        written, kwargs = _written(main_table)
        assert written["executionStatus"] == "ABORTED"
        assert written["executionLog"].splitlines() == [e["message"] for e in EVENTS]
        assert written["executionError"] == "States.Aborted: stopped"
        assert "ConditionExpression" in kwargs
        assert item["executionLog"] == written["executionLog"]
        request = read.call_args_list[0].kwargs
        assert request["logGroupName"] == "vams-wf"
        assert request["filterPattern"] == f'"{EXEC}"'
        assert request["startTime"] == le.elc.log_capture_start_ms("2026-11-03T17:44:00Z")

    def test_a_succeeded_run_stores_its_log_and_no_error(self):
        _item, main_table, _read = _reconcile({"status": "SUCCEEDED", "stopDate": STOP})

        written, _ = _written(main_table)
        assert written["executionLog"]
        assert "executionError" not in written

    def test_the_stored_log_is_redacted(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlc2lnbmF0dXJl"
        events = [{"message": f'{EXEC} {{"Authorization": "Bearer {token}"}}', "timestamp": 1}]
        _item, main_table, _read = _reconcile(
            {"status": "ABORTED", "stopDate": STOP},
            filter_log_events=MagicMock(return_value={"events": events}))

        written, _ = _written(main_table)
        assert token not in written["executionLog"]

    def test_an_empty_read_still_records_the_status_and_stores_no_log(self):
        # An absent log is what lets the readers fall back to a live read.
        _item, main_table, _read = _reconcile(
            {"status": "ABORTED", "stopDate": STOP},
            filter_log_events=MagicMock(return_value={"events": []}))

        written, _ = _written(main_table)
        assert written["executionStatus"] == "ABORTED"
        assert "executionLog" not in written

    def test_a_failed_read_still_records_the_status(self):
        _item, main_table, _read = _reconcile(
            {"status": "ABORTED", "stopDate": STOP},
            filter_log_events=MagicMock(side_effect=RuntimeError("throttled")))

        written, _ = _written(main_table)
        assert written["executionStatus"] == "ABORTED"
        assert "executionLog" not in written

    def test_a_still_running_poll_reads_no_log(self):
        item, main_table, read = _reconcile({"status": "RUNNING"})

        read.assert_not_called()
        written, _ = _written(main_table)
        assert "executionLog" not in written
        assert item["executionStatus"] == "RUNNING"


@pytest.mark.unit
class TestDetailsThenListing:
    def test_a_listing_after_the_details_reconcile_serves_the_stored_log(self):
        # The details write makes the row terminal, so the listing does not poll or read again and
        # lists the log the details reconcile stored.
        item, _main_table, _read = _reconcile({"status": "ABORTED", "stopDate": STOP})
        persisted = dict(item)
        describe = MagicMock()

        items = le.build_execution_items(
            input_items=[{"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1"}],
            fetch_main_row=lambda _id: persisted,
            describe_execution=describe,
            persist_main_row=MagicMock(),
            workflow_id_filter="", workflow_database_id="",
            fetch_execution_log_and_error=MagicMock(side_effect=AssertionError("no second read")))

        describe.assert_not_called()
        assert items[0]["executionLog"].splitlines() == [e["message"] for e in EVENTS]
