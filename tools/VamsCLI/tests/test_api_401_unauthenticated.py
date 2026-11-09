"""How the client handles an HTTP 401 from the VAMS API (issue #1212).

From VAMS 2.7.0 the API's authorizer answers a credential that does not authenticate (an expired or
invalid token, an unknown, disabled or expired API key) with 401 {"message":"Unauthorized"} instead of
a 403. The client refreshes and resends; a 401 that persists is a sign-in problem
(`AuthenticationError`), never a generic "API request failed (401)" `APIError`, and that holds for the
callers that pass raise_http_errors=True too. An override token is not refreshed.
"""

import json
import sys
from unittest.mock import MagicMock

import click
import pytest
import requests

from vamscli.constants import MAX_AUTH_RETRIES
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import APIError, AuthenticationError, OverrideTokenError
from vamscli.utils.global_exceptions import handle_global_exceptions

BASE = "https://abc123.execute-api.us-west-2.amazonaws.com/api"
UNAUTHORIZED = {"message": "Unauthorized"}


def _response(body=None, status=401):
    response = requests.Response()
    response.status_code = status
    response.reason = "Unauthorized" if status == 401 else "OK"
    response.url = BASE + "/database"
    response._content = b"" if body is None else json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def _client(override_token=False):
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = override_token
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "token-before"}
    return APIClient(BASE, profile_manager=profile_manager)


class _Wire:
    """The client's session and token refresh, replaced and recorded. The last response repeats."""

    def __init__(self, monkeypatch, client, *responses, refreshes_succeed=True):
        self.sends = []
        self.refreshes = 0
        self._responses = list(responses)

        def request(method, url, **kwargs):
            self.sends.append((method, url))
            return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

        def try_refresh_token(token_before=None):
            self.refreshes += 1
            return refreshes_succeed

        monkeypatch.setattr(client.session, "request", request)
        monkeypatch.setattr(client, "_try_refresh_token", try_refresh_token)


class TestA401IsRefreshedOnce:
    def test_a_401_then_success_returns_the_response_after_one_refresh(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(UNAUTHORIZED), _response({"ok": True}, status=200))

        response = client.get("/database", include_auth=True)

        assert response.status_code == 200
        assert (len(wire.sends), wire.refreshes) == (2, 1)

    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_a_persistent_401_is_an_authentication_error_not_an_api_error(self, monkeypatch,
                                                                          raise_http_errors):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(UNAUTHORIZED))

        with pytest.raises(AuthenticationError) as excinfo:
            client.get("/database", include_auth=True, raise_http_errors=raise_http_errors)

        assert not isinstance(excinfo.value, APIError)
        assert "HTTP 401" in str(excinfo.value)
        assert "vamscli auth login" in str(excinfo.value)
        # Bounded: no loop past the retry budget.
        assert len(wire.sends) == MAX_AUTH_RETRIES + 1
        assert wire.refreshes == MAX_AUTH_RETRIES

    def test_a_failed_refresh_stops_after_one_send(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(UNAUTHORIZED), refreshes_succeed=False)

        with pytest.raises(AuthenticationError):
            client.get("/database", include_auth=True)
        assert (len(wire.sends), wire.refreshes) == (1, 1)

    def test_an_override_token_is_never_refreshed(self, monkeypatch):
        client = _client(override_token=True)
        wire = _Wire(monkeypatch, client, _response(UNAUTHORIZED))

        with pytest.raises(OverrideTokenError):
            client.get("/database", include_auth=True)
        assert (len(wire.sends), wire.refreshes) == (1, 0)

    def test_an_unauthenticated_call_keeps_the_generic_error(self, monkeypatch):
        """Control: a call that sends no credential has nothing to refresh, so nothing changes."""
        client = _client()
        wire = _Wire(monkeypatch, client, _response(UNAUTHORIZED))

        with pytest.raises(APIError) as excinfo:
            client.get("/database", include_auth=False)
        assert "401" in str(excinfo.value)
        assert (len(wire.sends), wire.refreshes) == (1, 0)


class TestJsonOutput:
    def test_a_persistent_401_reports_an_authentication_error(self, monkeypatch):
        client = _client()
        _Wire(monkeypatch, client, _response(UNAUTHORIZED))
        monkeypatch.setattr(sys, "argv", ["vamscli", "database", "list", "--json-output"])

        @handle_global_exceptions()
        def command():
            client.get("/database", include_auth=True)

        with pytest.raises(click.ClickException) as excinfo:
            command()

        document = json.loads(excinfo.value.message)
        assert document["error_type"] == "AuthenticationError"
        assert document["error"].startswith("Authentication Error: ")
        assert document["additional_info"] == ["Run 'vamscli auth login' to re-authenticate."]
