# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workstation routes and a workstation that has run, which EDH reports in ``error`` (#1731).

The list and get routes keep such a row in its state with its key and secret, and its record carries
``connectionAvailable`` false, ``connectionUnavailableReason`` ``DESKTOP_ERROR`` and no ``activity``; no failed-launch
read is made for it. Connect and the Linux desktop sign-in answer 409 ``{"code": "DESKTOP_ERROR", "message": ...}``.
A start EDH accepted whose desktop EDH no longer answers is left to the sweep rather than recorded a failed launch.
Stop works as for any workstation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common import edhCapacityReservations as reservations
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh.common.edhSessionState import DESKTOP_ERROR_MESSAGE, start_stamp
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import ConditionalRowTable
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    SESSION_OWN, WORKSTATIONS_PATH, FakeEdhClient, install_service_fakes, make_event, own_row, response_body,
    state_record, workstation_path,
)

KEYED = {"apiKeyId": "key-1", "secretName": "vams/edh-session/x", "keyExpiresAt": "2099-01-01T00:00:00Z"}


def _run(monkeypatch, row, *, action="", method="GET", desktop_states=None, connection=None):
    edh = FakeEdhClient(get_desktop_states=desktop_states if desktop_states is not None
                        else [state_record(SESSION_OWN, "error")],
                        get_desktop_connection=connection)
    fakes = install_service_fakes(monkeypatch, edh=edh)
    table = ConditionalRowTable(**{k: v for k, v in row.items() if k != "bootstrapId"})
    fakes.table.query.return_value = {"Items": [row]}
    fakes.table.update_item.side_effect = table
    fakes.table.get_item.side_effect = table.read
    fakes.stored = table
    revoke = MagicMock(return_value=dict(bootstrap.CLEARED_KEY_ATTRIBUTES))
    monkeypatch.setattr(bootstrap, "revoke_session", revoke)
    monkeypatch.setattr(bootstrap, "bind_session_secret", MagicMock(return_value={}))
    monkeypatch.setattr(reservations, "labels_for", MagicMock(return_value={}))
    fakes.revoke = revoke
    if action == "list":
        event = make_event("GET", WORKSTATIONS_PATH)
    else:
        event = make_event(method, workstation_path(SESSION_OWN, action), path_params={"sessionId": SESSION_OWN})
    return service.lambda_handler(event, MagicMock()), fakes


def _error_listing():
    return {"session_uuid": SESSION_OWN, "session_owner": None, "session_state": "error",
            "connection_string": f"https://edh.example.internal/?#{SESSION_OWN}"}


@pytest.mark.unit
class TestTheRecord:
    @pytest.mark.parametrize("action", ["list", ""], ids=["list", "get"])
    def test_an_active_workstation_stays_active_not_connectable_with_the_reason(self, monkeypatch, action):
        response, fakes = _run(monkeypatch, own_row(**KEYED), action=action)
        assert response["statusCode"] == 200
        body = response_body(response)
        item = body["Items"][0] if action == "list" else body
        assert item["state"] == "ACTIVE"
        assert item["connectionAvailable"] is False
        assert item["connectionUnavailableReason"] == "DESKTOP_ERROR"
        assert item["activity"] is None
        assert item["lastError"] is None
        stored = fakes.stored.item
        assert stored["state"] == "ACTIVE" and stored["apiKeyId"] == "key-1"
        assert stored["secretName"] == "vams/edh-session/x"
        fakes.revoke.assert_not_called()
        assert fakes.edh.calls_to("get_desktop_failure") == []

    def test_a_stopped_workstation_stays_stopped_with_the_reason(self, monkeypatch):
        row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False)
        response, _fakes = _run(monkeypatch, row)
        body = response_body(response)
        assert (body["state"], body["connectionUnavailableReason"]) == ("STOPPED", "DESKTOP_ERROR")

    def test_a_new_launch_is_still_orphaned(self, monkeypatch):
        row = own_row(state="CREATING", edhState="placing", connectionAvailable=False, **KEYED)
        response, fakes = _run(monkeypatch, row)
        body = response_body(response)
        assert (body["state"], body["lastError"]) == ("ORPHANED", "LAUNCH_ERROR")
        assert body["connectionUnavailableReason"] is None

    def test_edh_recovering_the_desktop_clears_the_reason(self, monkeypatch):
        row = own_row(edhState="error", connectionAvailable=False, connectionUnavailableReason="DESKTOP_ERROR")
        response, _fakes = _run(monkeypatch, row, desktop_states=[state_record(SESSION_OWN, "running")],
                                connection={"session_uuid": SESSION_OWN, "session_state": "running",
                                            "connection_string": "https://edh.example.internal/h/?authToken=t"})
        body = response_body(response)
        assert body["state"] == "ACTIVE"
        assert body["connectionUnavailableReason"] is None


@pytest.mark.unit
class TestConnectAndSignIn:
    def test_connect_answers_409_with_the_desktop_error(self, monkeypatch):
        connection = {**_error_listing(), "session_owner": service.edh_uid_for(own_row()["userId"])}
        response, fakes = _run(monkeypatch, own_row(**KEYED), action="connect", connection=connection)
        assert response["statusCode"] == 409
        assert response_body(response) == {"code": "DESKTOP_ERROR", "message": DESKTOP_ERROR_MESSAGE}
        assert fakes.stored.item["connectionUnavailableReason"] == "DESKTOP_ERROR"
        assert fakes.stored.item["state"] == "ACTIVE"
        fakes.revoke.assert_not_called()

    def test_the_linux_sign_in_answers_409_with_the_desktop_error(self, monkeypatch):
        row = own_row(osFamily="linux", edhState="error", connectionAvailable=False,
                      connectionUnavailableReason="DESKTOP_ERROR")
        response, _fakes = _run(monkeypatch, row, action="os-sign-in")
        assert response["statusCode"] == 409
        assert response_body(response) == {"code": "DESKTOP_ERROR", "message": DESKTOP_ERROR_MESSAGE}


@pytest.mark.unit
class TestTheWayOut:
    def test_a_stop_of_a_workstation_in_error_stops_it(self, monkeypatch):
        row = own_row(edhState="error", connectionAvailable=False, connectionUnavailableReason="DESKTOP_ERROR",
                      **KEYED)
        response, fakes = _run(monkeypatch, row, action="stop", method="POST")
        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("stop_desktop")
        assert fakes.stored.item["state"] == "STOPPED"


@pytest.mark.unit
class TestAStartedDesktopEdhNoLongerAnswers:
    def test_the_list_leaves_it_to_the_sweep(self, monkeypatch):
        accepted = start_stamp(datetime.now(timezone.utc) - timedelta(minutes=10))
        row = own_row(state="CREATING", edhState="pending", connectionAvailable=False, startAcceptedAt=accepted,
                      **KEYED)
        response, fakes = _run(monkeypatch, row, action="list", desktop_states=[])
        assert response_body(response)["Items"][0]["state"] == "CREATING"
        assert fakes.stored.item["state"] == "CREATING"
        fakes.revoke.assert_not_called()

    def test_a_new_launch_edh_no_longer_answers_is_still_a_failed_launch(self, monkeypatch):
        row = own_row(state="CREATING", edhState="placing", connectionAvailable=False, **KEYED)
        response, fakes = _run(monkeypatch, row, action="list", desktop_states=[])
        assert response_body(response)["Items"][0]["state"] == "ORPHANED"
        fakes.revoke.assert_called_once()
