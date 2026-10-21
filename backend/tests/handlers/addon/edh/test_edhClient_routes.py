# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixture-backed contract of every EDH route wrapper.

Each wrapper is asserted against its sanitized EDH capture: the route constant it sends must be the
capture's ``request.path`` (an absolute ``/api/...`` route, so a handler reading the constant and the
client sending it cannot disagree), the URL on the wire its ``request.absolutePath``, and the query,
user header and form body what the capture carries, so an EDH-side assumption that is wrong fails
here rather than on the first live call. Response-side assertions use an oracle that does not go
through the client -- the count of ``session_uuid`` keys in ``response.json``, the length of the
captured ``message`` -- cross-checked against the producer's ``expect`` values.

EDH answers every list as a mapping keyed by id and every create with prose; the wrappers hand back
lists of rows and the prose under ``message``. The captures are source-derived until the live spike
replaces them; a capture that contradicts the client is a fixture to re-cut, never a test to loosen.
"""

import json
import urllib.parse
from unittest.mock import MagicMock

import pytest

# Imported at module level so a client that fails to import is a collection error for the whole file.
from backend.backend.handlers.addon.edh.common import edhClient as _ec  # noqa: F401
from backend.backend.handlers.addon.edh.common.edhSessionState import EDH_SCHEDULE_DAYS, never_auto_start_schedule
from backend.tests.handlers.addon.edh.edhTestSupport import (
    REDACTED,
    USER_HEADER,
    FakeResponse,
    count_key,
    first_value_of,
    fixture_or_skip,
    install_client_fakes,
    load_fixture,
    replay_response,
    sent_form,
)

# The directory lookup's "nobody" answer, in EDH's envelope.
EMPTY_DIRECTORY = {"success": True, "message": []}

# Two distinct desktop session ids in EDH's uuid4 form.
SESSION_A = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
SESSION_B = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
CONNECTION_URL = "https://dcv.example.internal/?authToken=abc"


def _schedule_with(**days):
    """The never-auto-start schedule with the given days replaced."""
    schedule = never_auto_start_schedule()
    schedule.update(days)
    return schedule


def _schedule_without(day):
    schedule = never_auto_start_schedule()
    schedule.pop(day)
    return schedule


# Schedules EDH's validator would take or refuse but its scheduler could not read as 0/0 minutes.
MALFORMED_SCHEDULES = {
    "not a dict": [never_auto_start_schedule()],
    "already serialized": json.dumps(never_auto_start_schedule()),
    "missing day": _schedule_without("sunday"),
    "missing stop": _schedule_with(monday={"start": 0}),
    "string minute": _schedule_with(monday={"start": "0", "stop": 0}),
    "bool minute": _schedule_with(tuesday={"start": False, "stop": 0}),
    "float minute": _schedule_with(wednesday={"start": 0.0, "stop": 0}),
    "past the last minute": _schedule_with(friday={"start": 0, "stop": 1441}),
    "negative minute": _schedule_with(saturday={"start": -1, "stop": 0}),
    "day not a window": _schedule_with(sunday=0),
}


def _ok(payload):
    """An EDH success envelope around ``payload``."""
    return FakeResponse(200, {"success": True, "message": payload})


def _refusal(text, status=500):
    """EDH's refusal envelope; SocaError appends the request id to every message."""
    return FakeResponse(status, {"success": False,
                                 "message": f"{text} (Request ID: 3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30)"})


def _capture(key):
    """The fixture and a FakeResponse replaying its recorded status and body."""
    fixture = fixture_or_skip(key)
    return fixture, replay_response(fixture)


def _only_call(recorded):
    assert len(recorded["calls"]) == 1, recorded["calls"]
    return recorded["calls"][0]


def _sent(call):
    """(method, wire path, query dict) of one recorded attempt."""
    parsed = urllib.parse.urlsplit(call["url"])
    return call["method"], parsed.path, dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))


def _captured_uid(fixture):
    uid = fixture["request"]["headers"].get(USER_HEADER)
    assert uid, f"{fixture['name']}: the capture carries no {USER_HEADER}"
    return uid


def _actor_uid(ec, fixture):
    """The uid an actor-header wrapper is called with: the capture's when it recorded one, else the default."""
    return fixture["request"]["headers"].get(USER_HEADER) or ec.EDH_SERVICE_ACTOR_UID


def _payload_count(fixture):
    """How many rows the captured EDH payload holds, read without the client's unwrapping."""
    payload = fixture["response"]["json"].get("message")
    return len(payload) if isinstance(payload, (list, dict)) else 0


def _expected_count(fixture):
    """The independent row count, cross-checked against the producer's ``expect.count`` when recorded."""
    expected = _payload_count(fixture)
    if "count" in fixture["expect"]:
        assert fixture["expect"]["count"] == expected, f"{fixture['name']}: expect.count disagrees with its body"
    return expected


def _assert_matches_capture(ec, call, fixture, *, route, method, user_header):
    """One attempt against the capture: route constant, method, wire path, query and the user header
    (``user_header`` is the uid the call must carry, or None for a header-less route)."""
    sent_method, path, query = _sent(call)
    request = fixture["request"]
    assert route == request["path"] == request["absolutePath"], (route, request["path"], request["absolutePath"])
    assert request["method"] == method, (request["method"], method)
    assert (sent_method, path) == (method, request["absolutePath"])
    assert query == request["query"]
    if user_header is None:
        assert ec.EDH_USER_HEADER not in call["headers"]
    else:
        assert call["headers"][ec.EDH_USER_HEADER] == user_header


def _assert_form_matches(ec, call, captured_body, *, ignore=()):
    """The sent form is urlencoded and has the capture's fields; values compare except where the
    sanitizer rewrote them (``REDACTED``) or the test says otherwise."""
    assert call["headers"]["Content-Type"] == ec.EDH_FORM_CONTENT_TYPE
    sent = sent_form(call)
    assert sent is not None, "no form body was sent"
    assert set(sent) == set(captured_body), (sorted(sent), sorted(captured_body))
    for key, captured in captured_body.items():
        if captured == REDACTED or key in ignore:
            continue
        assert sent[key] == str(captured), key


class _RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, message, *args, **kwargs):
        self.records.append([message, kwargs.get("extra")])

    debug = info = warning = error = exception = _record


@pytest.mark.unit
class TestUserHeaderPolicy:
    def test_a_user_scoped_route_refuses_a_call_without_the_header(self):
        # EDH answers a header-less call to a route that reads X-EDH-USER with 400, not 401: every
        # call carries a uid (the caller's, or the service actor's).
        fixture = load_fixture("auth_missing_user_header")
        assert USER_HEADER not in fixture["request"]["headers"]
        assert fixture["response"]["status"] == 400
        assert "X-EDH-USER" in json.dumps(fixture["response"]["json"])

    def test_a_request_given_no_uid_carries_the_service_actor(self, monkeypatch):
        # EDH's feature flags evaluate X-EDH-USER on the routes they gate, cluster-scoped ones included,
        # so a call without a caller uid names the service actor rather than nobody.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({})])
        ec.EdhClient().request("GET", ec.EDH_ROUTE_EKS_CLUSTERS)
        assert _only_call(recorded)["headers"][ec.EDH_USER_HEADER] == ec.EDH_SERVICE_ACTOR_UID == "vamsservice"

    @pytest.mark.parametrize("wrapper,args", [
        ("list_software_stacks", ("dcv",)),
        ("list_profiles", ("dcv",)),
        ("list_batch_job_definitions", ()),
        ("list_batch_queues", ()),
        ("list_eks_clusters", ()),
    ])
    def test_a_cluster_scoped_wrapper_sends_a_supplied_uid_in_place_of_the_service_actor(self, monkeypatch,
                                                                                          wrapper, args):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({})])
        getattr(ec.EdhClient(), wrapper)(*args, edh_uid="vcaller1234567890")
        assert _only_call(recorded)["headers"][ec.EDH_USER_HEADER] == "vcaller1234567890"


@pytest.mark.unit
class TestSessionName:
    def test_a_bootstrap_id_becomes_its_32_hex_digits(self):
        name = _ec.edh_session_name("3f2c9d4e-8a1b-4c6d-9e0f-1a2b3c4d5e6f")
        assert name == "3f2c9d4e8a1b4c6d9e0f1a2b3c4d5e6f"
        assert len(name) == 32 and name.isalnum()

    def test_a_value_edh_would_rewrite_is_refused(self):
        # EDH strips non-alphanumerics silently; a name that would come back different is refused here.
        for bad in ("not a uuid!", "a" * 33, ""):
            with pytest.raises(ValueError):
                _ec.edh_session_name(bad)

    def test_the_create_capture_sent_a_session_name_in_that_shape(self):
        fixture = load_fixture("desktop_create")
        name = fixture["request"]["body"][_ec.EDH_SESSION_NAME_FIELD]
        assert name == fixture["expect"]["sessionName"]
        assert _ec.edh_session_name(name) == name


@pytest.mark.unit
class TestDesktopRoutes:
    def test_a_connection_row_without_its_uuid_is_given_the_mapping_key(self, monkeypatch):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[
            _ok({SESSION_A: {"session_name": "x", "session_owner": "vabc"}})])
        assert ec.EdhClient().get_desktop_connection("vabc", SESSION_A) == {
            "session_uuid": SESSION_A, "session_name": "x", "session_owner": "vabc"}

    def test_list_all_desktops_carries_the_acting_uid(self, monkeypatch):
        fixture, response = _capture("desktops_list_all")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        uid = _actor_uid(ec, fixture)
        result = ec.EdhClient().list_all_desktops(edh_uid=uid)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_ROUTE_DESKTOPS_LIST_ALL, method="GET",
                                user_header=uid)
        expected = count_key(fixture["response"]["json"], ec.EDH_SESSION_ID_FIELD)
        assert expected >= 1
        assert expected == fixture["expect"]["count"]
        assert len(result) == expected

    def test_list_all_desktops_defaults_to_the_service_actor(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({})])
        assert ec.EdhClient().list_all_desktops() == []
        call = _only_call(recorded)
        assert call["headers"][ec.EDH_USER_HEADER] == ec.EDH_SERVICE_ACTOR_UID == "vamsservice"
        assert _sent(call)[1] == ec.EDH_ROUTE_DESKTOPS_LIST_ALL

    @pytest.mark.parametrize("fixture_key,state", [
        ("desktops_session_state", "running"),
        ("desktops_session_state_stopped", "stopped"),
    ])
    def test_get_desktop_states_reads_the_session_state_route_for_the_asked_ids(self, monkeypatch, fixture_key,
                                                                                state):
        fixture, response = _capture(fixture_key)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        session_id = fixture["request"]["query"][ec.EDH_SESSION_ID_FIELD]
        entry = fixture["response"]["json"]["message"][session_id]
        # Control on the oracle: the capture answers the asked id with the state under test.
        assert entry["state"] == state

        result = ec.EdhClient().get_desktop_states(_captured_uid(fixture), [session_id])

        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_ROUTE_DESKTOPS_SESSION_STATE, method="GET",
                                user_header=_captured_uid(fixture))
        assert _sent(call)[2] == {"session_uuid": session_id}
        assert result == [{"session_uuid": session_id, "session_state": state,
                           "instance_id": entry["instance_id"]}]

    def test_get_desktop_states_sends_nothing_for_no_session_ids(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[])
        assert ec.EdhClient().get_desktop_states("vabc", []) == []
        assert ec.EdhClient().get_desktop_states("vabc", [None, ""]) == []
        for uid in ("", None):
            with pytest.raises(ValueError):
                ec.EdhClient().get_desktop_states(uid, [SESSION_A])
        assert recorded["calls"] == []

    def test_get_desktop_states_never_sends_an_id_that_is_not_a_uuid(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({})])
        ec.EdhClient().get_desktop_states("vabc", [SESSION_A, "a,b", "x" * 36, SESSION_A.upper() + ",", SESSION_A])
        sent = _sent(_only_call(recorded))[2]
        assert sent == {"session_uuid": SESSION_A}
        assert "," not in sent["session_uuid"]

    def test_get_desktop_states_asks_forty_ids_per_request(self, monkeypatch):
        ids = [f"{index:08x}-0000-4000-8000-{index:012x}" for index in range(41)]
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({}), _ok({})])
        ec.EdhClient().get_desktop_states("vabc", ids)
        batches = [_sent(call)[2]["session_uuid"].split(",") for call in recorded["calls"]]
        assert ec.EDH_SESSION_STATE_BATCH_SIZE == 40
        assert batches == [ids[:40], ids[40:]]

    def test_get_desktop_states_drops_a_superseded_or_unasked_entry(self, monkeypatch):
        answer = {
            SESSION_A: {"state": "running", "instance_id": "i-1"},
            # EDH answers an id whose desktop was replaced under the same name with its successor's values.
            SESSION_B: {"state": "running", "instance_id": "i-2",
                        "current_session_uuid": "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"},
            "0f1e2d3c-4b5a-4968-8776-5a4b3c2d1e0f": {"state": "running", "instance_id": "i-3"},
        }
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok(answer)])
        assert ec.EdhClient().get_desktop_states("vabc", [SESSION_A, SESSION_B]) == [
            {"session_uuid": SESSION_A, "session_state": "running", "instance_id": "i-1"}]

    @pytest.mark.parametrize("response", [_ok([]), _ok("text"), FakeResponse(200, {"message": {}})],
                             ids=["list", "text", "no success key"])
    def test_get_desktop_states_refuses_an_answer_that_is_not_a_mapping(self, monkeypatch, response):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        with pytest.raises(ec.EdhContractError):
            ec.EdhClient().get_desktop_states("vabc", [SESSION_A])

    @pytest.mark.parametrize("entry", [{"state": "pending", "instance_id": None}, {"state": "pending"}],
                             ids=["null instance", "no instance key"])
    def test_get_desktop_states_keeps_a_record_without_an_instance_id(self, monkeypatch, entry):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({SESSION_A: entry})])
        assert ec.EdhClient().get_desktop_states("vabc", [SESSION_A]) == [
            {"session_uuid": SESSION_A, "session_state": "pending"}]

    @pytest.mark.parametrize("entry,expected", [
        ({"instance_id": "i-1"}, [{"session_uuid": SESSION_A, "instance_id": "i-1"}]),
        ({"state": "", "instance_id": "i-1"}, [{"session_uuid": SESSION_A, "instance_id": "i-1"}]),
        ("x", []),
    ], ids=["no state key", "empty state", "not a mapping"])
    def test_get_desktop_states_returns_an_entry_without_a_state_without_one(self, monkeypatch, entry, expected):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({SESSION_A: entry})])
        assert ec.EdhClient().get_desktop_states("vabc", [SESSION_A]) == expected

    def test_get_desktop_connection_asks_for_the_one_session(self, monkeypatch):
        fixture = fixture_or_skip("desktops_list_active")
        rows = fixture["response"]["json"]["message"]
        assert len(rows) >= 2
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                            results=[replay_response(fixture) for _ in rows])
        uid = _captured_uid(fixture)
        for session_id, row in rows.items():
            result = ec.EdhClient().get_desktop_connection(uid, session_id)
            call = recorded["calls"][-1]
            assert _sent(call)[1] == ec.EDH_ROUTE_DESKTOPS_LIST
            assert _sent(call)[2] == {"is_active": "true", "session_uuid": session_id}
            assert call["headers"][ec.EDH_USER_HEADER] == uid
            assert result == row
            assert "connection_string" in result
        assert len(recorded["calls"]) == len(rows)

    def test_get_desktop_connection_refuses_an_id_that_is_not_a_uuid_before_any_request(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[])
        for session_id in ("a,b", SESSION_A + "," + SESSION_B, SESSION_A + "x", "", None):
            with pytest.raises(ValueError):
                ec.EdhClient().get_desktop_connection("vabc", session_id)
        for uid in ("", None):
            with pytest.raises(ValueError):
                ec.EdhClient().get_desktop_connection(uid, SESSION_A)
        assert recorded["calls"] == []

    @pytest.mark.parametrize("rows", [
        {SESSION_A: {"session_uuid": SESSION_A, "session_owner": "vother", "connection_string": CONNECTION_URL}},
        {SESSION_A: {"session_uuid": SESSION_A, "connection_string": CONNECTION_URL}},
        {SESSION_B: {"session_uuid": SESSION_B, "session_owner": "vabc", "connection_string": CONNECTION_URL}},
    ], ids=["another owner", "no owner", "another session"])
    def test_get_desktop_connection_is_none_for_another_owner_or_no_owner(self, monkeypatch, rows):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok(rows)])
        assert ec.EdhClient().get_desktop_connection("vabc", SESSION_A) is None

    def test_create_desktop_posts_the_captured_form_and_returns_the_prose(self, monkeypatch):
        fixture, response = _capture("desktop_create")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        form = dict(fixture["request"]["body"])
        result = ec.EdhClient().create_desktop(_captured_uid(fixture), form)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_ROUTE_DESKTOPS_CREATE, method="POST",
                                user_header=_captured_uid(fixture))
        _assert_form_matches(ec, call, fixture["request"]["body"])
        # EDH's create answer is prose: no session_uuid anywhere in it; the uuid is read back by name.
        assert first_value_of(fixture["response"]["json"], ec.EDH_SESSION_ID_FIELD) is None
        assert result == {"message": fixture["response"]["json"]["message"]}

    def test_create_desktop_refuses_a_field_edh_would_drop(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().create_desktop("vabc", {"instance_type": "g5.xlarge", "tags": "edh:x=y"})
        assert recorded["calls"] == []

    @pytest.mark.parametrize("wrapper,fixture_key,method,route_name", [
        ("stop_desktop", "desktop_stop", "PUT", "EDH_ROUTE_DESKTOPS_STOP"),
        ("start_desktop", "desktop_start", "PUT", "EDH_ROUTE_DESKTOPS_START"),
        ("delete_desktop", "desktop_delete", "DELETE", "EDH_ROUTE_DESKTOPS_DELETE"),
    ])
    def test_session_mutations_name_the_session_in_the_form(self, monkeypatch, wrapper, fixture_key, method,
                                                            route_name):
        fixture, response = _capture(fixture_key)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        session_id = fixture["request"]["body"][ec.EDH_SESSION_ID_FIELD]
        assert session_id == fixture["expect"]["sessionUuid"]
        result = getattr(ec.EdhClient(), wrapper)(_captured_uid(fixture), session_id)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=getattr(ec, route_name), method=method,
                                user_header=_captured_uid(fixture))
        _assert_form_matches(ec, call, fixture["request"]["body"])
        assert result == {"message": fixture["response"]["json"]["message"]}

    def test_find_desktop_by_name_reads_the_cluster_listing_as_the_owner(self, monkeypatch):
        fixture, response = _capture("desktops_list_after_create")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        session_name, session_uuid = fixture["expect"]["sessionName"], fixture["expect"]["sessionUuid"]
        # Control on the oracle: the capture names both values, on a row the captured uid owns.
        serialized = json.dumps(fixture["response"]["json"])
        assert session_name in serialized and session_uuid in serialized
        assert fixture["response"]["json"]["message"][session_uuid]["session_owner"] == _captured_uid(fixture)
        result = ec.EdhClient().find_desktop_by_name(_captured_uid(fixture), session_name)
        call = _only_call(recorded)
        assert _sent(call)[:2] == ("GET", ec.EDH_ROUTE_DESKTOPS_LIST_ALL)
        assert _sent(call)[2] == {}
        assert call["headers"][ec.EDH_USER_HEADER] == _captured_uid(fixture)
        assert result[ec.EDH_SESSION_ID_FIELD] == session_uuid
        assert result[ec.EDH_SESSION_NAME_FIELD] == session_name

    def test_find_desktop_by_name_ignores_a_desktop_of_another_owner_or_none(self, monkeypatch):
        foreign = {
            "s-other": {"session_name": "abc", "session_owner": "vother", "created_on": "2026-09-11T10:05:00Z"},
            "s-ownerless": {"session_name": "abc", "created_on": "2026-09-11T10:09:00Z"},
        }
        own = {"s-own": {"session_name": "abc", "session_owner": "vabc", "created_on": "2026-09-11T10:00:00Z"}}
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok(foreign), _ok({**foreign, **own})])
        assert ec.EdhClient().find_desktop_by_name("vabc", "abc") is None
        assert ec.EdhClient().find_desktop_by_name("vabc", "abc")["session_uuid"] == "s-own"

    def test_find_desktop_by_name_refuses_an_empty_uid(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[])
        for uid in ("", None):
            with pytest.raises(ValueError):
                ec.EdhClient().find_desktop_by_name(uid, "abc")
        assert recorded["calls"] == []

    def test_find_desktop_by_name_prefers_the_newest_row_and_ignores_other_names(self, monkeypatch):
        rows = {
            "s-old": {"session_name": "abc", "session_owner": "vabc", "created_on": "2026-09-11T10:00:00Z"},
            "s-new": {"session_name": "abc", "session_owner": "vabc", "created_on": "2026-09-11T10:05:00Z"},
            "s-other": {"session_name": "xyz", "session_owner": "vabc", "created_on": "2026-09-11T10:09:00Z"},
        }
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok(rows)])
        assert ec.EdhClient().find_desktop_by_name("vabc", "abc")["session_uuid"] == "s-new"

    def test_find_desktop_by_name_is_none_until_edh_lists_the_row(self, monkeypatch):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok({})])
        assert ec.EdhClient().find_desktop_by_name("vabc", "abc") is None


@pytest.mark.unit
class TestDesktopSchedule:
    """EDH stores the schedule string it is sent and its scheduler reads it back with ``json.loads``,
    comparing integer minutes; its validator is looser (a Python literal, string minutes), so the client
    is what keeps an unreadable schedule off a desktop. EDH finds the desktop by the acting user as its
    owner."""

    def test_the_schedule_update_puts_the_captured_form_as_the_owner(self, monkeypatch):
        fixture, response = _capture("desktop_schedule")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        body = fixture["request"]["body"]
        session_id = body[ec.EDH_SESSION_ID_FIELD]
        assert session_id == fixture["expect"]["sessionUuid"]

        result = ec.EdhClient().update_desktop_schedule(_captured_uid(fixture), session_id,
                                                         json.loads(body[ec.EDH_SCHEDULE_FIELD]))

        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_ROUTE_DESKTOPS_SCHEDULE, method="PUT",
                                user_header=_captured_uid(fixture))
        _assert_form_matches(ec, call, body)
        assert set(body) == ec.EDH_DESKTOP_SCHEDULE_FIELDS
        assert result == {"message": fixture["response"]["json"]["message"]}

    def test_the_schedule_goes_out_as_json_with_integer_minutes(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok("updated")])
        schedule = never_auto_start_schedule()

        ec.EdhClient().update_desktop_schedule("vowner1234567890", "sess-1", schedule)

        sent = sent_form(_only_call(recorded))[ec.EDH_SCHEDULE_FIELD]
        assert json.loads(sent) == schedule
        assert sent != str(schedule)
        parsed = json.loads(sent)
        assert set(parsed) == set(EDH_SCHEDULE_DAYS)
        assert all(type(minute) is int for window in parsed.values() for minute in window.values())
        assert schedule == never_auto_start_schedule()

    def test_the_owner_is_the_acting_user_not_the_service_actor(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok("updated")])
        ec.EdhClient().update_desktop_schedule("vowner1234567890", "sess-1", never_auto_start_schedule())
        call = _only_call(recorded)
        assert call["headers"][ec.EDH_USER_HEADER] == "vowner1234567890" != ec.EDH_SERVICE_ACTOR_UID
        assert sent_form(call)[ec.EDH_SESSION_ID_FIELD] == "sess-1"

    @pytest.mark.parametrize("schedule", list(MALFORMED_SCHEDULES.values()), ids=list(MALFORMED_SCHEDULES))
    def test_a_schedule_edh_could_not_read_back_is_refused_before_any_call(self, monkeypatch, schedule):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().update_desktop_schedule("vowner1234567890", "sess-1", schedule)
        assert recorded["calls"] == []

    def test_the_first_and_last_minute_of_the_day_are_accepted(self, monkeypatch):
        """Control for the range refusals: EDH's bounds are inclusive."""
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok("updated")])
        schedule = _schedule_with(monday={"start": 0, "stop": 1440})
        ec.EdhClient().update_desktop_schedule("vowner1234567890", "sess-1", schedule)
        assert json.loads(sent_form(_only_call(recorded))[ec.EDH_SCHEDULE_FIELD]) == schedule

    @pytest.mark.parametrize("key,error_name", [
        ("desktop_schedule_disabled", "EdhApiError"),
        ("desktop_schedule_not_found", "EdhNotFoundError"),
    ])
    def test_a_refused_schedule_update_raises_its_typed_error(self, monkeypatch, key, error_name):
        fixture, response = _capture(key)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        body = fixture["request"]["body"]
        with pytest.raises(getattr(ec, error_name)):
            ec.EdhClient().update_desktop_schedule(_captured_uid(fixture), body[ec.EDH_SESSION_ID_FIELD],
                                                   json.loads(body[ec.EDH_SCHEDULE_FIELD]))
        _assert_matches_capture(ec, _only_call(recorded), fixture, route=ec.EDH_ROUTE_DESKTOPS_SCHEDULE,
                                method="PUT", user_header=_captured_uid(fixture))

    def test_a_schedule_update_whose_answer_is_lost_is_not_sent_again(self, monkeypatch):
        import urllib3
        lost = urllib3.exceptions.ReadTimeoutError(None, "/api/dcv/virtual_desktops/schedule", "Read timed out.")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[lost, _ok("updated")])
        with pytest.raises(ec.EdhOutcomeUnknownError):
            ec.EdhClient(profile="batch").update_desktop_schedule(
                "vowner1234567890", "sess-1", never_auto_start_schedule())
        _only_call(recorded)
        assert "PUT" not in ec._RETRIED_METHODS


@pytest.mark.unit
class TestCatalogRoutes:
    @pytest.mark.parametrize("kind,fixture_key", [
        ("dcv", "software_stacks_dcv"),
        ("target_nodes", "software_stacks_target_nodes"),
    ])
    def test_list_software_stacks_per_kind(self, monkeypatch, kind, fixture_key):
        fixture, response = _capture(fixture_key)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        result = ec.EdhClient().list_software_stacks(kind)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_SOFTWARE_STACK_ROUTES[kind], method="GET",
                                user_header=ec.EDH_SERVICE_ACTOR_UID)
        assert isinstance(result, list)
        assert len(result) == _expected_count(fixture)
        assert all(isinstance(row, dict) and ec.EDH_SOFTWARE_STACK_ID_FIELD in row for row in result)
        if kind == "dcv":
            assert result, "the source-derived DCV catalog registers at least one stack"

    def test_an_empty_dcv_catalog_is_an_empty_list_not_an_error(self, monkeypatch):
        # EDH answers an empty software-stack table with a 500 refusal worded "No AMI found".
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_refusal("No AMI found")])
        assert ec.EdhClient().list_software_stacks("dcv") == []

    def test_any_other_catalog_refusal_still_raises(self, monkeypatch):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[_refusal("Database Error: Query software_stacks. Helper: x")])
        with pytest.raises(ec.EdhApiError):
            ec.EdhClient().list_software_stacks("dcv")

    @pytest.mark.parametrize("kind,fixture_key", [("dcv", "profiles_dcv"), ("target_nodes", "profiles_target_nodes")])
    def test_list_profiles_per_kind(self, monkeypatch, kind, fixture_key):
        fixture, response = _capture(fixture_key)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        result = ec.EdhClient().list_profiles(kind)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_PROFILE_ROUTES[kind], method="GET", user_header=ec.EDH_SERVICE_ACTOR_UID)
        assert isinstance(result, list)
        assert len(result) == _expected_count(fixture)
        assert all(isinstance(row, dict) and ec.EDH_SOFTWARE_STACK_ID_FIELD in row for row in result)

    def test_an_empty_profile_table_is_an_empty_list(self, monkeypatch):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok("No Virtual Desktop Profiles found")])
        assert ec.EdhClient().list_profiles("dcv") == []

    def test_an_unknown_kind_is_refused_before_any_call(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        for wrapper in ("list_software_stacks", "list_profiles"):
            with pytest.raises(ValueError):
                getattr(ec.EdhClient(), wrapper)("windows")
        with pytest.raises(ValueError):
            ec.EdhClient().register_software_stack("windows", {"ami_id": "ami-1"})
        assert recorded["calls"] == []

    @pytest.mark.parametrize("kind,fixture_key", [
        ("dcv", "software_stack_register_dcv"),
        ("target_nodes", "software_stack_register_target_nodes"),
    ])
    def test_register_software_stack_sends_the_captured_form_with_the_acting_uid(self, monkeypatch, kind,
                                                                                 fixture_key):
        fixture, response = _capture(fixture_key)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        captured = fixture["request"]["body"]
        assert isinstance(captured, dict) and captured, f"{fixture['name']}: no form fields recorded"
        # Control: the capture used only fields EDH's parser reads for this kind.
        unknown = set(captured) - ec.EDH_SOFTWARE_STACK_REGISTER_FIELDS[kind]
        assert not unknown, sorted(unknown)
        uid = _actor_uid(ec, fixture)
        result = ec.EdhClient(profile="batch").register_software_stack(kind, dict(captured), edh_uid=uid)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_SOFTWARE_STACK_ROUTES[kind], method="POST",
                                user_header=uid)
        _assert_form_matches(ec, call, captured)
        assert result == {"message": fixture["response"]["json"]["message"]}

    def test_a_thumbnail_makes_the_registration_multipart(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok("ami-1 registered")])
        ec.EdhClient().register_software_stack("dcv", {"ami_id": "ami-1", "stack_name": "Ubuntu 22 GPU"},
                                               thumbnail=("thumb.png", b"\x89PNG", "image/png"))
        call = _only_call(recorded)
        assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
        assert call["headers"][ec.EDH_USER_HEADER] == ec.EDH_SERVICE_ACTOR_UID
        assert b'name="stack_name"' in call["body"] and b"Ubuntu 22 GPU" in call["body"]
        assert b'name="thumbnail"; filename="thumb.png"' in call["body"]

    def test_register_refuses_a_field_edh_would_drop(self, monkeypatch):
        # target_nodes stacks have no base_os: EDH derives the OS family from the AMI.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().register_software_stack("target_nodes", {"ami_id": "ami-1", "base_os": "linux"})
        assert recorded["calls"] == []

    def test_delete_software_stack_names_the_stack_in_the_form(self, monkeypatch):
        fixture, response = _capture("software_stack_delete_dcv")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        image_id = fixture["request"]["body"][ec.EDH_SOFTWARE_STACK_DELETE_FIELD]
        assert str(image_id) == str(fixture["expect"]["imageId"])
        uid = _actor_uid(ec, fixture)
        result = ec.EdhClient().delete_software_stack("dcv", image_id, edh_uid=uid)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_SOFTWARE_STACK_ROUTES["dcv"], method="DELETE",
                                user_header=uid)
        _assert_form_matches(ec, call, fixture["request"]["body"])
        assert result == {"message": fixture["response"]["json"]["message"]}

    def test_the_row_id_and_the_delete_field_are_different_names(self):
        # The list routes key each row by `id`; the DELETE routes read `software_stack_id` from the form.
        assert (_ec.EDH_SOFTWARE_STACK_ID_FIELD, _ec.EDH_SOFTWARE_STACK_DELETE_FIELD) == ("id", "software_stack_id")


@pytest.mark.unit
class TestProjectRoutes:
    """The project listing and the assignment that makes a registered stack launchable.

    EDH resolves a user's launchable stacks through the projects a stack is attached to, and its
    project update is a full replace: a field the update leaves out is cleared. Every assertion here
    is on the form the client sends, so a cleared member list or a dropped budget fails here.
    """

    PROJECT = {
        "id": 1, "project_name": "default", "is_active": True, "description": "Default project",
        "aws_budget": None, "hardware_profile_id": None,
        "software_stack_ids": [10], "target_node_software_stack_ids": [11],
        "application_profile_ids": [3, 4],
        "allowed_users": "alice,bob", "denied_users": "eve",
        "allowed_groups": "designers", "denied_groups": "contractors",
    }

    def _projects(self, *rows):
        """EDH's project listing: the envelope's message is a mapping keyed by project id."""
        return _ok({str(row["id"]): dict(row) for row in rows})

    def _row(self, **fields):
        return {**self.PROJECT, **fields}

    def test_list_projects_reads_the_project_route_as_the_service_actor(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(self.PROJECT)])
        result = ec.EdhClient().list_projects()
        call = _only_call(recorded)
        assert _sent(call) == ("GET", ec.EDH_ROUTE_PROJECTS, {})
        assert call["headers"][ec.EDH_USER_HEADER] == ec.EDH_SERVICE_ACTOR_UID
        assert [row[ec.EDH_PROJECT_NAME_FIELD] for row in result] == ["default"]

    def test_a_cluster_with_no_project_is_an_empty_list(self, monkeypatch):
        # EDH words an empty project table as the message "No Project found".
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[_ok("No Project found")])
        assert ec.EdhClient().list_projects() == []

    def test_assigning_a_vdi_stack_echoes_every_field_with_the_id_added(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(self.PROJECT), _ok("Project updated")])
        changed = ec.EdhClient().assign_software_stack_to_project("dcv", 20, "default")
        assert changed is True
        assert len(recorded["calls"]) == 2, recorded["calls"]
        listed, update = recorded["calls"]
        assert _sent(listed)[:2] == ("GET", ec.EDH_ROUTE_PROJECTS)
        assert _sent(update)[:2] == ("PUT", ec.EDH_ROUTE_PROJECTS)
        assert update["headers"][ec.EDH_USER_HEADER] == ec.EDH_SERVICE_ACTOR_UID
        assert sent_form(update) == {
            "project_id": "1",
            "description": "Default project",
            "allowed_users": "alice,bob",
            "denied_users": "eve",
            "allowed_groups": "designers",
            "denied_groups": "contractors",
            "application_profile_ids": "3,4",
            "software_stack_ids": "10,20",
            "target_nodes_software_stack_ids": "11",
        }

    def test_assigning_a_simulation_stack_lands_in_the_target_node_list(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(self.PROJECT), _ok("Project updated")])
        assert ec.EdhClient().assign_software_stack_to_project("target_nodes", 20, "default") is True
        form = sent_form(recorded["calls"][1])
        assert form["target_nodes_software_stack_ids"] == "11,20"
        assert form["software_stack_ids"] == "10"

    def test_the_two_kinds_are_read_and_written_under_different_names(self):
        # EDH's project listing reports the target-node stacks as `target_node_software_stack_ids`;
        # its update reads them as `target_nodes_software_stack_ids`.
        assert _ec.EDH_PROJECT_STACK_ID_FIELDS["target_nodes"] == (
            "target_node_software_stack_ids", "target_nodes_software_stack_ids")
        assert _ec.EDH_PROJECT_STACK_ID_FIELDS["dcv"] == ("software_stack_ids", "software_stack_ids")
        assert set(_ec.EDH_PROJECT_STACK_ID_FIELDS) == set(_ec.EDH_SOFTWARE_STACK_ROUTES)

    @pytest.mark.parametrize("kind,stack_id", [("dcv", 10), ("dcv", "10"), ("target_nodes", 11)])
    def test_a_stack_the_project_already_carries_sends_no_update(self, monkeypatch, kind, stack_id):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(self.PROJECT)])
        assert ec.EdhClient().assign_software_stack_to_project(kind, stack_id, "default") is False
        assert _sent(_only_call(recorded))[0] == "GET"

    def test_a_budget_and_a_hardware_profile_are_echoed_when_the_project_carries_them(self, monkeypatch):
        row = self._row(aws_budget="vams-workstations", hardware_profile_id=4)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(row), _ok("Project updated")])
        ec.EdhClient().assign_software_stack_to_project("dcv", 20, "default")
        form = sent_form(recorded["calls"][1])
        assert form["aws_budget"] == "vams-workstations" and form["hardware_profile_id"] == "4"

    def test_a_project_without_a_description_is_updated_with_an_empty_one(self, monkeypatch):
        # A missing required form field is a CLIENT_MISSING_PARAMETER refusal, so None is sent as "".
        row = self._row(description=None, application_profile_ids=[], target_node_software_stack_ids=[])
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(row), _ok("Project updated")])
        ec.EdhClient().assign_software_stack_to_project("dcv", 20, "default")
        form = sent_form(recorded["calls"][1])
        assert form["description"] == ""
        assert form["application_profile_ids"] == "" and form["target_nodes_software_stack_ids"] == ""

    def test_the_update_sends_only_fields_the_project_route_parses(self, monkeypatch):
        row = self._row(aws_budget="b", hardware_profile_id=1)
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(row), _ok("Project updated")])
        ec.EdhClient().assign_software_stack_to_project("dcv", 20, "default")
        form = sent_form(recorded["calls"][1])
        unknown = set(form) - ec.EDH_PROJECT_UPDATE_FIELDS
        assert not unknown, sorted(unknown)
        assert set(ec.EDH_PROJECT_MEMBERSHIP_FIELDS) <= set(form)

    def test_a_project_the_cluster_does_not_have_is_not_found(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                           results=[self._projects(self.PROJECT)])
        with pytest.raises(ec.EdhNotFoundError, match="vams-workstations"):
            ec.EdhClient().assign_software_stack_to_project("dcv", 20, "vams-workstations")
        assert _sent(_only_call(recorded))[0] == "GET"

    def test_an_unknown_kind_is_refused_before_any_call(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().assign_software_stack_to_project("windows", 20, "default")
        assert recorded["calls"] == []


@pytest.mark.unit
class TestEnsureUser:
    def test_user_exists_reads_the_directory_lookup(self, monkeypatch):
        fixture, response = _capture("ldap_user_get_by_uid")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        uid = fixture["request"]["query"][ec.EDH_LDAP_USER_FIELD]
        # Control on the oracle: the capture names the user it was asked about.
        assert uid in json.dumps(fixture["response"]["json"])
        assert ec.EdhClient().user_exists(uid) is True
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_ROUTE_LDAP_USER, method="GET", user_header=ec.EDH_SERVICE_ACTOR_UID)
        assert "body" not in call

    def test_an_absent_user_is_an_empty_lookup(self, monkeypatch):
        fixture, response = _capture("ldap_user_get_by_uid_absent")
        assert fixture["response"]["status"] == 200 and fixture["response"]["json"]["message"] == []
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        assert ec.EdhClient().user_exists(fixture["request"]["query"][ec.EDH_LDAP_USER_FIELD]) is False
        _assert_matches_capture(ec, _only_call(recorded), fixture, route=ec.EDH_ROUTE_LDAP_USER, method="GET",
                                user_header=ec.EDH_SERVICE_ACTOR_UID)

    def test_ensure_user_runs_under_the_batch_profile_only(self, monkeypatch):
        # Creating a user takes EDH a directory poll and two key generations; the api budget cannot hold it.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().ensure_user("vabc")
        assert recorded["calls"] == []

    def test_a_listed_user_is_probed_and_not_created(self, monkeypatch):
        fixture, response = _capture("ldap_user_get_by_uid")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        uid = fixture["request"]["query"][ec.EDH_LDAP_USER_FIELD]
        assert ec.EdhClient(profile="batch").ensure_user(uid, "alice") is None
        _assert_matches_capture(ec, _only_call(recorded), fixture, route=ec.EDH_ROUTE_LDAP_USER, method="GET",
                                user_header=ec.EDH_SERVICE_ACTOR_UID)

    def test_with_provisioning_enabled_an_unlisted_user_is_created_after_the_probe(self, monkeypatch):
        fixture, response = _capture("ldap_user_create")
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(200, EMPTY_DIRECTORY), response])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        captured = fixture["request"]["body"]
        edh_uid = captured[ec.EDH_LDAP_USER_FIELD]
        assert edh_uid == fixture["expect"]["edhUid"]
        assert set(captured) <= ec.EDH_LDAP_CREATE_FIELDS
        assert ec.EdhClient(profile="batch").ensure_user(edh_uid, "alice") is None
        probe, create = recorded["calls"]
        assert _sent(probe) == ("GET", ec.EDH_ROUTE_LDAP_USER, {ec.EDH_LDAP_USER_FIELD: edh_uid})
        # The probed and created uid is not a directory principal yet; the service actor acts.
        assert probe["headers"][ec.EDH_USER_HEADER] == ec.EDH_SERVICE_ACTOR_UID
        _assert_matches_capture(ec, create, fixture, route=ec.EDH_ROUTE_LDAP_USER, method="POST", user_header=ec.EDH_SERVICE_ACTOR_UID)
        # The password is redacted in the capture and random here; the sanitizer rewrites addresses.
        _assert_form_matches(ec, create, captured, ignore=(ec.EDH_LDAP_EMAIL_FIELD,))
        sent = sent_form(create)
        assert sent[ec.EDH_LDAP_EMAIL_FIELD] == f"{edh_uid}@{ec.EDH_LDAP_EMAIL_DOMAIN}"
        assert sent["sudoers"] == "0" and sent["shell"] == "/bin/bash"

    def test_the_password_is_random_strong_and_never_logged(self, monkeypatch):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(200, EMPTY_DIRECTORY), 200, FakeResponse(200, EMPTY_DIRECTORY), 200])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        sink = _RecordingLogger()
        monkeypatch.setattr(ec, "logger", sink)
        client = ec.EdhClient(profile="batch")
        client.ensure_user("vabc", "alice")
        client.ensure_user("vabc", "alice")
        creates = [call for call in recorded["calls"] if call["method"] == "POST"]
        assert creates, "no create was sent"
        passwords = [sent_form(call)[ec.EDH_LDAP_PASSWORD_FIELD] for call in creates]
        assert len(set(passwords)) == len(passwords) >= 2
        logged = json.dumps(sink.records)
        for password in passwords:
            assert len(password) >= 16
            assert any(c.islower() for c in password) and any(c.isupper() for c in password)
            assert any(c.isdigit() for c in password) and any(not c.isalnum() for c in password)
            assert password not in logged

    def test_the_display_name_is_not_sent(self, monkeypatch):
        # EDH's directory API has no display-name field; the argument is kept for the caller's records.
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(200, EMPTY_DIRECTORY), 200])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        ec.EdhClient(profile="batch").ensure_user("vabc", "Alice Example")
        _probe, create = recorded["calls"]
        assert set(sent_form(create)) == {"user", "password", "sudoers", "email", "shell"}
        assert b"Alice" not in create["body"]

    def test_a_user_created_between_probe_and_create_is_success(self, monkeypatch):
        # The duplicate capture is EDH's 500 for creating a user it already has: the race a probe
        # cannot exclude, tolerated rather than surfaced.
        fixture, response = _capture("ldap_user_duplicate")
        assert fixture["response"]["status"] == 500
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(200, EMPTY_DIRECTORY), response])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        edh_uid = fixture["request"]["body"][ec.EDH_LDAP_USER_FIELD]
        assert edh_uid == fixture["expect"]["edhUid"]
        assert ec.EdhClient(profile="batch").ensure_user(edh_uid) is None
        probe, create = recorded["calls"]
        assert (probe["method"], create["method"]) == ("GET", "POST")

    def test_a_failed_existence_check_at_create_is_not_taken_for_an_existing_user(self, monkeypatch):
        # EDH's create re-checks the directory first. When that check fails it answers "Unable to check if
        # user <uid> already exist, ..." (v1/ldap/user.py), which carries the same "already exist" words as
        # its duplicate refusal. Only the duplicate means the user is there.
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(200, EMPTY_DIRECTORY),
                     _refusal(" Unable to check if user vabc already exist, Unable to bind to LDAP server")])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient(profile="batch").ensure_user("vabc")
        assert [call["method"] for call in recorded["calls"]] == ["GET", "POST"]

    def test_a_directory_search_failure_on_the_probe_is_an_outage_not_an_absent_user(self, monkeypatch):
        # A directory outage answers the probe "Unable to find user because of ..." (v1/ldap/user.py), which
        # the not-found markers would read as "no such resource". An absent user is a 200 with [].
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[_refusal(" Unable to find user because of Can't contact LDAP server")])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient(profile="batch").ensure_user("vabc")
        assert [call["method"] for call in recorded["calls"]] == ["GET"]

    def test_with_provisioning_disabled_a_directory_outage_is_not_user_not_provisioned(self, monkeypatch):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[_refusal(" Unable to find user because of Can't contact LDAP server")])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "false")
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient(profile="batch").ensure_user("vabc")

    def test_any_other_refusal_still_raises(self, monkeypatch):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(200, EMPTY_DIRECTORY), _refusal("Unable to create user: invalid username")])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "true")
        with pytest.raises(ec.EdhApiError):
            ec.EdhClient(profile="batch").ensure_user("vabc")

    def test_with_provisioning_disabled_a_listed_user_is_probed_not_created(self, monkeypatch):
        fixture, response = _capture("ldap_user_get_by_uid")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "false")
        assert ec.EdhClient(profile="batch").ensure_user(fixture["request"]["query"]["user"]) is None
        assert _only_call(recorded)["method"] == "GET"

    def test_with_provisioning_disabled_an_unlisted_user_is_not_found_and_not_created(self, monkeypatch):
        fixture, response = _capture("ldap_user_get_by_uid_absent")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        monkeypatch.setattr(ec, "EDH_AUTO_PROVISION_USERS", "false")
        with pytest.raises(ec.EdhNotFoundError):
            ec.EdhClient(profile="batch").ensure_user(fixture["request"]["query"]["user"])
        assert _only_call(recorded)["method"] == "GET"


@pytest.mark.unit
class TestContainerRoutes:
    """Skipped, not failed, when a recording cluster had the containers feature off (no captures)."""

    def test_list_batch_job_definitions(self, monkeypatch):
        fixture, response = _capture("batch_job_definitions")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        # EDH defaults to ACTIVE; the capture may or may not have said so.
        result = ec.EdhClient().list_batch_job_definitions(status=fixture["request"]["query"].get("status"))
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_ROUTE_BATCH_JOB_DEFINITIONS, method="GET",
                                user_header=ec.EDH_SERVICE_ACTOR_UID)
        assert isinstance(result, list)
        assert len(result) == _expected_count(fixture)
        assert result, "the captured job submit used a definition from this list"

    def test_list_batch_queues(self, monkeypatch):
        fixture, response = _capture("batch_job_queues")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        result = ec.EdhClient().list_batch_queues()
        _assert_matches_capture(ec, _only_call(recorded), fixture, route=ec.EDH_ROUTE_BATCH_JOB_QUEUES, method="GET",
                                user_header=ec.EDH_SERVICE_ACTOR_UID)
        assert isinstance(result, list)
        assert len(result) == _expected_count(fixture)
        assert result, "the captured job submit used a queue from this list"

    def test_list_eks_clusters(self, monkeypatch):
        fixture, response = _capture("eks_clusters_list")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        result = ec.EdhClient().list_eks_clusters()
        _assert_matches_capture(ec, _only_call(recorded), fixture, route=ec.EDH_ROUTE_EKS_CLUSTERS, method="GET",
                                user_header=ec.EDH_SERVICE_ACTOR_UID)
        assert isinstance(result, list)
        assert len(result) == _expected_count(fixture)
        assert all(isinstance(name, str) for name in result)

    def test_submit_batch_job_posts_the_form_and_parses_the_job_id(self, monkeypatch):
        fixture, response = _capture("batch_job_submit")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        form = dict(fixture["request"]["body"])
        result = ec.EdhClient().submit_job(_captured_uid(fixture), "batch", form)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_CONTAINER_JOB_ROUTES["batch"], method="POST",
                                user_header=_captured_uid(fixture))
        _assert_form_matches(ec, call, fixture["request"]["body"])
        # Oracle: the id the producer recorded sits in EDH's prose and nowhere else.
        job_id = str(fixture["expect"]["jobId"])
        assert job_id in fixture["response"]["json"]["message"]
        assert result[ec.EDH_JOB_ID_FIELD] == job_id
        assert result[ec.EDH_JOB_HANDLE_FIELD] == f"batch:{job_id}"
        assert result["message"] == fixture["response"]["json"]["message"]

    def test_submit_eks_job_posts_the_form_and_synthesises_the_handle(self, monkeypatch):
        fixture, response = _capture("eks_job_submit")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        body = fixture["request"]["body"]
        result = ec.EdhClient().submit_job(_captured_uid(fixture), "eks", dict(body))
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_CONTAINER_JOB_ROUTES["eks"], method="POST",
                                user_header=_captured_uid(fixture))
        _assert_form_matches(ec, call, body)
        # EDH returns no id for an EKS job: the handle is the (cluster, namespace, lower-cased name) key.
        namespace = body.get("namespace") or ec.EDH_EKS_DEFAULT_NAMESPACE
        assert result["job_name"] == body["job_name"].lower()
        assert result["eks_cluster"] == body["eks_cluster"]
        assert result[ec.EDH_JOB_HANDLE_FIELD] == f"eks:{body['eks_cluster']}/{namespace}/{body['job_name'].lower()}"

    def test_a_submit_without_a_job_id_is_a_contract_error_that_logs_the_prose(self, monkeypatch):
        # EDH composes its prose after the job is submitted and writes the literal "unknown" when AWS
        # returned no id: the job is running, so the answer is a contract failure of an accepted request,
        # never a refusal of one, and EDH's prose is the only record of what it accepted, so the log
        # carries it.
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[_ok("Job render submitted successfully. Job ID: unknown"),
                     _ok("Job render submitted successfully.")])
        recorder = MagicMock()
        monkeypatch.setattr(ec, "logger", recorder)
        client = ec.EdhClient()
        for _ in range(2):
            with pytest.raises(ec.EdhContractError) as excinfo:
                client.submit_job("vabc", "batch", {"job_name": "render"})
            assert not isinstance(excinfo.value, ec.EdhApiError)
            assert excinfo.value.status == 200
            assert excinfo.value.job_submitted is True
        refusals = [call for call in recorder.warning.call_args_list
                    if call.args[0] == "EDH accepted the job without returning its id"]
        assert [(call.kwargs["extra"]["edhRoute"], call.kwargs["extra"]["edhBody"]) for call in refusals] == [
            (ec.EDH_CONTAINER_JOB_ROUTES["batch"], "Job render submitted successfully. Job ID: unknown"),
            (ec.EDH_CONTAINER_JOB_ROUTES["batch"], "Job render submitted successfully."),
        ]

    def test_submit_refuses_a_field_edh_would_drop(self, monkeypatch):
        # Batch has no `gpu` field (a GPU is a consumableResources entry); EDH would ignore it silently.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().submit_job("vabc", "batch", {"job_name": "render", "gpu": "1"})
        assert recorded["calls"] == []

    def test_list_batch_jobs_always_names_the_look_back_window(self, monkeypatch):
        fixture, response = _capture("batch_jobs_list")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        captured_query = dict(fixture["request"]["query"])
        hours = int(captured_query.pop("time_range", ec.EDH_BATCH_JOBS_TIME_RANGE_HOURS))
        result = ec.EdhClient().list_jobs(_captured_uid(fixture), "batch", time_range_hours=hours)
        call = _only_call(recorded)
        sent_method, path, query = _sent(call)
        assert ec.EDH_CONTAINER_JOBS_ROUTES["batch"] == fixture["request"]["path"] == fixture["request"]["absolutePath"]
        assert (sent_method, path) == ("GET", fixture["request"]["absolutePath"])
        assert call["headers"][ec.EDH_USER_HEADER] == _captured_uid(fixture)
        # EDH's default window is a day; the client always names one (the capture may have been
        # recorded with or without it, so the window is compared apart from the rest of the query).
        assert query.pop("time_range") == str(hours)
        assert query == captured_query
        assert isinstance(result, list)
        assert len(result) == _expected_count(fixture)
        assert all(row[ec.EDH_JOB_HANDLE_FIELD] == f"batch:{row['jobId']}" for row in result if "jobId" in row)

    def test_list_eks_jobs_passes_the_cluster_and_unkeys_the_mapping(self, monkeypatch):
        fixture, response = _capture("eks_jobs_list")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        captured_query = fixture["request"]["query"]
        cluster, namespace = captured_query["cluster"], captured_query.get("namespace")
        result = ec.EdhClient().list_jobs(_captured_uid(fixture), "eks", cluster=cluster, namespace=namespace)
        call = _only_call(recorded)
        sent_method, path, query = _sent(call)
        assert ec.EDH_CONTAINER_JOBS_ROUTES["eks"] == fixture["request"]["path"] == fixture["request"]["absolutePath"]
        assert (sent_method, path) == ("GET", fixture["request"]["absolutePath"])
        assert call["headers"][ec.EDH_USER_HEADER] == _captured_uid(fixture)
        # EDH defaults the namespace; the client always names it.
        assert query == {"cluster": cluster, "namespace": namespace or ec.EDH_EKS_DEFAULT_NAMESPACE}
        assert isinstance(result, list)
        assert len(result) == _expected_count(fixture)
        assert all(ec.EDH_EKS_JOB_UID_FIELD in row for row in result)

    def test_listing_eks_jobs_needs_the_cluster(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().list_jobs("vabc", "eks")
        assert recorded["calls"] == []

    def test_kill_batch_job_names_the_job_in_the_form(self, monkeypatch):
        fixture, response = _capture("batch_job_kill")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        job_id = fixture["request"]["body"][ec.EDH_JOB_ID_FIELD]
        assert str(job_id) == str(fixture["expect"]["jobId"])
        result = ec.EdhClient().kill_job(_captured_uid(fixture), "batch", f"batch:{job_id}")
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_CONTAINER_JOB_ROUTES["batch"], method="DELETE",
                                user_header=_captured_uid(fixture))
        # `reason` is optional on the EDH side; the client sends the id alone.
        expected = {key: value for key, value in fixture["request"]["body"].items() if key != "reason"}
        _assert_form_matches(ec, call, expected)
        assert result == {"message": fixture["response"]["json"]["message"]}

    def test_kill_eks_job_unpacks_the_composite_handle(self, monkeypatch):
        fixture, response = _capture("eks_job_kill")
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[response])
        body = fixture["request"]["body"]
        namespace = body.get("namespace") or ec.EDH_EKS_DEFAULT_NAMESPACE
        handle = f"eks:{body['eks_cluster']}/{namespace}/{body['job_name']}"
        result = ec.EdhClient().kill_job(_captured_uid(fixture), "eks", handle)
        call = _only_call(recorded)
        _assert_matches_capture(ec, call, fixture, route=ec.EDH_CONTAINER_JOB_ROUTES["eks"], method="DELETE",
                                user_header=_captured_uid(fixture))
        _assert_form_matches(ec, call, {**body, "namespace": namespace})
        assert result == {"message": fixture["response"]["json"]["message"]}

    def test_a_bare_job_id_is_accepted_for_batch_and_a_malformed_eks_handle_is_refused(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"],
                                            results=[_ok("Job j-1 terminated successfully.")])
        ec.EdhClient().kill_job("vabc", "batch", "j-1")
        assert sent_form(_only_call(recorded)) == {"job_id": "j-1"}
        for bad in ("eks:cluster-only", "eks:c/ns", "batch:j-1"):
            with pytest.raises(ValueError):
                ec.EdhClient().kill_job("vabc", "eks", bad)
        assert len(recorded["calls"]) == 1

    def test_an_unknown_backend_is_refused_before_any_call(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        with pytest.raises(ValueError):
            ec.EdhClient().submit_job("vabc", "k8s", {})
        with pytest.raises(ValueError):
            ec.EdhClient().list_jobs("vabc", "k8s")
        with pytest.raises(ValueError):
            ec.EdhClient().kill_job("vabc", "k8s", "x")
        assert recorded["calls"] == []

    def test_the_eks_routes_are_the_batch_routes_with_the_segment_swapped(self):
        assert _ec.EDH_CONTAINER_BACKENDS == ("batch", "eks")
        assert _ec.EDH_CONTAINER_JOB_ROUTES["eks"] == _ec.EDH_CONTAINER_JOB_ROUTES["batch"].replace("/batch/", "/eks/")
        assert _ec.EDH_CONTAINER_JOBS_ROUTES["eks"] == _ec.EDH_CONTAINER_JOBS_ROUTES["batch"].replace(
            "/batch/", "/eks/")
