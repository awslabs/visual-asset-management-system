# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The terminal writers and read paths around a run that is still ingesting large outputs (#1123).

- read-path reconcile (listing, details, the status change event): a state machine that SUCCEEDED on
  a definition that does not wait leaves the row RUNNING while its copies run, and is recorded FAILED
  once the maximum wait plus an hour has passed with no report; any other status is adopted as before;
- abort and the error handler stop the run's copy jobs (a warning names a copy left running);
- permanent delete refuses a run whose outputs are still being copied;
- the details response carries the ingestion phase and progress only when present;
- a launch whose workflow is on the older ASL schema redeploys first, and starts on its stored version
  when that redeploy fails and the pipelines are unchanged."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.workflows import test_system_pipeline_hooks as hooks

le = hooks.le
heh = hooks.heh
loc = le.loc
EXEC = hooks.EXEC


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ingesting_row(started_hours_ago=1):
    row = hooks._row(trigger="Manual")
    row["executionPhase"] = "INGESTING_OUTPUTS"
    row["outputIngestionStartedDate"] = _iso(datetime.now(timezone.utc) - timedelta(hours=started_hours_ago))
    row["outputIngestion"] = {"filesTotal": 2, "filesDone": 1, "bytesTotal": 10, "bytesDone": 5}
    return row


def _described(status="SUCCEEDED"):
    now = datetime.now(timezone.utc)
    return {"status": status, "startDate": now - timedelta(hours=2), "stopDate": now}


def _list(row, described):
    persisted = []
    items = le.build_execution_items(
        [{"workflowExecutionId": EXEC}], fetch_main_row=lambda eid: dict(row),
        describe_execution=lambda arn: described, persist_main_row=persisted.append,
        workflow_id_filter="", workflow_database_id="",
        fetch_execution_log_and_error=lambda eid, item, d: (
            ": ".join(p for p in (d.get("error", ""), d.get("cause", "")) if p), ""))
    return items[0], persisted[0]


@pytest.mark.unit
class TestListReconcileHold:
    def test_a_succeeded_state_machine_of_an_ingesting_run_is_not_adopted(self):
        item, persisted = _list(_ingesting_row(), _described())
        assert item["executionStatus"] == "RUNNING"
        assert "executionStatus" not in persisted and "executionStopDate" not in persisted

    def test_the_hold_expires_after_the_maximum_wait(self):
        item, persisted = _list(_ingesting_row(started_hours_ago=50), _described())
        assert item["executionStatus"] == "FAILED"
        assert loc.REASON_NOT_REPORTED in persisted["executionError"]

    def test_a_failed_state_machine_is_adopted_as_before(self):
        item, _persisted = _list(_ingesting_row(), _described("ABORTED"))
        assert item["executionStatus"] == "ABORTED"

    def test_a_run_without_the_phase_is_reconciled_as_before(self):
        item, _persisted = _list(hooks._row(trigger="Manual"), _described())
        assert item["executionStatus"] == "SUCCEEDED"


@pytest.mark.unit
@pytest.mark.aws
class TestDetailsReconcileHold:
    def test_the_details_reconcile_keeps_an_ingesting_run_running(self):
        row = _ingesting_row()
        with hooks._main_table(row) as table, \
                patch.object(le.dynamodb, "Table", return_value=table), \
                patch.object(le.sfn, "describe_execution", return_value=_described()):
            le._reconcile_main_status(EXEC, dict(row), force=True)
            stored = hooks._stored(table)
        assert stored["executionStatus"] == "RUNNING"
        assert not stored.get("executionStopDate")


@pytest.mark.unit
class TestDetailsFields:
    def test_phase_and_progress_are_reported_when_present(self):
        files = [{"relativeKey": "big.e57", "status": "RUNNING", "bytesTotal": 10, "partsTotal": 4,
                  "partsDone": 1, "error": ""}]
        with patch.object(le, "_ingestion_file_progress", return_value=(files, False)) as progress:
            fields = le._output_ingestion_fields(_ingesting_row())
        progress.assert_called_once_with(EXEC)
        assert fields == {"executionPhase": "INGESTING_OUTPUTS",
                          "outputIngestion": {"filesTotal": 2, "filesDone": 1, "bytesTotal": 10,
                                              "bytesDone": 5, "files": files,
                                              "filesTruncated": False}}

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"])
    def test_a_terminal_run_reports_no_phase_even_if_its_row_still_has_one(self, status):
        """[in-dev D4] a terminal row never reports executionPhase / outputIngestion."""
        row = _ingesting_row()
        row["executionStatus"] = status
        with patch.object(le, "_ingestion_file_progress") as progress:
            assert le._output_ingestion_fields(row) == {}
        progress.assert_not_called()

    def test_nothing_is_added_for_other_runs(self):
        assert le._output_ingestion_fields(hooks._row()) == {}

    def test_each_copy_reports_its_status_and_part_progress_bounded(self):
        rows = [{"jobId": f"{EXEC}#{i:03d}", "relativeKey": f"out/f{i:03d}.bin", "status": "RUNNING",
                 "sourceSize": 5, "partCount": 4, "partsCopied": 2} for i in range(le.MAX_INGESTION_FILES_RETURNED + 1)]
        rows[0].update({"status": "FAILED", "error": loc.REASON_SOURCE_CHANGED, "partsCopied": 9})
        rows[1].update({"status": "SUCCEEDED", "error": "leftover"})
        with patch.object(le, "get_table_name", return_value="t-copy-jobs"), \
                patch.object(le.dynamodb, "Table", return_value=MagicMock()), \
                patch.object(le.loc, "jobs_for_execution", return_value=list(reversed(rows))):
            files, truncated = le._ingestion_file_progress(EXEC)
        assert truncated is True
        assert len(files) == le.MAX_INGESTION_FILES_RETURNED
        assert files[0] == {"relativeKey": "out/f000.bin", "status": "FAILED", "bytesTotal": 5,
                            "partsTotal": 4, "partsDone": 4, "error": loc.REASON_SOURCE_CHANGED}
        assert files[1]["error"] == ""  # only a failed or cancelled copy names a reason
        assert files[2]["partsDone"] == 2

    def test_a_progress_read_failure_returns_no_files(self):
        with patch.object(le, "get_table_name", side_effect=KeyError("x")):
            assert le._ingestion_file_progress(EXEC) == ([], False)

    def test_an_ingested_output_file_reports_its_outcome(self):
        out = le._scrub_output_file({"relativeFilePath": "a", "fileType": "file",
                                     "ingestionStatus": "SUCCEEDED"})
        assert out["ingestionStatus"] == "SUCCEEDED"
        assert "ingestionStatus" not in le._scrub_output_file({"relativeFilePath": "a", "fileType": "file"})


@pytest.mark.unit
@pytest.mark.aws
class TestAbortStopsCopies:
    def test_an_abort_cancels_the_runs_copy_jobs(self):
        with hooks._main_table(_ingesting_row()) as table, hooks._callback():
            with hooks._abort(table, [_ingesting_row()]) as drive, \
                    patch.object(le, "_cancel_output_copies", return_value=[]) as cancel:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert hooks._stored(table)["executionStatus"] == "ABORTED"
        cancel.assert_called_once_with(EXEC)

    def test_a_copy_left_running_is_reported(self):
        warning = "The copy of output 'big.e57' could not be stopped and may still be running."
        with hooks._main_table(_ingesting_row()) as table, hooks._callback():
            with hooks._abort(table, [_ingesting_row()]) as drive, \
                    patch.object(le, "_cancel_output_copies", return_value=[warning]):
                response = drive["single"](EXEC)
        assert warning in response["body"]

    def test_cancel_never_raises(self):
        with patch.object(le, "get_table_name", side_effect=KeyError("x")):
            assert le._cancel_output_copies(EXEC) == []


def _assert_phase_cleared(stored, status):
    assert stored["executionStatus"] == status
    assert "executionPhase" not in stored
    assert "outputIngestion" not in stored


@pytest.mark.unit
@pytest.mark.aws
class TestTerminalWritersClearThePhase:
    """[in-dev D4] every writer of a terminal status removes executionPhase and outputIngestion from
    the stored row (before the fix only the finalize write removed the phase, and none removed the
    counters)."""

    def test_abort(self):
        with hooks._main_table(_ingesting_row()) as table, hooks._callback():
            with hooks._abort(table, [_ingesting_row()]) as drive, \
                    patch.object(le, "_cancel_output_copies", return_value=[]):
                assert drive["single"](EXEC)["statusCode"] == 200
            _assert_phase_cleared(hooks._stored(table), "ABORTED")

    def test_group_abort(self):
        with hooks._main_table(_ingesting_row()) as table, hooks._callback():
            with hooks._abort(table, [_ingesting_row()]) as drive, \
                    patch.object(le, "_cancel_output_copies", return_value=[]):
                assert drive["group"]()["statusCode"] == 200
            _assert_phase_cleared(hooks._stored(table), "ABORTED")

    @pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED_OUT"])
    def test_status_change_reconcile_of_a_run_stopped_outside_vams(self, status):
        row = _ingesting_row()
        with hooks._main_table(row) as table, hooks._callback(), \
                patch.object(le.dynamodb, "Table", return_value=table), \
                patch.object(le.sfn, "describe_execution", return_value=_described(status)), \
                patch.object(le, "_cancel_output_copies", return_value=[]):
            le._reconcile_main_status(EXEC, dict(row), force=True)
            _assert_phase_cleared(hooks._stored(table), status)

    def test_details_read_path_expiry(self):
        row = _ingesting_row(started_hours_ago=50)
        with hooks._main_table(row) as table, hooks._callback(), \
                patch.object(le.dynamodb, "Table", return_value=table), \
                patch.object(le.sfn, "describe_execution", return_value=_described()), \
                patch.object(le, "_cancel_output_copies", return_value=[]):
            le._reconcile_main_status(EXEC, dict(row), force=True)
            _assert_phase_cleared(hooks._stored(table), "FAILED")

    def test_listing_read_path_expiry(self):
        row = _ingesting_row(started_hours_ago=50)
        with hooks._main_table(row) as table, hooks._callback():
            _item, persisted = _list(row, _described())
            le._persist_list_reconcile(table, persisted, lambda eid: dict(row))
            _assert_phase_cleared(hooks._stored(table), "FAILED")

    def test_error_handler_terminal_write(self):
        with hooks._main_table(_ingesting_row()) as table:
            dynamo = MagicMock()
            dynamo.Table.return_value = table
            assert le.eo.finalize_main_row(dynamo, "t", EXEC, hooks._row()["workflowDatabaseId"],
                                           hooks._row()["workflowId"], "FAILED", "2026-11-06T00:00:00Z",
                                           execution_error="OutputIngestionTimeout")
            _assert_phase_cleared(hooks._stored(table), "FAILED")


def _status_event(row, status):
    return {"source": le.SFN_STATUS_CHANGE_SOURCE, "detail-type": le.SFN_STATUS_CHANGE_DETAIL_TYPE,
            "detail": {"executionArn": row["workflow_execution_arn"], "name": EXEC, "status": status}}


@pytest.mark.unit
class TestStoppedOutsideVamsStopsCopies:
    """A run stopped outside VAMS while it waits on its copies: the status change event (a push, not a
    read) stops them, so nothing keeps copying into the asset of a run that has ended."""

    def _push(self, row, status):
        row.setdefault("workflow_execution_arn", f"arn:aws:states:us-east-1:111111111111:execution:vams-x:{EXEC}")
        with patch.object(le, "get_execution_main_row", return_value=dict(row)), \
                patch.object(le, "_reconcile_main_status"), \
                patch.object(le, "_cancel_output_copies", return_value=[]) as cancel:
            result = le.handle_sfn_status_change(_status_event(row, status))
        return result, cancel

    @pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED_OUT"])
    def test_an_ingesting_run_stopped_outside_vams_cancels_its_copies(self, status):
        result, cancel = self._push(_ingesting_row(), status)
        assert result == {"handled": True, "reconciled": True}
        cancel.assert_called_once_with(EXEC)

    def test_a_succeeded_event_leaves_the_copies_running(self):
        # A run on a definition that does not wait SUCCEEDED its state machine while copying.
        _result, cancel = self._push(_ingesting_row(), "SUCCEEDED")
        cancel.assert_not_called()

    def test_a_run_without_large_outputs_is_unchanged(self):
        _result, cancel = self._push(hooks._row(trigger="Manual"), "ABORTED")
        cancel.assert_not_called()


@pytest.mark.unit
class TestErrorHandlerStopsCopies:
    def test_the_error_handler_cancels_copy_jobs_and_logs_a_copy_left_running(self):
        warning = "The copy of output 'big.e57' could not be stopped and may still be running."
        logs = MagicMock()
        with patch.object(heh, "_cancel_output_copies", return_value=[warning]) as cancel, \
                patch.object(heh.dynamodb, "Table", side_effect=lambda name: logs
                             if name == heh.pipeline_execution_logs_table else MagicMock()), \
                patch.object(heh, "_get_pipeline_rows", return_value=[
                    {"pipelineExecutionId": "P1", "executionStatus": "RUNNING"}]), \
                patch.object(heh.eo, "mark_inflight_pipelines_terminal", return_value=[]), \
                patch.object(heh, "_fetch_execution_log", return_value=""), \
                patch.object(heh, "_execution_start_date", return_value=""), \
                patch.object(heh, "_refresh_execution_log"), \
                patch.object(heh.el, "release_locks_for_execution"):
            heh.lambda_handler({"body": {"workflowExecutionId": EXEC, "workflowDatabaseId": "wdb",
                                         "workflowId": "wf1"},
                                "errorInfo": {"Error": "OutputIngestionTimeout",
                                              "Cause": "Ingesting output files did not finish within 48 hours."}},
                               MagicMock())
        cancel.assert_called_once_with(EXEC)
        error_log = logs.put_item.call_args.kwargs["Item"]["errorLog"]
        assert warning in error_log

    def test_the_timeout_message_names_the_wait(self):
        message = heh._extract_error_message({
            "Error": "OutputIngestionTimeout",
            "Cause": "Ingesting output files did not finish within 48 hours."})
        assert message == "OutputIngestionTimeout: Ingesting output files did not finish within 48 hours."


@pytest.mark.unit
class TestPermanentDeleteGuard:
    def test_an_ingesting_run_cannot_be_deleted(self):
        row = _ingesting_row()
        with patch.object(le, "get_execution_main_row", return_value=row), \
                patch.object(le, "get_workflow_execution_configuration_row", return_value={}), \
                patch.object(le, "authorize_abort", return_value=(True, "")), \
                patch.object(le.sfn, "describe_execution", return_value=_described()) as describe:
            response = le.permanent_delete_execution({"requestContext": {"http": {}}}, EXEC)
        assert response["statusCode"] == 400
        assert "in progress" in response["body"]
        describe.assert_not_called()  # whatever the state machine reports
