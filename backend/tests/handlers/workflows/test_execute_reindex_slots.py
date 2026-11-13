# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""executeWorkflow claims a reindex slot for a reindex launch only.

The reindex launcher's cross-call (SYSTEM_USER, triggerType systemReindex, lambdaCrossCall.reindexSlots)
claims one of the workflow's slot rows before the state machine starts; with every candidate held it
answers 429 and starts nothing, releasing the locks it took. An upload-triggered, manual or API launch
never touches a slot row, whatever it sends. The harness is the perInputFileVersion suite's."""

import json

import pytest
from unittest.mock import MagicMock

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
SLOT = [el.reindex_slot_key(WF_DB, WF_ID, index) for index in range(3)]


def _cross_call(trigger_type="systemReindex", slots=None, user="SYSTEM_USER"):
    body = dict(ONE_FILE, triggerType=trigger_type)
    cross_call = {"userName": user}
    if slots is not None:
        cross_call["reindexSlots"] = slots
    return {
        "requestContext": {"http": {"method": "POST", "path": f"/workflows/{WF_DB}/{WF_ID}/execute"}},
        "pathParameters": {"workflowDatabaseId": WF_DB, "workflowId": WF_ID},
        "queryStringParameters": {},
        "body": json.dumps(body),
        "lambdaCrossCall": cross_call,
    }


def _slot_puts(table):
    return [put["Item"]["lockKey"] for put in table.puts if "|reindexSlot|" in put["Item"]["lockKey"]]


@pytest.mark.unit
class TestAReindexLaunchClaimsASlot:
    def test_a_free_candidate_is_claimed_before_the_machine_starts(self):
        table = _LockTable()
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                          event=_cross_call(slots={"count": 3, "candidates": [2]}))
        assert response["statusCode"] == 200
        assert _slot_puts(table) == [SLOT[2]]
        assert table.journal.index(("lock", SLOT[2])) < table.journal.index(("start", table.journal[-1][1]))
        sfn.start_execution.assert_called_once()

    def test_the_slot_row_expires_after_the_runs_longest_life_without_the_day_floor(self):
        table = _LockTable()
        _run(table, tokens=("SYSTEM_USER",), event=_cross_call(slots={"count": 3, "candidates": [0]}))
        slot = next(put["Item"] for put in table.puts if put["Item"]["lockKey"] == SLOT[0])
        lock = next(put["Item"] for put in table.puts if put["Item"]["lockKey"] == KEY_F)
        # The harness pipeline's taskTimeout (100000 s) is above the lock's one-day floor, so both rows
        # expire together here; a shorter pipeline shows the slot's bound has no floor.
        assert slot["expiresAt"] == lock["expiresAt"]
        assert slot["lockScope"] == el.LOCK_SCOPE_REINDEX_SLOT
        assert el.reindex_slot_ttl_seconds([{"executionConfig": {"taskTimeout": "18000"}}]) == 18000 + 1800
        assert el.lock_ttl_seconds([{"executionConfig": {"taskTimeout": "18000"}}]) == 86400 + 1800

    def test_every_candidate_held_answers_429_starts_nothing_and_releases_the_locks_it_took(self):
        table = _LockTable(held={SLOT[0]: "E-a", SLOT[1]: "E-b"})
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                          event=_cross_call(slots={"count": 3, "candidates": [0, 1]}))
        assert response["statusCode"] == 429
        assert json.loads(response["body"])["message"] == el.REINDEX_SLOTS_BUSY_MESSAGE
        sfn.start_execution.assert_not_called()
        assert ("release", KEY_F) in table.journal

    def test_no_candidates_tries_every_slot(self):
        table = _LockTable(held={SLOT[0]: "E-a", SLOT[1]: "E-b"})
        response, _sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",),
                                           event=_cross_call(slots={"count": 3, "candidates": []}))
        assert response["statusCode"] == 200
        assert _slot_puts(table) == SLOT


@pytest.mark.unit
class TestOtherLaunchesNeverTouchASlot:
    @pytest.mark.parametrize("event", [
        _cross_call(trigger_type="fileUpload", slots={"count": 3, "candidates": [0]}),
        _cross_call(trigger_type="manual", slots={"count": 3, "candidates": [0]}),
        _cross_call(slots=None),
        _cross_call(slots={"count": 0}),
        _cross_call(slots={"count": True}),
    ])
    def test_an_upload_manual_or_malformed_cross_call_claims_no_slot(self, event):
        table = _LockTable(held={key: "E-x" for key in SLOT})
        response, sfn, _log, _stop = _run(table, tokens=("SYSTEM_USER",), event=event)
        assert response["statusCode"] == 200
        assert _slot_puts(table) == []
        sfn.start_execution.assert_called_once()

    def test_an_api_request_cannot_name_slots(self):
        event = _event(ONE_FILE)
        event["lambdaCrossCall"] = None
        assert ewv2._reindex_slot_keys_for(event, MagicMock(triggerType="manual"), WF_DB, WF_ID) is None
        assert ewv2._reindex_slot_keys_for(_event(ONE_FILE), MagicMock(triggerType="systemReindex"),
                                           WF_DB, WF_ID) is None


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
