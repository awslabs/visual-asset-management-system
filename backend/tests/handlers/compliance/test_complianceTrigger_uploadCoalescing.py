# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Consecutive upload completions on one asset coalesce into one evaluation over the final file set.

`vamscli` and the web uploader send an upload of more than 50 files as several upload requests, in
parallel, and each request's completion stamps `lastUploadAt` on the asset row and raises a stream
MODIFY. Evaluating each MODIFY at once judged the asset on the first request's files only (a 100-file
upload: two evaluations, the first an `evaluation_error` for a rule input in the second request). Now
an upload completion's MODIFY is held back on the trigger queue until it is
UPLOAD_COMPLETION_SETTLE_SECONDS old, and then evaluated only when the row records no later completion;
the upload API's objects of a still-settling completion leave the evaluation to it. Clients do not
change. An asset-version revert, a direct bucket write and a single-request upload keep their single
evaluation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.common.test_indexerEvents import indexer_message, s3_event_record
from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA
from backend.tests.handlers.compliance.test_complianceTrigger import (
    AUTO_EVAL_DB, _info_messages, _run, _s3_head, _sns_event, _stream_message)
from handlers.compliance import complianceTrigger as trigger

MOD = "handlers.compliance.complianceTrigger"
QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/ComplianceTriggerQueue"
# The settle window the trigger holds an upload completion back for (pinned below).
SETTLE = 60

T0 = datetime(2026, 10, 26, 20, 30, 17, tzinfo=timezone.utc)  # first request's completion
FIRST = T0
SECOND = T0 + timedelta(seconds=6)                             # second request's, 6 s later
PREVIOUS_EVALUATION = "2026-10-26T20:00:00+00:00"


def _iso(instant):
    return instant.isoformat(timespec="microseconds")


def _completion(at):
    """The stream MODIFY an upload request's completion raises: `lastChangeSource: upload`, with
    `lastChangeAt` and `lastUploadAt` the completion's instant."""
    message = _stream_message(change_source="upload", change_at=_iso(at))
    message["dynamodb"]["NewImage"]["lastUploadAt"] = {"S": _iso(at)}
    return message


def _row(last_upload_at, **extra):
    row = {"databaseId": DB, "assetId": ASSET, "lastUploadAt": _iso(last_upload_at),
           "lastChangeSource": "upload", "lastChangeAt": _iso(last_upload_at)}
    row.update(extra)
    return row


def _record(last_evaluated_at=PREVIOUS_EVALUATION):
    return {"schemaName": SCHEMA, "schemaSource": "database", "lastEvaluationId": "eval-x",
            "lastEvaluatedAt": last_evaluated_at, "lastEvaluationStatus": "completed"}


def _event(*messages, receive_count=1):
    event = _sns_event(*messages)
    for record in event["Records"]:
        record["attributes"]["ApproximateReceiveCount"] = str(receive_count)
    return event


def _object_event(name, written_at):
    return indexer_message(s3_event_record(
        f"{ASSET}/{name}", event_time=written_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")))


def deliver(event, now, row, record=None, queue_url=QUEUE_URL, sqs=None, head="upload"):
    """One trigger invocation at `now` over `event`, the asset row reading as `row`."""
    sqs = sqs if sqs is not None else MagicMock(name="sqs_client")
    with patch(f"{MOD}._now", return_value=now, create=True), \
            patch(f"{MOD}.trigger_queue_url", queue_url, create=True), \
            patch(f"{MOD}.sqs_client", sqs, create=True):
        mocks = _run(event, database_item=AUTO_EVAL_DB, compliance_record=record or _record(),
                     asset_rows=[row], asset_item=row, head=_s3_head(head))
    mocks["sqs"] = sqs
    mocks["failed"] = [f["itemIdentifier"] for f in mocks["response"]["batchItemFailures"]]
    return mocks


def _deferred_for(mocks):
    return [call.kwargs["VisibilityTimeout"]
            for call in mocks["sqs"].change_message_visibility.call_args_list]


@pytest.mark.unit
class TestAnUploadInSeveralRequestsIsEvaluatedOnce:

    def test_a_100_file_upload_in_two_requests_yields_one_evaluation_over_both(self):
        evaluations = []

        def count(mocks):
            evaluations.extend(c.args for c in mocks["run_evaluation"].call_args_list)
            return mocks

        # 1. The first request completes; its MODIFY arrives 2 s later and is held back.
        m = count(deliver(_event(_completion(FIRST)), FIRST + timedelta(seconds=2), _row(FIRST)))
        assert m["failed"] == ["q-1"]
        assert _deferred_for(m) == [SETTLE - 2]
        assert m["sqs"].change_message_visibility.call_args.kwargs["QueueUrl"] == QUEUE_URL
        # 2. The first request's objects, copied before its stamp, wait for that completion.
        m = count(deliver(_event(_object_event("part-01.txt", FIRST - timedelta(seconds=1))),
                          FIRST + timedelta(seconds=3), _row(FIRST)))
        assert m["failed"] == [] and m["run_evaluation"].call_count == 0
        # 3. The second request completes 6 s after the first; its MODIFY is held back too.
        m = count(deliver(_event(_completion(SECOND)), SECOND + timedelta(seconds=2), _row(SECOND)))
        assert m["failed"] == ["q-1"]
        # 4. The second request's objects wait as well.
        m = count(deliver(_event(_object_event("part.stl", SECOND - timedelta(seconds=1))),
                          SECOND + timedelta(seconds=3), _row(SECOND)))
        assert m["failed"] == []
        # 5. The first completion comes back: the row records the later one, so it steps aside.
        m = count(deliver(_event(_completion(FIRST), receive_count=2),
                          FIRST + timedelta(seconds=SETTLE + 1), _row(SECOND)))
        assert m["failed"] == [] and m["run_evaluation"].call_count == 0
        assert any("followed by a later completion" in line for line in _info_messages(m))
        # 6. The second completion comes back and evaluates, with every file in place.
        m = count(deliver(_event(_completion(SECOND), receive_count=2),
                          SECOND + timedelta(seconds=SETTLE + 1), _row(SECOND)))
        assert m["failed"] == []
        # 7. A late object event after that evaluation started is covered by it.
        started = _iso(SECOND + timedelta(seconds=SETTLE + 1))
        count(deliver(_event(_object_event("part-99.txt", SECOND - timedelta(seconds=1))),
                      SECOND + timedelta(seconds=SETTLE + 5), _row(SECOND), record=_record(started)))
        assert evaluations == [(DB, ASSET, SCHEMA, "SYSTEM_USER")]

    def test_both_completions_in_one_batch_are_held_back_together(self):
        m = deliver(_event(_completion(FIRST), _completion(SECOND)),
                    SECOND + timedelta(seconds=1), _row(SECOND))
        assert m["failed"] == ["q-1", "q-2"]
        assert m["run_evaluation"].call_count == 0

    def test_a_single_request_upload_is_evaluated_once_after_it_settles(self):
        """The 20-file case: one completion, deferred once, then one evaluation."""
        m = deliver(_event(_completion(FIRST)), FIRST + timedelta(seconds=1), _row(FIRST))
        assert m["failed"] == ["q-1"] and m["run_evaluation"].call_count == 0
        m = deliver(_event(_completion(FIRST), receive_count=2),
                    FIRST + timedelta(seconds=SETTLE + 1), _row(FIRST))
        m["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")


@pytest.mark.unit
class TestWhenACompletionIsNotHeldBack:

    def test_a_completion_first_delivered_after_it_settled_is_evaluated_at_once(self):
        m = deliver(_event(_completion(FIRST)), FIRST + timedelta(seconds=SETTLE + 1), _row(FIRST))
        assert _deferred_for(m) == [] and m["failed"] == []
        m["run_evaluation"].assert_called_once()

    def test_a_redelivered_completion_is_never_held_back_again(self):
        m = deliver(_event(_completion(FIRST), receive_count=2), FIRST + timedelta(seconds=5),
                    _row(FIRST))
        assert _deferred_for(m) == []
        m["run_evaluation"].assert_called_once()

    @pytest.mark.parametrize("queue_url, sqs_error", [("", None), (QUEUE_URL, RuntimeError("down"))])
    def test_a_completion_that_cannot_be_held_back_is_evaluated_at_once(self, queue_url, sqs_error):
        sqs = MagicMock(name="sqs_client")
        if sqs_error:
            sqs.change_message_visibility.side_effect = sqs_error
        m = deliver(_event(_completion(FIRST)), FIRST + timedelta(seconds=2), _row(FIRST),
                    queue_url=queue_url, sqs=sqs)
        assert m["failed"] == []
        m["run_evaluation"].assert_called_once()

    def test_an_asset_version_revert_is_evaluated_at_once(self):
        revert = _stream_message(change_source="assetVersion", change_at=_iso(FIRST))
        m = deliver(_event(revert), FIRST + timedelta(seconds=2),
                    _row(FIRST - timedelta(hours=1), lastAssetVersionAt=_iso(FIRST),
                         lastChangeSource="assetVersion"))
        assert _deferred_for(m) == []
        m["run_evaluation"].assert_called_once()

    def test_an_upload_followed_by_a_revert_leaves_the_evaluation_to_the_revert(self):
        m = deliver(_event(_completion(FIRST), receive_count=2),
                    FIRST + timedelta(seconds=SETTLE + 1),
                    _row(FIRST, lastAssetVersionAt=_iso(FIRST + timedelta(seconds=10))))
        m["run_evaluation"].assert_not_called()

    def test_a_direct_bucket_write_is_not_held_back(self):
        m = deliver(_event(_object_event("direct.stl", FIRST + timedelta(seconds=5))),
                    FIRST + timedelta(seconds=7), _row(FIRST), head=None)
        m["run_evaluation"].assert_called_once()

    def test_an_upload_object_of_a_settled_completion_is_judged_by_its_stamp(self):
        m = deliver(_event(_object_event("late.stl", FIRST - timedelta(seconds=1))),
                    FIRST + timedelta(seconds=SETTLE + 1), _row(FIRST))
        m["run_evaluation"].assert_called_once()

    def test_a_workflow_execution_write_back_is_still_skipped(self):
        write_back = _stream_message(change_source="workflowExecution", change_at=_iso(FIRST),
                                     workflow_execution_id="exec-1")
        m = deliver(_event(write_back), FIRST + timedelta(seconds=1), _row(FIRST))
        assert _deferred_for(m) == []
        m["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestTheSettleWindow:

    def test_the_wait_is_what_remains_of_the_window(self):
        with patch(f"{MOD}._now", return_value=FIRST + timedelta(seconds=15)):
            assert trigger.upload_completion_wait_seconds(FIRST) == SETTLE - 15
            assert trigger.upload_completion_wait_seconds(FIRST - timedelta(seconds=SETTLE)) == 0
            assert trigger.upload_completion_wait_seconds(None) == 0

    def test_the_window_spans_the_gap_measured_between_two_requests_of_one_upload(self):
        assert trigger.UPLOAD_COMPLETION_SETTLE_SECONDS == SETTLE
        assert 6 < SETTLE <= 120
