# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``EdhClient.get_desktop_failure``: the launch-failure reason EDH recorded for one of the caller's desktops
(in-dev D8).

The read asks EDH's per-user listing for the one session, active first and, when EDH answers no record of
it, inactive (EDH deactivates a desktop whose placement failed within minutes); it never sends a third
request. Only a record whose ``session_uuid`` is the id asked and whose ``session_owner`` is the caller
counts. From that record it reads the ``recent_events`` alone and returns the shared parser's map, never
the connection string, the tokens or the password EDH renders beside them, and logs none of them.
"""

import json
import urllib.parse

import pytest

from backend.backend.handlers.addon.edh.common import edhClient as _ec  # noqa: F401
from backend.tests.handlers.addon.edh.edhTestSupport import FakeResponse, install_client_fakes

UID = "vabcdefghij23456"
SESSION_A = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
SESSION_B = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
# Credential-shaped values EDH renders beside the events; none may come back or reach a log line.
TOKEN = "tok-" + "z" * 24
PASSWORD = "pw-" + "q" * 16
CAPACITY_TEXT = ("An error occurred (InsufficientInstanceCapacity) when calling the CreateCapacityReservation "
                 "operation (reached max retries: 4): Insufficient capacity.")
CAPACITY_FAILURE = {"event": "placement_failed", "errorCode": "InsufficientInstanceCapacity",
                    "operation": "CreateCapacityReservation"}


def _event(event_type, sub_status="", checkpoint="", received_at="2026-10-22T15:22:09.000000+00:00"):
    return {"id": "e-1", "event_type": event_type, "checkpoint": checkpoint, "sub_status": sub_status,
            "received_at": received_at}


def _record(session_id=SESSION_A, owner=UID, events=(), **fields):
    """A desktop as EDH's per-user listing renders it, credentials included."""
    record = {
        "session_uuid": session_id, "session_owner": owner, "session_state": "error",
        "session_name": "3f2a9c1e4b7d4e0a9d2b6c1f8a7e5b30",
        "connection_string": "https://dcv.example.internal/host/?authToken=" + TOKEN + "#" + session_id,
        "authentication_token": TOKEN, "session_token": TOKEN, "broker_session_id": TOKEN,
        "session_local_admin_password": PASSWORD, "last_event_type": "placement_failed",
        "last_checkpoint": None, "recent_events": list(events),
    }
    record.update(fields)
    return record


def _answer(*records):
    """EDH's listing envelope: the records keyed by session id."""
    return FakeResponse(200, {"success": True, "message": {record["session_uuid"]: record for record in records}})


def _failed(**fields):
    return _record(events=[_event("session-ready"), _event("placement_failed", CAPACITY_TEXT)], **fields)


def _queries(recorded):
    """(wire path, query) of every request sent, in order."""
    sent = []
    for call in recorded["calls"]:
        parsed = urllib.parse.urlsplit(call["url"])
        sent.append((parsed.path, dict(urllib.parse.parse_qsl(parsed.query))))
    return sent


class _RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, message, *args, **kwargs):
        self.records.append([message, kwargs.get("extra")])

    debug = info = warning = error = exception = _record


@pytest.mark.unit
class TestGetDesktopFailure:
    def test_an_active_record_answers_from_one_request_as_the_caller(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_answer(_failed())])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) == CAPACITY_FAILURE

        assert _queries(recorded) == [(ec.EDH_ROUTE_DESKTOPS_LIST, {"is_active": "true", "session_uuid": SESSION_A})]
        assert recorded["calls"][0]["method"] == "GET"
        assert recorded["calls"][0]["headers"][ec.EDH_USER_HEADER] == UID

    def test_a_desktop_edh_deactivated_is_read_from_the_inactive_listing(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                            results=[_answer(), _answer(_failed())])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) == CAPACITY_FAILURE

        assert _queries(recorded) == [
            (ec.EDH_ROUTE_DESKTOPS_LIST, {"is_active": "true", "session_uuid": SESSION_A}),
            (ec.EDH_ROUTE_DESKTOPS_LIST, {"is_active": "false", "session_uuid": SESSION_A}),
        ]
        assert recorded["calls"][1]["headers"][ec.EDH_USER_HEADER] == UID

    def test_an_active_record_without_a_failure_is_no_failure_and_asks_nothing_more(self, monkeypatch):
        record = _record(events=[_event("session-ready")], session_state="running")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_answer(record)])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) is None
        assert [query["is_active"] for _path, query in _queries(recorded)] == ["true"]

    def test_no_record_in_either_listing_is_no_failure_after_two_requests(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                            results=[_answer(), _answer(), _answer(_failed())])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) is None
        assert [query["is_active"] for _path, query in _queries(recorded)] == ["true", "false"]

    @pytest.mark.parametrize("record", [
        _failed(owner="vsomeoneelse12345"),
        _failed(owner=None),
        _failed(session_id=SESSION_B),
    ], ids=["another owner", "no owner", "another session"])
    def test_a_record_of_another_owner_or_session_is_no_record(self, monkeypatch, record):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                            results=[_answer(record), _answer(record)])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) is None
        # Neither answer held the caller's desktop, so the inactive listing was asked as well.
        assert [query["is_active"] for _path, query in _queries(recorded)] == ["true", "false"]

    def test_the_owners_record_beside_another_owners_is_the_one_read(self, monkeypatch):
        foreign = _record(session_id=SESSION_B, owner="vsomeoneelse12345",
                          events=[_event("placement_failed", "An error occurred (VcpuLimitExceeded) when "
                                                             "calling the RunInstances operation")])
        ec, _recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_answer(foreign, _failed())])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) == CAPACITY_FAILURE

    def test_the_newest_failure_event_is_returned(self, monkeypatch):
        events = [
            _event("placement_failed", CAPACITY_TEXT, received_at="2026-10-22T15:20:00.000000+00:00"),
            _event("placed", received_at="2026-10-22T15:21:00.000000+00:00"),
            _event("session-failed", "bootstrap failed", "dcv_server_start",
                   received_at="2026-10-22T15:24:00.000000+00:00"),
        ]
        ec, _recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_answer(_record(events=events))])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) == {
            "event": "session-failed", "checkpoint": "dcv_server_start"}

    def test_text_that_names_no_aws_error_is_a_failure_without_a_code(self, monkeypatch):
        record = _record(events=[_event("placement_failed", "No capacity in any subnet")])
        ec, _recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_answer(record)])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) == {"event": "placement_failed"}

    @pytest.mark.parametrize("events", [None, "placement_failed", {"0": _event("placement_failed")}],
                             ids=["none", "a string", "a mapping"])
    def test_events_edh_does_not_render_as_a_list_are_no_failure(self, monkeypatch, events):
        ec, _recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                             results=[_answer(_record(recent_events=events))])

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A) is None

    def test_no_credential_edh_renders_comes_back(self, monkeypatch):
        ec, _recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_answer(_failed())])

        failure = ec.EdhClient().get_desktop_failure(UID, SESSION_A)

        assert set(failure) <= {"event", "errorCode", "operation", "checkpoint"}
        text = json.dumps(failure)
        assert TOKEN not in text
        assert PASSWORD not in text
        assert "Insufficient capacity." not in text

    def test_edh_text_is_logged_cut_short_and_no_credential_is_logged(self, monkeypatch):
        long_text = CAPACITY_TEXT + " " + "x" * 1000
        record = _record(events=[_event("placement_failed", long_text)])
        ec, _recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_answer(), _answer(record)])
        recording = _RecordingLogger()
        monkeypatch.setattr(ec, "logger", recording)

        assert ec.EdhClient().get_desktop_failure(UID, SESSION_A)["errorCode"] == "InsufficientInstanceCapacity"

        logged = json.dumps(recording.records)
        assert recording.records, "EDH's failure text was not logged"
        assert CAPACITY_TEXT in logged
        assert "x" * 300 not in logged
        assert TOKEN not in logged
        assert PASSWORD not in logged
        assert "connection_string" not in logged

    def test_a_refused_read_raises_its_typed_error_and_asks_nothing_more(self, monkeypatch):
        refusal = FakeResponse(500, {"success": False, "message": "Your administrator has disabled virtual desktops"})
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[refusal, _answer(_failed())])

        with pytest.raises(ec.EdhError):
            ec.EdhClient().get_desktop_failure(UID, SESSION_A)
        assert [query["is_active"] for _path, query in _queries(recorded)] == ["true"]

    def test_an_id_that_is_not_a_uuid_or_an_empty_uid_is_refused_before_any_request(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[])
        for session_id in ("a,b", SESSION_A + "," + SESSION_B, SESSION_A + "x", "", None):
            with pytest.raises(ValueError):
                ec.EdhClient().get_desktop_failure(UID, session_id)
        for uid in ("", None):
            with pytest.raises(ValueError):
                ec.EdhClient().get_desktop_failure(uid, SESSION_A)
        assert recorded["calls"] == []
