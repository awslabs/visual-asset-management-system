# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The log stored on an execution record is the execution's own events, bounded and redacted.

Both stored-log readers -- the end-state step that records a completed run, and the listing that
records a run terminated outside VAMS -- read the shared workflow log group. One FilterLogEvents call
with no start time came back with no events on a group holding older streams, because the call's scan
budget was spent on those streams, so the record kept an empty log. The read is now anchored on the
execution's start date and follows nextToken past empty pages. The stored text is bounded to 256 KB,
keeps the newest events with a marker for those left out, and is redacted before it is stored."""

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import executionLogCapture as elc
from tests.handlers.workflows.test_processOutput_execution_status_file import po
from tests.handlers.workflows.test_reconcile_and_abort_races import le

GROUP_ARN = "arn:aws:logs:us-east-1:111122223333:log-group:/vams/workflows:*"
START = "2026-11-03T10:00:00Z"
START_MS = int(datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _pages(*pages):
    """A logs client whose filter_log_events serves ``pages`` in order: each a list of messages, with
    a nextToken on every page but the last. Records each call's kwargs."""
    client = MagicMock()
    calls = []
    responses = []
    for index, messages in enumerate(pages):
        response = {"events": [{"message": m, "logStreamName": f"stream-{index}"} for m in messages]}
        if index < len(pages) - 1:
            response["nextToken"] = f"token-{index + 1}"
        responses.append(response)

    def _filter(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    client.filter_log_events.side_effect = _filter
    return client, calls


@pytest.mark.unit
class TestCaptureExecutionLog:
    def test_empty_pages_are_followed_to_the_events(self):
        client, calls = _pages([], [], ["first", "second"])

        text, stream = elc.capture_execution_log(client, "/vams/workflows", "E1")

        assert text == "first\nsecond"
        assert stream == "stream-2"
        assert [c.get("nextToken") for c in calls] == [None, "token-1", "token-2"]
        assert all(c["filterPattern"] == '"E1"' for c in calls)

    def test_every_page_is_read_until_the_log_ends(self):
        client, calls = _pages(["a"], ["b"], ["c"])

        text, _ = elc.capture_execution_log(client, "/vams/workflows", "E1")

        assert text == "a\nb\nc"
        assert len(calls) == 3

    def test_the_read_is_anchored_on_the_start_time(self):
        client, calls = _pages(["a"])

        elc.capture_execution_log(client, "g", "E1", start_time_ms=elc.log_capture_start_ms(START))

        assert calls[0]["startTime"] == START_MS - elc.LOG_CAPTURE_START_MARGIN_MS

    @pytest.mark.parametrize("value", ["", None, "not a date", "2026-11-03"])
    def test_an_unusable_start_date_leaves_the_read_unanchored(self, value):
        assert elc.log_capture_start_ms(value) is None
        client, calls = _pages(["a"])
        elc.capture_execution_log(client, "g", "E1", start_time_ms=elc.log_capture_start_ms(value))
        assert "startTime" not in calls[0]

    def test_a_large_log_keeps_the_newest_events_within_the_bound(self):
        line = "x" * 1000
        pages = [[f"{p:03d}-{i:03d}-{line}" for i in range(100)] for p in range(5)]  # ~500 KB
        client, _ = _pages(*pages)

        text, _ = elc.capture_execution_log(client, "g", "E1")

        assert len(text.encode("utf-8")) <= elc.STORED_EXECUTION_LOG_MAX_BYTES
        lines = text.split("\n")
        assert lines[-1].startswith("004-099-")
        omitted = int(lines[0].split(" ", 1)[0].lstrip("["))
        assert lines[0] == elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=omitted)
        assert omitted + len(lines) - 1 == 500

    def test_a_multibyte_event_over_the_bound_is_cut_to_whole_characters(self):
        client, _ = _pages(["\u00e9" * (elc.STORED_EXECUTION_LOG_MAX_BYTES)])

        text, _ = elc.capture_execution_log(client, "g", "E1")

        assert len(text.encode("utf-8")) <= elc.STORED_EXECUTION_LOG_MAX_BYTES
        assert set(text) == {"\u00e9"}

    def test_events_are_redacted_before_they_are_stored(self):
        client, _ = _pages(['{"authorization": "Bearer eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl"}'])

        text, _ = elc.capture_execution_log(client, "g", "E1")

        assert "eyJhbGciOi" not in text
        assert "<redacted>" in text

    def test_the_page_budget_stops_the_read_with_a_marker(self):
        client, calls = _pages(["a"], ["b"], ["c"])

        text, _ = elc.capture_execution_log(client, "g", "E1", max_pages=2)

        assert len(calls) == 2
        assert text == "a\nb\n" + elc.READ_STOPPED_MARKER

    def test_a_passed_deadline_still_makes_the_first_call(self):
        client, calls = _pages(["a"], ["b"])

        text, _ = elc.capture_execution_log(client, "g", "E1", deadline=time.monotonic() - 1)

        assert len(calls) == 1
        assert text == "a\n" + elc.READ_STOPPED_MARKER

    def test_a_later_failed_call_keeps_what_was_read(self):
        client = MagicMock()
        client.filter_log_events.side_effect = [
            {"events": [{"message": "a"}], "nextToken": "t1"}, RuntimeError("throttled")]

        text, _ = elc.capture_execution_log(client, "g", "E1")

        assert text == "a\n" + elc.READ_STOPPED_MARKER

    def test_no_events_stores_nothing(self):
        client, _ = _pages([], [])

        assert elc.capture_execution_log(client, "g", "E1", max_pages=1) == ("", "")

    def test_a_failed_first_call_raises(self):
        client = MagicMock()
        client.filter_log_events.side_effect = RuntimeError("denied")

        with pytest.raises(RuntimeError):
            elc.capture_execution_log(client, "g", "E1")


@pytest.mark.unit
class TestEndStateRecord:
    def test_the_completion_read_follows_empty_pages_from_the_start_date(self):
        client, calls = _pages([], ["done"])
        with patch.object(po, "logs_client", client):
            text, stream = po._fetch_execution_logs(GROUP_ARN, "E1", execution_start_date=START)

        assert (text, stream) == ("done", "stream-1")
        assert calls[0]["logGroupName"] == "/vams/workflows"
        assert calls[0]["startTime"] == START_MS - elc.LOG_CAPTURE_START_MARGIN_MS

    def test_the_start_date_is_read_from_the_main_row(self):
        table = MagicMock()
        table.get_item.return_value = {"Item": {"executionStartDate": START}}
        dynamo = MagicMock()
        dynamo.Table.return_value = table
        with patch.object(po, "dynamodb", dynamo):
            assert po._execution_start_date("E1", "db1", "wf1") == START

        key = table.get_item.call_args.kwargs["Key"]
        assert key["workflowExecutionId"] == "E1"

    def test_an_unreadable_main_row_leaves_the_read_unanchored(self):
        dynamo = MagicMock()
        dynamo.Table.return_value.get_item.side_effect = RuntimeError("throttled")
        with patch.object(po, "dynamodb", dynamo):
            assert po._execution_start_date("E1", "db1", "wf1") == ""

    def test_a_failed_read_records_no_log(self):
        client = MagicMock()
        client.filter_log_events.side_effect = RuntimeError("denied")
        with patch.object(po, "logs_client", client):
            assert po._fetch_execution_logs(GROUP_ARN, "E1") == ("", "")


@pytest.mark.unit
class TestListingReconcileRecord:
    def test_the_reconcile_read_follows_empty_pages_from_the_start_date(self):
        client, calls = _pages([], ["aborted"])
        with patch.object(le, "logs_client", client):
            text = le._fetch_execution_logs(GROUP_ARN, "E1", execution_start_date=START)

        assert text == "aborted"
        assert calls[0]["startTime"] == START_MS - elc.LOG_CAPTURE_START_MARGIN_MS

    def test_the_reconcile_read_is_bounded_by_its_page_budget(self):
        client, calls = _pages(*([[]] * (le.RECONCILE_LOG_CAPTURE_MAX_PAGES + 3)))
        with patch.object(le, "logs_client", client):
            assert le._fetch_execution_logs(GROUP_ARN, "E1") == ""

        assert len(calls) == le.RECONCILE_LOG_CAPTURE_MAX_PAGES

    def test_a_spent_request_deadline_makes_one_call(self):
        client, calls = _pages([], ["late"])
        with patch.object(le, "logs_client", client):
            assert le._fetch_execution_logs(GROUP_ARN, "E1", deadline=time.monotonic() - 1) == ""

        assert len(calls) == 1
