"""APIClient with an injected, per-instance bearer token.

A process that serves several users, such as a remote MCP server, builds one APIClient per request
from the caller's own token: ``APIClient(base_url, bearer_token=token)``. These tests hold that:

* the token is sent as ``Authorization: Bearer <token>`` and only on authenticated requests;
* nothing reads or writes a profile: no ProfileManager is built, the active profile is not read,
  the config directory stays empty, and the token never reaches any file (the request log records
  it redacted);
* two clients with different tokens share no state (headers, session, profile);
* a 401 or an authorizer deny is reported at once, without a refresh or a resend;
* a token that cannot be sent as a header value as is (blank, whitespace, a "Bearer " prefix, a
  control character) is refused without echoing it;
* a client built the existing way (a profile manager, no token) is unchanged.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from vamscli.utils import api_client as api_client_module
from vamscli.utils.api_client import (
    APIClient,
    _INJECTED_TOKEN_DENIED_MESSAGE,
    _INJECTED_TOKEN_REJECTED_MESSAGE,
)
from vamscli.utils.exceptions import AuthenticationError, PermissionDeniedError

BASE = "https://abc123.execute-api.us-west-2.amazonaws.com/api"
TOKEN_A = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyLWEifQ.c2lnbmF0dXJlLWE"
TOKEN_B = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyLWIifQ.c2lnbmF0dXJlLWI"
AUTHORIZER_DENY = {"Message": "User is not authorized to access this resource with an explicit deny"}


def _response(body=None, status=200):
    response = requests.Response()
    response.status_code = status
    response.reason = {200: "OK", 401: "Unauthorized", 403: "Forbidden"}.get(status, "")
    response.url = BASE + "/database"
    response._content = b"" if body is None else json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


class _Wire:
    """The client's session, recorded. The last response repeats."""

    def __init__(self, monkeypatch, client, *responses):
        self.sends = []
        self._responses = list(responses)

        def request(method, url, headers=None, **kwargs):
            self.sends.append({"method": method, "url": url, "headers": dict(headers or {})})
            return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

        monkeypatch.setattr(client.session, "request", request)


@pytest.fixture
def no_profile_access(monkeypatch, tmp_path):
    """Point the config directory at an empty temp dir and fail on any profile access."""
    config_dir = tmp_path / "vamscli-config"
    monkeypatch.setattr("vamscli.constants.get_config_dir", lambda: config_dir)
    monkeypatch.setattr("vamscli.utils.profile.get_config_dir", lambda: config_dir)

    def _forbidden(*args, **kwargs):
        raise AssertionError("an injected-token client touched a profile")

    monkeypatch.setattr(api_client_module, "ProfileManager", _forbidden)
    monkeypatch.setattr(api_client_module, "read_active_profile_name", _forbidden)
    return config_dir


def _files_under(*roots):
    for root in roots:
        root = Path(root)
        if root.exists():
            yield from (p for p in root.rglob("*") if p.is_file())


class TestTheTokenIsSent:
    def test_an_authenticated_request_carries_the_injected_token(self, monkeypatch, no_profile_access):
        client = APIClient(BASE, bearer_token=TOKEN_A)
        wire = _Wire(monkeypatch, client, _response({"ok": True}))

        client.get("/database", include_auth=True)

        assert wire.sends[0]["headers"]["Authorization"] == f"Bearer {TOKEN_A}"
        assert client.uses_injected_token is True
        assert client.profile_manager is None

    def test_an_unauthenticated_request_carries_no_token(self, monkeypatch, no_profile_access):
        client = APIClient(BASE, bearer_token=TOKEN_A)
        wire = _Wire(monkeypatch, client, _response({"ok": True}))

        client.get("/api/version", include_auth=False)

        assert "Authorization" not in wire.sends[0]["headers"]

    def test_a_given_profile_manager_is_not_asked_for_a_token(self, monkeypatch):
        profile_manager = MagicMock()
        client = APIClient(BASE, profile_manager=profile_manager, bearer_token=TOKEN_A)
        wire = _Wire(monkeypatch, client, _response({"ok": True}))

        client.get("/database", include_auth=True)

        assert wire.sends[0]["headers"]["Authorization"] == f"Bearer {TOKEN_A}"
        profile_manager.load_auth_profile.assert_not_called()
        profile_manager.is_override_token.assert_not_called()
        profile_manager.save_auth_profile.assert_not_called()


class TestNothingIsPersisted:
    @pytest.mark.no_mock_logging
    @pytest.mark.parametrize("status, body", [
        (200, {"ok": True}),
        (401, {"message": "Unauthorized"}),
        (403, AUTHORIZER_DENY),
    ], ids=["success", "rejected", "denied"])
    def test_the_token_reaches_no_file(self, monkeypatch, no_profile_access, redirect_log_dir, status, body):
        client = APIClient(BASE, bearer_token=TOKEN_A)
        _Wire(monkeypatch, client, _response(body, status=status))

        try:
            client.get("/database", include_auth=True)
        except (AuthenticationError, PermissionDeniedError):
            pass

        assert not no_profile_access.exists() or not any(_files_under(no_profile_access))
        logged = [p.read_text(errors="ignore") for p in _files_under(redirect_log_dir)]
        # The request log is written (with the Authorization header redacted), so the check below
        # reads a real request record rather than an empty directory.
        assert any("API Request: GET" in text for text in logged)
        for path in _files_under(no_profile_access, redirect_log_dir):
            assert TOKEN_A not in path.read_text(errors="ignore"), path

    def test_a_rejected_login_profile_call_deletes_nothing(self, monkeypatch):
        profile_manager = MagicMock()
        client = APIClient(BASE, profile_manager=profile_manager, bearer_token=TOKEN_A)
        _Wire(monkeypatch, client, _response({"message": "Unauthorized"}, status=401))

        with pytest.raises(AuthenticationError):
            client.call_login_profile("user-a")

        profile_manager.delete_auth_profile.assert_not_called()


class TestClientsAreIndependent:
    def test_two_clients_send_their_own_tokens(self, monkeypatch, no_profile_access):
        client_a = APIClient(BASE, bearer_token=TOKEN_A)
        client_b = APIClient(BASE, bearer_token=TOKEN_B)
        wire_a = _Wire(monkeypatch, client_a, _response({"ok": True}))
        wire_b = _Wire(monkeypatch, client_b, _response({"ok": True}))

        client_a.get("/database")
        client_b.get("/database")
        client_a.get("/database")

        assert [s["headers"]["Authorization"] for s in wire_a.sends] == [f"Bearer {TOKEN_A}"] * 2
        assert [s["headers"]["Authorization"] for s in wire_b.sends] == [f"Bearer {TOKEN_B}"]
        assert client_a.session is not client_b.session
        assert client_a.session.headers.get("Authorization") is None
        assert client_b.session.headers.get("Authorization") is None


class TestRejectionIsReportedAtOnce:
    def test_a_401_is_not_refreshed_or_resent(self, monkeypatch, no_profile_access):
        client = APIClient(BASE, bearer_token=TOKEN_A)
        wire = _Wire(monkeypatch, client, _response({"message": "Unauthorized"}, status=401))
        refresh = MagicMock(return_value=True)
        monkeypatch.setattr(client, "_do_refresh_token", refresh)

        with pytest.raises(AuthenticationError) as excinfo:
            client.get("/database")

        assert str(excinfo.value) == _INJECTED_TOKEN_REJECTED_MESSAGE
        assert "vamscli auth login" not in str(excinfo.value)
        assert len(wire.sends) == 1
        refresh.assert_not_called()

    def test_an_authorizer_deny_is_not_refreshed_or_resent(self, monkeypatch, no_profile_access):
        client = APIClient(BASE, bearer_token=TOKEN_A)
        wire = _Wire(monkeypatch, client, _response(AUTHORIZER_DENY, status=403))
        refresh = MagicMock(return_value=True)
        monkeypatch.setattr(client, "_do_refresh_token", refresh)

        with pytest.raises(PermissionDeniedError) as excinfo:
            client.get("/database")

        assert str(excinfo.value) == _INJECTED_TOKEN_DENIED_MESSAGE
        assert len(wire.sends) == 1
        refresh.assert_not_called()

    def test_refresh_is_refused(self, no_profile_access):
        client = APIClient(BASE, bearer_token=TOKEN_A)
        assert client._try_refresh_token(token_before=TOKEN_A) is False

    def test_a_handler_denial_is_still_a_permission_denial(self, monkeypatch, no_profile_access):
        client = APIClient(BASE, bearer_token=TOKEN_A)
        _Wire(monkeypatch, client, _response({"message": "Not Authorized"}, status=403))

        with pytest.raises(PermissionDeniedError):
            client.get("/database")


class TestTokenShape:
    @pytest.mark.parametrize("token", ["", "   ", f"Bearer {TOKEN_A}", TOKEN_A + "\n", TOKEN_A + "\r\nX: y",
                                       "abc\x00def", 123],
                             ids=["empty", "blank", "bearer-prefix", "newline", "header-injection",
                                  "control", "not-a-string"])
    def test_an_unsendable_token_is_refused_without_echo(self, token, no_profile_access):
        with pytest.raises(ValueError) as excinfo:
            APIClient(BASE, bearer_token=token)
        if isinstance(token, str) and token.strip():
            assert TOKEN_A not in str(excinfo.value)

    def test_an_api_key_is_accepted(self, monkeypatch, no_profile_access):
        client = APIClient(BASE, bearer_token="vams_abcdef0123456789")
        wire = _Wire(monkeypatch, client, _response({"ok": True}))
        client.get("/database")
        assert wire.sends[0]["headers"]["Authorization"] == "Bearer vams_abcdef0123456789"


class TestTheProfileClientIsUnchanged:
    def test_without_a_token_the_profile_token_is_sent(self, monkeypatch):
        profile_manager = MagicMock()
        profile_manager.is_override_token.return_value = False
        profile_manager.load_auth_profile.return_value = {"access_token": "profile-token"}
        client = APIClient(BASE, profile_manager=profile_manager)
        wire = _Wire(monkeypatch, client, _response({"ok": True}))

        client.get("/database")

        assert client.uses_injected_token is False
        assert client.profile_manager is profile_manager
        assert wire.sends[0]["headers"]["Authorization"] == "Bearer profile-token"
