# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workstation routes and a start EDH accepted but never finished (in-dev D16): the record of a CREATING row
the sweep recorded ``START_STALLED`` carries the code and its ``startFailure``, reads no ``activity`` (the start is
stuck, not under way) and never returns the internal ``startAcceptedAt``; a stop of it clears the code, the failure
and the stamp, after which the workstation starts again; a delete and a failed launch drop the stamp as they move
the row out of CREATING."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.tests.handlers.addon.edh.edhServiceTestSupport import SESSION_OWN, own_row, response_body, state_record
from backend.tests.handlers.addon.edh.test_edhWorkstationService_start_async import _run

FAILURE = {"observedAt": "2026-10-24T15:30:00+00:00"}


def _stalled(**fields):
    """A CREATING row whose accepted start the sweep recorded stalled, its key kept."""
    accepted = start_stamp(datetime.now(timezone.utc) - timedelta(hours=1))
    return own_row(state="CREATING", edhState="pending", connectionAvailable=False, activity="starting",
                   lastError="START_STALLED", startFailure=dict(FAILURE), startAcceptedAt=accepted,
                   apiKeyId="key-2", secretName="vams/edh-session/x", **fields)


def _booting():
    return [state_record(SESSION_OWN, "pending")]


@pytest.mark.unit
class TestTheRecordOfAStalledStart:
    @pytest.mark.parametrize("action, method", [("list", "GET"), ("", "GET")])
    def test_it_carries_the_code_and_the_failure_and_reads_no_activity(self, monkeypatch, action, method):
        response, _ = _run(monkeypatch, _stalled(), action=action, method=method, desktop_states=_booting())
        assert response["statusCode"] == 200
        body = response_body(response)
        item = body["Items"][0] if action == "list" else body
        assert item["state"] == "CREATING"
        assert item["lastError"] == "START_STALLED"
        assert item["startFailure"] == FAILURE
        assert item["activity"] is None
        assert "startAcceptedAt" not in item

    def test_a_start_that_has_not_stalled_still_reads_starting(self, monkeypatch):
        accepted = start_stamp(datetime.now(timezone.utc) - timedelta(minutes=5))
        row = own_row(state="CREATING", edhState="pending", connectionAvailable=False, startAcceptedAt=accepted)
        response, _ = _run(monkeypatch, row, action="", method="GET", desktop_states=_booting())
        body = response_body(response)
        assert body["activity"] == "starting" and body["lastError"] is None
        assert "startAcceptedAt" not in body


@pytest.mark.unit
class TestTheWayOut:
    def test_a_stop_clears_the_stall_its_failure_and_the_stamp(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stalled(), action="stop", desktop_states=_booting())
        assert response["statusCode"] == 200
        stored = fakes.stored.item
        assert stored["state"] == "STOPPED"
        assert stored["lastError"] is None
        assert stored["startFailure"] is None
        assert stored["startAcceptedAt"] is None
        body = response_body(response)
        assert body["lastError"] is None and body["startFailure"] is None

    def test_a_stop_takes_the_key_back_as_any_stop_does(self, monkeypatch):
        _, fakes = _run(monkeypatch, _stalled(), action="stop", desktop_states=_booting())
        assert fakes.revoked, "the stop revoked nothing"
        assert {row["bootstrapId"] for row in fakes.revoked} == {own_row()["bootstrapId"]}

    def test_once_the_stop_settles_the_workstation_starts_again(self, monkeypatch):
        _, stopped = _run(monkeypatch, _stalled(), action="stop", desktop_states=_booting())
        response, fakes = _run(monkeypatch, dict(stopped.stored.item, bootstrapId=own_row()["bootstrapId"]),
                               desktop_states=[state_record(SESSION_OWN, "stopped")])
        assert response["statusCode"] == 202
        payloads = [call.kwargs["Payload"] for call in fakes.lambda_client.invoke.call_args_list]
        assert payloads, "the start was not handed to the worker"

    def test_a_stop_leaves_a_code_that_is_not_a_stall(self, monkeypatch):
        row = own_row(state="ACTIVE", lastError="LEASE_EXPIRED")
        _, fakes = _run(monkeypatch, row, action="stop", desktop_states=[state_record(SESSION_OWN, "running")])
        assert fakes.stored.item["lastError"] == "LEASE_EXPIRED"


@pytest.mark.unit
class TestOtherWritesOutOfCreatingDropTheStamp:
    def test_a_delete_drops_the_stamp(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stalled(), action="", method="DELETE", desktop_states=_booting())
        assert response["statusCode"] == 200
        assert fakes.stored.item["state"] == "DELETED"
        assert fakes.stored.item["startAcceptedAt"] is None

    def test_a_desktop_edh_no_longer_has_is_a_failed_launch_not_a_stall(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stalled(), action="list", method="GET", desktop_states=[])
        assert response["statusCode"] == 200
        stored = fakes.stored.item
        assert stored["state"] == "ORPHANED"
        assert stored["lastError"] == "LAUNCH_ERROR"
        assert stored["startFailure"] is None and stored["startAcceptedAt"] is None
