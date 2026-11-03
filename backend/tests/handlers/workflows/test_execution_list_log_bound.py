# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset executions list carries a shortened copy of each row's stored execution log.

A run stores up to 256 KB of log text, and a list page holds up to 200 rows, so listing the stored
text whole could exceed the 6 MB synchronous Lambda response limit. Each listed row carries at most
the newest 16 KB, led by the same marker the stored bound uses; the stored value and the logs
endpoint keep the whole stored log."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

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

from backend.backend.common.workflows import executionLogCapture as elc  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
ROWS = 200
LAMBDA_RESPONSE_LIMIT = 6 * 1024 * 1024
LISTED_MAX = 16 * 1024


def _stored_log(execution_id):
    """A stored log at the 256 KB store bound: the stored marker, then numbered events, newest last."""
    lines = [elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=7)]
    size = len(lines[0]) + 1
    i = 0
    while True:
        line = f'{{"id": "{i:06d}", "type": "TaskStateEntered", "execution": "{execution_id}", "pad": "{"x" * 200}"}}'
        size += len(line) + 1
        if size > elc.STORED_EXECUTION_LOG_MAX_BYTES:
            break
        lines.append(line)
        i += 1
    return "\n".join(lines), i


def _page():
    main_rows, newest = {}, {}
    input_items = []
    for n in range(ROWS):
        execution_id = f"e{n:031d}"
        text, count = _stored_log(execution_id)
        newest[execution_id] = (text.split("\n")[-1], count)
        main_rows[execution_id] = {
            "workflowExecutionId": execution_id, "workflowId": "wf", "workflowDatabaseId": "wdb",
            "executionStatus": "SUCCEEDED", "executionStartDate": "2026-11-03T10:00:00Z",
            "executionStopDate": "2026-11-03T10:05:00Z", "executionError": "",
            "executionLog": text}
        input_items.append({"workflowExecutionId": execution_id, "inputAssetFileKey": "/f.glb",
                            "databaseId": "db", "assetId": "a1"})
    items = le.build_execution_items(
        input_items=input_items, fetch_main_row=main_rows.get, describe_execution=MagicMock(),
        persist_main_row=MagicMock(), workflow_id_filter="", workflow_database_id="")
    return items, main_rows, newest


@pytest.mark.unit
class TestTheListedLogIsBounded:
    def test_a_full_page_of_full_stored_logs_stays_under_the_lambda_response_limit(self):
        items, _, _ = _page()
        assert len(items) == ROWS
        body = json.dumps({"message": {"Items": items, "filterStartDate": "2026-08-05T10:00:00Z"}})
        assert len(body.encode("utf-8")) < LAMBDA_RESPONSE_LIMIT

    def test_each_listed_log_is_at_most_16_kb_and_ends_with_the_newest_events(self):
        items, main_rows, newest = _page()
        for item in items:
            listed = item["executionLog"]
            assert len(listed.encode("utf-8")) <= LISTED_MAX
            last_line, count = newest[item["workflowExecutionId"]]
            assert listed.endswith(last_line)
            listed_lines = listed.split("\n")
            # The marker counts the store's own 7 plus the events the list left out.
            listed_events = len(listed_lines) - 1
            assert listed_lines[0] == elc.EARLIER_EVENTS_OMITTED_MARKER.format(
                count=7 + count - listed_events)

    def test_the_stored_row_keeps_the_whole_log(self):
        _, main_rows, _ = _page()
        for row in main_rows.values():
            assert len(row["executionLog"].encode("utf-8")) > 200 * 1024


@pytest.mark.unit
class TestTheLogsEndpointReturnsTheWholeStoredLog:
    def test_truncated_mode_returns_the_stored_log_unshortened(self):
        execution_id = "e" + "0" * 31
        text, _ = _stored_log(execution_id)
        main = {"workflowExecutionId": execution_id, "workflowId": "wf", "workflowDatabaseId": "wdb",
                "executionLogGroupArn": "arn:aws:logs:us-east-1:000000000000:log-group:/g:*",
                "executionLog": text, "executionError": ""}
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
                patch(f"{MOD}.authorize_execution_access", return_value=(True, "")):
            response = le.get_execution_logs({}, execution_id, {})
        assert response["statusCode"] == 200, response
        message = json.loads(response["body"])["message"]
        assert message["logsSource"] == "stored"
        assert message["executionLog"] == text


@pytest.mark.unit
class TestBoundLogText:
    def test_text_within_the_bound_is_unchanged(self):
        assert elc.bound_log_text("a\nb", 100) == "a\nb"
        assert elc.bound_log_text("", 100) == ""
        assert elc.bound_log_text(None, 100) == ""

    def test_keeps_the_newest_lines_with_a_marker(self):
        lines = [f"event {i:04d} " + "y" * 90 for i in range(100)]
        out = elc.bound_log_text("\n".join(lines), 2048)
        assert len(out.encode("utf-8")) <= 2048
        out_lines = out.split("\n")
        assert out_lines[-1] == lines[-1]
        assert out_lines[1:] == lines[100 - (len(out_lines) - 1):]
        assert out_lines[0] == elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=100 - (len(out_lines) - 1))

    def test_keeps_the_read_stopped_marker_at_the_end(self):
        lines = ["z" * 100] * 50 + [elc.READ_STOPPED_MARKER]
        out = elc.bound_log_text("\n".join(lines), 2048)
        assert out.endswith(elc.READ_STOPPED_MARKER)

    def test_a_single_oversized_newest_line_keeps_its_end(self):
        line = "start-" + "é" * 5000 + "-end"
        out = elc.bound_log_text("old\n" + line, 2048)
        assert len(out.encode("utf-8")) <= 2048
        assert out.endswith("-end")
        assert out.split("\n")[0] == elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=1)
