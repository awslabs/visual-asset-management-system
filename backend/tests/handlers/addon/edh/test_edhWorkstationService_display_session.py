# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connect and Windows sign-in to a running desktop whose DCV session has ended (D20).

EDH lists such a desktop running with its fallback link (entry point and broker session id, no query). Connect
and Windows sign-in answer 409 ``DISPLAY_SESSION_ENDED`` with the sentence that says what to do instead of
handing the dead link out, record the reason on the row and leave it ACTIVE; the record returns the reason
until a connectable link, or a state other than running, clears it. The token-free state read the get and list
routes make cannot see the session, so it keeps the reason while EDH still reports the desktop running.
"""

import json

import pytest

from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    SESSION_OWN,
    USER,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    state_record,
    workstation_path,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

FALLBACK_URL = "https://edh.example.internal/?#" + "a" * 36
GOOD_URL = "https://edh.example.internal/?authToken=s3cr3t&username=vabc#" + SESSION_OWN
MESSAGE = ("The workstation is running but its remote display session has ended. "
           "Stop it, then start it again, to reconnect.")
PASSWORD = "winpw-" + "x" * 12


def _desktop(connection_string, state="running"):
    return {
        service.EDH_FIELD_SESSION_UUID: SESSION_OWN,
        service.EDH_FIELD_SESSION_STATE: state,
        service.EDH_FIELD_CONNECTION_STRING: connection_string,
        service.EDH_FIELD_SESSION_OWNER: edh_uid_for(USER),
        service.EDH_FIELD_LOCAL_ADMIN_PASSWORD: PASSWORD,
    }


def _ended_row(**overrides):
    return own_row(connectionAvailable=False, connectionUnavailableReason="DISPLAY_SESSION_ENDED", **overrides)


def _installed(monkeypatch, row, **edh):
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(**edh))
    fakes.table.query.return_value = {"Items": [row]}
    return fakes


def _call(fakes, action=None):
    """GET the caller's workstation record, or its ``action`` route."""
    event = make_event("GET", workstation_path(SESSION_OWN, action), path_params={"sessionId": SESSION_OWN})
    return fakes.module.lambda_handler(event, {})


def _written(fakes):
    """Every attribute the handler wrote onto the row, by name."""
    written = {}
    for call in fakes.table.update_item.call_args_list:
        names = call.kwargs["ExpressionAttributeNames"]
        values = call.kwargs["ExpressionAttributeValues"]
        for placeholder, name in names.items():
            value_key = ":v" + placeholder[2:]
            if placeholder.startswith("#a") and value_key in values:
                written[name] = values[value_key]
    return written


@pytest.mark.unit
class TestConnectToAnEndedDisplaySession:
    @pytest.mark.parametrize("segment, row", [("connect", own_row()),
                                              ("windows-sign-in", own_row(osFamily="windows"))],
                             ids=["connect", "windows sign-in"])
    def test_the_dead_link_is_refused_with_what_to_do(self, monkeypatch, segment, row):
        fakes = _installed(monkeypatch, row, get_desktop_connection=_desktop(FALLBACK_URL))

        response = _call(fakes, segment)

        assert response["statusCode"] == 409
        assert response_body(response) == {"code": "DISPLAY_SESSION_ENDED", "message": MESSAGE}
        assert FALLBACK_URL not in response["body"] and PASSWORD not in response["body"]
        written = _written(fakes)
        assert written["connectionAvailable"] is False
        assert written["connectionUnavailableReason"] == "DISPLAY_SESSION_ENDED"
        assert "state" not in written
        assert FALLBACK_URL not in json.dumps(written)

    def test_a_second_connect_is_refused_the_same_way_without_a_write(self, monkeypatch):
        fakes = _installed(monkeypatch, _ended_row(), get_desktop_connection=_desktop(FALLBACK_URL))

        response = _call(fakes, "connect")

        assert response["statusCode"] == 409
        assert response_body(response)["code"] == "DISPLAY_SESSION_ENDED"
        fakes.table.update_item.assert_not_called()

    def test_a_good_link_again_connects_and_clears_the_reason(self, monkeypatch):
        fakes = _installed(monkeypatch, _ended_row(), get_desktop_connection=_desktop(GOOD_URL))

        response = _call(fakes, "connect")

        assert response["statusCode"] == 200
        assert response_body(response)["connectionUrl"] == GOOD_URL
        written = _written(fakes)
        assert (written["connectionAvailable"], written["connectionUnavailableReason"]) == (True, None)

    @pytest.mark.parametrize("desktop", [_desktop(""), _desktop(GOOD_URL, state="stopped")], ids=["empty", "stopped"])
    def test_every_other_not_ready_answer_stands(self, monkeypatch, desktop):
        fakes = _installed(monkeypatch, own_row(), get_desktop_connection=desktop)

        response = _call(fakes, "connect")

        assert response["statusCode"] == 409
        assert response_body(response) == {"message": "Workstation is not ready for connection"}


@pytest.mark.unit
class TestTheRecordReturnsTheReason:
    def test_get_keeps_the_reason_while_edh_reports_the_desktop_running(self, monkeypatch):
        fakes = _installed(monkeypatch, _ended_row(), get_desktop_states=[state_record(SESSION_OWN)])

        response = _call(fakes)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["connectionAvailable"], body["connectionUnavailableReason"], body["activity"]) == (
            "ACTIVE", False, "DISPLAY_SESSION_ENDED", None)
        fakes.table.update_item.assert_not_called()

    def test_the_list_returns_the_reason(self, monkeypatch):
        fakes = _installed(monkeypatch, _ended_row(), get_desktop_states=[state_record(SESSION_OWN)])

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response_body(response)["Items"][0]["connectionUnavailableReason"] == "DISPLAY_SESSION_ENDED"

    def test_a_stopped_desktop_clears_the_reason(self, monkeypatch):
        fakes = _installed(monkeypatch, _ended_row(), get_desktop_states=[state_record(SESSION_OWN, "stopped")])

        body = response_body(_call(fakes))

        assert (body["state"], body["connectionUnavailableReason"]) == ("STOPPED", None)
        assert _written(fakes)["connectionUnavailableReason"] is None

    def test_a_connectable_row_returns_no_reason(self, monkeypatch):
        fakes = _installed(monkeypatch, own_row(), get_desktop_states=[state_record(SESSION_OWN)])

        body = response_body(_call(fakes))

        assert (body["connectionAvailable"], body["connectionUnavailableReason"]) == (True, None)
