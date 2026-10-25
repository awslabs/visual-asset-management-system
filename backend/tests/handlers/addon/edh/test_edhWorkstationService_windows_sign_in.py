# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Windows sign-in route: owner + Tier-2, then the Windows account of the caller's ACTIVE Windows desktop.

The route answers only what connect would connect to. The user name is the ``username`` query value of the
desktop's connection string, else its EDH session owner, else the caller's EDH uid; a ``DOMAIN\\user`` name
is a directory account, which signs in with the user's own directory password, so no password is returned.
The password is a credential, so beside the happy path the tests assert where it must NOT appear: the log,
the edhProxy audit record and the row update.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import EDH_FIELD_INSTANCE_ID
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    API_KEY_CLAIMS,
    BOOTSTRAP_OWN,
    SESSION_OTHER,
    SESSION_OWN,
    USER,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    other_row,
    own_row,
    response_body,
    workstation_path,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

PASSWORD = "winpw-" + "x" * 12
CONNECTION_URL = "https://dcv.example.internal/?authToken=s3cr3t&username=vabc#" + SESSION_OWN

# The record EDH answers when a test names none: the caller's running Windows desktop.
_RUNNING = object()


def _desktop(connection_string=CONNECTION_URL, password=PASSWORD, owner=None, drop=()):
    desktop = {
        service.EDH_FIELD_SESSION_UUID: SESSION_OWN,
        service.EDH_FIELD_SESSION_STATE: "running",
        service.EDH_FIELD_CONNECTION_STRING: connection_string,
        service.EDH_FIELD_SESSION_OWNER: owner or edh_uid_for(USER),
        service.EDH_FIELD_LOCAL_ADMIN_PASSWORD: password,
    }
    for field in drop:
        desktop.pop(field)
    return desktop


def _url(query):
    return f"https://dcv.example.internal/?{query}#{SESSION_OWN}"


def _windows(**overrides):
    return own_row(osFamily="windows", **overrides)


def _installed(monkeypatch, *, row=None, desktop=_RUNNING, enforce=None):
    edh = FakeEdhClient(get_desktop_connection=_desktop() if desktop is _RUNNING else desktop)
    fakes = install_service_fakes(monkeypatch, edh=edh, enforce=enforce)
    fakes.table.query.return_value = {"Items": [_windows() if row is None else row]}
    return fakes


def _sign_in(fakes, session_id=SESSION_OWN, rest=False):
    event = make_event("GET", workstation_path(session_id, "windows-sign-in"),
                       path_params={"sessionId": session_id}, rest=rest)
    return fakes.module.lambda_handler(event, {})


def _update_values(fakes):
    return [value for update in fakes.table.update_item.call_args_list
            for placeholder, value in update.kwargs["ExpressionAttributeValues"].items()
            if placeholder.startswith(":v")]


@pytest.mark.unit
class TestWindowsSignIn:
    def test_an_api_key_caller_is_refused_before_edh_is_asked(self, monkeypatch):
        # A Linux desktop's temporary session key must not read the password of the owner's Windows desktop.
        edh = FakeEdhClient(get_desktop_connection=_desktop())
        fakes = install_service_fakes(monkeypatch, edh=edh, claims=API_KEY_CLAIMS)
        fakes.table.query.return_value = {"Items": [_windows()]}

        response = _sign_in(fakes)

        assert response["statusCode"] == 403
        assert PASSWORD not in response["body"]
        assert fakes.edh.calls_to("get_desktop_connection") == []

    def test_the_owner_receives_the_windows_sign_in(self, monkeypatch):
        fakes = _installed(monkeypatch)

        response = _sign_in(fakes)

        assert response["statusCode"] == 200
        assert response_body(response) == {
            "sessionId": SESSION_OWN, "username": "vabc", "password": PASSWORD, "directoryAccount": False}
        assert fakes.edh.calls_to("get_desktop_connection") == [(edh_uid_for(USER), SESSION_OWN)]

    def test_sign_in_reads_only_its_session(self, monkeypatch):
        fakes = _installed(monkeypatch)

        response = _sign_in(fakes)

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("get_desktop_connection") == [(edh_uid_for(USER), SESSION_OWN)]
        assert fakes.edh.calls_to("get_desktop_states") == []

    def test_sign_in_refuses_a_record_of_another_session(self, monkeypatch):
        foreign = {**_desktop(), service.EDH_FIELD_SESSION_UUID: SESSION_OTHER,
                   EDH_FIELD_INSTANCE_ID: "i-0another0000000"}
        fakes = _installed(monkeypatch, desktop=foreign)

        response = _sign_in(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not ready for connection"
        assert PASSWORD not in response["body"]
        written = [name for update in fakes.table.update_item.call_args_list
                   for name in update.kwargs["ExpressionAttributeNames"].values()]
        assert "instanceId" not in written
        assert "i-0another0000000" not in json.dumps(_update_values(fakes))

    def test_tier2_is_enforced_from_the_row_with_get(self, monkeypatch):
        fakes = _installed(monkeypatch)

        _sign_in(fakes)

        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhWorkstation", "edhImageId": "42", "edhOsFamily": "windows",
             "edhInstanceType": "g5.xlarge"},
            "GET",
        )

    def test_the_response_is_never_stored_by_a_cache(self, monkeypatch):
        fakes = _installed(monkeypatch)

        response = _sign_in(fakes)

        assert "no-store" in response["headers"]["Cache-Control"]

    def test_the_password_is_returned_exactly_as_edh_lists_it(self, monkeypatch):
        """Nothing trims, percent-decodes or escapes the credential."""
        listed = "  p&w#d\"'%41+%\u00e9 " + "x" * 4
        fakes = _installed(monkeypatch, desktop=_desktop(password=listed))

        response = _sign_in(fakes)

        assert response["statusCode"] == 200
        assert response_body(response)["password"] == listed

    @pytest.mark.parametrize("query", [
        "username=vabc",
        "username=%20vabc%20",
        "authToken=s3cr3t&username=vabc&username=other",
    ])
    def test_the_user_name_is_the_connection_strings_username_value(self, monkeypatch, query):
        fakes = _installed(monkeypatch, desktop=_desktop(connection_string=_url(query)))

        response = _sign_in(fakes)

        assert response_body(response)["username"] == "vabc"

    @pytest.mark.parametrize("query", ["username=CORP%5Calice", "username=CORP\\alice"])
    def test_a_directory_account_gets_no_password(self, monkeypatch, query):
        fakes = _installed(monkeypatch, desktop=_desktop(connection_string=_url(query)))

        response = _sign_in(fakes)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["username"], body["password"], body["directoryAccount"]) == ("CORP\\alice", None, True)
        assert PASSWORD not in response["body"]

    def test_a_directory_account_needs_no_listed_password(self, monkeypatch):
        desktop = _desktop(connection_string=_url("username=CORP%5Calice"),
                           drop=(service.EDH_FIELD_LOCAL_ADMIN_PASSWORD,))
        fakes = _installed(monkeypatch, desktop=desktop)

        response = _sign_in(fakes)

        assert response["statusCode"] == 200
        assert response_body(response)["directoryAccount"] is True

    @pytest.mark.parametrize("query", ["authToken=s3cr3t", "authToken=s3cr3t&username=", "username=%20%20"])
    def test_without_a_username_value_the_session_owner_signs_in(self, monkeypatch, query):
        desktop = _desktop(connection_string=_url(query), owner="vowner1234567890")
        fakes = _installed(monkeypatch, desktop=desktop)

        response = _sign_in(fakes)

        assert response_body(response)["username"] == "vowner1234567890"

    def test_without_a_username_or_an_owner_the_callers_edh_uid_signs_in(self, monkeypatch):
        desktop = _desktop(connection_string=_url("authToken=s3cr3t"), drop=(service.EDH_FIELD_SESSION_OWNER,))
        fakes = _installed(monkeypatch, desktop=desktop)

        response = _sign_in(fakes)

        assert response_body(response)["username"] == edh_uid_for(USER)

    def test_a_connection_string_that_does_not_parse_falls_back_to_the_owner(self, monkeypatch):
        desktop = _desktop(connection_string="https://[bad/?username=x", owner="vowner1234567890")
        fakes = _installed(monkeypatch, desktop=desktop)

        response = _sign_in(fakes)

        assert response["statusCode"] == 200
        assert response_body(response)["username"] == "vowner1234567890"

    @pytest.mark.parametrize("password", ["missing", None, "", "   ", 123])
    def test_a_desktop_without_a_password_is_not_available_yet(self, monkeypatch, password):
        if password == "missing":
            desktop = _desktop(drop=(service.EDH_FIELD_LOCAL_ADMIN_PASSWORD,))
        else:
            desktop = _desktop(password=password)
        fakes = _installed(monkeypatch, desktop=desktop)

        response = _sign_in(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Windows password is not available yet"

    def test_a_linux_workstation_is_refused_before_edh_is_asked(self, monkeypatch):
        fakes = _installed(monkeypatch, row=own_row())

        response = _sign_in(fakes)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Workstation is not a Windows workstation"
        fakes.client_factory.assert_not_called()

    def test_another_users_session_is_not_found_and_edh_is_not_asked(self, monkeypatch):
        fakes = _installed(monkeypatch, row=other_row(osFamily="windows"))

        response = _sign_in(fakes, SESSION_OTHER)

        assert response["statusCode"] == 404
        assert response_body(response)["message"] == "Workstation not found"
        assert fakes.edh.calls == []
        fakes.log_authorization.assert_called_once()
        assert fakes.log_authorization.call_args.args[1] is False

    def test_a_launch_without_a_session_has_nothing_to_sign_in_to(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        row = _windows(state="CREATING", edhState=None, connectionAvailable=False)
        row.pop("sessionId")
        fakes.table.get_item.return_value = {"Item": row}

        response = _sign_in(fakes, BOOTSTRAP_OWN)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation has no EDH session"
        fakes.client_factory.assert_not_called()

    def test_a_stored_state_other_than_active_is_not_ready_without_asking_edh(self, monkeypatch):
        fakes = _installed(monkeypatch, row=_windows(state="STOPPED", edhState="stopped", connectionAvailable=False))

        response = _sign_in(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not ready for connection"
        fakes.client_factory.assert_not_called()

    def test_a_desktop_absent_from_the_active_list_is_not_ready(self, monkeypatch):
        fakes = _installed(monkeypatch, desktop=None)

        response = _sign_in(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not ready for connection"
        assert False in _update_values(fakes)

    @pytest.mark.parametrize("listed", [
        {service.EDH_FIELD_CONNECTION_STRING: ""},
        {service.EDH_FIELD_SESSION_STATE: "stopped"},
    ])
    def test_a_listed_desktop_connect_would_refuse_is_not_ready(self, monkeypatch, listed):
        fakes = _installed(monkeypatch, desktop={**_desktop(), **listed})

        response = _sign_in(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not ready for connection"
        assert PASSWORD not in response["body"]

    def test_a_denied_verdict_is_403_without_edh(self, monkeypatch):
        fakes = _installed(monkeypatch, enforce=lambda obj, act: False)

        response = _sign_in(fakes)

        assert response["statusCode"] == 403
        assert fakes.edh.calls == []

    def test_edh_unavailable_is_a_502(self, monkeypatch):
        fakes = _installed(monkeypatch, desktop=EdhUnavailableError("read timeout"))

        assert _sign_in(fakes)["statusCode"] == 502

    def test_a_rest_shaped_event_is_served(self, monkeypatch):
        fakes = _installed(monkeypatch)

        response = _sign_in(fakes, rest=True)

        assert response["statusCode"] == 200
        assert response_body(response)["password"] == PASSWORD


@pytest.mark.unit
class TestThePasswordIsACredential:
    def test_the_password_reaches_the_body_alone(self, monkeypatch):
        fakes = _installed(monkeypatch, row=_windows(connectionAvailable=False, edhState="starting"))
        logger = MagicMock()
        monkeypatch.setattr(service, "logger", logger)

        response = _sign_in(fakes)

        assert response_body(response)["password"] == PASSWORD
        assert fakes.table.update_item.call_args_list, "the observed state was not written back"
        assert logger.mock_calls, "nothing was logged, so this would be vacuous"
        assert PASSWORD not in str(logger.mock_calls)
        assert PASSWORD not in json.dumps(edh_proxy_records(fakes))
        assert PASSWORD not in str(fakes.log_actions.call_args_list)
        assert PASSWORD not in str(fakes.table.update_item.call_args_list)

    def test_the_audit_record_names_the_sign_in_read(self, monkeypatch):
        fakes = _installed(monkeypatch)

        _sign_in(fakes)

        records = edh_proxy_records(fakes)
        assert [(record["operation"], record["edhStatus"]) for record in records] == [
            ("workstationWindowsSignIn", "ok")]
        assert (records[0]["edhMethod"], records[0]["edhRoute"]) == service.EDH_AUDIT_ROUTES["get_desktop_connection"]
