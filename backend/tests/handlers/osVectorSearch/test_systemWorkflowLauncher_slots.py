# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""systemWorkflowLauncher: at most REINDEX_SLOT_COUNT reindex runs at once, and waiting is not failure.

Before calling executeWorkflow the launcher reads the slot rows. With none free it does not call it: it
sends the message again, delayed, with its wait count raised, and lets the original be deleted, so the
wait never reaches the dead-letter queue. With some free it passes them as candidates, and a 429 (another
launch took them first) is the same wait."""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch.test_systemWorkflowLauncher import MESSAGE, TRIGGER_ROW, _invoke_response, _sqs
from tests.handlers.osVectorSearch.vectorsearch_support import load_handler

NOW = 1_800_000_000


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slot_row(m, index, holder, acquired=NOW - 60, expires=NOW + 3600):
    return {"lockKey": m.el.reindex_slot_key("GLOBAL", "system-genai-metadata", index),
            "workflowExecutionId": holder, "acquiredAt": _iso(acquired), "expiresAt": expires}


@pytest.fixture
def launcher(monkeypatch):
    monkeypatch.setenv("REINDEX_SLOT_COUNT", "3")
    m = load_handler("systemWorkflowLauncher")
    m.lambda_client = MagicMock()
    m.lambda_client.invoke.return_value = _invoke_response(200)
    m.sqs_client = MagicMock()
    m.workflow_triggers_table = MagicMock()
    m.workflow_triggers_table.query.return_value = {"Items": [TRIGGER_ROW]}
    m.workflow_executions_table = MagicMock()
    m.workflow_executions_table.get_item.return_value = {"Item": {"executionStatus": "RUNNING"}}
    m._trigger_row_cache.clear()
    m.dynamodb = MagicMock()
    m.time = MagicMock(time=MagicMock(return_value=NOW))
    m.dynamodb.batch_get_item.return_value = {"Responses": {m.workflow_execution_locks_table_name: []}}
    return m


def _rows(m, rows):
    m.dynamodb.batch_get_item.return_value = {"Responses": {m.workflow_execution_locks_table_name: rows}}


def _run(m, message=None):
    return m.lambda_handler({"Records": [_sqs("m1", message or MESSAGE)]}, MagicMock())


def _cross_call(m):
    return json.loads(m.lambda_client.invoke.call_args.kwargs["Payload"])["lambdaCrossCall"]


@pytest.mark.unit
class TestTheLaunchCarriesFreeSlots:
    def test_free_slots_are_passed_as_candidates_with_the_slot_count(self, launcher):
        _rows(launcher, [_slot_row(launcher, 1, "E-b")])
        response = _run(launcher)
        assert response.get("batchItemFailures", []) == []
        slots = _cross_call(launcher)["reindexSlots"]
        assert slots["count"] == 3 and sorted(slots["candidates"]) == [0, 2]
        launcher.sqs_client.send_message.assert_not_called()

    def test_an_expired_slot_counts_as_free(self, launcher):
        _rows(launcher, [_slot_row(launcher, index, f"E{index}", expires=NOW - 1) for index in range(3)])
        _run(launcher)
        assert sorted(_cross_call(launcher)["reindexSlots"]["candidates"]) == [0, 1, 2]

    def test_an_unprocessed_key_is_read_as_held(self, launcher):
        key = launcher.el.reindex_slot_key("GLOBAL", "system-genai-metadata", 0)
        launcher.dynamodb.batch_get_item.return_value = {
            "Responses": {launcher.workflow_execution_locks_table_name: [_slot_row(launcher, 1, "E-b")]},
            "UnprocessedKeys": {launcher.workflow_execution_locks_table_name: {"Keys": [{"lockKey": key}]}}}
        _run(launcher)
        assert _cross_call(launcher)["reindexSlots"]["candidates"] == [2]


@pytest.mark.unit
class TestWaitingIsNotFailure:
    def test_with_every_slot_held_nothing_is_launched_and_the_message_is_sent_again_delayed(self, launcher):
        _rows(launcher, [_slot_row(launcher, index, f"E{index}") for index in range(3)])
        response = _run(launcher)
        launcher.lambda_client.invoke.assert_not_called()
        assert response.get("batchItemFailures", []) == [], "the original is deleted, not redelivered"
        sent = launcher.sqs_client.send_message.call_args.kwargs
        assert sent["QueueUrl"] == launcher.launch_queue_url and sent["DelaySeconds"] == 30
        assert json.loads(sent["MessageBody"]) == dict(MESSAGE, slotWaits=1)
        assert json.loads(response["body"])["waited"] == 1

    def test_the_delay_doubles_per_wait_up_to_sqs_maximum(self, launcher):
        assert [launcher.wait_delay_seconds(waits) for waits in range(1, 9)] == [
            30, 60, 120, 240, 480, 900, 900, 900]
        _rows(launcher, [_slot_row(launcher, index, f"E{index}") for index in range(3)])
        _run(launcher, dict(MESSAGE, slotWaits=40))
        sent = launcher.sqs_client.send_message.call_args.kwargs
        assert sent["DelaySeconds"] == 900 and json.loads(sent["MessageBody"])["slotWaits"] == 41

    def test_a_429_from_execute_workflow_is_a_wait_too(self, launcher):
        launcher.lambda_client.invoke.return_value = _invoke_response(429)
        response = _run(launcher)
        assert response.get("batchItemFailures", []) == []
        assert json.loads(launcher.sqs_client.send_message.call_args.kwargs["MessageBody"])["slotWaits"] == 1

    def test_a_failed_resend_is_a_batch_item_failure(self, launcher):
        _rows(launcher, [_slot_row(launcher, index, f"E{index}") for index in range(3)])
        launcher.sqs_client.send_message.side_effect = RuntimeError("throttled")
        response = _run(launcher)
        assert [entry["itemIdentifier"] for entry in response["batchItemFailures"]] == ["m1"]

    def test_a_long_backfill_waits_any_number_of_times_without_a_failure(self, launcher):
        _rows(launcher, [_slot_row(launcher, index, f"E{index}") for index in range(3)])
        message = dict(MESSAGE)
        for _ in range(50):
            assert _run(launcher, message).get("batchItemFailures", []) == []
            message = json.loads(launcher.sqs_client.send_message.call_args.kwargs["MessageBody"])
        assert message["slotWaits"] == 50


@pytest.mark.unit
class TestAnEndedHolderIsNominated:
    def test_an_old_held_slot_whose_holder_ended_is_a_candidate(self, launcher):
        old = NOW - launcher.RECLAIM_MIN_AGE_SECONDS - 10
        _rows(launcher, [_slot_row(launcher, 0, "E-done", acquired=old),
                         _slot_row(launcher, 1, "E-run", acquired=old),
                         _slot_row(launcher, 2, "E-young")])
        launcher.workflow_executions_table.get_item.side_effect = lambda **kw: {
            "Item": {"executionStatus": "SUCCEEDED" if kw["Key"]["workflowExecutionId"] == "E-done" else "RUNNING"}}
        _run(launcher)
        assert _cross_call(launcher)["reindexSlots"]["candidates"] == [0]

    def test_a_holder_with_no_record_is_a_candidate_and_a_young_slot_is_not_read(self, launcher):
        _rows(launcher, [_slot_row(launcher, 0, "E-gone", acquired=NOW - 7200),
                         _slot_row(launcher, 1, "E-a"), _slot_row(launcher, 2, "E-b")])
        launcher.workflow_executions_table.get_item.return_value = {}
        _run(launcher)
        assert _cross_call(launcher)["reindexSlots"]["candidates"] == [0]
        assert launcher.workflow_executions_table.get_item.call_count == 1


@pytest.mark.unit
def test_a_launch_past_the_wait_bound_is_left_to_the_dead_letter_queue(launcher):
    """The launcher sends a waiting launch back to the queue it consumes, a chain Lambda's recursive-loop
    detection does not stop here, so the handler bounds it: past MAX_SLOT_WAITS it is a failure."""
    _rows(launcher, [_slot_row(launcher, index, f"E{index}") for index in range(3)])
    response = _run(launcher, dict(MESSAGE, slotWaits=launcher.MAX_SLOT_WAITS))
    assert [entry["itemIdentifier"] for entry in response["batchItemFailures"]] == ["m1"]
    launcher.sqs_client.send_message.assert_not_called()
