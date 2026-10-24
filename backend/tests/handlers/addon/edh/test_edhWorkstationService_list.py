# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workstation list: the caller's session rows, EDH's live state merged in, Tier-2 per item.

Ownership is a key condition on the userIdIndex, with a Python comparison behind it as the second
line (the apiKeyService user-scope shape). EDH's state for the rows that hold a session id is read in
one session-state read and matched to each row by session id; a launch still CREATING without one is
returned as stored. What a record implies for the row is persisted, so the row is the truth as of the
last observation. The paging case uses the shared Pager so a loop that resumes from the wrong cursor,
or never resumes, fails with a message rather than hanging.
"""

import pytest
from unittest.mock import MagicMock

from backend.backend.handlers.addon.edh.common import edhClient as ec
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh.common.edhClient import EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import EDH_FIELD_INSTANCE_ID, LAST_ERROR_LAUNCH_ERROR
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    BOOTSTRAP_OWN,
    SESSION_OWN,
    USER,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    other_row,
    own_row,
    response_body,
    state_record,
    workstation_path,
)
from backend.tests.handlers.addon.edh.edhTestSupport import load_fixture
from backend.tests.pagingStub import Pager
import backend.backend.handlers.addon.edh.edhWorkstationService as service


# A second session of the caller's, and the bootstrap of a launch EDH has not listed yet.
SESSION_SECOND = "2b3c4d5e-6f70-4a8b-9c0d-1e2f3a4b5c6d"
BOOTSTRAP_CREATING = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"


def _desktop(session_id, state="running", connection_string="https://dcv.example.internal/?authToken=abc"):
    """An EDH desktop record built from the handler's field constants."""
    return {
        service.EDH_FIELD_SESSION_UUID: session_id,
        service.EDH_FIELD_SESSION_STATE: state,
        service.EDH_FIELD_CONNECTION_STRING: connection_string,
    }


def _key_condition(condition):
    """(attribute name, operator, value) for a boto3 KeyConditionExpression."""
    expression = condition.get_expression()
    return expression['values'][0].name, expression['operator'], expression['values'][1]


def _items(response):
    return response_body(response)["Items"]


def _update_values(fakes):
    """The attribute values of the update the merge wrote."""
    return [value for placeholder, value in fakes.table.update_item.call_args[1]["ExpressionAttributeValues"].items()
            if placeholder.startswith(":v")]


def _session_less_creating_row(**overrides):
    """The row a launch leaves while EDH has not listed its desktop yet."""
    row = own_row(state="CREATING", edhState=None, connectionAvailable=False, **overrides)
    row.pop("sessionId")
    return row


@pytest.mark.unit
class TestWorkstationList:
    def test_rows_are_read_from_the_user_id_index_for_the_caller(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 200
        assert [item["sessionId"] for item in _items(response)] == [SESSION_OWN]
        query_kwargs = fakes.table.query.call_args[1]
        assert query_kwargs["IndexName"] == "userIdIndex"
        assert _key_condition(query_kwargs["KeyConditionExpression"]) == ("userId", "=", USER)
        fakes.table.scan.assert_not_called()

    def test_a_foreign_row_from_the_index_is_still_excluded(self, monkeypatch):
        """Second line behind the key condition: a wrong IndexName can never list someone else's row."""
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [own_row(), other_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert [item["sessionId"] for item in _items(response)] == [SESSION_OWN]

    def test_edh_state_is_mapped_onto_the_row_and_persisted(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch,
            edh=FakeEdhClient(get_desktop_states=[_desktop(SESSION_OWN, state="stopped", connection_string="")]))
        fakes.table.query.return_value = {"Items": [own_row(edhState="running", connectionAvailable=True)]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        item = _items(response)[0]
        assert (item["state"], item["edhState"], item["connectionAvailable"]) == ("STOPPED", "stopped", False)
        fakes.table.update_item.assert_called()
        assert fakes.table.update_item.call_args[1]["Key"] == {"bootstrapId": BOOTSTRAP_OWN}
        assert {"STOPPED", "stopped", False} <= set(_update_values(fakes))
        assert (edh_uid_for(USER), [SESSION_OWN]) in fakes.edh.calls_to("get_desktop_states")

    def test_a_running_desktop_is_active_and_connectable(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[state_record(SESSION_OWN)]))
        fakes.table.query.return_value = {"Items": [
            own_row(state="CREATING", edhState="pending", connectionAvailable=False)]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        item = _items(response)[0]
        assert (item["state"], item["edhState"], item["connectionAvailable"]) == ("ACTIVE", "running", True)
        assert {"running", True} <= set(_update_values(fakes))

    def test_a_row_that_matches_edh_is_not_rewritten(self, monkeypatch):
        """Control: only what differs from the stored row is written, so an unchanged row costs no write."""
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[_desktop(SESSION_OWN)]))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 200
        fakes.table.update_item.assert_not_called()

    def test_a_row_absent_from_edh_keeps_its_stored_state(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[_desktop("someone-elses")]))
        fakes.table.query.return_value = {
            "Items": [own_row(state="STOPPED", edhState="stopped", connectionAvailable=False)]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        item = _items(response)[0]
        assert (item["state"], item["edhState"], item["connectionAvailable"]) == ("STOPPED", "stopped", False)
        fakes.table.update_item.assert_not_called()

    def test_a_creating_row_without_a_session_id_is_returned_as_stored_without_an_edh_call(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [_session_less_creating_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls == []
        fakes.client_factory.assert_not_called()
        fakes.table.update_item.assert_not_called()
        assert _items(response) == [service._record(_session_less_creating_row())]

    def test_the_list_reads_the_state_of_the_rows_with_a_session_id(self, monkeypatch):
        creating = _session_less_creating_row(bootstrapId=BOOTSTRAP_CREATING)
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [
            own_row(), own_row(bootstrapId="b-2", sessionId=SESSION_SECOND), creating]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 200
        reads = fakes.edh.calls_to("get_desktop_states")
        assert {(uid, session) for uid, sessions in reads for session in sessions} == {
            (edh_uid_for(USER), SESSION_OWN), (edh_uid_for(USER), SESSION_SECOND)}
        assert fakes.edh.calls_to("get_desktop_connection") == []
        assert len(fakes.edh.calls) <= 1

    def test_a_desktop_in_error_orphans_the_row_with_a_generic_code(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_states=[_desktop(SESSION_OWN, state="error")]))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        item = _items(response)[0]
        assert (item["state"], item["lastError"]) == ("ORPHANED", LAST_ERROR_LAUNCH_ERROR)

    def test_a_launch_whose_desktop_edh_no_longer_reports_is_shown_as_a_failed_launch(self, monkeypatch):
        """EDH deactivates a desktop whose placement failed within minutes, after which its session-state
        read leaves the id out: a CREATING row then reads as the failed launch it is, with its key taken
        back, instead of "starting" until a sweep records it deleted."""
        revoke_fn = MagicMock(return_value={"apiKeyId": "", "keyExpiresAt": "", "secretName": ""})
        monkeypatch.setattr(bootstrap, "revoke_session", revoke_fn)
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[]))
        fakes.table.query.return_value = {"Items": [
            own_row(state="CREATING", edhState="placing", connectionAvailable=False,
                    apiKeyId="key-own", secretName="vams/edh-session/own")]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        item = _items(response)[0]
        assert (item["state"], item["lastError"], item["connectionAvailable"]) == (
            "ORPHANED", LAST_ERROR_LAUNCH_ERROR, False)
        written = [set(call[1]["ExpressionAttributeValues"].values())
                   for call in fakes.table.update_item.call_args_list]
        # The failed launch is written first; the revoke then clears the credential attributes, and the read of
        # EDH's reason for the failure (none scripted here) stamps the row.
        assert written, "the failed launch was never written"
        assert len(written) <= 3
        assert {"ORPHANED", LAST_ERROR_LAUNCH_ERROR} <= written[0]
        assert {(c.args[1]["bootstrapId"], c.kwargs["delete_secret"]) for c in revoke_fn.call_args_list} == {
            (BOOTSTRAP_OWN, True)}

    def test_a_launch_error_code_the_launch_recorded_is_kept_on_the_failed_launch(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[]))
        fakes.table.query.return_value = {"Items": [
            own_row(state="CREATING", edhState="placing", connectionAvailable=False,
                    lastError="CAPACITY_UNAVAILABLE")]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert (_items(response)[0]["state"], _items(response)[0]["lastError"]) == (
            "ORPHANED", "CAPACITY_UNAVAILABLE")

    def test_a_launch_edh_answers_with_a_successor_is_not_failed(self, monkeypatch):
        # Control: EDH continued the desktop under another session id, which the sweep follows.
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_states=[], state_successors=[SESSION_OWN]))
        fakes.table.query.return_value = {"Items": [
            own_row(state="CREATING", edhState="pending", connectionAvailable=False)]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert _items(response)[0]["state"] == "CREATING"
        fakes.table.update_item.assert_not_called()

    def test_an_active_row_edh_no_longer_reports_is_left_to_the_sweep(self, monkeypatch):
        # Control: a desktop that ran may be held out of EDH's answer by Save and Resume.
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[]))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert _items(response)[0]["state"] == "ACTIVE"
        fakes.table.update_item.assert_not_called()

    def test_a_failed_read_leaves_a_creating_row_alone(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_states=EdhUnavailableError("timeout")))
        fakes.table.query.return_value = {"Items": [
            own_row(state="CREATING", edhState="placing", connectionAvailable=False)]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 502
        fakes.table.update_item.assert_not_called()

    def test_a_failed_launch_shows_on_the_single_workstation_read_too(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[]))
        row = own_row(state="CREATING", edhState="placing", connectionAvailable=False)
        fakes.table.query.return_value = {"Items": [row]}
        fakes.table.get_item.return_value = {"Item": dict(row)}

        response = fakes.module.lambda_handler(make_event("GET", workstation_path(SESSION_OWN)), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["lastError"]) == ("ORPHANED", LAST_ERROR_LAUNCH_ERROR)

    def test_deleted_rows_are_not_listed(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {
            "Items": [own_row(), own_row(bootstrapId="deleted-1", sessionId="deleted-session", state="DELETED")]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert [item["sessionId"] for item in _items(response)] == [SESSION_OWN]

    def test_no_rows_means_no_edh_call(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert _items(response) == []
        fakes.client_factory.assert_not_called()
        assert edh_proxy_records(fakes) == []

    def test_each_row_is_enforced_as_an_edh_workstation_with_get(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {
            "Items": [own_row(imageId="42", osFamily="linux", instanceType="g5.xlarge")]}

        fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        fakes.enforcer.enforce.assert_any_call(
            {"object__type": "edhWorkstation", "edhImageId": "42", "edhOsFamily": "linux",
             "edhInstanceType": "g5.xlarge"},
            "GET",
        )

    def test_a_denied_row_is_dropped_and_an_allowed_one_kept(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, enforce=lambda obj, act: obj["edhInstanceType"] == "g5.xlarge")
        fakes.table.query.return_value = {
            "Items": [own_row(), own_row(bootstrapId="b-2", sessionId="s-2", instanceType="p4d.24xlarge")]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert [item["sessionId"] for item in _items(response)] == [SESSION_OWN]

    def test_the_index_is_read_to_exhaustion(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        pager = Pager(
            {"Items": [own_row()], "LastEvaluatedKey": {"bootstrapId": "page-1"}},
            {"Items": [own_row(bootstrapId="b-2", sessionId="s-2")]},
            name="userIdIndex",
        )
        fakes.table.query.side_effect = pager

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert sorted(item["sessionId"] for item in _items(response)) == sorted([SESSION_OWN, "s-2"])
        pager.assert_paged_to_exhaustion()

    def test_edh_unavailable_is_a_502(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_states=EdhUnavailableError("timeout")))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 502
        assert {("workstationList", "EdhUnavailableError")} <= {
            (record["operation"], record["edhStatus"]) for record in edh_proxy_records(fakes)}

    def test_a_rest_shaped_event_is_served(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH, rest=True), {})

        assert response["statusCode"] == 200
        assert len(_items(response)) == 1

    def test_the_captured_session_state_is_read_through_the_field_constants(self, monkeypatch):
        """The client's session-state constants name the keys EDH's session-state answer carries, and
        the record built from a captured entry through them is matched to its row and observed."""
        fixture = load_fixture("desktops_session_state")
        session_id = fixture["request"]["query"][ec.EDH_SESSION_ID_FIELD]
        entry = fixture["response"]["json"]["message"][session_id]
        record = {
            service.EDH_FIELD_SESSION_UUID: session_id,
            service.EDH_FIELD_SESSION_STATE: entry[ec.EDH_DESKTOP_STATE_FIELD],
            EDH_FIELD_INSTANCE_ID: entry[EDH_FIELD_INSTANCE_ID],
        }
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[record]))
        fakes.table.query.return_value = {"Items": [
            own_row(sessionId=session_id, edhState=None, connectionAvailable=False)]}

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        reads = fakes.edh.calls_to("get_desktop_states")
        assert {(uid, session) for uid, sessions in reads for session in sessions} == {
            (edh_uid_for(USER), session_id)}
        item = _items(response)[0]
        assert item["edhState"] == entry[ec.EDH_DESKTOP_STATE_FIELD]
        assert entry[EDH_FIELD_INSTANCE_ID] in _update_values(fakes)
