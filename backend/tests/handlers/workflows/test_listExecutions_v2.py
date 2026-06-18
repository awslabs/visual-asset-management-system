# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest
from datetime import datetime
from unittest.mock import MagicMock

os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_NAME", "t-exec")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")

from backend.backend.handlers.workflows import listExecutions as le


@pytest.mark.unit
class TestBuildExecutionItems:
    def test_joins_inputs_and_main_rows_into_wire_shape(self, monkeypatch):
        # Inputs GSI returns one execution for the asset
        input_items = [{
            "workflowExecutionId": "E1", "inputAssetFileKey": "/x.glb",
            "databaseId": "db", "assetId": "a1",
            "workflowId": "wf", "workflowDatabaseId": "wdb",
            "executionStartDate": "2026-06-16T00:00:00Z",
        }]
        main_row = {
            "executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
            "workflow_execution_arn": "arn:ex", "workflow_arn": "arn:sm",
            "executionStatus": "SUCCEEDED",
            "executionStartDate": "2026-06-16T00:00:00Z",
            "executionStopDate": "2026-06-16T00:05:00Z",
        }
        items = le.build_execution_items(
            input_items=input_items,
            fetch_main_row=lambda eid: main_row,
            describe_execution=lambda arn: None,  # not called: stopDate present
            persist_main_row=lambda item: None,
            workflow_id_filter="",
            workflow_database_id="",
        )
        assert len(items) == 1
        it = items[0]
        # wire field names preserved for the frontend/CLI
        assert it["executionId"] == "E1"
        assert it["startDate"] == "2026-06-16T00:00:00Z"
        assert it["stopDate"] == "2026-06-16T00:05:00Z"
        assert it["inputAssetFileKey"] == "/x.glb"
        assert it["databaseId"] == "db" and it["assetId"] == "a1"
        assert it["executionStatus"] == "SUCCEEDED"

    def test_dedupes_multiple_input_files_same_execution(self):
        input_items = [
            {"workflowExecutionId": "E1", "inputAssetFileKey": "/x.glb", "databaseId": "db",
             "assetId": "a1", "workflowId": "wf", "workflowDatabaseId": "wdb",
             "executionStartDate": "2026-06-16T00:00:00Z"},
            {"workflowExecutionId": "E1", "inputAssetFileKey": "/y.glb", "databaseId": "db",
             "assetId": "a1", "workflowId": "wf", "workflowDatabaseId": "wdb",
             "executionStartDate": "2026-06-16T00:00:00Z"},
        ]
        main_row = {"executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
                    "workflow_execution_arn": "arn:ex", "executionStatus": "SUCCEEDED",
                    "executionStartDate": "2026-06-16T00:00:00Z",
                    "executionStopDate": "2026-06-16T00:05:00Z"}
        items = le.build_execution_items(
            input_items=input_items, fetch_main_row=lambda eid: main_row,
            describe_execution=lambda arn: None, persist_main_row=lambda item: None,
            workflow_id_filter="", workflow_database_id="",
        )
        assert len(items) == 1  # one execution despite two input files
        # The first-seen (newest) input file's key is the one that surfaces.
        assert items[0]["inputAssetFileKey"] == "/x.glb"

    def test_running_execution_reconciled(self):
        # Main row has no stop date -> describe_execution drives reconciliation.
        input_items = [{
            "workflowExecutionId": "E1", "inputAssetFileKey": "/x.glb",
            "databaseId": "db", "assetId": "a1",
            "workflowId": "wf", "workflowDatabaseId": "wdb",
            "executionStartDate": "2026-06-16T00:00:00Z",
        }]
        main_row = {
            "executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
            "workflow_execution_arn": "arn:ex",
            "executionStatus": "RUNNING",
            "executionStartDate": "2026-06-16T00:00:00Z",
            "executionStopDate": "",
        }
        sfn_response = {
            "status": "SUCCEEDED",
            "startDate": datetime(2026, 6, 16, 0, 0, 0),
            "stopDate": datetime(2026, 6, 16, 0, 5, 0),
        }
        persist = MagicMock()
        items = le.build_execution_items(
            input_items=input_items,
            fetch_main_row=lambda eid: main_row,
            describe_execution=lambda arn: sfn_response,
            persist_main_row=persist,
            workflow_id_filter="", workflow_database_id="",
        )
        assert len(items) == 1
        it = items[0]
        assert it["stopDate"] == "2026-06-16T00:05:00Z"
        assert it["startDate"] == "2026-06-16T00:00:00Z"
        assert it["executionStatus"] == "SUCCEEDED"
        persist.assert_called_once()

    def test_workflow_filter_positive_and_negative(self):
        input_items = [
            {"workflowExecutionId": "E1", "inputAssetFileKey": "/x.glb", "databaseId": "db",
             "assetId": "a1", "workflowId": "wf1", "workflowDatabaseId": "wdb",
             "executionStartDate": "2026-06-16T00:00:00Z"},
            {"workflowExecutionId": "E2", "inputAssetFileKey": "/y.glb", "databaseId": "db",
             "assetId": "a1", "workflowId": "wf2", "workflowDatabaseId": "wdb",
             "executionStartDate": "2026-06-15T00:00:00Z"},
        ]
        main_rows = {
            "E1": {"executionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "wdb",
                   "workflowDatabaseId:workflowId": "wdb:wf1", "workflow_execution_arn": "arn:e1",
                   "executionStatus": "SUCCEEDED", "executionStartDate": "2026-06-16T00:00:00Z",
                   "executionStopDate": "2026-06-16T00:05:00Z"},
            "E2": {"executionId": "E2", "workflowId": "wf2", "workflowDatabaseId": "wdb",
                   "workflowDatabaseId:workflowId": "wdb:wf2", "workflow_execution_arn": "arn:e2",
                   "executionStatus": "SUCCEEDED", "executionStartDate": "2026-06-15T00:00:00Z",
                   "executionStopDate": "2026-06-15T00:05:00Z"},
        }
        items = le.build_execution_items(
            input_items=input_items,
            fetch_main_row=lambda eid: main_rows.get(eid),
            describe_execution=lambda arn: None,
            persist_main_row=lambda item: None,
            workflow_id_filter="wf1", workflow_database_id="wdb",
        )
        # Only the execution matching the workflow filter is returned (E2 dropped).
        assert len(items) == 1
        assert items[0]["executionId"] == "E1"

    def test_missing_main_row_skipped(self):
        input_items = [
            {"workflowExecutionId": "E1", "inputAssetFileKey": "/x.glb", "databaseId": "db",
             "assetId": "a1", "workflowId": "wf", "workflowDatabaseId": "wdb",
             "executionStartDate": "2026-06-16T00:00:00Z"},
            {"workflowExecutionId": "E2", "inputAssetFileKey": "/y.glb", "databaseId": "db",
             "assetId": "a1", "workflowId": "wf", "workflowDatabaseId": "wdb",
             "executionStartDate": "2026-06-15T00:00:00Z"},
        ]
        main_rows = {
            "E1": {"executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
                   "workflow_execution_arn": "arn:e1", "executionStatus": "SUCCEEDED",
                   "executionStartDate": "2026-06-16T00:00:00Z",
                   "executionStopDate": "2026-06-16T00:05:00Z"},
            # E2 intentionally absent -> fetch_main_row returns None.
        }
        items = le.build_execution_items(
            input_items=input_items,
            fetch_main_row=lambda eid: main_rows.get(eid),
            describe_execution=lambda arn: None,
            persist_main_row=lambda item: None,
            workflow_id_filter="", workflow_database_id="",
        )
        # E2 is skipped (no main row); only E1 surfaces, no crash.
        assert len(items) == 1
        assert items[0]["executionId"] == "E1"

    def _running_input(self):
        return [{
            "workflowExecutionId": "E1", "inputAssetFileKey": "/x.glb",
            "databaseId": "db", "assetId": "a1",
            "workflowId": "wf", "workflowDatabaseId": "wdb",
            "executionStartDate": "2026-06-16T00:00:00Z",
        }]

    def test_terminal_row_never_polls_sfn(self):
        # A row with a stop date is terminal -> describe_execution must not be called.
        main_row = {"executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
                    "workflow_execution_arn": "arn:e1", "executionStatus": "SUCCEEDED",
                    "executionStartDate": "2026-06-16T00:00:00Z",
                    "executionStopDate": "2026-06-16T00:05:00Z"}
        describe = MagicMock()
        persist = MagicMock()
        items = le.build_execution_items(
            input_items=self._running_input(),
            fetch_main_row=lambda eid: main_row,
            describe_execution=describe,
            persist_main_row=persist,
            workflow_id_filter="", workflow_database_id="",
        )
        describe.assert_not_called()
        persist.assert_not_called()
        assert items[0]["executionStatus"] == "SUCCEEDED"

    def test_recent_sync_skips_poll(self):
        # No stop date, but lastSfnSyncCheckDate is "now" -> within the 30s window, so
        # do NOT poll SFN; serve the table's current status.
        main_row = {"executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
                    "workflow_execution_arn": "arn:e1", "executionStatus": "RUNNING",
                    "executionStartDate": "2026-06-16T00:00:00Z", "executionStopDate": "",
                    "lastSfnSyncCheckDate": le.er.iso_now()}
        describe = MagicMock()
        persist = MagicMock()
        items = le.build_execution_items(
            input_items=self._running_input(),
            fetch_main_row=lambda eid: main_row,
            describe_execution=describe,
            persist_main_row=persist,
            workflow_id_filter="", workflow_database_id="",
        )
        describe.assert_not_called()
        persist.assert_not_called()
        assert items[0]["executionStatus"] == "RUNNING" and items[0]["stopDate"] == ""

    def test_stale_sync_polls_and_stamps_sync_time(self):
        # No stop date and an old sync time -> poll SFN; still running, so only the
        # sync-check time is stamped + persisted (no stop date yet).
        main_row = {"executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
                    "workflow_execution_arn": "arn:e1", "executionStatus": "RUNNING",
                    "executionStartDate": "2026-06-16T00:00:00Z", "executionStopDate": "",
                    "lastSfnSyncCheckDate": "2000-01-01T00:00:00Z"}
        describe = MagicMock(return_value={"status": "RUNNING", "startDate": None, "stopDate": None})
        persist = MagicMock()
        items = le.build_execution_items(
            input_items=self._running_input(),
            fetch_main_row=lambda eid: main_row,
            describe_execution=describe,
            persist_main_row=persist,
            workflow_id_filter="", workflow_database_id="",
        )
        describe.assert_called_once()
        persist.assert_called_once()
        assert main_row["lastSfnSyncCheckDate"] != "2000-01-01T00:00:00Z"  # re-stamped
        assert items[0]["stopDate"] == ""  # still running

    def test_non_success_terminal_pulls_error_and_log(self):
        # A poll observing ABORTED captures both the error message and the full log,
        # persists them onto the row, and surfaces them in the wire item.
        main_row = {"executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
                    "workflow_execution_arn": "arn:e1", "executionStatus": "RUNNING",
                    "executionStartDate": "2026-06-16T00:00:00Z", "executionStopDate": "",
                    "lastSfnSyncCheckDate": ""}
        sfn_response = {"status": "ABORTED",
                        "startDate": datetime(2026, 6, 16, 0, 0, 0),
                        "stopDate": datetime(2026, 6, 16, 0, 3, 0),
                        "error": "States.Runtime", "cause": "aborted by user"}
        persist = MagicMock()
        fetch = MagicMock(return_value=("States.Runtime: aborted by user", "log line 1\nlog line 2"))
        items = le.build_execution_items(
            input_items=self._running_input(),
            fetch_main_row=lambda eid: main_row,
            describe_execution=lambda arn: sfn_response,
            persist_main_row=persist,
            workflow_id_filter="", workflow_database_id="",
            fetch_execution_log_and_error=fetch,
        )
        fetch.assert_called_once()
        it = items[0]
        assert it["executionStatus"] == "ABORTED"
        assert it["stopDate"] == "2026-06-16T00:03:00Z"
        assert it["executionError"] == "States.Runtime: aborted by user"
        assert it["executionLog"] == "log line 1\nlog line 2"
        # Persisted onto the row too.
        assert main_row["executionError"] == "States.Runtime: aborted by user"
        assert main_row["executionLog"] == "log line 1\nlog line 2"

    def test_success_terminal_pulls_log_but_not_error(self):
        # A SUCCEEDED poll captures the full execution log (always) but stores NO error
        # message (executionError stays empty for a successful run).
        main_row = {"executionId": "E1", "workflowId": "wf", "workflowDatabaseId": "wdb",
                    "workflow_execution_arn": "arn:e1", "executionStatus": "RUNNING",
                    "executionStartDate": "2026-06-16T00:00:00Z", "executionStopDate": "",
                    "lastSfnSyncCheckDate": ""}
        sfn_response = {"status": "SUCCEEDED",
                        "startDate": datetime(2026, 6, 16, 0, 0, 0),
                        "stopDate": datetime(2026, 6, 16, 0, 5, 0)}
        # The callback returns an error_text too, but the caller must ignore it on success.
        fetch = MagicMock(return_value=("should-be-ignored", "ok log line"))
        items = le.build_execution_items(
            input_items=self._running_input(),
            fetch_main_row=lambda eid: main_row,
            describe_execution=lambda arn: sfn_response,
            persist_main_row=MagicMock(),
            workflow_id_filter="", workflow_database_id="",
            fetch_execution_log_and_error=fetch,
        )
        fetch.assert_called_once()  # log is pulled even on success
        it = items[0]
        assert it["executionStatus"] == "SUCCEEDED"
        assert it["executionLog"] == "ok log line"
        assert it["executionError"] == ""  # no error message stored for a success
        # The success path never writes an error message onto the row.
        assert main_row.get("executionError", "") == ""
        # ...but it does persist the captured log.
        assert main_row["executionLog"] == "ok log line"
