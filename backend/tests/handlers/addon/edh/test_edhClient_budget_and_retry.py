# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH client's request-latency budget, deadline and retry policy.

API Gateway cuts every route at 29 s and the Lambda keeps running, so an EDH create that is retried
after a read timeout can produce a second desktop the caller never sees. The arithmetic test states
the bound the ``api`` profile must satisfy; the policy tests state which methods may be retried at
all, as UPPER bounds on the attempts recorded -- a client that sends fewer requests is never wrong
here. Every tightening carries a control asserting the behaviour that must not change: the ordinary
one-attempt request, the GET retry, the header set a user-scoped call sends.
"""

import json
import urllib.parse

import pytest
import urllib3

# Imported at module level so a client that fails to import is a collection error for the whole
# file, and so every test binds the one module object install_client_fakes patches.
from backend.backend.handlers.addon.edh.common import edhClient as _ec  # noqa: F401
from backend.tests.handlers.addon.edh.edhTestSupport import FakeResponse, Slow, install_client_fakes, sent_form

ORIGIN = "https://internal-edh-alb.example.internal"
LIST = "/api/dcv/virtual_desktops/list"
PROFILES = "/api/dcv/virtual_desktops/profiles"
CREATE = "/api/dcv/virtual_desktops/create"
STOP = "/api/dcv/virtual_desktops/stop"
DELETE = "/api/dcv/virtual_desktops/delete"
STACKS = "/api/dcv/virtual_desktops/software_stacks"
# EDH's answer to a business refusal: a 500 carrying its envelope.
REFUSAL = {"success": False, "message": "Unable to Launch Desktop x (owner: v1): refused (Request ID: 1)"}


def _client_module():
    import backend.backend.handlers.addon.edh.common.edhClient as ec
    return ec


def _paths(recorded):
    return {urllib.parse.urlsplit(call["url"]).path for call in recorded["calls"]}


@pytest.mark.unit
class TestApiProfileBudget:
    def test_the_api_profile_is_the_design_pair(self):
        # Timeout(connect=3, read=8), 2 attempts, backoff <= 1 s, under a 20 s budget. Unclamped the
        # worst case is 2 x (3 + 8) + 1 = 23 s; the deadline clamp (TestDeadline) is the bound.
        ec = _client_module()
        profile = ec._PROFILES["api"]
        assert (profile.connect_timeout, profile.read_timeout) == (3.0, 8.0)
        assert (profile.attempts, profile.backoff_cap) == (2, 1.0)
        assert profile.budget == ec.EDH_REQUEST_BUDGET_SECONDS == 20

    def test_the_budget_sits_under_the_gateway_floor(self):
        ec = _client_module()
        assert ec.API_GATEWAY_TIMEOUT_FLOOR_SECONDS == 29
        assert ec.EDH_REQUEST_BUDGET_SECONDS < (
            ec.API_GATEWAY_TIMEOUT_FLOOR_SECONDS - ec.REQUEST_OVERHEAD_RESERVE_SECONDS)

    def test_the_api_profile_is_the_default(self):
        ec = _client_module()
        client = ec.EdhClient()
        assert client._profile is ec._PROFILES["api"]
        assert client.profile_name == "api"

    def test_the_batch_profile_is_wider_than_the_api_profile(self):
        ec = _client_module()
        api, batch = ec._PROFILES["api"], ec._PROFILES["batch"]
        assert batch.read_timeout > api.read_timeout
        assert batch.attempts >= api.attempts
        assert batch.budget > api.budget

    def test_an_unknown_profile_is_refused(self):
        ec = _client_module()
        with pytest.raises(ValueError):
            ec.EdhClient(profile="fast")

    def test_backoff_arithmetic_is_not_vacuous(self):
        # Positive control for _backoff_total: 3 attempts with cap 10 pause 1 + 2 s; 2 attempts pause 1 s.
        ec = _client_module()
        assert ec._backoff_total(ec._PROFILES["batch"]) == 3.0
        assert ec._backoff_total(ec._PROFILES["api"]) == 1.0


@pytest.mark.unit
class TestDeadline:
    """The clock is the FakeClock install_client_fakes put in place of the client's time module."""

    def test_an_exhausted_deadline_makes_no_http_call(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        client = ec.EdhClient(deadline=ec.time.monotonic() - 1.0)
        with pytest.raises(ec.EdhUnavailableError):
            client.request("GET", PROFILES)
        assert recorded["calls"] == []

    def test_less_than_the_minimum_attempt_window_makes_no_http_call(self, monkeypatch):
        # urllib3 refuses a zero timeout, and a TLS handshake needs about a second: below the
        # minimum window the request is refused rather than started.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        client = ec.EdhClient(deadline=ec.time.monotonic() + ec._MIN_ATTEMPT_SECONDS / 2)
        with pytest.raises(ec.EdhUnavailableError):
            client.request("GET", PROFILES)
        assert recorded["calls"] == []
        assert ec._MIN_ATTEMPT_SECONDS == 1.0

    def test_per_attempt_timeouts_are_clamped_so_their_sum_fits_the_remaining_budget(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        client = ec.EdhClient(deadline=ec.time.monotonic() + 2.0)
        client.request("GET", PROFILES)
        timeout = recorded["calls"][0]["timeout"]
        assert (timeout.connect_timeout, timeout.read_timeout) == (1.0, 1.0)
        assert timeout.connect_timeout + timeout.read_timeout <= 2.0

    def test_a_fresh_client_sends_the_profile_timeouts(self, monkeypatch):
        # Control: with the whole budget left, the design's pair goes out unchanged.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("GET", PROFILES)
        timeout = recorded["calls"][0]["timeout"]
        assert (timeout.connect_timeout, timeout.read_timeout) == (3.0, 8.0)

    def test_a_retry_after_a_slow_attempt_gets_only_what_remains(self, monkeypatch):
        # 20 s budget: the first attempt spends 11 s (3 connect + 8 read) and the backoff 1 s, so
        # the retry has 8 s and is given connect 3 + read 5 -- the request ends at the deadline.
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[Slow(11.0, OSError("read timed out")), 200])
        ec.EdhClient().request("GET", PROFILES)
        first, second = (call["timeout"] for call in recorded["calls"])
        assert (first.connect_timeout, first.read_timeout) == (3.0, 8.0)
        assert (second.connect_timeout, second.read_timeout) == (3.0, 5.0)
        assert recorded["clock"].slept == [1.0]

    def test_the_deadline_is_shared_by_every_call_of_one_client(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[Slow(2.0, 200), 200])
        client = ec.EdhClient()
        before = client.remaining_seconds()
        client.request("GET", LIST, edh_uid="v1", params={"is_active": "true"})
        client.request("GET", PROFILES)
        assert before - client.remaining_seconds() == 2.0
        assert _paths(recorded) == {LIST, PROFILES}


@pytest.mark.unit
class TestASingleAttemptReadsToTheDeadline:
    """A request sent once has no second attempt to save budget for, so under the api profile it reads
    for what the budget leaves after its connect phase instead of the 8 s a retried GET is given: an EDH
    start that answers after 8 s is an answer, not an unknown outcome. The clamp still holds it inside
    what remains; the GET pair and the batch profile are unchanged."""

    START = "/api/dcv/virtual_desktops/start"

    @pytest.mark.parametrize("method", ["PUT", "POST", "DELETE"])
    def test_a_fresh_client_reads_a_mutation_for_the_rest_of_the_budget(self, monkeypatch, method):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request(method, self.START, edh_uid="v1", form={"session_uuid": "s"})
        timeout = recorded["calls"][0]["timeout"]
        assert (timeout.connect_timeout, timeout.read_timeout) == (3.0, 17.0)
        assert timeout.read_timeout > ec._PROFILES["api"].read_timeout
        assert timeout.connect_timeout + timeout.read_timeout <= ec.EDH_REQUEST_BUDGET_SECONDS

    @pytest.mark.parametrize("remaining, pair", [(12.0, (3.0, 9.0)), (6.0, (3.0, 3.0)), (2.0, (1.0, 1.0))])
    def test_a_mutation_never_reads_past_the_deadline(self, monkeypatch, remaining, pair):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient(deadline=ec.time.monotonic() + remaining).request(
            "PUT", self.START, edh_uid="v1", form={"session_uuid": "s"})
        timeout = recorded["calls"][0]["timeout"]
        assert (timeout.connect_timeout, timeout.read_timeout) == pair
        assert timeout.connect_timeout + timeout.read_timeout <= remaining

    def test_a_mutation_after_a_slow_read_gets_only_what_remains(self, monkeypatch):
        # The start route lists before it starts: the list spent 5 s of the 20, the start reads for 12.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[Slow(5.0, 200), 200])
        client = ec.EdhClient()
        client.request("GET", LIST, edh_uid="v1", params={"is_active": True})
        client.request("PUT", self.START, edh_uid="v1", form={"session_uuid": "s"})
        start = recorded["calls"][1]["timeout"]
        assert (start.connect_timeout, start.read_timeout) == (3.0, 12.0)

    def test_a_mutation_that_reads_to_its_timeout_leaves_its_connect_allowance_for_a_read_of_the_outcome(
            self, monkeypatch):
        # A connect completes in milliseconds, so a read that runs out ends about a connect allowance before
        # the deadline: enough for the list that finds out what the lost start did.
        read_timeout = urllib3.exceptions.ReadTimeoutError(None, self.START, "Read timed out. (read timeout=17.0)")
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[Slow(17.0, read_timeout), 200])
        client = ec.EdhClient()
        with pytest.raises(ec.EdhOutcomeUnknownError):
            client.request("PUT", self.START, edh_uid="v1", form={"session_uuid": "s"})
        client.request("GET", LIST, edh_uid="v1", params={"is_active": True})
        follow_up = recorded["calls"][1]["timeout"]
        assert client.remaining_seconds() == 3.0
        assert (follow_up.connect_timeout, follow_up.read_timeout) == (1.5, 1.5)

    def test_a_get_keeps_the_profile_pair(self, monkeypatch):
        # Control: the retried GET is not widened.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("GET", LIST, edh_uid="v1", params={"is_active": True})
        timeout = recorded["calls"][0]["timeout"]
        assert (timeout.connect_timeout, timeout.read_timeout) == (3.0, 8.0)

    def test_the_batch_profile_keeps_its_read_timeout_for_a_mutation(self, monkeypatch):
        # Control: the batch profile's read already outlasts the routes it calls, and its callers are
        # bounded by their own Lambda timeouts.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient(profile="batch").request("POST", CREATE, edh_uid="v1", form={})
        timeout = recorded["calls"][0]["timeout"]
        assert (timeout.connect_timeout, timeout.read_timeout) == (10.0, 60.0)


@pytest.mark.unit
class TestRetryPolicy:
    def test_a_get_is_retried_after_a_transport_error(self, monkeypatch):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[OSError("connection reset"), 200])
        assert ec.EdhClient().request("GET", PROFILES) == {}
        assert 2 <= len(recorded["calls"]) <= ec._PROFILES["api"].attempts
        assert {call["method"] for call in recorded["calls"]} == {"GET"}

    def test_a_get_is_retried_after_a_5xx_that_is_not_an_edh_refusal(self, monkeypatch):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(503, b"<html>Service Unavailable</html>"),
                     FakeResponse(200, {"success": True, "message": {}})])
        assert ec.EdhClient().request("GET", PROFILES) == {"success": True, "message": {}}
        assert 2 <= len(recorded["calls"]) <= ec._PROFILES["api"].attempts

    def test_a_5xx_edh_refusal_is_final_and_not_retried(self, monkeypatch):
        # EDH answers a business refusal with 500 and its envelope; repeating it would spend the
        # budget on an answer that will not change.
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(500, REFUSAL), 200])
        with pytest.raises(ec.EdhApiError):
            ec.EdhClient().request("GET", PROFILES)
        assert recorded["calls"], "the request was never sent"
        assert len(recorded["calls"]) <= 1
        assert recorded["clock"].slept == []

    def test_a_get_gives_up_within_the_profile_attempts(self, monkeypatch):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[OSError("a"), OSError("b")])
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient().request("GET", PROFILES)
        assert 1 <= len(recorded["calls"]) <= ec._PROFILES["api"].attempts

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
    def test_a_mutation_is_never_retried_after_a_transport_error(self, monkeypatch, method):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[OSError("read timed out"), 200])
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient().request(method, CREATE, edh_uid="v1", form={"a": "1"})
        assert recorded["calls"], "the mutation was never sent"
        assert len(recorded["calls"]) <= 1

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
    def test_a_mutation_is_never_retried_after_a_5xx(self, monkeypatch, method):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[FakeResponse(502, b""), 200])
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient().request(method, STOP, edh_uid="v1", form={"session_uuid": "s"})
        assert recorded["calls"], "the mutation was never sent"
        assert len(recorded["calls"]) <= 1

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
    def test_the_batch_profile_does_not_retry_a_mutation_either(self, monkeypatch, method):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[OSError("read timed out"), 200])
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient(profile="batch").request(method, STACKS, form={"a": "b"})
        assert recorded["calls"], "the mutation was never sent"
        assert len(recorded["calls"]) <= 1

    def test_head_is_not_retried_either(self, monkeypatch):
        # Design: 2 attempts for GET only. HEAD would be safe to repeat, and is still sent once.
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[OSError("a"), 200])
        with pytest.raises(ec.EdhUnavailableError):
            ec.EdhClient().request("HEAD", PROFILES)
        assert recorded["calls"], "the request was never sent"
        assert len(recorded["calls"]) <= 1
        assert ec._RETRIED_METHODS == frozenset({"GET"})

    def test_no_pause_exceeds_the_api_backoff_cap(self, monkeypatch):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k1"], results=[OSError("a"), 200])
        ec.EdhClient().request("GET", PROFILES)
        assert recorded["clock"].slept == [1.0]


@pytest.mark.unit
class TestRootKeyRefreshOn401:
    def test_a_401_rereads_the_secret_and_resends_with_the_new_key(self, monkeypatch):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["k-old", "k-new"],
            results=[FakeResponse(401, {"success": False, "message": "Not authorized"}), 200])
        ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={"a": "1"})
        sent_keys = [call["headers"][ec.EDH_TOKEN_HEADER] for call in recorded["calls"]]
        assert "k-old" in sent_keys
        assert "k-new" in sent_keys
        assert sent_keys.index("k-old") < sent_keys.index("k-new")
        assert recorded["secret_reads"] >= 2

    def test_a_second_401_raises_auth_error(self, monkeypatch):
        # Control: one re-read, then give up.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k-old", "k-new"], results=[401, 401])
        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", PROFILES)
        assert excinfo.value.status == 401
        assert recorded["secret_reads"] >= 2
        assert len(recorded["calls"]) <= 2


@pytest.mark.unit
class TestHeadersAndBodies:
    def test_user_scoped_requests_carry_both_headers_and_the_query(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("GET", LIST, edh_uid="vfpmansl7byak6gq", params={"is_active": True})
        call = recorded["calls"][0]
        assert call["headers"][ec.EDH_TOKEN_HEADER] == "k1"
        assert call["headers"][ec.EDH_USER_HEADER] == "vfpmansl7byak6gq"
        assert call["headers"]["Accept"] == "application/json"
        assert call["headers"]["Accept-Language"] == "en"
        assert call["url"] == f"{ORIGIN}/api/dcv/virtual_desktops/list?is_active=true"

    def test_a_request_given_no_uid_carries_the_service_actor_and_the_root_key(self, monkeypatch):
        # EDH's feature flags evaluate X-EDH-USER on the routes they gate, cluster-scoped ones included,
        # so a call without a caller uid names the service actor rather than nobody.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("GET", PROFILES)
        assert recorded["calls"][0]["headers"][ec.EDH_USER_HEADER] == ec.EDH_SERVICE_ACTOR_UID
        assert recorded["calls"][0]["headers"][ec.EDH_TOKEN_HEADER] == "k1"

    def test_a_form_goes_out_urlencoded_with_its_content_type(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("PUT", STOP, edh_uid="v1", form={"session_uuid": "s"})
        call = recorded["calls"][0]
        assert call["headers"]["Content-Type"] == ec.EDH_FORM_CONTENT_TYPE == "application/x-www-form-urlencoded"
        assert call["body"] == b"session_uuid=s"
        assert sent_form(call) == {"session_uuid": "s"}
        assert "fields" not in call

    def test_a_delete_carries_its_form_in_the_body_not_the_query(self, monkeypatch):
        # EDH's DELETE routes read form fields; urllib3 would put the `fields=` of a DELETE into the
        # query string, so the client encodes the body itself.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("DELETE", DELETE, edh_uid="v1", form={"session_uuid": "s"})
        call = recorded["calls"][0]
        assert call["url"] == f"{ORIGIN}/api/dcv/virtual_desktops/delete"
        assert sent_form(call) == {"session_uuid": "s"}
        assert call["headers"]["Content-Type"] == ec.EDH_FORM_CONTENT_TYPE

    def test_form_values_are_encoded_as_edh_parses_them(self, monkeypatch):
        # Booleans travel as EDH's true/false words, numbers as digits, None fields are omitted.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("POST", CREATE, edh_uid="v1",
                               form={"hibernate": False, "disk_size": 100, "subnet_id": None, "project": "default"})
        assert sent_form(recorded["calls"][0]) == {"hibernate": "false", "disk_size": "100", "project": "default"}

    def test_a_file_part_switches_the_body_to_multipart(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("POST", STACKS, form={"stack_name": "Ubuntu 22 GPU"},
                               files={"thumbnail": ("thumb.png", b"\x89PNG", "image/png")})
        call = recorded["calls"][0]
        assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
        assert b'name="stack_name"' in call["body"]
        assert b"Ubuntu 22 GPU" in call["body"]
        assert b'name="thumbnail"; filename="thumb.png"' in call["body"]
        assert b"\x89PNG" in call["body"]
        assert "fields" not in call

    def test_a_json_body_cannot_be_sent(self):
        # Every EDH write route reads form fields; the request signature offers no JSON body.
        ec = _client_module()
        with pytest.raises(TypeError):
            ec.EdhClient().request("POST", CREATE, edh_uid="v1", body={"a": 1})

    def test_the_header_constants_are_the_edh_names(self):
        ec = _client_module()
        assert (ec.EDH_USER_HEADER, ec.EDH_TOKEN_HEADER) == ("X-EDH-USER", "X-EDH-TOKEN")

    def test_a_list_body_is_wrapped_under_the_payload_key(self, monkeypatch):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[FakeResponse(200, [1, 2])])
        assert ec.EdhClient().request("GET", PROFILES) == {"message": [1, 2]}

    def test_an_empty_2xx_body_is_an_empty_dict(self, monkeypatch):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[FakeResponse(204)])
        assert ec.EdhClient().request("DELETE", DELETE, edh_uid="v1", form={"session_uuid": "s"}) == {}

    def test_a_relative_path_without_a_leading_slash_is_still_joined(self, monkeypatch):
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200])
        ec.EdhClient().request("GET", "api/dcv/virtual_desktops/profiles")
        assert recorded["calls"][0]["url"] == f"{ORIGIN}/api/dcv/virtual_desktops/profiles"

    def test_every_route_is_joined_to_the_endpoint_origin(self, monkeypatch):
        # EDH mounts every resource at /api/... with no version segment; the endpoint is scheme and
        # host only, and the route carries the whole path.
        ec, recorded = install_client_fakes(monkeypatch, root_keys=["k1"], results=[200, 200])
        client = ec.EdhClient()
        client.request("GET", "/api/ldap/user", params={"user": "vabc"})
        client.request("GET", "/api/containers/batch/job_queue")
        assert ec.EDH_API_ENDPOINT == ORIGIN
        assert {call["url"] for call in recorded["calls"]} == {
            f"{ORIGIN}/api/ldap/user?user=vabc", f"{ORIGIN}/api/containers/batch/job_queue"}


class _RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, message, *args, **kwargs):
        self.records.append([message, kwargs.get("extra")])

    debug = info = warning = error = exception = _record


@pytest.mark.unit
class TestLoggingHygiene:
    def test_no_log_line_carries_the_root_key_the_headers_or_a_success_body(self, monkeypatch):
        ec, recorded = install_client_fakes(
            monkeypatch, root_keys=["ROOT-KEY-SECRET-1", "ROOT-KEY-SECRET-2"],
            results=[
                OSError("boom"),
                FakeResponse(401, {"success": False, "message": "Not authorized"}),
                FakeResponse(200, {"success": True, "message": {
                    "s1": {"connection_string": "https://dcv.example/?authToken=TOKEN-VALUE"}}}),
            ])
        sink = _RecordingLogger()
        monkeypatch.setattr(ec, "logger", sink)
        ec.EdhClient().request("GET", LIST, edh_uid="vabc", params={"is_active": "true"})
        flattened = json.dumps(sink.records)
        assert len(sink.records) >= 2, "the transport retry and the 401 must both have logged"
        assert "ROOT-KEY-SECRET" not in flattened
        assert "TOKEN-VALUE" not in flattened
        assert "X-EDH-TOKEN" not in flattened
        assert "/api/dcv/virtual_desktops/list" in flattened

    def test_error_bodies_are_logged_truncated_with_the_status(self, monkeypatch):
        ec, _ = install_client_fakes(
            monkeypatch, root_keys=["k1"],
            results=[FakeResponse(500, {"success": False, "message": "x" * 2000})])
        sink = _RecordingLogger()
        monkeypatch.setattr(ec, "logger", sink)
        with pytest.raises(ec.EdhApiError):
            ec.EdhClient().request("POST", CREATE, edh_uid="v1", form={})
        error_records = [extra for _message, extra in sink.records if extra and "edhBody" in extra]
        assert error_records, "the refusal was not logged"
        assert all(extra["edhStatus"] == 500 and len(extra["edhBody"]) <= ec._ERROR_BODY_LOG_LIMIT
                   for extra in error_records)
