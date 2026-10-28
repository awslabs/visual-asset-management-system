# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The get and list routes notice a running desktop whose display session has ended (D21).

D20 records ``connectionUnavailableReason`` ``DISPLAY_SESSION_ENDED`` only from a record that carries EDH's
connection link, and the get and list routes read EDH's token-free session-state route, which carries none. So a
desktop whose DCV session ended while it kept running read Connectable until someone chose Connect. The get route
now also makes the read Connect makes, EDH's per-user listing narrowed to the one session, for an ACTIVE row with a
session id that EDH's state read reports running; the list route makes it for such rows whose last check
(``connectionCheckedAt``) is absent or five minutes old, oldest first, at most two per request. The verdict is
D20's ``connection_verdict``; the check's stamp is written with it. The link carries a DCV token EDH issues on this
read and is never returned, logged or stored. Every attempt writes the stamp: a read that fails, or that lists no
record of the desktop, writes it alone and leaves the rest of the row as the state read left it, so a desktop whose
check keeps failing waits out the interval and cannot take a list slot on every refresh. Connect and Windows sign-in
make the same read and write the stamp too. A check that changes nothing else writes its stamp alone and leaves
``updatedAt``, which the session sweep reads to defer a row a request has just written.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common.edhClient import EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    SESSION_OWN,
    USER,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    state_record,
    workstation_path,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

FALLBACK_URL = "https://edh.example.internal/?#" + "a" * 36
GOOD_URL = "https://edh.example.internal/?authToken=s3cr3t-t0k3n&username=vabc#" + SESSION_OWN
PASSWORD = "winpw-" + "x" * 12
STAMP = "connectionCheckedAt"
SESSION_B = "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e"
SESSION_C = "3c4d5e6f-7a8b-4c9d-8e1f-2a3b4c5d6e7f"
SESSION_D = "4d5e6f7a-8b9c-4d0e-9f2a-3b4c5d6e7f8a"
# The audit trail's (verb, route) of the per-user listing read.
CONNECTION_AUDIT_ROUTE = service.EDH_AUDIT_ROUTES["get_desktop_connection"]


def _desktop(session_id=SESSION_OWN, connection_string=GOOD_URL, state="running"):
    """A desktop as EDH's per-user listing renders it, with synthetic credential values."""
    return {
        service.EDH_FIELD_SESSION_UUID: session_id,
        service.EDH_FIELD_SESSION_STATE: state,
        service.EDH_FIELD_CONNECTION_STRING: connection_string,
        service.EDH_FIELD_SESSION_OWNER: edh_uid_for(USER),
        service.EDH_FIELD_LOCAL_ADMIN_PASSWORD: PASSWORD,
    }


class _PerSessionEdh(FakeEdhClient):
    """A scripted client whose per-user listing answers each session id from ``links``: a link renders the desktop
    with it, an exception is raised, and a missing id has no record."""

    def __init__(self, links, **results):
        super().__init__(**results)
        self.links = links

    def get_desktop_connection(self, edh_uid, session_id):
        self.calls.append(("get_desktop_connection", (edh_uid, session_id)))
        link = self.links.get(session_id)
        if isinstance(link, Exception):
            raise link
        return None if link is None else _desktop(session_id, link)


def _installed(monkeypatch, rows, edh):
    fakes = install_service_fakes(monkeypatch, edh=edh)
    fakes.table.query.return_value = {"Items": list(rows)}
    logger = MagicMock()
    monkeypatch.setattr(service, "logger", logger)
    fakes.logger = logger
    return fakes


def _get(fakes):
    event = make_event("GET", workstation_path(SESSION_OWN), path_params={"sessionId": SESSION_OWN})
    return fakes.module.lambda_handler(event, {})


def _list(fakes):
    return fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})


def _writes(fakes):
    """Each row update the handler made: (bootstrapId, {attribute name: value})."""
    writes = []
    for call in fakes.table.update_item.call_args_list:
        names = call.kwargs["ExpressionAttributeNames"]
        values = call.kwargs["ExpressionAttributeValues"]
        written = {name: values[":v" + placeholder[2:]] for placeholder, name in names.items()
                   if placeholder.startswith("#a") and ":v" + placeholder[2:] in values}
        writes.append((call.kwargs["Key"]["bootstrapId"], written))
    return writes


def _written(fakes):
    return {name: value for _key, written in _writes(fakes) for name, value in written.items()}


def _assert_stamp_alone(fakes):
    """The handler made one write, the check stamp alone, conditioned on the row being live, updatedAt untouched."""
    (call,) = fakes.table.update_item.call_args_list
    assert [set(written) for _key, written in _writes(fakes)] == [{STAMP}]
    assert "attribute_exists(bootstrapId)" in call.kwargs["ConditionExpression"]


def _checked_sessions(fakes):
    return [session_id for _uid, session_id in fakes.edh.calls_to("get_desktop_connection")]


def _ago(seconds):
    return (service.datetime.now(service.timezone.utc) - service.timedelta(seconds=seconds)).isoformat()


def _no_link_leaves(fakes, response, link):
    assert link not in response["body"]
    assert PASSWORD not in response["body"]
    assert link not in json.dumps(_writes(fakes), default=str)
    assert link not in str(fakes.logger.mock_calls)
    assert link not in str(fakes.log_actions.mock_calls)


@pytest.mark.unit
class TestGetChecksTheDisplaySession:
    def test_a_fallback_link_records_the_session_ended(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)],
            get_desktop_connection=_desktop(connection_string=FALLBACK_URL)))

        response = _get(fakes)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["connectionAvailable"], body["connectionUnavailableReason"], body["activity"]) == (
            "ACTIVE", False, "DISPLAY_SESSION_ENDED", None)
        assert _checked_sessions(fakes) == [SESSION_OWN]
        written = _written(fakes)
        assert (written["connectionAvailable"], written["connectionUnavailableReason"]) == (
            False, "DISPLAY_SESSION_ENDED")
        assert "state" not in written
        assert isinstance(written[STAMP], str)
        service.datetime.fromisoformat(written[STAMP])
        assert len(fakes.table.update_item.call_args_list) == 1
        _no_link_leaves(fakes, response, FALLBACK_URL)

    def test_a_signing_link_clears_a_recorded_reason(self, monkeypatch):
        ended = own_row(connectionAvailable=False, connectionUnavailableReason="DISPLAY_SESSION_ENDED")
        fakes = _installed(monkeypatch, [ended], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=_desktop()))

        response = _get(fakes)

        body = response_body(response)
        assert (body["connectionAvailable"], body["connectionUnavailableReason"], body["activity"]) == (
            True, None, None)
        written = _written(fakes)
        assert (written["connectionAvailable"], written["connectionUnavailableReason"]) == (True, None)
        assert STAMP in written
        _no_link_leaves(fakes, response, GOOD_URL)

    def test_a_check_that_changes_nothing_else_writes_its_stamp_alone(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=_desktop()))

        response = _get(fakes)

        assert response_body(response)["connectionAvailable"] is True
        assert [set(written) for _key, written in _writes(fakes)] == [{STAMP}]
        _no_link_leaves(fakes, response, GOOD_URL)

    def test_the_check_runs_on_every_get(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row(connectionCheckedAt=_ago(5))], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=_desktop()))

        _get(fakes)

        assert _checked_sessions(fakes) == [SESSION_OWN]

    def test_the_check_is_audited_as_the_get(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=_desktop()))

        _get(fakes)

        records = [record for record in edh_proxy_records(fakes)
                   if (record["edhMethod"], record["edhRoute"]) == CONNECTION_AUDIT_ROUTE]
        assert [(record["operation"], record["edhStatus"]) for record in records] == [("workstationGet", "ok")]

    def test_a_failed_read_answers_the_state_read_and_logs_a_warning(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)],
            get_desktop_connection=EdhUnavailableError("timeout " + GOOD_URL)))

        response = _get(fakes)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["connectionAvailable"], body["connectionUnavailableReason"]) == (
            "ACTIVE", True, None)
        _assert_stamp_alone(fakes)
        (warning,) = fakes.logger.warning.call_args_list
        assert warning.kwargs["extra"]["error"] == "EdhUnavailableError"
        assert warning.kwargs["extra"]["sessionId"] == SESSION_OWN
        _no_link_leaves(fakes, response, GOOD_URL)

    def test_a_read_refused_for_its_arguments_is_logged_the_same_way(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=ValueError("bad id")))

        response = _get(fakes)

        assert response["statusCode"] == 200
        _assert_stamp_alone(fakes)
        assert fakes.logger.warning.call_args.kwargs["extra"]["error"] == "ValueError"

    def test_no_record_in_the_listing_stamps_the_check_alone(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=None))

        response = _get(fakes)

        assert _checked_sessions(fakes) == [SESSION_OWN]
        body = response_body(response)
        assert (body["state"], body["connectionAvailable"], body["connectionUnavailableReason"]) == (
            "ACTIVE", True, None)
        _assert_stamp_alone(fakes)

    def test_a_row_deleted_since_it_was_read_gets_no_stamp_and_the_get_answers(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=EdhUnavailableError("timeout")))
        fakes.table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

        response = _get(fakes)

        assert response["statusCode"] == 200
        assert fakes.table.update_item.call_count == 1
        assert STAMP not in response_body(response)

    @pytest.mark.parametrize("row, edh_state", [
        (own_row(state="STOPPED", edhState="stopped", connectionAvailable=False), "stopped"),
        (own_row(state="CREATING", edhState="pending", connectionAvailable=False), "pending"),
    ], ids=["stopped", "creating"])
    def test_a_desktop_that_is_not_running_is_not_checked(self, monkeypatch, row, edh_state):
        fakes = _installed(monkeypatch, [row], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN, edh_state)], get_desktop_connection=_desktop()))

        response = _get(fakes)

        assert response["statusCode"] == 200
        assert _checked_sessions(fakes) == []

    def test_a_row_the_state_read_moves_out_of_running_is_not_checked(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN, "stopped")], get_desktop_connection=_desktop()))

        body = response_body(_get(fakes))

        assert body["state"] == "STOPPED"
        assert _checked_sessions(fakes) == []

    def test_a_desktop_the_state_read_does_not_answer_is_not_checked(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[], get_desktop_connection=_desktop()))

        _get(fakes)

        assert _checked_sessions(fakes) == []

    def test_the_stamp_is_not_on_the_record(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row(connectionCheckedAt=_ago(30))], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=_desktop()))

        body = response_body(_get(fakes))

        assert STAMP in _written(fakes)
        assert STAMP not in body


@pytest.mark.unit
class TestListChecksDueDisplaySessions:
    def _row(self, bootstrap_id, session_id, **overrides):
        return own_row(bootstrapId=bootstrap_id, sessionId=session_id, **overrides)

    def test_only_a_row_whose_stamp_is_due_is_checked(self, monkeypatch):
        rows = [self._row("b-fresh", SESSION_OWN, connectionCheckedAt=_ago(60)),
                self._row("b-stale", SESSION_B, connectionCheckedAt=_ago(600))]
        fakes = _installed(monkeypatch, rows, _PerSessionEdh(
            {SESSION_OWN: GOOD_URL, SESSION_B: FALLBACK_URL},
            get_desktop_states=[state_record(SESSION_OWN), state_record(SESSION_B)]))

        response = _list(fakes)

        assert response["statusCode"] == 200
        assert _checked_sessions(fakes) == [SESSION_B]
        items = {item["bootstrapId"]: item for item in response_body(response)["Items"]}
        assert (items["b-stale"]["connectionAvailable"], items["b-stale"]["connectionUnavailableReason"]) == (
            False, "DISPLAY_SESSION_ENDED")
        assert (items["b-fresh"]["connectionAvailable"], items["b-fresh"]["connectionUnavailableReason"]) == (
            True, None)
        assert [key for key, written in _writes(fakes) if STAMP in written] == ["b-stale"]
        assert all(STAMP not in item for item in items.values())
        _no_link_leaves(fakes, response, FALLBACK_URL)

    def test_at_most_two_rows_are_checked_oldest_first(self, monkeypatch):
        rows = [self._row("b-400", SESSION_OWN, connectionCheckedAt=_ago(400)),
                self._row("b-900", SESSION_B, connectionCheckedAt=_ago(900)),
                self._row("b-never", SESSION_C),
                self._row("b-350", SESSION_D, connectionCheckedAt=_ago(350))]
        fakes = _installed(monkeypatch, rows, _PerSessionEdh(
            {session: GOOD_URL for session in (SESSION_OWN, SESSION_B, SESSION_C, SESSION_D)},
            get_desktop_states=[state_record(session) for session in (SESSION_OWN, SESSION_B, SESSION_C, SESSION_D)]))

        response = _list(fakes)

        assert response["statusCode"] == 200
        assert _checked_sessions(fakes) == [SESSION_C, SESSION_B]
        assert sorted(key for key, written in _writes(fakes) if STAMP in written) == ["b-900", "b-never"]
        _no_link_leaves(fakes, response, GOOD_URL)

    def test_rows_that_are_not_running_or_hold_no_session_are_skipped(self, monkeypatch):
        session_less = own_row(bootstrapId="b-creating", state="CREATING", edhState=None, connectionAvailable=False)
        session_less.pop("sessionId")
        rows = [self._row("b-stopped", SESSION_OWN, state="STOPPED", edhState="stopped", connectionAvailable=False),
                self._row("b-starting", SESSION_B, state="CREATING", edhState="pending", connectionAvailable=False),
                session_less,
                self._row("b-running", SESSION_C)]
        fakes = _installed(monkeypatch, rows, _PerSessionEdh(
            {SESSION_OWN: GOOD_URL, SESSION_B: GOOD_URL, SESSION_C: GOOD_URL},
            get_desktop_states=[state_record(SESSION_OWN, "stopped"), state_record(SESSION_B, "pending"),
                                state_record(SESSION_C)]))

        response = _list(fakes)

        assert response["statusCode"] == 200
        assert _checked_sessions(fakes) == [SESSION_C]

    def test_a_failed_read_leaves_the_list_answering(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=EdhUnavailableError("timeout")))

        response = _list(fakes)

        assert response["statusCode"] == 200
        assert response_body(response)["Items"][0]["connectionAvailable"] is True
        _assert_stamp_alone(fakes)
        assert fakes.logger.warning.call_args.kwargs["extra"]["operation"] == "workstationList"

    def test_rows_whose_check_keeps_failing_wait_out_the_interval_while_a_third_gets_its_turn(self, monkeypatch):
        # Two rows ahead in the queue whose check fails (EDH errors on one, lists no record of the other), and a
        # third due row behind them. The handler mirrors each write onto the row dicts the table serves, so the
        # second request reads the stamps the first wrote.
        rows = [self._row("b-erroring", SESSION_OWN),
                self._row("b-unlisted", SESSION_B, connectionCheckedAt=_ago(900)),
                self._row("b-waiting", SESSION_C, connectionCheckedAt=_ago(600))]
        fakes = _installed(monkeypatch, rows, _PerSessionEdh(
            {SESSION_OWN: EdhUnavailableError("timeout"), SESSION_C: FALLBACK_URL},
            get_desktop_states=[state_record(session) for session in (SESSION_OWN, SESSION_B, SESSION_C)]))

        first = _list(fakes)
        second = _list(fakes)

        assert (first["statusCode"], second["statusCode"]) == (200, 200)
        assert _checked_sessions(fakes) == [SESSION_OWN, SESSION_B, SESSION_C]
        failing = [written for key, written in _writes(fakes) if key in ("b-erroring", "b-unlisted")]
        assert [set(written) for written in failing] == [{STAMP}, {STAMP}]
        items = {item["bootstrapId"]: item for item in response_body(second)["Items"]}
        assert items["b-waiting"]["connectionUnavailableReason"] == "DISPLAY_SESSION_ENDED"
        assert (items["b-erroring"]["connectionAvailable"], items["b-unlisted"]["connectionAvailable"]) == (True, True)

        _list(fakes)

        assert _checked_sessions(fakes) == [SESSION_OWN, SESSION_B, SESSION_C]

    def test_the_check_is_audited_as_the_list(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN)], get_desktop_connection=_desktop()))

        _list(fakes)

        operations = [record["operation"] for record in edh_proxy_records(fakes)
                      if (record["edhMethod"], record["edhRoute"]) == CONNECTION_AUDIT_ROUTE]
        assert operations == ["workstationList"]


@pytest.mark.unit
class TestConnectStampsTheCheck:
    def test_connect_writes_the_stamp_with_its_observation(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(get_desktop_connection=_desktop()))
        event = make_event("GET", workstation_path(SESSION_OWN, "connect"), path_params={"sessionId": SESSION_OWN})

        response = fakes.module.lambda_handler(event, {})

        assert response["statusCode"] == 200
        assert response_body(response)["connectionUrl"] == GOOD_URL
        assert [set(written) for _key, written in _writes(fakes)] == [{STAMP}]
        assert GOOD_URL not in json.dumps(_writes(fakes), default=str)
        assert GOOD_URL not in str(fakes.logger.mock_calls)

    def test_a_refused_connect_writes_the_stamp_with_the_reason(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row()], FakeEdhClient(
            get_desktop_connection=_desktop(connection_string=FALLBACK_URL)))
        event = make_event("GET", workstation_path(SESSION_OWN, "connect"), path_params={"sessionId": SESSION_OWN})

        response = fakes.module.lambda_handler(event, {})

        assert response["statusCode"] == 409
        (write,) = [written for _key, written in _writes(fakes)]
        assert write["connectionUnavailableReason"] == "DISPLAY_SESSION_ENDED"
        assert STAMP in write and "updatedAt" in write


@pytest.mark.unit
class TestWindowsSignInStampsTheCheck:
    def _sign_in(self, fakes):
        event = make_event("GET", workstation_path(SESSION_OWN, "windows-sign-in"),
                           path_params={"sessionId": SESSION_OWN})
        return fakes.module.lambda_handler(event, {})

    def test_sign_in_writes_the_stamp_alone_when_nothing_else_changed(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row(osFamily="windows")], FakeEdhClient(
            get_desktop_connection=_desktop()))

        response = self._sign_in(fakes)

        assert response["statusCode"] == 200
        assert response_body(response)["password"] == PASSWORD
        assert [set(written) for _key, written in _writes(fakes)] == [{STAMP}]
        written = json.dumps(_writes(fakes), default=str)
        assert GOOD_URL not in written and PASSWORD not in written
        assert PASSWORD not in str(fakes.logger.mock_calls)

    def test_a_refused_sign_in_writes_the_stamp_with_the_reason(self, monkeypatch):
        fakes = _installed(monkeypatch, [own_row(osFamily="windows")], FakeEdhClient(
            get_desktop_connection=_desktop(connection_string=FALLBACK_URL)))

        response = self._sign_in(fakes)

        assert response["statusCode"] == 409
        assert response_body(response)["code"] == "DISPLAY_SESSION_ENDED"
        (write,) = [written for _key, written in _writes(fakes)]
        assert write["connectionUnavailableReason"] == "DISPLAY_SESSION_ENDED"
        assert STAMP in write and "updatedAt" in write
