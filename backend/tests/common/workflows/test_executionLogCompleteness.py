# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A stored execution log is never served when it holds less than the live log read (#967).

CloudWatch Logs makes a run's events searchable some seconds after they happen (5 to 16 seconds were
seen), and not in timestamp order: a failed run's last events were searchable about 5 seconds before
its first ones. The end-state step and the error handler run as the run ends, so their first read can
be empty, hold only some events, or hold the newest events without the oldest. Two things keep the
served log complete:

- After the completion or failure write, each writer re-reads until two full re-reads in a row,
  taken after a minimum settle time, find the same number of events with the same total size,
  within a bounded wait, and stores that read when it is longer. A read the wait could not confirm
  is stored with a marker saying it may be incomplete. The wait comes after the status write, so a
  run is not shown as finished any later.
- The truncated logs endpoint reads the live log too and serves whichever copy holds more of the
  log's lines, and the live read when it holds as many as a stored copy marked as possibly
  incomplete, so a stored copy is served only when the live read is empty, unavailable or shorter.
"""

import json
import os
import re
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
WRITE_MS = 1_800_000_000_000
TWELVE = [f"event {i} {EXEC}" for i in range(12)]
# Reads the settling loop makes before two re-reads can agree: the first read, then one every
# SETTLE_INTERVAL_SECONDS up to SETTLE_MIN_SECONDS.
READS_TO_AGREE = 1 + -(-elc.SETTLE_MIN_SECONDS // elc.SETTLE_INTERVAL_SECONDS)
OMITTED = re.compile(r"^\[(\d+) earlier log events are not stored")


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


def _timed(messages):
    """``messages`` timestamped 100 ms apart in the order they were logged."""
    return [(m, WRITE_MS - 5000 + i * 100) for i, m in enumerate(messages)]


class _Clock:
    """A monotonic clock that only the settling read's sleeps move forward."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _settle(client, budget=30, **kwargs):
    clock = _Clock()
    with patch.object(elc.time, "monotonic", clock.monotonic):
        text, _stream = elc.capture_settled_execution_log(
            client, "g", EXEC, deadline=clock.now + budget, sleep=clock.sleep, **kwargs)
    return text, clock


@pytest.mark.unit
class TestHeldLogLines:
    def test_markers_are_not_counted(self):
        text = "\n".join([elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=40), "a", "b",
                          elc.READ_STOPPED_MARKER, elc.POSSIBLY_INCOMPLETE_MARKER])
        assert elc.held_log_lines(text) == 2

    @pytest.mark.parametrize("text,count", [("", 0), (None, 0), ("a", 1), ("a\nb\nc", 3)])
    def test_lines(self, text, count):
        assert elc.held_log_lines(text) == count


@pytest.mark.unit
class TestLogMayBeIncomplete:
    @pytest.mark.parametrize("text", [f"a\n{elc.POSSIBLY_INCOMPLETE_MARKER}",
                                      f"a\n{elc.READ_STOPPED_MARKER}",
                                      f"a\n{elc.READ_STOPPED_MARKER}\n{elc.POSSIBLY_INCOMPLETE_MARKER}"])
    def test_a_marked_log(self, text):
        assert elc.log_may_be_incomplete(text) is True

    @pytest.mark.parametrize("text", ["", None, "a\nb",
                                      f"{elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=3)}\na"])
    def test_an_unmarked_log(self, text):
        assert elc.log_may_be_incomplete(text) is False


@pytest.mark.unit
class TestCaptureSettledExecutionLog:
    def test_an_empty_first_read_waits_for_the_events(self):
        # The failed-run case: the handler ran before the first event was searchable.
        client, _calls = _reads([], [], _timed(TWELVE))

        text, _clock = _settle(client)

        assert text.split("\n") == TWELVE

    def test_later_events_searchable_first_do_not_end_the_wait(self):
        # The re-check smoke case: the run's last 4 events were searchable about 5 s before its
        # first 4, and stayed alone over several reads.
        newest_four = _timed(TWELVE)[8:]
        client, _calls = _reads(newest_four, newest_four, newest_four, newest_four, _timed(TWELVE))

        text, _clock = _settle(client)

        assert text.split("\n") == TWELVE

    def test_two_agreeing_re_reads_before_the_minimum_settle_time_do_not_end_the_wait(self):
        client, calls = _reads(_timed(TWELVE))

        text, clock = _settle(client)

        assert text.split("\n") == TWELVE
        assert len(calls) == READS_TO_AGREE
        assert clock.now - 1000.0 >= elc.SETTLE_MIN_SECONDS

    def test_the_read_ends_on_the_second_of_two_agreeing_re_reads(self):
        reads = [_timed(TWELVE[:n]) for n in (2, 5, 7, 9, 11)] + [_timed(TWELVE)]
        client, calls = _reads(*reads)

        text, _clock = _settle(client)

        assert text.split("\n") == TWELVE
        assert len(calls) == len(reads) + 1

    def test_large_events_searchable_newest_first_are_all_read(self):
        # The re-check smoke case: about 385 KB of large step events, of which only the newest was
        # searchable at first; the stored copy was that one event, with no marker.
        events = _timed([f"step {i} {EXEC} {'p' * 3200}" for i in range(120)])
        client, _calls = _reads(events[-1:], events[-1:], events[-1:], events[-1:], events)

        text, _clock = _settle(client)

        lines = text.split("\n")
        assert len(text.encode("utf-8")) <= elc.STORED_EXECUTION_LOG_MAX_BYTES
        assert lines[-1] == events[-1][0]
        assert int(OMITTED.match(lines[0]).group(1)) == 120 - elc.held_log_lines(text)
        assert elc.held_log_lines(text) > 70
        assert not elc.log_may_be_incomplete(text)

    def test_reads_over_the_bound_are_compared_by_the_events_returned(self):
        # Both reads keep the same newest lines under the bound; only the count of events
        # CloudWatch returned shows the second one is fuller.
        events = _timed([f"step {i} {EXEC} {'p' * 3200}" for i in range(120)])
        later = events[40:]
        client, _calls = _reads(later, later, events)

        text, _clock = _settle(client)

        assert int(OMITTED.match(text.split("\n")[0]).group(1)) == 120 - elc.held_log_lines(text)

    def test_a_single_event_over_the_bound_is_compared_whole(self):
        # The stored copy of an event over the bound is clipped, so reads that differ only past the
        # clip store the same text; they still do not agree.
        head = f"{EXEC} " + "h" * (elc.STORED_EXECUTION_LOG_MAX_BYTES + 1000)
        client, _calls = _reads(*([[(head, WRITE_MS)], [(head + "tail", WRITE_MS)]] * 20))

        text, _clock = _settle(client)

        assert text.endswith("\n" + elc.POSSIBLY_INCOMPLETE_MARKER)
        assert len(text.encode("utf-8")) <= elc.STORED_EXECUTION_LOG_MAX_BYTES

    def test_reads_that_never_agree_store_the_largest_marked_as_possibly_incomplete(self):
        # One more event in every read until the budget runs out.
        reads = [_timed([f"event {i} {EXEC}" for i in range(n + 1)]) for n in range(40)]
        client, calls = _reads(*reads)

        text, clock = _settle(client)

        lines = text.split("\n")
        assert lines[-1] == elc.POSSIBLY_INCOMPLETE_MARKER
        assert elc.held_log_lines(text) == len(calls)
        assert elc.log_may_be_incomplete(text) is True
        assert clock.now < 1000.0 + 30

    def test_at_the_budget_a_shorter_last_read_does_not_replace_the_largest(self):
        many = _timed(TWELVE)
        reads = []
        for _ in range(20):
            reads += [many, _timed(TWELVE[:3])]
        client, _calls = _reads(*reads)

        text, _clock = _settle(client)

        assert text.split("\n") == TWELVE + [elc.POSSIBLY_INCOMPLETE_MARKER]

    def test_a_marked_read_stays_within_the_bound(self):
        reads = [_timed([f"step {i} {EXEC} {'p' * 3200}" for i in range(80 + n)]) for n in range(40)]
        client, _calls = _reads(*reads)

        text, _clock = _settle(client)

        assert text.endswith(elc.POSSIBLY_INCOMPLETE_MARKER)
        assert len(text.encode("utf-8")) <= elc.STORED_EXECUTION_LOG_MAX_BYTES

    def test_reads_stopped_at_their_page_budget_never_agree(self):
        client = MagicMock()
        client.filter_log_events.return_value = {"events": [_event(TWELVE[0], WRITE_MS)],
                                                 "nextToken": "more"}

        text, _clock = _settle(client, max_pages=1)

        assert text.split("\n") == [TWELVE[0], elc.READ_STOPPED_MARKER,
                                    elc.POSSIBLY_INCOMPLETE_MARKER]

    def test_empty_reads_never_agree(self):
        client, calls = _reads([])

        text, clock = _settle(client)

        assert text == ""
        assert clock.now < 1000.0 + 30
        assert len(calls) == 1 + int((30 - 1e-9) // elc.SETTLE_INTERVAL_SECONDS)

    def test_no_wait_starts_past_the_deadline(self):
        client, calls = _reads([])

        text, clock = _settle(client, budget=elc.SETTLE_INTERVAL_SECONDS - 0.5)

        assert (text, clock.sleeps, len(calls)) == ("", [], 1)

    def test_without_a_deadline_the_wait_is_bounded(self):
        client, calls = _reads([])
        clock = _Clock()

        with patch.object(elc.time, "monotonic", clock.monotonic):
            text, _ = elc.capture_settled_execution_log(client, "g", EXEC, sleep=clock.sleep)

        assert text == ""
        assert len(calls) <= elc.SETTLE_MAX_READS
        assert clock.now - 1000.0 <= elc.SETTLE_BUDGET_SECONDS

    def test_a_sleep_that_does_not_pass_time_still_ends(self):
        client, calls = _reads(_timed(TWELVE))

        text, _ = elc.capture_settled_execution_log(client, "g", EXEC, sleep=lambda _s: None)

        assert text.split("\n") == TWELVE + [elc.POSSIBLY_INCOMPLETE_MARKER]
        assert len(calls) == elc.SETTLE_MAX_READS

    def test_a_failed_re_read_keeps_the_largest_read_marked(self):
        client = MagicMock()
        client.filter_log_events.side_effect = [
            {"events": [_event(TWELVE[0], WRITE_MS)]}, RuntimeError("throttled")]

        text, _ = _settle(client)

        assert text.split("\n") == [TWELVE[0], elc.POSSIBLY_INCOMPLETE_MARKER]

    def test_a_failed_first_read_raises(self):
        client = MagicMock()
        client.filter_log_events.side_effect = RuntimeError("denied")

        with pytest.raises(RuntimeError):
            _settle(client)


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


def _handler_refresh(module, client, stored_log):
    """Run ``module._refresh_execution_log`` on a clock its settle sleeps move forward."""
    dynamo = MagicMock()
    clock = _Clock()
    with patch.object(module, "logs_client", client), patch.object(module, "dynamodb", dynamo), \
            patch.object(module, "workflow_execution_log_group_arn", GROUP_ARN), \
            patch.object(module, "_settle_sleep", clock.sleep), \
            patch.object(elc.time, "monotonic", clock.monotonic):
        module._refresh_execution_log(EXEC, "db", "wf", "", stored_log)
    return dynamo.Table.return_value.update_item


def _written(update):
    return update.call_args.kwargs["ExpressionAttributeValues"][":lg"]


@pytest.mark.unit
class TestEndStateRefresh:
    def test_the_settled_read_replaces_a_partial_stored_log(self):
        client, _ = _reads(_timed(TWELVE[:2]), _timed(TWELVE))

        update = _handler_refresh(po, client, "\n".join(TWELVE[:2]))

        assert _written(update).split("\n") == TWELVE

    def test_large_events_searchable_newest_first_are_stored(self):
        events = _timed([f"step {i} {EXEC} {'p' * 3200}" for i in range(120)])
        client, _ = _reads(events[-1:], events[-1:], events[-1:], events)

        update = _handler_refresh(po, client, events[-1][0])

        written = _written(update)
        assert elc.held_log_lines(written) > 70
        assert len(written.encode("utf-8")) <= elc.STORED_EXECUTION_LOG_MAX_BYTES

    def test_an_unchanged_read_writes_nothing(self):
        client, _ = _reads(_timed(TWELVE))

        update = _handler_refresh(po, client, "\n".join(TWELVE))

        update.assert_not_called()

    def test_an_unconfirmed_read_is_stored_marked(self):
        reads = [_timed([f"event {i} {EXEC}" for i in range(n + 1)]) for n in range(40)]
        client, _ = _reads(*reads)

        update = _handler_refresh(po, client, "")

        assert elc.log_may_be_incomplete(_written(update)) is True

    def test_a_failed_read_writes_nothing_and_does_not_raise(self):
        client = MagicMock()
        client.filter_log_events.side_effect = RuntimeError("denied")

        _handler_refresh(po, client, "").assert_not_called()

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
                                      "workflowDatabaseId": "db", "workflowId": "wf"})

        assert order == [("record", "partial"),
                         ("refresh", EXEC, "db", "wf", "2026-11-03T17:44:00Z", "partial")]


@pytest.mark.unit
class TestErrorHandlerRefresh:
    def test_a_failed_run_whose_first_read_was_empty_gets_its_log(self):
        client, _ = _reads([], _timed(TWELVE))

        update = _handler_refresh(heh, client, "")

        assert _written(update).split("\n") == TWELVE

    def test_a_failed_run_whose_first_events_are_searchable_last_gets_them(self):
        # The re-check smoke case: 4 of the 9 events logged before the handler were stored, and the
        # run's first 4 events, searchable about 5 s later, were missing.
        nine = [f"event {i} {EXEC}" for i in range(9)]
        newest = _timed(nine)[4:]
        client, _ = _reads(newest, newest, newest, newest, _timed(nine))

        update = _handler_refresh(heh, client, "")

        assert _written(update).split("\n") == nine

    def test_the_refreshed_log_stays_within_its_share_of_the_main_row(self):
        client, _ = _reads([("x" * 1000 + f" {i}", WRITE_MS - 400 + i) for i in range(400)])

        update = _handler_refresh(heh, client, "")

        limit = min(elc.STORED_EXECUTION_LOG_MAX_BYTES, heh.MAX_ERROR_LOG_FIELD_BYTES)
        assert len(_written(update).encode("utf-8")) <= limit

    def test_an_unconfirmed_read_stays_within_its_share_of_the_main_row(self):
        reads = [[("x" * 1000 + f" {i}", WRITE_MS - 400 + i) for i in range(300 + n)]
                 for n in range(40)]
        client, _ = _reads(*reads)

        update = _handler_refresh(heh, client, "")

        written = _written(update)
        limit = min(elc.STORED_EXECUTION_LOG_MAX_BYTES, heh.MAX_ERROR_LOG_FIELD_BYTES)
        assert written.endswith(elc.POSSIBLY_INCOMPLETE_MARKER)
        assert len(written.encode("utf-8")) <= limit

    def test_the_reconcile_refreshes_last(self):
        with patch.object(heh, "_fetch_execution_log", return_value="") as first, \
                patch.object(heh, "_execution_start_date", return_value="2026-11-03T17:44:00Z"), \
                patch.object(heh, "_get_pipeline_rows", return_value=[]), \
                patch.object(heh, "dynamodb", MagicMock()), \
                patch.object(heh.el, "release_locks_for_execution") as release, \
                patch.object(heh, "_refresh_execution_log",
                             side_effect=lambda *a: release.assert_called_once()) as refresh:
            heh.reconcile_failed_execution(
                {"workflowExecutionId": EXEC, "workflowDatabaseId": "db", "workflowId": "wf"},
                {"Error": "States.TaskFailed"})

        first.assert_called_once_with(EXEC, "2026-11-03T17:44:00Z")
        refresh.assert_called_once_with(EXEC, "db", "wf", "2026-11-03T17:44:00Z", "")


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

    def test_a_stored_copy_marked_possibly_incomplete_gives_way_to_an_equal_live_read(self):
        stored = "\n".join(TWELVE + [elc.POSSIBLY_INCOMPLETE_MARKER])

        body = _logs_response(_main(stored), _live(TWELVE))

        assert body["logsSource"] == "live"
        assert body["executionLog"].split("\n") == TWELVE

    def test_a_stored_copy_marked_possibly_incomplete_is_kept_over_a_shorter_live_read(self):
        stored = "\n".join(TWELVE + [elc.POSSIBLY_INCOMPLETE_MARKER])

        body = _logs_response(_main(stored), _live(TWELVE[:5]))

        assert (body["logsSource"], body["executionLog"]) == ("stored", stored)

    def test_a_stored_copy_marked_possibly_incomplete_is_served_when_the_live_read_is_empty(self):
        stored = "\n".join(TWELVE[:4] + [elc.POSSIBLY_INCOMPLETE_MARKER])

        body = _logs_response(_main(stored), [])

        assert (body["logsSource"], body["executionLog"]) == ("stored", stored)

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
