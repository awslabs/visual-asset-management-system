# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A stored execution log is never served when it holds less than the live log read (#967).

CloudWatch Logs makes a run's events searchable some seconds after they happen (5 to 16 seconds were
seen), and the end-state step and the error handler run as the run ends, so their first read can be
empty (a short failed run) or hold only some events (a short successful run stored 2 of its 12).
Two things keep the served log complete:

- After the completion or failure write, each writer re-reads until the events logged up to its own
  invocation are in, within a bounded wait, and stores that read when it is longer. The wait comes
  after the status write, so a run is not shown as finished any later.
- The truncated logs endpoint reads the live log too and serves whichever copy holds more of the
  log's lines, so a stored copy is served only when the live read is empty, unavailable or shorter.
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")

from backend.backend.common.workflows import executionLogCapture as elc  # noqa: E402
from backend.backend.common.workflows import executionOutputs as eo  # noqa: E402
from tests.handlers.workflows.test_processOutput_execution_status_file import po  # noqa: E402
from tests.handlers.workflows.test_reconcile_and_abort_races import le  # noqa: E402
from tests.handlers.workflows.test_error_handler_deadline_stop import heh  # noqa: E402

EXEC = "e4000000000000000000000000000004"
GROUP_ARN = "arn:aws:logs:us-east-1:111122223333:log-group:/vams/workflows:*"
INVOKED_MS = 1_800_000_000_000
TWELVE = [f"event {i} {EXEC}" for i in range(12)]


def _event(message, timestamp):
    return {"message": message, "logStreamName": "s", "timestamp": timestamp}


def _reads(*reads):
    """A logs client whose successive READS (one filter_log_events call each, no nextToken) serve
    ``reads`` in order; each is a list of ``(message, timestamp)``. The last read repeats."""
    client = MagicMock()
    calls = []

    def _filter(**kwargs):
        calls.append(kwargs)
        events = reads[min(len(calls), len(reads)) - 1]
        return {"events": [_event(m, t) for m, t in events]}

    client.filter_log_events.side_effect = _filter
    return client, calls


def _ingested(count, newest_ms):
    """The first ``count`` of the twelve events, the last of them at ``newest_ms``."""
    return [(TWELVE[i], newest_ms - (count - 1 - i) * 100) for i in range(count)]


class _Sleeps:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


@pytest.mark.unit
class TestHeldLogLines:
    def test_markers_are_not_counted(self):
        text = "\n".join([elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=40), "a", "b",
                          elc.READ_STOPPED_MARKER])
        assert elc.held_log_lines(text) == 2

    @pytest.mark.parametrize("text,count", [("", 0), (None, 0), ("a", 1), ("a\nb\nc", 3)])
    def test_lines(self, text, count):
        assert elc.held_log_lines(text) == count


@pytest.mark.unit
class TestCaptureSettledExecutionLog:
    def _settle(self, client, reached_ms=INVOKED_MS, budget=30):
        sleeps = _Sleeps()
        text, _stream = elc.capture_settled_execution_log(
            client, "g", EXEC, reached_ms=reached_ms, deadline=time.monotonic() + budget,
            sleep=sleeps)
        return text, sleeps

    def test_an_empty_first_read_waits_for_the_events(self):
        # The failed-run case: the handler ran before the first event was searchable.
        client, calls = _reads([], [], _ingested(12, INVOKED_MS - 500))

        text, sleeps = self._settle(client)

        assert text.split("\n") == TWELVE
        assert len(calls) == 3
        assert sleeps.calls == [elc.SETTLE_INTERVAL_SECONDS] * 2

    def test_a_partial_read_waits_for_the_rest(self):
        # The successful-run case: 2 of 12 events were searchable at the first read.
        client, _calls = _reads(_ingested(2, INVOKED_MS - 6000), _ingested(12, INVOKED_MS - 300))

        text, _sleeps = self._settle(client)

        assert text.split("\n") == TWELVE

    def test_a_read_that_reaches_the_invocation_does_not_wait(self):
        client, calls = _reads(_ingested(12, INVOKED_MS - elc.SETTLE_REACHED_TOLERANCE_MS))

        text, sleeps = self._settle(client)

        assert text.split("\n") == TWELVE
        assert len(calls) == 1
        assert sleeps.calls == []

    def test_a_read_that_stops_growing_settles_after_the_quiet_reads(self):
        # A cold start leaves the newest event further back than the tolerance; once re-reads stop
        # adding events the read is taken as complete.
        client, calls = _reads(_ingested(12, INVOKED_MS - 10_000))

        text, sleeps = self._settle(client)

        assert text.split("\n") == TWELVE
        assert len(calls) == 1 + elc.SETTLE_QUIET_READS
        assert len(sleeps.calls) == elc.SETTLE_QUIET_READS

    def test_without_an_invocation_time_the_quiet_reads_decide(self):
        client, calls = _reads(_ingested(5, 1), _ingested(12, 2))

        text, _sleeps = self._settle(client, reached_ms=None)

        assert text.split("\n") == TWELVE
        assert len(calls) == 2 + elc.SETTLE_QUIET_READS

    def test_the_wait_ends_at_the_deadline(self):
        client, calls = _reads([])
        clock = [0.0]

        def _sleep(seconds):
            clock[0] += seconds

        with patch.object(elc.time, "monotonic", side_effect=lambda: clock[0]):
            text, _ = elc.capture_settled_execution_log(
                client, "g", EXEC, reached_ms=INVOKED_MS, deadline=20, sleep=_sleep)

        assert text == ""
        assert clock[0] < 20
        assert len(calls) == 1 + int((20 - 1e-9) // elc.SETTLE_INTERVAL_SECONDS)

    def test_no_wait_starts_past_the_deadline(self):
        client, calls = _reads([])

        text, sleeps = self._settle(client, budget=elc.SETTLE_INTERVAL_SECONDS - 0.5)

        assert (text, sleeps.calls, len(calls)) == ("", [], 1)

    def test_a_failed_re_read_keeps_the_read_before_it(self):
        client = MagicMock()
        client.filter_log_events.side_effect = [
            {"events": [_event(TWELVE[0], INVOKED_MS - 9000)]}, RuntimeError("throttled")]

        text, _ = self._settle(client)

        assert text == TWELVE[0]

    def test_a_shorter_re_read_does_not_replace_the_read(self):
        client, _ = _reads(_ingested(3, INVOKED_MS - 9000), _ingested(1, INVOKED_MS - 9200))

        text, _ = self._settle(client)

        assert text.split("\n") == TWELVE[:3]

    def test_a_re_read_reaching_a_newer_event_replaces_a_read_at_the_bound(self):
        # Over the stored bound newer events push older lines out, so the newer read can hold fewer.
        big = "x" * 1000
        first = [(f"old {i} {big}", INVOKED_MS - 20_000 + i) for i in range(30)]
        second = first[10:] + [(f"new {big} {'y' * 4000}", INVOKED_MS - 100)]
        client, _ = _reads(first, second)

        sleeps = _Sleeps()
        text, _ = elc.capture_settled_execution_log(
            client, "g", EXEC, reached_ms=INVOKED_MS, max_bytes=24 * 1024,
            deadline=time.monotonic() + 30, sleep=sleeps)

        assert text.split("\n")[-1].startswith("new ")

    def test_a_failed_first_read_raises(self):
        client = MagicMock()
        client.filter_log_events.side_effect = RuntimeError("denied")

        with pytest.raises(RuntimeError):
            self._settle(client)


def _conditional_failure():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
                       "UpdateItem")


@pytest.fixture
def main_table():
    """A real (moto) main execution table, so the refresh's condition is evaluated as DynamoDB does."""
    with mock_aws():
        dynamo = boto3.resource("dynamodb", region_name="us-east-1")
        dynamo.create_table(
            TableName="t-main",
            KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                       {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "workflowExecutionId", "AttributeType": "S"},
                                  {"AttributeName": "workflowDatabaseId:workflowId",
                                   "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        yield dynamo


KEY = {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": "db:wf"}


def _stored(dynamo):
    return dynamo.Table("t-main").get_item(Key=KEY).get("Item")


@pytest.mark.unit
class TestRefreshMainRowLog:
    def test_a_longer_read_replaces_a_shorter_stored_log(self, main_table):
        main_table.Table("t-main").put_item(Item=dict(KEY, executionStatus="SUCCEEDED", executionLog="a"))

        assert eo.refresh_main_row_log(main_table, "t-main", EXEC, "db", "wf", "a\nb") is True

        assert _stored(main_table) == dict(KEY, executionStatus="SUCCEEDED", executionLog="a\nb")

    def test_an_empty_stored_log_is_replaced(self, main_table):
        main_table.Table("t-main").put_item(Item=dict(KEY, executionLog=""))

        assert eo.refresh_main_row_log(main_table, "t-main", EXEC, "db", "wf", "a") is True
        assert _stored(main_table)["executionLog"] == "a"

    def test_a_row_without_a_log_attribute_gets_one(self, main_table):
        main_table.Table("t-main").put_item(Item=dict(KEY, executionStatus="FAILED"))

        assert eo.refresh_main_row_log(main_table, "t-main", EXEC, "db", "wf", "a") is True
        assert _stored(main_table)["executionLog"] == "a"

    @pytest.mark.parametrize("stored", ["a\nb", "a\nb\nc"])
    def test_a_stored_log_at_least_as_long_is_kept(self, main_table, stored):
        main_table.Table("t-main").put_item(Item=dict(KEY, executionLog=stored))

        assert eo.refresh_main_row_log(main_table, "t-main", EXEC, "db", "wf", "x\ny") is False
        assert _stored(main_table)["executionLog"] == stored

    def test_the_length_is_counted_in_utf8_bytes(self, main_table):
        # Two characters stored, one two-byte character read: the read is not longer.
        main_table.Table("t-main").put_item(Item=dict(KEY, executionLog="ab"))

        assert eo.refresh_main_row_log(main_table, "t-main", EXEC, "db", "wf", "\u00e9") is False
        assert _stored(main_table)["executionLog"] == "ab"

    def test_a_deleted_row_is_not_recreated(self, main_table):
        assert eo.refresh_main_row_log(main_table, "t-main", EXEC, "db", "wf", "a") is False
        assert _stored(main_table) is None

    def test_a_failed_condition_is_the_expected_outcome(self):
        dynamo = MagicMock()
        dynamo.Table.return_value.update_item.side_effect = _conditional_failure()

        assert eo.refresh_main_row_log(dynamo, "t", EXEC, "db", "wf", "a") is False

    def test_any_other_write_error_surfaces(self):
        dynamo = MagicMock()
        dynamo.Table.return_value.update_item.side_effect = RuntimeError("throttled")

        with pytest.raises(RuntimeError):
            eo.refresh_main_row_log(dynamo, "t", EXEC, "db", "wf", "a")

    def test_an_empty_log_writes_nothing(self):
        dynamo = MagicMock()

        assert eo.refresh_main_row_log(dynamo, "t", EXEC, "db", "wf", "") is False
        dynamo.Table.assert_not_called()


def _no_sleep(_seconds):
    return None


@pytest.mark.unit
class TestEndStateRefresh:
    def _refresh(self, client, stored_log):
        dynamo = MagicMock()
        with patch.object(po, "logs_client", client), patch.object(po, "dynamodb", dynamo), \
                patch.object(po, "workflow_execution_log_group_arn", GROUP_ARN), \
                patch.object(po, "_settle_sleep", _no_sleep):
            po._refresh_execution_log(EXEC, "db", "wf", "", stored_log, INVOKED_MS)
        return dynamo.Table.return_value.update_item

    def test_the_settled_read_replaces_a_partial_stored_log(self):
        client, _ = _reads(_ingested(12, INVOKED_MS - 200))

        update = self._refresh(client, "\n".join(TWELVE[:2]))

        assert update.call_args.kwargs["ExpressionAttributeValues"][":lg"].split("\n") == TWELVE

    def test_an_unchanged_read_writes_nothing(self):
        client, _ = _reads(_ingested(12, INVOKED_MS - 200))

        update = self._refresh(client, "\n".join(TWELVE))

        update.assert_not_called()

    def test_a_failed_read_writes_nothing_and_does_not_raise(self):
        client = MagicMock()
        client.filter_log_events.side_effect = RuntimeError("denied")

        self._refresh(client, "").assert_not_called()

    def test_the_handler_refreshes_after_the_completion_write(self):
        order = []
        with patch.object(po, "_fetch_execution_logs", return_value=("partial", "s")), \
                patch.object(po, "_execution_start_date", return_value="2026-11-03T17:44:00Z"), \
                patch.object(po, "_resolve_output_path_keys"), \
                patch.object(po, "record_execution_outputs",
                             side_effect=lambda **kw: order.append(("record", kw["execution_log"]))), \
                patch.object(po, "_refresh_execution_log",
                             side_effect=lambda *a: order.append(("refresh",) + a)):
            po._process_results_only({"workflowExecutionId": EXEC, "endStatePipelineExecutionId": "p1",
                                      "workflowDatabaseId": "db", "workflowId": "wf"}, INVOKED_MS)

        assert order == [("record", "partial"),
                         ("refresh", EXEC, "db", "wf", "2026-11-03T17:44:00Z", "partial", INVOKED_MS)]


@pytest.mark.unit
class TestErrorHandlerRefresh:
    def _refresh(self, client, stored_log):
        dynamo = MagicMock()
        with patch.object(heh, "logs_client", client), patch.object(heh, "dynamodb", dynamo), \
                patch.object(heh, "workflow_execution_log_group_arn", GROUP_ARN), \
                patch.object(heh, "_settle_sleep", _no_sleep):
            heh._refresh_execution_log(EXEC, "db", "wf", "", stored_log, INVOKED_MS)
        return dynamo.Table.return_value.update_item

    def test_a_failed_run_whose_first_read_was_empty_gets_its_log(self):
        client, calls = _reads([], _ingested(12, INVOKED_MS - 300))

        update = self._refresh(client, "")

        assert update.call_args.kwargs["ExpressionAttributeValues"][":lg"].split("\n") == TWELVE
        assert len(calls) == 2

    def test_the_refreshed_log_stays_within_its_share_of_the_main_row(self):
        client, _ = _reads([("x" * 1000 + f" {i}", INVOKED_MS - 400 + i) for i in range(400)])

        update = self._refresh(client, "")

        written = update.call_args.kwargs["ExpressionAttributeValues"][":lg"]
        limit = min(elc.STORED_EXECUTION_LOG_MAX_BYTES, heh.MAX_ERROR_LOG_FIELD_BYTES)
        assert len(written.encode("utf-8")) <= limit

    def test_the_reconcile_refreshes_last_with_its_invocation_time(self):
        with patch.object(heh, "_fetch_execution_log", return_value="") as first, \
                patch.object(heh, "_execution_start_date", return_value="2026-11-03T17:44:00Z"), \
                patch.object(heh, "_get_pipeline_rows", return_value=[]), \
                patch.object(heh, "dynamodb", MagicMock()), \
                patch.object(heh.el, "release_locks_for_execution") as release, \
                patch.object(heh, "_refresh_execution_log",
                             side_effect=lambda *a: release.assert_called_once()) as refresh:
            heh.reconcile_failed_execution(
                {"workflowExecutionId": EXEC, "workflowDatabaseId": "db", "workflowId": "wf"},
                {"Error": "States.TaskFailed"}, INVOKED_MS)

        first.assert_called_once_with(EXEC, "2026-11-03T17:44:00Z")
        refresh.assert_called_once_with(EXEC, "db", "wf", "2026-11-03T17:44:00Z", "", INVOKED_MS)

    def test_the_handler_passes_its_invocation_time(self):
        with patch.object(heh, "reconcile_failed_execution") as reconcile:
            before = int(time.time() * 1000)
            heh.lambda_handler({"body": {"workflowExecutionId": EXEC}}, None)
            after = int(time.time() * 1000)

        invoked_ms = reconcile.call_args.args[2]
        assert before <= invoked_ms <= after


def _logs_response(main, live_events):
    with patch.object(le, "get_execution_main_row", return_value=main), \
            patch.object(le, "authorize_execution_access", return_value=(True, "")), \
            patch.object(le, "_full_log_search",
                         return_value={"events": live_events, "nextToken": None}), \
            patch.object(le, "_sfn_execution_history_events",
                         return_value={"events": [], "nextToken": None}):
        resp = le.get_execution_logs({}, EXEC, {"mode": "truncated"})
    assert resp["statusCode"] == 200
    return json.loads(resp["body"])["message"]


def _main(stored):
    return {"workflowId": "wf", "workflowDatabaseId": "db", "executionLog": stored,
            "executionError": "", "executionLogGroupArn": GROUP_ARN,
            "executionStartDate": "2026-11-03T17:44:00Z"}


def _live(messages):
    return [{"timestamp": i, "message": m} for i, m in enumerate(messages)]


@pytest.mark.unit
class TestTheLogsEndpointServesTheMoreCompleteCopy:
    def test_a_partial_stored_copy_gives_way_to_the_live_log(self):
        # The smoke case: 2 of 12 events stored, all 12 searchable now.
        body = _logs_response(_main("\n".join(TWELVE[:2])), _live(TWELVE))

        assert body["logsSource"] == "live"
        assert body["executionLog"].split("\n") == TWELVE

    def test_a_complete_stored_copy_is_served_when_the_live_read_matches_it(self):
        body = _logs_response(_main("\n".join(TWELVE)), _live(TWELVE))

        assert body["logsSource"] == "stored"
        assert body["executionLog"].split("\n") == TWELVE

    def test_a_stored_copy_longer_than_one_live_page_is_served(self):
        stored = "\n".join(f"line {i}" for i in range(150))

        body = _logs_response(_main(stored), _live([f"line {i}" for i in range(100)]))

        assert body["logsSource"] == "stored"
        assert body["executionLog"] == stored

    def test_the_stored_markers_do_not_count_as_lines(self):
        stored = "\n".join([elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=900), "a",
                            elc.READ_STOPPED_MARKER])

        body = _logs_response(_main(stored), _live(["a", "b"]))

        assert body["logsSource"] == "live"

    def test_an_empty_live_read_serves_the_stored_copy(self):
        body = _logs_response(_main("only stored"), [])

        assert (body["logsSource"], body["executionLog"]) == ("stored", "only stored")

    def test_the_live_log_is_redacted(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlc2lnbmF0dXJl"
        body = _logs_response(_main(""), _live([f'{{"Authorization": "Bearer {token}"}}']))

        assert body["logsSource"] == "live"
        assert token not in body["executionLog"]

    def test_the_response_shape_is_unchanged(self):
        body = _logs_response(_main("a"), _live(["a", "b"]))

        assert set(body) == {"mode", "executionLog", "executionError", "logsSource"}
