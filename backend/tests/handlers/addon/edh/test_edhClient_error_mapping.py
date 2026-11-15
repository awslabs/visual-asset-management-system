# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EDH answers become typed errors; typed errors become curated API responses.

EDH signals almost every refusal as a 500 carrying its ``{"success": false, "message": ...}``
envelope, and reserves 400 for a missing parameter or header, 401 for the token and 3xx for a route
it does not serve. A refusal is therefore recognised by the envelope and classified by the wording of
its message; a 5xx WITHOUT the envelope is the only thing read as EDH being unavailable. The wording
arms are pinned twice: every recorded refusal under ``fixtures/`` is replayed as captured, so a re-cut
capture re-checks the arm it lands on, and each marker of the client's own lists is driven through a
neutral envelope. No capture records a capacity refusal, so those sentences alone are spelled out here,
as EDH's source words them, beside the not-found and dry-run sentences that share their words. The
upstream text is logged for diagnosis and never returned: a caller sees a fixed message per error class
(Rule 11), so an EDH message that names another user's session stays in CloudWatch.
"""

import http.client
import json
import os
import socket
import ssl
import threading
import time

import pytest
import urllib3
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common import edhClient as _ec
from backend.tests.handlers.addon.edh.edhTestSupport import (
    FIXTURE_FILES,
    FIXTURES_DIR,
    USER_HEADER,
    FakeResponse,
    install_client_fakes,
    load_fixture,
    replay_response,
)

CREATE = "/api/dcv/virtual_desktops/create"
START = "/api/dcv/virtual_desktops/start"
SESSION = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"

# Every recorded answer the client classifies rather than returns, by its registry key, with the typed
# error it must become. A capture a live re-cut turns into a success skips its case (only a refusal can
# pin an arm); a recorded refusal absent from this table fails the directory control below.
CAPTURED_REFUSALS = {
    "auth_missing_user_header": "EdhApiError",
    "auth_wrong_token": "EdhAuthError",
    "batch_job_definition_register_probe": "EdhApiError",
    "containers_ecr_repository_list_v1_probe": "EdhAuthError",
    "desktop_not_found": "EdhNotFoundError",
    "desktop_schedule_disabled": "EdhApiError",
    "desktop_schedule_not_found": "EdhNotFoundError",
    "desktop_start_foreign_owner": "EdhNotFoundError",
    "desktop_stop_foreign_owner": "EdhNotFoundError",
    "disk_size_out_of_range": "EdhApiError",
    "instance_type_not_allowed": "EdhApiError",
    "ldap_user_create_rejected_minimal": "EdhApiError",
    "ldap_user_duplicate": "EdhApiError",
    "ldap_user_invalid_uid": "EdhApiError",
    "ldap_user_uid_too_long": "EdhApiError",
    "ldap_users_list_v1_probe": "EdhAuthError",
    "quota_error": "EdhQuotaError",
    "software_stacks_dcv_register_json_probe": "EdhApiError",
}

# What urllib3 2.x raises out of a pool request with retries off, by the phase it comes from. The connect
# phase (HTTPConnection._new_conn) raises ConnectTimeoutError or its subclasses NewConnectionError and
# NameResolutionError before a byte of the request is written; the rest can arrive once it is on its way.
CONNECT_PHASE_ERRORS = {
    "connect timeout": lambda: urllib3.exceptions.ConnectTimeoutError(
        None, "Connection to internal-edh-alb.example.internal timed out. (connect timeout=3.0)"),
    "connection refused": lambda: urllib3.exceptions.NewConnectionError(
        None, "Failed to establish a new connection: [Errno 111] Connection refused"),
    "name resolution": lambda: urllib3.exceptions.NameResolutionError(
        "internal-edh-alb.example.internal", None, socket.gaierror(-2, "Name or service not known")),
}
AFTER_SEND_ERRORS = {
    "read timeout": lambda: urllib3.exceptions.ReadTimeoutError(
        None, START, "Read timed out. (read timeout=8.0)"),
    "connection reset": lambda: urllib3.exceptions.ProtocolError(
        "Connection aborted.", ConnectionResetError(104, "Connection reset by peer")),
    "closed unanswered": lambda: urllib3.exceptions.ProtocolError(
        "Connection aborted.", http.client.RemoteDisconnected("Remote end closed connection without response")),
    "tls record cut": lambda: urllib3.exceptions.SSLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
    "body cut short": lambda: urllib3.exceptions.ProtocolError(
        "Connection broken: IncompleteRead(3 bytes read, 10 more expected)",
        http.client.IncompleteRead(b"abc", 10)),
}

# EDH refusals that name capacity, as EDH's source words them, by the request that meets them, with the
# typed error each must become. EDH puts its error prefix and a space before the helper text (a generic
# error's prefix is empty), and a start that EC2 refuses carries botocore's ClientError text. The last two
# share the capacity sentences' words and say something else: a session that is not the caller's, and
# EDH's dry run of a launch whose AMI does not exist.
EDH_SOURCE_REFUSALS = {
    "create, every subnet probed": (
        "POST", CREATE,
        " Unable to find available capacity in all subnets provided "
        "['subnet-0a1b2c3d4e5f60718', 'subnet-0f1e2d3c4b5a69788']. Try again later.",
        "EdhCapacityError"),
    "create, the selected subnet": (
        "POST", CREATE,
        " Unable to provision the Virtual Desktop because the available AWS capacity in the selected subnet "
        "is insufficient. Try again later or choose a different subnet / instance type.",
        "EdhCapacityError"),
    "start, no reserved capacity": (
        "PUT", START,
        " No reserved capacity available for g5.xlarge in subnet-0a1b2c3d4e5f60718 right now, and On-Demand "
        "fallback is disabled. Please try again later.",
        "EdhCapacityError"),
    "start, insufficient instance capacity": (
        "PUT", START,
        f"Unable to Restart Desktop {SESSION} (owner: v1):  Unable to start instance due to "
        + str(ClientError({"Error": {"Code": "InsufficientInstanceCapacity",
                                     "Message": "We currently do not have sufficient g5.xlarge capacity in "
                                                "the Availability Zone you requested."}}, "StartInstances")),
        "EdhCapacityError"),
    "start, reservation capacity exceeded": (
        "PUT", START,
        f"Unable to Restart Desktop {SESSION} (owner: v1):  Unable to start instance due to "
        + str(ClientError({"Error": {"Code": "ReservationCapacityExceeded",
                                     "Message": "There is not enough capacity in the targeted Capacity "
                                                "Reservation."}}, "StartInstances")),
        "EdhCapacityError"),
    "start, a session that is not the caller's": (
        "PUT", START,
        f"Unable to Restart Desktop {SESSION} (owner: v1):  Unable to find this session. Please refresh your "
        "browser and try again.",
        "EdhNotFoundError"),
    "create, dry run of an AMI that does not exist": (
        "POST", CREATE,
        "Error while invoking ec2 API. Error while invoking ec2 API. Unable to launch capacity: "
        + str({"Code": "InvalidAMIID.NotFound", "Message": "The image id '[ami-0123456789abcdef0]' does not exist"})
        + "  (Request ID: 7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f)",
        "EdhNotFoundError"),
}


def _client_module():
    import backend.backend.handlers.addon.edh.common.edhClient as ec
    return ec


def _refusal(text):
    """EDH's refusal envelope: SocaError appends the request id to every message."""
    return {"success": False, "message": f"{text} (Request ID: 3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30)"}


def _is_refusal_shaped(fixture):
    """An answer the client classifies: a status outside 2xx, or a 2xx carrying ``success: false``."""
    status = fixture["response"]["status"]
    body = fixture["response"]["json"]
    return not 200 <= status < 300 or (isinstance(body, dict) and body.get("success") is False)


def _replay(monkeypatch, fixture):
    """The client module and a thunk sending the captured request against the captured answer.

    Two identical answers and two root keys, so an arm that re-reads the key and retries once (the
    401 and 3xx arms) meets the same answer again.
    """
    ec, _ = install_client_fakes(monkeypatch, root_keys=["k1", "k2"], results=[replay_response(fixture)] * 2)
    request = fixture["request"]
    kwargs = {"edh_uid": request["headers"].get(USER_HEADER)}
    if request["body"] is not None:
        kwargs["form"] = request["body"]
    if request["query"]:
        kwargs["params"] = request["query"]
    return ec, lambda: ec.EdhClient().request(request["method"], request["absolutePath"], **kwargs)


@pytest.mark.unit
class TestEdhAnswersToTypedErrors:
    """The status arms, driven with neutral bodies: nothing here depends on EDH's wording."""

    @pytest.mark.parametrize("status,body,error_name", [
        (401, _refusal("refused"), "EdhAuthError"),
        (302, b"", "EdhAuthError"),
        (400, _refusal("refused"), "EdhApiError"),
        (400, {"message": {"field": "missing"}}, "EdhApiError"),
        (403, _refusal("refused"), "EdhApiError"),
        # A refusal envelope is classified by its wording whatever its status; only an unwrapped 404
        # reads as a route EDH does not serve.
        (404, _refusal("refused"), "EdhApiError"),
        (404, b"", "EdhNotFoundError"),
        (500, _refusal("refused"), "EdhApiError"),
        (500, {"message": "Internal Server Error"}, "EdhUnavailableError"),
        (503, b"<html>Service Unavailable</html>", "EdhUnavailableError"),
        (502, b"", "EdhUnavailableError"),
    ])
    def test_each_answer_maps_to_its_typed_error(self, monkeypatch, status, body, error_name):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1", "k2"], results=[FakeResponse(status, body)] * 2)
        with pytest.raises(getattr(ec, error_name)) as excinfo:
            ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={})
        assert excinfo.value.status == status
        assert isinstance(excinfo.value, ec.EdhError)

    @pytest.mark.parametrize("marker", _ec.EDH_NOT_FOUND_MARKERS)
    def test_a_refusal_naming_a_not_found_marker_is_a_not_found_error(self, monkeypatch, marker):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(500, _refusal(f"refused: {marker}"))])
        with pytest.raises(ec.EdhNotFoundError) as excinfo:
            ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={})
        assert excinfo.value.status == 500

    @pytest.mark.parametrize("marker", _ec.EDH_QUOTA_ERROR_MARKERS)
    def test_a_refusal_naming_a_quota_marker_is_a_quota_error(self, monkeypatch, marker):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(500, _refusal(f"refused: {marker}"))])
        with pytest.raises(ec.EdhQuotaError) as excinfo:
            ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={})
        assert excinfo.value.status == 500

    def test_the_markers_are_read_case_insensitively(self, monkeypatch):
        marker = _ec.EDH_QUOTA_ERROR_MARKERS[0]
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(500, _refusal(marker.upper()))])
        with pytest.raises(ec.EdhQuotaError):
            ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={})

    def test_a_200_carrying_a_refusal_envelope_is_classified_not_returned(self, monkeypatch):
        marker = _ec.EDH_NOT_FOUND_MARKERS[0]
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(200, _refusal(f"the desktop {marker}"))])
        with pytest.raises(ec.EdhNotFoundError) as excinfo:
            ec.EdhClient().request("GET", "/api/dcv/virtual_desktops/list", edh_uid="v1",
                                   params={"is_active": "true"})
        assert excinfo.value.status == 200

    def test_a_non_json_2xx_body_is_a_contract_error_not_a_refusal(self, monkeypatch):
        # A 2xx the client cannot read may have been executed upstream: it is EDH's contract that
        # failed, not the caller's request, so it is never reported as a rejection.
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[FakeResponse(200, b"<html>")])
        with pytest.raises(ec.EdhContractError) as excinfo:
            ec.EdhClient().request("GET", "/api/dcv/virtual_desktops/profiles")
        assert not isinstance(excinfo.value, ec.EdhApiError)
        assert excinfo.value.status == 200
        assert excinfo.value.body_text == "<html>"
        assert excinfo.value.job_submitted is False

    @pytest.mark.parametrize("method,path,job_submitted", [
        ("POST", _ec.EDH_CONTAINER_JOB_ROUTES["batch"], True),
        ("POST", _ec.EDH_CONTAINER_JOB_ROUTES["eks"], True),
        ("GET", _ec.EDH_CONTAINER_JOBS_ROUTES["batch"], False),
        ("DELETE", _ec.EDH_CONTAINER_JOB_ROUTES["batch"], False),
    ], ids=["batch submit", "eks submit", "batch listing", "batch kill"])
    def test_an_unreadable_2xx_marks_the_job_as_submitted_on_the_submit_route_alone(self, monkeypatch, method,
                                                                                    path, job_submitted):
        # Only the submit leaves a running job behind its unreadable answer; a listing or a kill does not.
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[FakeResponse(200, b"<html>")])
        with pytest.raises(ec.EdhContractError) as excinfo:
            ec.EdhClient().request(method, path, edh_uid="v1", form={"job_name": "render"})
        assert excinfo.value.job_submitted is job_submitted

    def test_a_success_envelope_is_returned_whole(self, monkeypatch):
        # Control: the wrappers unwrap `message`; request() hands back the envelope as EDH sent it.
        envelope = {"success": True, "message": "started"}
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[FakeResponse(200, envelope)])
        assert ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={}) == envelope

    def test_the_marker_lists_are_lower_cased_and_disjoint(self):
        ec = _client_module()
        assert ec.EDH_NOT_FOUND_MARKERS and ec.EDH_QUOTA_ERROR_MARKERS and ec.EDH_CAPACITY_ERROR_MARKERS
        assert all(marker == marker.lower() for marker in
                   ec.EDH_QUOTA_ERROR_MARKERS + ec.EDH_NOT_FOUND_MARKERS + ec.EDH_CAPACITY_ERROR_MARKERS)
        # The not-found arm is tested first, so a quota marker containing a not-found one could never fire.
        assert not any(not_found in quota
                       for quota in ec.EDH_QUOTA_ERROR_MARKERS for not_found in ec.EDH_NOT_FOUND_MARKERS)
        # The capacity arm is tested before both, so neither may hold a marker containing a capacity one.
        assert not any(capacity in other
                       for other in ec.EDH_NOT_FOUND_MARKERS + ec.EDH_QUOTA_ERROR_MARKERS
                       for capacity in ec.EDH_CAPACITY_ERROR_MARKERS)

    def test_no_capacity_marker_is_a_word_edh_also_uses_for_other_refusals(self):
        # EDH's dry run words a missing AMI as "Unable to launch capacity: ...", and a reservation it could
        # not detach names its "capacity reservation"; neither is a shortage.
        markers = _client_module().EDH_CAPACITY_ERROR_MARKERS
        assert all(marker.strip() != "capacity" and "capacity reservation" not in marker for marker in markers)


@pytest.mark.unit
class TestCapacityRefusals:
    """EDH found no AWS capacity for the instance type: the capacity arm is read before the not-found and quota
    arms, since the create path's sentence opens with "unable to find", and its error is none of theirs."""

    @pytest.mark.parametrize("case", sorted(EDH_SOURCE_REFUSALS))
    def test_each_edh_sentence_maps_to_its_typed_error(self, monkeypatch, case):
        method, path, text, error_name = EDH_SOURCE_REFUSALS[case]
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[FakeResponse(500, _refusal(text))])
        with pytest.raises(ec.EdhError) as excinfo:
            ec.EdhClient().request(method, path, edh_uid="v1", form={"session_uuid": SESSION})
        assert type(excinfo.value).__name__ == error_name
        assert excinfo.value.status == 500

    @pytest.mark.parametrize("marker", _ec.EDH_CAPACITY_ERROR_MARKERS)
    def test_a_refusal_naming_a_capacity_marker_is_a_capacity_error(self, monkeypatch, marker):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(500, _refusal(f"refused: {marker}"))])
        with pytest.raises(ec.EdhCapacityError) as excinfo:
            ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={})
        assert excinfo.value.status == 500

    @pytest.mark.parametrize("other", _ec.EDH_NOT_FOUND_MARKERS + _ec.EDH_QUOTA_ERROR_MARKERS)
    def test_a_capacity_marker_is_read_before_every_other_arm(self, monkeypatch, other):
        marker = _ec.EDH_CAPACITY_ERROR_MARKERS[-1]
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(500, _refusal(f"{other}: {marker}"))])
        with pytest.raises(ec.EdhCapacityError):
            ec.EdhClient().request("PUT", START, edh_uid="v1", form={"session_uuid": SESSION})

    def test_the_capacity_error_is_a_class_of_its_own(self):
        # Every handler of a missing resource, a quota, a plain refusal or an unreachable EDH keeps its meaning.
        ec = _client_module()
        assert issubclass(ec.EdhCapacityError, ec.EdhError)
        assert not issubclass(ec.EdhCapacityError, (ec.EdhNotFoundError, ec.EdhQuotaError, ec.EdhApiError,
                                                    ec.EdhUnavailableError))


@pytest.mark.unit
class TestCapturedRefusals:
    """Recorded EDH refusals replayed as captured, each landing on the arm its wording or status selects."""

    @pytest.mark.parametrize("key,error_name", sorted(CAPTURED_REFUSALS.items()))
    def test_each_captured_refusal_maps_to_its_typed_error(self, monkeypatch, key, error_name):
        fixture = load_fixture(key)
        if not _is_refusal_shaped(fixture):
            pytest.skip(f"{FIXTURE_FILES[key]} records a success, not a refusal")
        ec, send = _replay(monkeypatch, fixture)
        with pytest.raises(getattr(ec, error_name)) as excinfo:
            send()
        assert excinfo.value.status == fixture["response"]["status"]

    def test_a_captured_refusal_envelope_carries_its_text_for_the_log(self, monkeypatch):
        # The wrappers read markers from body_text (ensure_user's duplicate wording), and the log line
        # carries it; the replayed envelope's message is what arrives there.
        fixture = load_fixture("ldap_user_duplicate")
        ec, send = _replay(monkeypatch, fixture)
        with pytest.raises(ec.EdhApiError) as excinfo:
            send()
        assert excinfo.value.body_text == fixture["response"]["json"]["message"]
        assert ec.EDH_USER_EXISTS_PATTERN.search(excinfo.value.body_text)

    def test_every_recorded_refusal_is_replayed(self):
        # Directory-driven: a refusal the spike records under a new name has to be classified here.
        key_by_file = {file_name: key for key, file_name in FIXTURE_FILES.items()}
        recorded = []
        for file_name in sorted(os.listdir(FIXTURES_DIR)):
            if not file_name.endswith(".json"):
                continue
            with open(os.path.join(FIXTURES_DIR, file_name), "r", encoding="utf-8") as handle:
                if _is_refusal_shaped(json.load(handle)):
                    recorded.append(file_name)
        assert recorded, "no refusal has been captured"
        unmapped = [name for name in recorded if key_by_file.get(name) not in CAPTURED_REFUSALS]
        assert not unmapped, f"captured refusals with no replay case: {unmapped}"

    def test_every_replay_case_names_a_registered_capture(self):
        assert set(CAPTURED_REFUSALS) <= set(FIXTURE_FILES)
        for key in CAPTURED_REFUSALS:
            assert os.path.exists(os.path.join(FIXTURES_DIR, FIXTURE_FILES[key])), key


@pytest.mark.unit
class TestRedirectsAreAnswersNotHops:
    """Through a real urllib3 pool, not the test seam: a 3xx comes back as a response for the classifier."""

    def test_a_real_pool_hands_a_302_back_for_classification(self, monkeypatch):
        ec = _client_module()
        monkeypatch.setattr(ec, "_load_edh_root_key", lambda: "k1")
        seen = []

        class StubConnection:
            def urlopen(self, method, url, **kwargs):
                seen.append(kwargs)
                return urllib3.response.HTTPResponse(
                    body=b"", status=302, headers={"Location": "https://edh.example.internal/login"},
                    preload_content=False)

        pool = urllib3.PoolManager()
        monkeypatch.setattr(pool, "connection_from_host", lambda *args, **kwargs: StubConnection())
        monkeypatch.setattr(ec, "_get_http_pool", lambda: pool)

        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", "/api/dcv/virtual_desktops/list", edh_uid="v1", params={"is_active": True})

        assert excinfo.value.status == 302
        # One exchange, retries off. The 302 reaching the classifier is itself the proof that nothing
        # was followed: with following on, the pool raises MaxRetryError instead. (The pool passes
        # redirect=False to the connection whatever the caller asked, so that kwarg would prove nothing.)
        assert [kwargs["retries"] for kwargs in seen] == [False]


@pytest.mark.unit
class TestAnUnansweredMutationIsAnUnknownOutcome:
    """A request sent once that loses its answer after it may have reached EDH is an unknown outcome, never
    'EDH is unavailable': the caller reads EDH's state before it concludes anything. The subclass keeps
    every existing handler of the unavailable error working."""

    @pytest.mark.parametrize("method", ["PUT", "POST", "DELETE"])
    @pytest.mark.parametrize("error", sorted(AFTER_SEND_ERRORS))
    def test_a_failure_that_can_follow_the_send_is_an_unknown_outcome(self, monkeypatch, method, error):
        cause = AFTER_SEND_ERRORS[error]()
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[cause, 200])
        with pytest.raises(ec.EdhOutcomeUnknownError) as excinfo:
            ec.EdhClient().request(method, START, edh_uid="v1", form={"session_uuid": "s"})
        assert isinstance(excinfo.value, ec.EdhUnavailableError)
        assert excinfo.value.__cause__ is cause
        assert len(recorded["calls"]) == 1

    @pytest.mark.parametrize("error", sorted(CONNECT_PHASE_ERRORS))
    def test_a_connect_phase_failure_is_unavailable_and_nothing_more(self, monkeypatch, error):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[CONNECT_PHASE_ERRORS[error](), 200])
        with pytest.raises(ec.EdhUnavailableError) as excinfo:
            ec.EdhClient().request("PUT", START, edh_uid="v1", form={"session_uuid": "s"})
        assert not isinstance(excinfo.value, ec.EdhOutcomeUnknownError)
        assert len(recorded["calls"]) == 1

    def test_a_pool_that_cannot_be_built_never_sent_the_request(self, monkeypatch):
        # The CA bundle is loaded as the pool is built, inside the HTTP seam and before any connection.
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[
            _ec.EdhUnavailableError("The EDH CA bundle could not be loaded")])
        with pytest.raises(ec.EdhUnavailableError) as excinfo:
            ec.EdhClient().request("PUT", START, edh_uid="v1", form={"session_uuid": "s"})
        assert not isinstance(excinfo.value, ec.EdhOutcomeUnknownError)

    @pytest.mark.parametrize("error", sorted(AFTER_SEND_ERRORS))
    def test_a_get_is_never_an_unknown_outcome(self, monkeypatch, error):
        # Control: a GET is retried and reads state; when every attempt fails EDH is unavailable.
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[AFTER_SEND_ERRORS[error](), AFTER_SEND_ERRORS[error]()])
        with pytest.raises(ec.EdhUnavailableError) as excinfo:
            ec.EdhClient().request("GET", "/api/dcv/virtual_desktops/list", edh_uid="v1")
        assert not isinstance(excinfo.value, ec.EdhOutcomeUnknownError)
        assert len(recorded["calls"]) == ec._PROFILES["api"].attempts

    def test_the_batch_profile_classifies_the_same_way(self, monkeypatch):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[AFTER_SEND_ERRORS["read timeout"]()])
        with pytest.raises(ec.EdhOutcomeUnknownError):
            ec.EdhClient(profile="batch").request("POST", CREATE, edh_uid="v1", form={})

    def test_the_unknown_outcome_is_still_an_unavailable_error(self):
        # Every caller that handles an unreachable EDH keeps handling this one.
        ec = _client_module()
        assert issubclass(ec.EdhOutcomeUnknownError, ec.EdhUnavailableError)


class _Listener:
    """A loopback TCP listener that reads each whole request and then either holds the connection open
    unanswered or closes it unanswered, recording every request it read."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []
        self._held = []
        self._stopped = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stopped.is_set():
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            conn.settimeout(5.0)
            self.requests.append(self._read_request(conn))
            if self.answer == "close":
                conn.close()
            else:
                self._held.append(conn)

    @staticmethod
    def _read_request(conn):
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return data
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                           if line.lower().startswith(b"content-length:")), 0)
            while len(body) < length:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                body += chunk
            return head + b"\r\n\r\n" + body
        except OSError:
            return data

    def close(self):
        self._stopped.set()
        self._thread.join(timeout=2.0)
        self._sock.close()
        for conn in self._held:
            conn.close()


def _real_pool_client(monkeypatch, port):
    """The client module aimed at a loopback port through a real urllib3 pool and the real clock."""
    ec = _client_module()
    monkeypatch.setattr(ec, "_load_edh_root_key", lambda: "k1")
    monkeypatch.setattr(ec, "EDH_API_ENDPOINT", f"http://127.0.0.1:{port}")
    pool = urllib3.PoolManager()
    monkeypatch.setattr(ec, "_get_http_pool", lambda: pool)
    return ec


@pytest.mark.unit
class TestTheTransportPhaseThroughARealPool:
    """The classification held to what urllib3 itself raises, not to exception instances built here: a
    real pool sends a PUT at a loopback listener. The deadline is 1.5 s, so each attempt is given a
    0.75 s connect and a 0.75 s read."""

    @pytest.mark.parametrize("answer", ["hold", "close"])
    def test_a_put_that_reached_the_listener_and_lost_its_answer_is_an_unknown_outcome(self, monkeypatch, answer):
        listener = _Listener(answer)
        try:
            ec = _real_pool_client(monkeypatch, listener.port)
            with pytest.raises(ec.EdhOutcomeUnknownError) as excinfo:
                ec.EdhClient(deadline=time.monotonic() + 1.5).request(
                    "PUT", START, edh_uid="v1", form={"session_uuid": "s"})
        finally:
            listener.close()
        # The listener read the whole request, so it was delivered: the outcome is EDH's to report.
        assert len(listener.requests) == 1
        assert listener.requests[0].startswith(b"PUT " + START.encode())
        assert listener.requests[0].endswith(b"session_uuid=s")
        expected = (urllib3.exceptions.ReadTimeoutError if answer == "hold"
                    else urllib3.exceptions.ProtocolError)
        assert isinstance(excinfo.value.__cause__, expected)

    def test_a_put_to_a_port_nothing_listens_on_is_unavailable(self, monkeypatch):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        ec = _real_pool_client(monkeypatch, port)
        with pytest.raises(ec.EdhUnavailableError) as excinfo:
            ec.EdhClient(deadline=time.monotonic() + 1.5).request(
                "PUT", START, edh_uid="v1", form={"session_uuid": "s"})
        assert not isinstance(excinfo.value, ec.EdhOutcomeUnknownError)
        # A refusal (NewConnectionError) or, where the stack retries the handshake, the connect timeout.
        assert isinstance(excinfo.value.__cause__, urllib3.exceptions.ConnectTimeoutError)


@pytest.mark.unit
class TestTypedErrorToResponse:
    @pytest.mark.parametrize("error_name,status", [
        ("EdhQuotaError", 429),
        ("EdhCapacityError", 400),
        ("EdhNotFoundError", 404),
        ("EdhAuthError", 502),
        ("EdhUnavailableError", 502),
        ("EdhOutcomeUnknownError", 502),
        ("EdhContractError", 502),
        ("EdhApiError", 400),
        ("EdhError", 502),
    ])
    def test_each_error_maps_to_its_status_with_a_curated_message(self, error_name, status):
        ec = _client_module()
        error = getattr(ec, error_name)("EDH said: session internal-id-77 of owner v9 exceeded QUOTA-XYZ")
        response = ec.map_edh_error_to_response(error, None)
        assert response["statusCode"] == status
        body = json.loads(response["body"])
        assert body["message"]
        assert "internal-id-77" not in body["message"]
        assert "v9" not in body["message"]
        assert "QUOTA-XYZ" not in body["message"]

    def test_the_response_carries_the_common_headers(self):
        ec = _client_module()
        response = ec.map_edh_error_to_response(ec.EdhApiError("x"), None)
        assert response["headers"]["Content-Type"] == "application/json"
        assert response["isBase64Encoded"] is False

    def test_a_contract_error_on_a_job_submit_tells_the_caller_to_check_the_job_list_before_retrying(self):
        # The job may be running, so the message must not invite a plain retry the way the unavailable
        # message does.
        ec = _client_module()
        error = ec.EdhContractError("x", status=200, body_text="Job ID: unknown", job_submitted=True)
        body = json.loads(ec.map_edh_error_to_response(error, None)["body"])
        assert body["message"] == ("EDH accepted the request but returned an unreadable answer. "
                                   "Check the job list before retrying.")
        assert "Try again later" not in body["message"]

    def test_a_contract_error_on_any_other_route_keeps_the_generic_message(self):
        # A listing that came back unreadable has no job to check; the message still invites no retry.
        ec = _client_module()
        body = json.loads(ec.map_edh_error_to_response(ec.EdhContractError("x"), None)["body"])
        assert body["message"] == "EDH accepted the request but returned an unreadable answer."
        assert "job list" not in body["message"]
        assert "Try again later" not in body["message"]

    def test_an_unknown_outcome_tells_the_caller_to_refresh_before_trying_again(self):
        # The request may still be in progress in EDH, so the message must not invite a plain retry.
        ec = _client_module()
        response = ec.map_edh_error_to_response(
            ec.EdhOutcomeUnknownError("EDH did not answer PUT /api/dcv/virtual_desktops/start"), None)
        assert response["statusCode"] == 502
        message = json.loads(response["body"])["message"]
        assert message == ("EDH did not confirm the request in time; it may still be in progress. "
                           "Refresh before trying again.")
        assert "Try again later" not in message

    def test_an_unknown_outcome_is_flagged_so_a_caller_does_not_report_it_as_a_failure(self):
        ec = _client_module()
        response = ec.map_edh_error_to_response(
            ec.EdhOutcomeUnknownError("EDH did not answer PUT /api/dcv/virtual_desktops/start"), None)
        assert json.loads(response["body"]) == {
            "message": ("EDH did not confirm the request in time; it may still be in progress. "
                        "Refresh before trying again."),
            "outcomeUnknown": True,
        }

    def test_an_edh_that_was_never_reached_keeps_the_unavailable_message(self):
        ec = _client_module()
        response = ec.map_edh_error_to_response(ec.EdhUnavailableError("EDH unreachable for PUT /x"), None)
        assert response["statusCode"] == 502
        assert json.loads(response["body"]) == {"message": "EDH is unavailable. Try again later."}

    def test_a_capacity_refusal_tells_the_caller_to_try_again_later(self):
        # EDH answered definitely, so neither the unavailable 502 nor the 429 both clients retry on their own.
        ec = _client_module()
        response = ec.map_edh_error_to_response(
            ec.EdhCapacityError("EDH found no capacity for PUT /api/dcv/virtual_desktops/start", status=500), None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"]) == {
            "message": "EDH found no available AWS capacity for this instance type. Try again later."}

    @pytest.mark.parametrize("error_name", [
        "EdhQuotaError", "EdhCapacityError", "EdhNotFoundError", "EdhAuthError", "EdhUnavailableError",
        "EdhContractError", "EdhApiError", "EdhError",
    ])
    def test_every_other_error_carries_the_message_alone(self, error_name):
        ec = _client_module()
        body = json.loads(ec.map_edh_error_to_response(getattr(ec, error_name)("x"), None)["body"])
        assert set(body) == {"message"}

    def test_a_contract_error_on_a_job_submit_is_flagged_as_an_unknown_outcome(self):
        # EDH accepted the job but its answer names none, so whether a job is running is unconfirmed.
        ec = _client_module()
        error = ec.EdhContractError("x", status=200, body_text="Job ID: unknown", job_submitted=True)
        response = ec.map_edh_error_to_response(error, None)
        assert response["statusCode"] == 502
        assert json.loads(response["body"]) == {
            "message": ("EDH accepted the request but returned an unreadable answer. "
                        "Check the job list before retrying."),
            "outcomeUnknown": True,
        }

    def test_the_event_reaches_the_error_helper(self, monkeypatch):
        ec = _client_module()
        captured = {}

        def fake_general_error(status_code=400, body=None, event=None):
            captured.update(status_code=status_code, body=body, event=event)
            return {"isBase64Encoded": False, "statusCode": status_code, "headers": {}, "body": json.dumps(body)}

        monkeypatch.setattr(ec, "general_error", fake_general_error)
        # REST-shaped, as API Gateway v1 delivers it: explicit null params, top-level path/method.
        event = {
            "httpMethod": "POST",
            "path": "/addon/edh/workstations",
            "pathParameters": None,
            "queryStringParameters": None,
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"},
                               "authorizer": {"vams:tokens": '["alice"]'}},
        }
        ec.map_edh_error_to_response(ec.EdhQuotaError("q"), event)
        assert captured["event"] is event
        assert captured["status_code"] == 429
        assert captured["body"] == {"message": "The EDH quota for this operation has been reached."}


@pytest.mark.unit
class TestListAllDesktopsContractError:
    """Section 7: list_all_desktops must raise EdhContractError on malformed success."""

    def test_list_all_raises_on_empty_200_body(self, monkeypatch):
        """An empty 2xx body becomes {}, and _as_list turns that into [] -- should raise."""
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(200, b"")])
        with pytest.raises(ec.EdhContractError) as excinfo:
            ec.EdhClient().list_all_desktops()
        assert "success" in str(excinfo.value).lower()

    def test_list_all_raises_on_success_true_with_string_message(self, monkeypatch):
        """success:true + string message -- _as_list returns [] -- should raise."""
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(200, {"success": True, "message": "some text"})])
        with pytest.raises(ec.EdhContractError) as excinfo:
            ec.EdhClient().list_all_desktops()
        assert "dict" in str(excinfo.value).lower()

    def test_list_all_raises_on_success_true_with_list_message(self, monkeypatch):
        """success:true + list message -- should raise."""
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(200, {"success": True, "message": ["item1", "item2"]})])
        with pytest.raises(ec.EdhContractError) as excinfo:
            ec.EdhClient().list_all_desktops()

    def test_list_all_accepts_empty_dict_message(self, monkeypatch):
        """success:true + empty dict message -- valid empty listing."""
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(200, {"success": True, "message": {}})])
        result = ec.EdhClient().list_all_desktops()
        assert result == []

    def test_list_all_accepts_dict_message_with_desktops(self, monkeypatch):
        """success:true + dict of desktops -- valid listing."""
        desktops = {
            "uuid1": {"session_uuid": "uuid1", "session_name": "desktop1"},
            "uuid2": {"session_uuid": "uuid2", "session_name": "desktop2"}
        }
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(200, {"success": True, "message": desktops})])
        result = ec.EdhClient().list_all_desktops()
        assert len(result) == 2
        assert result[0]["session_uuid"] in ("uuid1", "uuid2")


@pytest.mark.unit
class TestClassifyErrorNonEnvelope429:
    """Section 11: non-envelope HTTP 429 should be transient, not definite API error."""

    def test_non_envelope_429_is_unavailable_error(self, monkeypatch):
        """A bare HTTP 429 (WAF/ALB throttle) should be EdhUnavailableError."""
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(429, b"<html>Rate Limit Exceeded</html>")])
        with pytest.raises(ec.EdhUnavailableError) as excinfo:
            ec.EdhClient().request("POST", "/api/dcv/virtual_desktops/create", edh_uid="v1", form={})
        assert excinfo.value.status == 429

    def test_envelope_429_refusal_stays_api_error(self, monkeypatch):
        """An EDH envelope refusal with 429 status stays EdhApiError (could be a quota)."""
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(429, _refusal("too many requests"))])
        # If the message has quota markers, it becomes EdhQuotaError; otherwise EdhApiError
        with pytest.raises(ec.EdhApiError):
            ec.EdhClient().request("POST", "/api/dcv/virtual_desktops/create", edh_uid="v1", form={})

    def test_other_non_envelope_4xx_stays_api_error(self, monkeypatch):
        """Other non-envelope 4xx (403, 413) stay EdhApiError."""
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(403, b"<html>Forbidden</html>")])
        with pytest.raises(ec.EdhApiError) as excinfo:
            ec.EdhClient().request("POST", "/api/dcv/virtual_desktops/create", edh_uid="v1", form={})
        assert excinfo.value.status == 403
