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
import json
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
        row = _ingesting_row()
        with patch.object(le, "_ingestion_file_progress", return_value=(files, False)) as progress:
            fields = le._output_ingestion_fields(row)
        progress.assert_called_once_with(EXEC, row)
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


@pytest.mark.unit
class TestDetailsReadReconcilesEndedCopies:
    """#1123 H2 backstop: a copy execution that ended outside its states (its status change event
    was missed) is handed to the copy engine when the run's details are read, so the run is
    finalized instead of waiting out the maximum ingestion wait."""

    ROWS = [{"jobId": f"{EXEC}#a", "relativeKey": "a.bin", "status": "RUNNING", "executionArn": "x:a"},
            {"jobId": f"{EXEC}#b", "relativeKey": "b.bin", "status": "RUNNING", "executionArn": "x:b"}]

    def _read(self, statuses, function_name="copyEngine", lam=None):
        lam = lam or MagicMock()
        sfn = MagicMock()
        sfn.describe_execution.side_effect = lambda executionArn: statuses[executionArn]
        env = {loc.ENV_ENGINE_FUNCTION_NAME: function_name} if function_name else {}
        with patch.dict(le.os.environ, env), \
                patch.object(le, "get_table_name", return_value="t-copy-jobs"), \
                patch.object(le.dynamodb, "Table", return_value=MagicMock()), \
                patch.object(le.loc, "jobs_for_execution", return_value=[dict(r) for r in self.ROWS]), \
                patch.object(le, "sfn", sfn), patch.object(le, "lambda_client", lam):
            if not function_name:
                le.os.environ.pop(loc.ENV_ENGINE_FUNCTION_NAME, None)
            files, _ = le._ingestion_file_progress(EXEC, _ingesting_row())
        return files, lam, sfn

    def test_an_ended_copy_is_handed_to_the_engine_asynchronously(self):
        files, lam, _sfn = self._read({"x:a": {"status": "FAILED"}, "x:b": {"status": "RUNNING"}})
        assert len(files) == 2
        lam.invoke.assert_called_once()
        kwargs = lam.invoke.call_args.kwargs
        assert kwargs["FunctionName"] == "copyEngine" and kwargs["InvocationType"] == "Event"
        assert json.loads(kwargs["Payload"]) == {"action": "reconcile", "executionArn": "x:a"}

    def test_running_copies_are_not_handed_over(self):
        _files, lam, _sfn = self._read({"x:a": {"status": "RUNNING"}, "x:b": {"status": "RUNNING"}})
        lam.invoke.assert_not_called()

    def test_a_function_without_the_engine_name_checks_nothing(self):
        _files, lam, sfn = self._read({}, function_name="")
        sfn.describe_execution.assert_not_called()
        lam.invoke.assert_not_called()

    def test_a_failed_hand_over_still_returns_the_progress(self):
        failing = MagicMock(**{"invoke.side_effect": RuntimeError("lambda down")})
        files, lam, _sfn = self._read({"x:a": {"status": "FAILED"}, "x:b": {"status": "FAILED"}},
                                      lam=failing)
        assert len(files) == 2
        lam.invoke.assert_called_once()  # the first failure ends the hand-over for this read

    def test_a_read_without_the_run_row_checks_nothing(self):
        sfn = MagicMock()
        with patch.dict(le.os.environ, {loc.ENV_ENGINE_FUNCTION_NAME: "copyEngine"}), \
                patch.object(le, "get_table_name", return_value="t-copy-jobs"), \
                patch.object(le.dynamodb, "Table", return_value=MagicMock()), \
                patch.object(le.loc, "jobs_for_execution", return_value=[dict(r) for r in self.ROWS]), \
                patch.object(le, "sfn", sfn):
            le._ingestion_file_progress(EXEC)
        sfn.describe_execution.assert_not_called()

    def test_finished_copies_cost_no_stamp_and_no_describe(self):
        sfn = MagicMock()
        claim = MagicMock(return_value=True)
        rows = [{**r, "status": "SUCCEEDED"} for r in self.ROWS]
        with patch.dict(le.os.environ, {loc.ENV_ENGINE_FUNCTION_NAME: "copyEngine"}), \
                patch.object(le, "_claim_copy_reconcile_check", claim), patch.object(le, "sfn", sfn):
            le._reconcile_ended_copies(rows, _ingesting_row())
        claim.assert_not_called()
        sfn.describe_execution.assert_not_called()


@pytest.mark.unit
@pytest.mark.aws
class TestDetailsReadCheckInterval:
    """#1123 round-3 L11: the details read checks a run's copy executions at most once per
    COPY_RECONCILE_CHECK_INTERVAL_SECONDS, however many viewers poll it (5 s each): the check is
    claimed on the run's row with a conditional stamp."""

    ROWS = TestDetailsReadReconcilesEndedCopies.ROWS

    def _check(self, table, sfn):
        with patch.dict(le.os.environ, {loc.ENV_ENGINE_FUNCTION_NAME: "copyEngine"}), \
                patch.object(le, "dynamodb", MagicMock(Table=lambda name: table)), \
                patch.object(le, "sfn", sfn), patch.object(le, "lambda_client", MagicMock()):
            le._reconcile_ended_copies([dict(r) for r in self.ROWS], _ingesting_row())

    def test_many_reads_within_the_interval_describe_once(self):
        sfn = MagicMock(**{"describe_execution.return_value": {"status": "RUNNING"}})
        with hooks._main_table(_ingesting_row()) as table:
            for _ in range(10):  # ten viewers or polls within the interval
                self._check(table, sfn)
            stamp = hooks._stored(table)[loc.COPY_RECONCILE_CHECK_ATTRIBUTE]
        assert sfn.describe_execution.call_count == len(self.ROWS)  # one check: each open copy once
        assert stamp

    def test_a_stamp_older_than_the_interval_allows_the_next_check(self):
        sfn = MagicMock(**{"describe_execution.return_value": {"status": "RUNNING"}})
        old = _iso(datetime.now(timezone.utc) - timedelta(seconds=loc.COPY_RECONCILE_CHECK_INTERVAL_SECONDS + 5))
        with hooks._main_table({**_ingesting_row(), loc.COPY_RECONCILE_CHECK_ATTRIBUTE: old}) as table:
            self._check(table, sfn)
            assert hooks._stored(table)[loc.COPY_RECONCILE_CHECK_ATTRIBUTE] > old
        assert sfn.describe_execution.call_count == len(self.ROWS)

    def test_a_recent_stamp_skips_the_check(self):
        sfn = MagicMock()
        recent = _iso(datetime.now(timezone.utc) - timedelta(seconds=5))
        with hooks._main_table({**_ingesting_row(), loc.COPY_RECONCILE_CHECK_ATTRIBUTE: recent}) as table:
            self._check(table, sfn)
        sfn.describe_execution.assert_not_called()

    def test_a_row_that_no_longer_exists_is_not_created_or_checked(self):
        sfn = MagicMock()
        with hooks._main_table() as table:
            self._check(table, sfn)
            assert table.scan()["Items"] == []
        sfn.describe_execution.assert_not_called()

    def test_an_ingested_output_file_reports_its_outcome(self):
        out = le._scrub_output_file({"relativeFilePath": "a", "fileType": "file",
                                     "ingestionStatus": "SUCCEEDED"})
        assert out["ingestionStatus"] == "SUCCEEDED"
        assert "ingestionStatus" not in le._scrub_output_file({"relativeFilePath": "a", "fileType": "file"})


@pytest.mark.unit
@pytest.mark.aws
class TestAbortStopsCopies:
    def test_an_abort_cancels_the_runs_copy_jobs(self):
        with hooks._main_table(_ingesting_row()) as table:
            with hooks._abort(table, [_ingesting_row()]) as drive, \
                    patch.object(le, "_cancel_output_copies", return_value=[]) as cancel:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert hooks._stored(table)["executionStatus"] == "ABORTED"
        cancel.assert_called_once_with(EXEC)

    def test_a_copy_left_running_is_reported(self):
        warning = "The copy of output 'big.e57' could not be stopped and may still be running."
        with hooks._main_table(_ingesting_row()) as table:
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
        with hooks._main_table(_ingesting_row()) as table:
            with hooks._abort(table, [_ingesting_row()]) as drive, \
                    patch.object(le, "_cancel_output_copies", return_value=[]):
                assert drive["single"](EXEC)["statusCode"] == 200
            _assert_phase_cleared(hooks._stored(table), "ABORTED")

    def test_group_abort(self):
        with hooks._main_table(_ingesting_row()) as table:
            with hooks._abort(table, [_ingesting_row()]) as drive, \
                    patch.object(le, "_cancel_output_copies", return_value=[]):
                assert drive["group"]()["statusCode"] == 200
            _assert_phase_cleared(hooks._stored(table), "ABORTED")

    @pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED_OUT"])
    def test_status_change_reconcile_of_a_run_stopped_outside_vams(self, status):
        row = _ingesting_row()
        with hooks._main_table(row) as table, \
                patch.object(le.dynamodb, "Table", return_value=table), \
                patch.object(le.sfn, "describe_execution", return_value=_described(status)), \
                patch.object(le, "_cancel_output_copies", return_value=[]):
            le._reconcile_main_status(EXEC, dict(row), force=True)
            _assert_phase_cleared(hooks._stored(table), status)

    def test_details_read_path_expiry(self):
        row = _ingesting_row(started_hours_ago=50)
        with hooks._main_table(row) as table, \
                patch.object(le.dynamodb, "Table", return_value=table), \
                patch.object(le.sfn, "describe_execution", return_value=_described()), \
                patch.object(le, "_cancel_output_copies", return_value=[]):
            le._reconcile_main_status(EXEC, dict(row), force=True)
            _assert_phase_cleared(hooks._stored(table), "FAILED")

    def test_listing_read_path_expiry(self):
        row = _ingesting_row(started_hours_ago=50)
        with hooks._main_table(row) as table:
            _item, persisted = _list(row, _described())
            # The listing's persist (#1135 removed the callback wrapper around it).
            le._persist_reconciled_main_row(
                table, persisted, le.LIST_RECONCILED_MAIN_ROW_ATTRIBUTES,
                only_if_not_terminal=any(
                    attr in persisted for attr in le.COMPLETION_OWNED_MAIN_ROW_ATTRIBUTES))
            _assert_phase_cleared(hooks._stored(table), "FAILED")

    def test_error_handler_terminal_write(self):
        with hooks._main_table(_ingesting_row()) as table:
            dynamo = MagicMock()
            dynamo.Table.return_value = table
            le.eo.finalize_main_row(dynamo, "t", EXEC, hooks._row()["workflowDatabaseId"],
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

    @pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED_OUT"])
    def test_copies_are_stopped_when_a_read_recorded_the_end_first(self, status):
        """[in-dev D8] A details or list read can record a run stopped in the console as terminal
        before its status event arrives; the read never stops copies and its terminal write removes
        executionPhase, so the event must still stop the copies of a run that was ingesting."""
        row = _ingesting_row()
        row.update({"executionStatus": status, "executionStopDate": _iso(datetime.now(timezone.utc))})
        for attr in ("executionPhase", "outputIngestion"):
            row.pop(attr)
        result, cancel = self._push(row, status)
        assert result == {"handled": True, "reconciled": False}
        cancel.assert_called_once_with(EXEC)

    def test_a_terminal_run_that_never_ingested_is_not_queried(self):
        row = hooks._row(trigger="Manual")
        row.update({"executionStatus": "ABORTED", "executionStopDate": _iso(datetime.now(timezone.utc))})
        _result, cancel = self._push(row, "ABORTED")
        cancel.assert_not_called()

    def test_a_succeeded_run_recorded_first_leaves_its_copies(self):
        row = _ingesting_row()
        row.update({"executionStatus": "SUCCEEDED", "executionStopDate": _iso(datetime.now(timezone.utc))})
        _result, cancel = self._push(row, "SUCCEEDED")
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
