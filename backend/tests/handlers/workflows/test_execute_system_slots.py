# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""executeWorkflow claims a concurrency slot only for a marked system-pipeline launch that asks for one.

A system-pipeline launcher's cross-call (SYSTEM_USER plus the vamsSystemPipeline source marker, and a
top-level systemExecution object carrying `concurrencySlots: {count, candidates}`) claims one of the
workflow's slot rows before the state machine starts; with every candidate held it answers 429 and starts
nothing, releasing the locks it took. A malformed slot request in a marked call is refused before any read.
Any other launch (upload-triggered, manual, API, re-run, or a SYSTEM_USER call without the marker) never
touches a slot row, whatever it sends. Core names no feature: the trigger value here is a neutral one. The
harness is the perInputFileVersion suite's."""

import json

import pytest
from unittest.mock import MagicMock

from backend.backend.common.workflows import systemPipelines as sp
from tests.handlers.workflows.test_execute_per_input_file_version_lock import (  # noqa: F401
    KEY_F,
    WF_DB,
    WF_ID,
    _LockTable,
    _event,
    _run,
    el,
    ewv2,
)

ONE_FILE = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
SLOT = [el.system_slot_key(WF_DB, WF_ID, index) for index in range(3)]
SYSTEM_TRIGGER = "System-TestFeature"


def _system_call(slots=None, marker=True, user="SYSTEM_USER", trigger=SYSTEM_TRIGGER, body=None):
    """A system-pipeline launch: the marked cross-call and its systemExecution (with `slots` as its
    concurrencySlots when given)."""
    spec = {"triggerType": trigger, "context": {"runId": "r-1"}}
    if slots is not None:
        spec[sp.SYSTEM_EXECUTION_SLOTS_KEY] = slots
    cross_call = {"userName": user}
    if marker:
        cross_call["source"] = sp.SYSTEM_PIPELINE_SOURCE_MARKER
    return {
        "requestContext": {"http": {"method": "POST", "path": f"/workflows/{WF_DB}/{WF_ID}/execute"}},
        "pathParameters": {"workflowDatabaseId": WF_DB, "workflowId": WF_ID},
        "queryStringParameters": {},
        "body": json.dumps(body if body is not None else ONE_FILE),
        "lambdaCrossCall": cross_call,
        sp.SYSTEM_EXECUTION_EVENT_KEY: spec,
    }


def _slot_puts(table):
    return [put["Item"]["lockKey"] for put in table.puts if "|systemSlot|" in put["Item"]["lockKey"]]


@pytest.mark.unit
class TestAMarkedLaunchClaimsASlot:
    def test_a_free_candidate_is_claimed_before_the_machine_starts(self):
        table = _LockTable()
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                          event=_system_call(slots={"count": 3, "candidates": [2]}))
        assert response["statusCode"] == 200
        assert _slot_puts(table) == [SLOT[2]]
        assert table.journal.index(("lock", SLOT[2])) < table.journal.index(("start", table.journal[-1][1]))
        sfn.start_execution.assert_called_once()

    def test_the_slot_row_is_a_generic_system_slot_and_expires_after_the_runs_longest_life(self):
        table = _LockTable()
        _run(table, tokens=("SYSTEM_USER",), event=_system_call(slots={"count": 3, "candidates": [0]}))
        slot = next(put["Item"] for put in table.puts if put["Item"]["lockKey"] == SLOT[0])
        lock = next(put["Item"] for put in table.puts if put["Item"]["lockKey"] == KEY_F)
        assert SLOT[0] == f"{WF_DB}:{WF_ID}|systemSlot|0"
        # The harness pipeline's taskTimeout (100000 s) is above the lock's one-day floor, so both rows
        # expire together here; a shorter pipeline shows the slot's bound has no floor.
        assert slot["expiresAt"] == lock["expiresAt"]
        assert slot["lockScope"] == el.LOCK_SCOPE_SYSTEM_SLOT == "systemSlot"
        assert el.system_slot_ttl_seconds([{"executionConfig": {"taskTimeout": "18000"}}]) == 18000 + 1800
        assert el.lock_ttl_seconds([{"executionConfig": {"taskTimeout": "18000"}}]) == 86400 + 1800

    def test_every_candidate_held_answers_429_starts_nothing_and_releases_the_locks_it_took(self):
        table = _LockTable(held={SLOT[0]: "E-a", SLOT[1]: "E-b"})
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                          event=_system_call(slots={"count": 3, "candidates": [0, 1]}))
        assert response["statusCode"] == 429
        assert json.loads(response["body"])["message"] == el.SYSTEM_SLOTS_BUSY_MESSAGE
        sfn.start_execution.assert_not_called()
        assert ("release", KEY_F) in table.journal

    def test_no_candidates_tries_the_slots_in_order(self):
        table = _LockTable(held={SLOT[0]: "E-a", SLOT[1]: "E-b"})
        response, _sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                           event=_system_call(slots={"count": 3, "candidates": []}))
        assert response["statusCode"] == 200
        assert _slot_puts(table) == SLOT

    def test_no_candidates_tries_at_most_the_candidate_bound(self):
        keys = ewv2._system_slot_keys_for(_system_call(slots={"count": 1000}), WF_DB, WF_ID)
        assert keys == [el.system_slot_key(WF_DB, WF_ID, i) for i in range(el.MAX_SYSTEM_SLOT_CANDIDATES)]


@pytest.mark.unit
class TestOtherLaunchesNeverTouchASlot:
    @pytest.mark.parametrize("event", [
        # A SYSTEM_USER call without the marker (the upload trigger dispatcher's shape), with the slot
        # request in systemExecution: ignored, launched as an ordinary run.
        _system_call(slots={"count": 3, "candidates": [0]}, marker=False),
        # The shape an earlier release's launcher sent: the slots on lambdaCrossCall.
        {**_system_call(marker=False), "lambdaCrossCall": {
            "userName": "SYSTEM_USER", "reindexSlots": {"count": 3, "candidates": [0]}}},
        # A marked call that asks for no slot.
        _system_call(slots=None),
    ])
    def test_an_unmarked_or_slotless_call_claims_no_slot(self, event):
        table = _LockTable(held={key: "E-x" for key in SLOT})
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",), event=event)
        assert response["statusCode"] == 200
        assert _slot_puts(table) == []
        sfn.start_execution.assert_called_once()

    def test_an_api_request_cannot_name_slots(self):
        body = dict(ONE_FILE, systemExecution={"triggerType": SYSTEM_TRIGGER,
                                               "concurrencySlots": {"count": 3, "candidates": [0]}})
        event = _event(body)
        event[sp.SYSTEM_EXECUTION_EVENT_KEY] = {"concurrencySlots": {"count": 3}}
        assert ewv2._system_slot_keys_for(event, WF_DB, WF_ID) is None
        table = _LockTable()
        response, sfn, _log, _stop = _run(table, event=_event(body))
        assert response["statusCode"] == 200 and _slot_puts(table) == []


@pytest.mark.unit
class TestAMalformedSlotRequestIsRefused:
    @pytest.mark.parametrize("slots", [
        {"count": 0}, {"count": True}, {"count": "3"}, {"count": el.MAX_SYSTEM_SLOTS + 1},
        {"count": 3, "candidates": [3]}, {"count": 3, "candidates": [-1]}, {"count": 3, "candidates": [True]},
        {"count": 3, "candidates": [0, 0]}, {"count": 3, "candidates": "0"},
        {"count": 20, "candidates": list(range(el.MAX_SYSTEM_SLOT_CANDIDATES + 1))},
        [], "3",
    ])
    def test_it_is_refused_with_400_before_any_lock_or_start(self, slots):
        table = _LockTable()
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",), event=_system_call(slots=slots))
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == sp.SYSTEM_EXECUTION_INVALID_MESSAGE
        assert table.puts == []
        sfn.start_execution.assert_not_called()

    def test_the_bounds_are_inclusive(self):
        assert sp.resolve_concurrency_slots(_system_call(slots={"count": el.MAX_SYSTEM_SLOTS})) == (
            el.MAX_SYSTEM_SLOTS, [])
        candidates = list(range(el.MAX_SYSTEM_SLOT_CANDIDATES))
        assert sp.resolve_concurrency_slots(_system_call(slots={"count": 16, "candidates": candidates})) == (
            16, candidates)
        assert sp.resolve_concurrency_slots(_system_call(slots={"count": 1, "candidates": [0]})) == (1, [0])


@pytest.mark.unit
class TestAnEndedHolderIsTakenOver:
    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"])
    def test_a_holder_whose_record_ended_does_not_still_run(self, status, monkeypatch):
        main = MagicMock()
        main.get_item.return_value = {"Item": {"executionStatus": status, "workflow_execution_arn": "arn:e"}}
        monkeypatch.setattr(ewv2.dynamodb, "Table", MagicMock(return_value=main))
        assert ewv2._holder_still_running(WF_DB, WF_ID)("E-old") is False

    def test_a_holder_stopped_outside_vams_does_not_still_run(self, monkeypatch):
        main = MagicMock()
        main.get_item.return_value = {"Item": {"executionStatus": "RUNNING", "workflow_execution_arn": "arn:e"}}
        monkeypatch.setattr(ewv2.dynamodb, "Table", MagicMock(return_value=main))
        monkeypatch.setattr(ewv2.el, "sfn_execution_has_ended", lambda client, arn: arn == "arn:e")
        assert ewv2._holder_still_running(WF_DB, WF_ID)("E-old") is False

    def test_a_running_holder_and_an_unreadable_record_keep_the_slot(self, monkeypatch):
        main = MagicMock()
        main.get_item.return_value = {"Item": {"executionStatus": "RUNNING", "workflow_execution_arn": "arn:e"}}
        monkeypatch.setattr(ewv2.dynamodb, "Table", MagicMock(return_value=main))
        monkeypatch.setattr(ewv2.el, "sfn_execution_has_ended", lambda client, arn: False)
        assert ewv2._holder_still_running(WF_DB, WF_ID)("E-run") is True
        main.get_item.side_effect = RuntimeError("throttled")
        assert ewv2._holder_still_running(WF_DB, WF_ID)("E-run") is True
