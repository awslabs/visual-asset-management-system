# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The connect route: owner + Tier-2, then the connection_string of the caller's active desktop.

The route answers only for a row stored ACTIVE: it reads EDH's per-user record of that one session
once and returns its ``connection_string`` while EDH reports it ``running``. Any other stored
state is refused before EDH is asked. The URL carries a DCV session credential, so
beside the happy path the tests assert where it must NOT appear: the edhProxy audit record and the
row update.
"""

import json

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import EDH_FIELD_INSTANCE_ID, EDH_SESSION_STATE_RUNNING
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
from backend.tests.handlers.addon.edh.edhTestSupport import load_fixture
import backend.backend.handlers.addon.edh.edhWorkstationService as service

CONNECTION_URL = "https://dcv.example.internal/?authToken=s3cr3t#" + SESSION_OWN


def _desktop(session_id=SESSION_OWN, connection_string=CONNECTION_URL, state="running"):
    return {
        service.EDH_FIELD_SESSION_UUID: session_id,
        service.EDH_FIELD_SESSION_STATE: state,
        service.EDH_FIELD_CONNECTION_STRING: connection_string,
    }


def _connect(fakes, session_id=SESSION_OWN, rest=False):
    event = make_event("GET", workstation_path(session_id, "connect"), path_params={"sessionId": session_id}, rest=rest)
    return fakes.module.lambda_handler(event, {})


def _update_values(fakes):
    return [value for placeholder, value in fakes.table.update_item.call_args[1]["ExpressionAttributeValues"].items()
            if placeholder.startswith(":v")]


@pytest.mark.unit
class TestConnect:
    def test_an_api_key_caller_is_refused_before_edh_is_asked(self, monkeypatch):
        # A desktop's own temporary session key authenticates as an API key: it must not obtain a DCV
        # connection to any of the owner's desktops.
        fakes = install_service_fakes(monkeypatch, claims=API_KEY_CLAIMS,
                                      edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 403
        assert CONNECTION_URL not in response["body"]
        assert fakes.edh.calls_to("get_desktop_connection") == []
        fakes.table.update_item.assert_not_called()

    def test_the_owner_receives_the_connection_url(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row(connectionAvailable=False, edhState="starting")]}

        response = _connect(fakes)

        assert response["statusCode"] == 200
        assert response_body(response) == {
            "sessionId": SESSION_OWN, "connectionUrl": CONNECTION_URL, "osFamily": "linux"}
        assert fakes.edh.calls_to("get_desktop_connection") == [(edh_uid_for(USER), SESSION_OWN)]
        update = fakes.table.update_item.call_args[1]
        assert update["Key"] == {"bootstrapId": own_row()["bootstrapId"]}
        values = _update_values(fakes)
        assert True in values and "running" in values
        assert CONNECTION_URL not in json.dumps(values)

    def test_tier2_is_enforced_from_the_row_with_get(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row(imageId="42", osFamily="linux", instanceType="g5.xlarge")]}

        _connect(fakes)

        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhWorkstation", "edhImageId": "42", "edhOsFamily": "linux",
             "edhInstanceType": "g5.xlarge"},
            "GET",
        )

    def test_a_row_already_connectable_is_not_rewritten(self, monkeypatch):
        """Control: a row that already records what EDH reports is answered with its check stamp written alone
        (D21), neither its observed attributes nor its updatedAt rewritten."""
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 200
        assert response_body(response)["connectionUrl"] == CONNECTION_URL
        (write,) = fakes.table.update_item.call_args_list
        assert set(write.kwargs["ExpressionAttributeNames"].values()) == {"connectionCheckedAt", "state"}
        assert CONNECTION_URL not in json.dumps(write.kwargs, default=str)

    def test_a_desktop_absent_from_the_active_list_is_not_ready(self, monkeypatch):
        another = {**_desktop(session_id="another"), EDH_FIELD_INSTANCE_ID: "i-0another0000000"}
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=another))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not ready for connection"
        assert CONNECTION_URL not in response["body"]
        assert False in _update_values(fakes)
        written = [name for call in fakes.table.update_item.call_args_list
                   for name in call[1]["ExpressionAttributeNames"].values()]
        assert "instanceId" not in written
        assert "i-0another0000000" not in json.dumps(_update_values(fakes))

    def test_connect_reads_only_the_connected_session(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("get_desktop_connection") == [(edh_uid_for(USER), SESSION_OWN)]
        assert fakes.edh.calls_to("get_desktop_states") == []

    def test_an_empty_connection_string_is_not_ready(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop(connection_string="")))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 409
        assert False in _update_values(fakes)

    def test_a_desktop_that_is_not_running_is_not_ready(self, monkeypatch):
        """A listed session with a connection_string is still not connectable until EDH reports it running."""
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop(state="stopped")))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not ready for connection"
        assert CONNECTION_URL not in response["body"]
        values = _update_values(fakes)
        assert {"STOPPED", "stopped", False} <= set(values)
        assert CONNECTION_URL not in json.dumps(values)

    def test_a_launch_without_a_session_has_nothing_to_connect_to(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        row = own_row(state="CREATING", edhState=None, connectionAvailable=False)
        row.pop("sessionId")
        fakes.table.get_item.return_value = {"Item": row}

        response = _connect(fakes, BOOTSTRAP_OWN)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation has no EDH session"
        fakes.client_factory.assert_not_called()
        fakes.table.update_item.assert_not_called()

    def test_a_bootstrap_id_connects_to_the_desktop_of_its_session(self, monkeypatch):
        """The bootstrap id keeps addressing the row once it has a session id (in-dev D22); EDH is read, and the
        response answers, by the session id."""
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.get_item.return_value = {"Item": own_row()}

        response = _connect(fakes, BOOTSTRAP_OWN)

        assert response["statusCode"] == 200
        assert response_body(response) == {
            "sessionId": SESSION_OWN, "connectionUrl": CONNECTION_URL, "osFamily": "linux"}
        assert fakes.edh.calls_to("get_desktop_connection") == [(edh_uid_for(USER), SESSION_OWN)]

    def test_a_stored_state_other_than_active_is_not_ready_without_asking_edh(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [
            own_row(state="STOPPED", edhState="stopped", connectionAvailable=False)]}

        response = _connect(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not ready for connection"
        fakes.client_factory.assert_not_called()
        fakes.table.update_item.assert_not_called()

    def test_a_deleted_row_edh_still_lists_as_running_is_not_resurrected(self, monkeypatch):
        """A row the user deleted stays deleted while EDH's terminate is still in flight: the URL EDH
        still lists for it is not returned and the row is not merged back to ACTIVE."""
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row(state="DELETED", connectionAvailable=False)]}

        response = _connect(fakes)

        assert response["statusCode"] == 409
        assert CONNECTION_URL not in response["body"]
        fakes.client_factory.assert_not_called()
        fakes.table.update_item.assert_not_called()

    def test_a_desktop_in_error_orphans_the_row_and_is_not_ready(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop(state="error")))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 409
        assert CONNECTION_URL not in response["body"]
        values = _update_values(fakes)
        assert {"ORPHANED", "LAUNCH_ERROR", False} <= set(values)
        assert CONNECTION_URL not in json.dumps(values)

    def test_another_users_session_is_not_found_and_edh_is_not_asked(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop(session_id=SESSION_OTHER)))
        fakes.table.query.return_value = {"Items": [other_row()]}

        response = _connect(fakes, SESSION_OTHER)

        assert response["statusCode"] == 404
        assert response_body(response)["message"] == "Workstation not found"
        assert fakes.edh.calls == []
        fakes.log_authorization.assert_called_once()
        assert fakes.log_authorization.call_args.args[1] is False
        assert fakes.log_authorization.call_args.args[2]["action"] == "GET"

    def test_a_denied_verdict_is_403_without_edh(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()), enforce=lambda obj, act: False)
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 403
        assert fakes.edh.calls == []

    def test_edh_unavailable_is_a_502(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_connection=EdhUnavailableError("timeout")))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes)

        assert response["statusCode"] == 502
        fakes.table.update_item.assert_not_called()

    def test_the_audit_record_names_the_call_but_never_the_url(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row()]}

        _connect(fakes)

        records = edh_proxy_records(fakes)
        assert [(record["operation"], record["edhStatus"]) for record in records] == [("workstationConnect", "ok")]
        assert (records[0]["edhMethod"], records[0]["edhRoute"]) == service.EDH_AUDIT_ROUTES["get_desktop_connection"]
        assert "s3cr3t" not in json.dumps(records)

    def test_a_rest_shaped_event_is_served(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=_desktop()))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _connect(fakes, rest=True)

        assert response["statusCode"] == 200
        assert response_body(response)["connectionUrl"] == CONNECTION_URL

    def test_the_captured_active_list_yields_its_connection_string(self, monkeypatch):
        """A record of EDH's active list, read through the field constants, is answered as an absolute
        URL that appears in the body alone."""
        fixture = load_fixture("desktops_list_active")
        desktops = list(fixture["response"]["json"]["message"].values())
        assert len(desktops) == fixture["expect"]["count"]
        captured = next(desktop for desktop in desktops
                        if desktop[service.EDH_FIELD_SESSION_STATE] == EDH_SESSION_STATE_RUNNING)
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=captured))
        fakes.table.query.return_value = {"Items": [
            own_row(sessionId=captured[service.EDH_FIELD_SESSION_UUID], edhState=None, connectionAvailable=False)]}

        response = _connect(fakes, captured[service.EDH_FIELD_SESSION_UUID])

        assert response["statusCode"] == 200
        body = response_body(response)
        assert body["connectionUrl"] == captured[service.EDH_FIELD_CONNECTION_STRING]
        assert body["connectionUrl"].startswith("https://")
        assert captured[service.EDH_FIELD_CONNECTION_STRING] not in json.dumps(_update_values(fakes))
        assert captured[service.EDH_FIELD_CONNECTION_STRING] not in json.dumps(edh_proxy_records(fakes))
