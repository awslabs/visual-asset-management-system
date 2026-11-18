"""APIClient methods, helpers and log redaction of audit log search: requests sent, error mapping,
the poll loop, the filter builder and the keep-content-out-of-the-log-file rule."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import requests

from vamscli import constants
from vamscli.utils import audit_search as aus
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    APIError,
    AuditQueryNotFoundError,
    AuditSearchNotSupportedError,
    AuditSearchTimeoutError,
    InvalidAuditRequestError,
    PermissionDeniedError,
)
from vamscli.utils.logging import redact_to_text

BASE = "https://api.example.com"
QID = "11111111-2222-3333-4444-555555555555"
NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)


def _response(body=None, status=200, headers=None):
    response = requests.Response()
    response.status_code = status
    response.url = BASE
    response._content = b"" if body is None else json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def _client():
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "t"}
    return APIClient(BASE, profile_manager=profile_manager)


class _Wire:
    def __init__(self, monkeypatch, client, *responses):
        self.sends = []
        self._responses = list(responses)

        def request(method, url, **kwargs):
            self.sends.append((method, url, kwargs))
            return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

        monkeypatch.setattr(client.session, "request", request)


class TestRequests:
    def test_each_method_uses_its_route(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({"ok": True}))
        client.list_audit_log_groups()
        client.start_audit_query({"startTime": 1, "endTime": 2})
        client.get_audit_query(QID, wait=20, page_size=50, starting_token="50", sort="asc")
        client.stop_audit_query(QID)
        client.export_audit_query(QID, "ndjson", "10")
        routes = [(m, u.replace(BASE, "")) for m, u, _ in wire.sends]
        assert routes == [
            ("GET", "/audit/log-groups"), ("POST", "/audit/queries"),
            ("GET", f"/audit/queries/{QID}"), ("DELETE", f"/audit/queries/{QID}"),
            ("GET", f"/audit/queries/{QID}/export"),
        ]
        assert wire.sends[1][2]["json"] == {"startTime": 1, "endTime": 2}
        assert wire.sends[2][2]["params"] == {"wait": 20, "pageSize": 50, "startingToken": "50", "sort": "asc"}
        assert wire.sends[4][2]["params"] == {"format": "ndjson", "startingToken": "10"}

    def test_a_handle_cannot_change_the_route(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({}))
        client.get_audit_query("a/b?c#d")
        assert "%2F" in wire.sends[0][1] and "?" not in wire.sends[0][1].split("queries/")[1]


class TestErrorMapping:
    @pytest.mark.parametrize("status,body,expected", [
        (404, {"message": "Query not found"}, AuditQueryNotFoundError),
        (400, {"message": "Invalid audit search request"}, InvalidAuditRequestError),
        (500, {"message": "Internal Server Error"}, APIError),
    ])
    def test_status_maps_to_the_cli_error(self, monkeypatch, status, body, expected):
        client = _client()
        _Wire(monkeypatch, client, _response(body, status=status))
        with pytest.raises(expected) as caught:
            client.get_audit_query(QID)
        assert QID not in str(caught.value)

    def test_404_never_says_why(self, monkeypatch):
        client = _client()
        _Wire(monkeypatch, client, _response({"message": "Query not found"}, status=404))
        with pytest.raises(AuditQueryNotFoundError) as caught:
            client.stop_audit_query(QID)
        assert "unknown, has expired or is not yours" in str(caught.value)

    def test_a_400_message_is_the_servers_generic_text(self, monkeypatch):
        client = _client()
        _Wire(monkeypatch, client, _response(
            {"message": "Too many audit searches are running. Try again later."}, status=400))
        with pytest.raises(InvalidAuditRequestError, match="Too many audit searches"):
            client.start_audit_query({})

    def test_the_handler_403_is_a_permission_denial(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({"message": "Not Authorized"}, status=403))
        with pytest.raises(PermissionDeniedError, match="Not Authorized"):
            client.start_audit_query({"logGroups": ["x"]})
        assert len(wire.sends) == 1

    def test_a_missing_route_is_not_supported(self, monkeypatch):
        client = _client()
        _Wire(monkeypatch, client, _response(
            {"message": "Missing Authentication Token"}, status=403,
            headers={"x-amzn-ErrorType": "MissingAuthenticationTokenException"}))
        with pytest.raises(AuditSearchNotSupportedError, match="not enabled on this deployment") as raised:
            client.list_audit_log_groups()
        # A stale feature list after the switch flips off gets the same answer as an older VAMS, so the
        # text must not tell the user to upgrade a 2.7.0 deployment: it says to sign in again.
        message = str(raised.value)
        assert "Sign in again" in message and "upgrade" not in message.lower()


class TestFilters:
    def test_times(self):
        assert aus.parse_time("90m", NOW) == int(NOW.timestamp()) - 5400
        assert aus.parse_time("2w", NOW) == int(NOW.timestamp()) - 1209600
        assert aus.parse_time("now", NOW) == int(NOW.timestamp())
        assert aus.parse_time("2026-11-17T12:00:00Z", NOW) == int(NOW.timestamp())
        assert aus.parse_time("2026-11-17T14:00:00+02:00", NOW) == int(NOW.timestamp())
        assert aus.parse_time("1763380800", NOW) == 1763380800

    @pytest.mark.parametrize("bad", ["", "soon", "12x", "2026-13-40"])
    def test_bad_times(self, bad):
        with pytest.raises(InvalidAuditRequestError):
            aus.parse_time(bad, NOW)

    def test_window_defaults_and_order(self):
        assert aus.build_window(None, None, NOW) == (int(NOW.timestamp()) - 86400, int(NOW.timestamp()))
        with pytest.raises(InvalidAuditRequestError):
            aus.build_window("1h", "2h", NOW)

    def test_values_are_deduplicated_and_checked(self):
        body = aus.build_filters(actions=["a", "a", "b"], now=NOW)
        assert body["actions"] == ["a", "b"] and "users" not in body
        with pytest.raises(InvalidAuditRequestError):
            aus.build_filters(outcomes=["fine"], now=NOW)

    def test_contains_limits_follow_the_limits_given(self):
        assert aus.validate_contains(["abcde"] * 5, limits=(5, 128)) == ["abcde"] * 5
        assert aus.validate_contains(["a" * 128], limits=(3, 128)) == ["a" * 128]
        with pytest.raises(InvalidAuditRequestError, match="At most 2"):
            aus.validate_contains(["abc"] * 3, limits=(2, 64))
        with pytest.raises(InvalidAuditRequestError, match="3 to 10 characters"):
            aus.validate_contains(["a" * 11], limits=(3, 10))
        with pytest.raises(InvalidAuditRequestError):
            aus.validate_contains(["ab"], limits=(5, 128))  # the 3-character floor stays local

    def test_contains_limits_match_the_servers(self):
        assert aus.validate_contains(["abc", "a.b_c:d@e+f/g-h i"]) == ["abc", "a.b_c:d@e+f/g-h i"]
        for bad in (["ab"], ["x" * 65], ["a*bc"], ["abc"] * 4, ["a\nbc"]):
            with pytest.raises(InvalidAuditRequestError):
                aus.validate_contains(bad)

    def test_page_size(self):
        assert aus.check_page_size(None) is None and aus.check_page_size(500) == 500
        with pytest.raises(InvalidAuditRequestError):
            aus.check_page_size(0)


class TestPollLoop:
    def _api(self, pages):
        api = MagicMock()
        api.get_audit_query.side_effect = pages
        return api

    def test_poll_after_ms_is_honoured_with_a_floor_and_ceiling(self):
        sleeps = []
        api = self._api([
            {"status": "Running", "pollAfterMs": 5000}, {"status": "Running", "pollAfterMs": 1},
            {"status": "Running", "pollAfterMs": 10 ** 9}, {"status": "Running"}, {"status": "Complete"}])
        aus.wait_for_audit_query(api, QID, sleep=sleeps.append)
        assert sleeps == [5.0, 0.25, 30, 0.25]

    def test_polls_are_sequential_and_ask_for_the_long_poll_wait(self):
        api = self._api([{"status": "Running"}, {"status": "Complete"}])
        aus.wait_for_audit_query(api, QID, page_size=10, sort="asc", sleep=lambda s: None)
        assert [c.kwargs["wait"] for c in api.get_audit_query.call_args_list] == [20, 20]
        assert api.get_audit_query.call_args.kwargs["page_size"] == 10

    def test_a_dropped_poll_is_retried_a_few_times_then_raised(self):
        api = self._api([APIError("net"), APIError("net"), {"status": "Complete"}])
        assert aus.wait_for_audit_query(api, QID, sleep=lambda s: None)["status"] == "Complete"
        api = self._api([APIError("net")] * 5)
        with pytest.raises(APIError):
            aus.wait_for_audit_query(api, QID, sleep=lambda s: None)
        assert api.get_audit_query.call_count == constants.AUDIT_NETWORK_RETRIES + 1

    def test_a_refusal_ends_the_loop_at_once(self):
        api = self._api([AuditQueryNotFoundError("gone")])
        with pytest.raises(AuditQueryNotFoundError):
            aus.wait_for_audit_query(api, QID, sleep=lambda s: None)
        assert api.get_audit_query.call_count == 1

    def test_the_client_deadline_names_the_handle(self):
        clock = iter([0, 0, 100, 100, 100])
        api = self._api([{"status": "Running"}] * 5)
        with pytest.raises(AuditSearchTimeoutError) as caught:
            aus.wait_for_audit_query(api, QID, timeout=50, sleep=lambda s: None, clock=lambda: next(clock))
        assert caught.value.json_fields == {"queryId": QID}


class TestExportHelpers:
    def test_chunks_follow_next_token(self):
        api = MagicMock()
        api.export_audit_query.side_effect = [
            {"content": "a", "NextToken": "1"}, {"content": "b"}]
        assert [c for c, _ in aus.export_chunks(api, QID, "csv")] == ["a", "b"]
        assert [c.args for c in api.export_audit_query.call_args_list] == [(QID, "csv", None), (QID, "csv", "1")]


class TestLogFileKeepsAuditContentOut:
    def test_contains_terms_are_not_logged(self):
        text = redact_to_text({"startTime": 1, "endTime": 2, "contains": ["hunter2-secret", "other"],
                               "users": ["carol"]})
        assert "hunter2" not in text and "other" not in text
        assert "2 terms omitted" in text
        assert "carol" not in text and "masked: 1 values, 5 characters" in text

    def test_a_request_without_contains_still_hides_who_is_searched(self):
        text = redact_to_text({"startTime": 1, "endTime": 2, "includeProbes": False,
                               "users": ["carol"], "entityIds": ["e-secret"], "apiKeyIds": ["k-secret"],
                               "actions": ["assetUpdate"]})
        assert "carol" not in text and "e-secret" not in text and "k-secret" not in text
        assert "assetUpdate" in text

    def test_rows_and_export_text_are_not_logged(self):
        page = {"queryId": QID, "status": "Complete", "Items": [{"user": "carol", "details": "pii-value"}] * 3}
        export = {"queryId": QID, "format": "csv", "content": "user\ncarol-pii\n"}
        assert "pii-value" not in redact_to_text(page) and "3 rows omitted" in redact_to_text(page)
        assert "carol-pii" not in redact_to_text(export) and "characters omitted" in redact_to_text(export)

    def test_one_shown_row_keeps_only_its_identity(self):
        row = {"recordId": "r1", "groupKey": "auditActions", "timestamp": "t", "action": "x",
               "outcome": "success", "user": "carol", "requestParameters": {"p": "pii-value"}}
        text = redact_to_text(row)
        assert "r1" in text and "pii-value" not in text and "carol" not in text

    def test_other_payloads_are_unchanged(self):
        assert "hello" in redact_to_text({"contains": "hello"})
        assert "row" in redact_to_text({"Items": ["row"]})


class TestContainsLimitsFromAuditGroups:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        aus._limits_cache.clear()
        yield
        aus._limits_cache.clear()

    def _api(self, limits, url="https://a.example/api", profile="p1"):
        api = MagicMock()
        api.base_url = url
        api.profile_manager.profile_name = profile
        api.list_audit_log_groups.return_value = {"limits": limits}
        return api

    def test_reads_the_deployments_limits(self):
        api = self._api({"maxContainsTerms": 5, "maxContainsTermLength": 128})
        assert aus.get_contains_limits(api) == (5, 128)

    def test_cached_per_deployment_and_profile_and_expires(self):
        api = self._api({"maxContainsTerms": 4, "maxContainsTermLength": 100})
        clock = iter([0.0, 10.0, 20.0, 1000.0]).__next__
        assert aus.get_contains_limits(api, clock) == (4, 100)
        assert aus.get_contains_limits(api, clock) == (4, 100)
        assert api.list_audit_log_groups.call_count == 1
        other = self._api({"maxContainsTerms": 2, "maxContainsTermLength": 20}, profile="p2")
        assert aus.get_contains_limits(other, clock) == (2, 20)
        assert aus.get_contains_limits(api, clock) == (4, 100)  # read again after the cache time
        assert api.list_audit_log_groups.call_count == 2

    @pytest.mark.parametrize("limits", [{}, {"maxContainsTerms": "x"}, {"maxContainsTerms": 0},
                                        {"maxContainsTerms": True}])
    def test_missing_or_odd_values_use_the_defaults(self, limits):
        assert aus.get_contains_limits(self._api(limits)) == (3, 64)

    def test_a_failed_read_uses_the_defaults_and_is_not_cached(self):
        api = self._api({})
        api.list_audit_log_groups.side_effect = RuntimeError("boom")
        assert aus.get_contains_limits(api) == (3, 64)
        api.list_audit_log_groups.side_effect = None
        api.list_audit_log_groups.return_value = {"limits": {"maxContainsTerms": 5}}
        assert aus.get_contains_limits(api) == (5, 64)

    def test_no_terms_means_no_request(self):
        api = self._api({})
        assert aus.resolve_contains_limits(api, []) is None
        assert aus.resolve_contains_limits(api, ["  "]) is None
        api.list_audit_log_groups.assert_not_called()
        assert aus.resolve_contains_limits(api, ["abc"]) == (3, 64)

    def test_find_record_counts_rows_without_an_id(self):
        api = MagicMock()
        api.get_audit_query.return_value = {"Items": [{"recordId": "a"}, {"user": "x"}, {"recordId": ""}]}
        stats = {}
        assert aus.find_record(api, QID, "nope", stats=stats) is None
        assert stats == {"withoutId": 2}
