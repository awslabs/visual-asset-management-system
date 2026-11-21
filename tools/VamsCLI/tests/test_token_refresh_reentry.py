"""A token refresh never waits on itself.

A refresh with no refresh token falls back to the saved username and password, then reads the login
profile and the feature switches while it still holds the module's refresh lock. When the API refuses
those reads too (an authorizer deny on every call, such as a deployment that limits API access by
source IP), each read asks for a refresh of its own. The lock is not reentrant, so the CLI must answer
those nested requests without a refresh instead of waiting on the lock its own thread holds. The probe
runs in a thread with a deadline, so a regression fails the test instead of hanging the suite.
"""

import json
import threading
from unittest.mock import MagicMock

import pytest
import requests

from vamscli.utils import api_client as api_client_module
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import AuthenticationError, PermissionDeniedError

BASE = "https://abc123.execute-api.us-west-2.amazonaws.com/api"
AUTHORIZER_DENY = {
    "Message": "User is not authorized to access this resource with an explicit deny"
}
DEADLINE_SECONDS = 20


def _response(body, status):
    response = requests.Response()
    response.status_code = status
    response.url = BASE
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def _client_with_saved_credentials(monkeypatch):
    profile_manager = MagicMock()
    profile_manager.profile_name = "default"
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    # No refresh token, so the refresh falls back to the saved credentials.
    profile_manager.load_auth_profile.return_value = {"access_token": "token-before"}
    profile_manager.load_credentials.return_value = {"username": "alice", "password": "secret"}
    profile_manager.load_config.return_value = {
        "amplify_config": {
            "region": "us-east-1",
            "cognitoUserPoolId": "us-east-1_pool",
            "cognitoAppClientId": "client",
        }
    }
    authenticator = MagicMock()
    authenticator.authenticate.return_value = {"access_token": "token-after"}
    monkeypatch.setattr(
        "vamscli.auth.cognito.CognitoAuthenticator", lambda *args, **kwargs: authenticator
    )
    return APIClient(BASE, profile_manager=profile_manager), authenticator


def _run_with_deadline(call):
    outcome = {}

    def run():
        try:
            outcome["result"] = call()
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=DEADLINE_SECONDS)
    assert not thread.is_alive(), "the token refresh waited on the lock its own thread holds"
    return outcome


@pytest.fixture
def fresh_refresh_lock(monkeypatch):
    """A lock of the test's own, so a thread a regression leaves waiting holds nothing other tests use."""
    monkeypatch.setattr(api_client_module, "_TOKEN_REFRESH_LOCK", threading.Lock())


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (403, AUTHORIZER_DENY, PermissionDeniedError),
        (401, {"message": "Unauthorized"}, AuthenticationError),
    ],
)
def test_a_re_authentication_whose_reads_are_refused_ends(
    monkeypatch, fresh_refresh_lock, status, body, expected
):
    client, authenticator = _client_with_saved_credentials(monkeypatch)
    sent = []

    def request(method, url, **kwargs):
        sent.append(url)
        return _response(body, status)

    monkeypatch.setattr(client.session, "request", request)

    outcome = _run_with_deadline(lambda: client.get("/database", include_auth=True))

    assert isinstance(outcome.get("error"), expected), outcome
    assert authenticator.authenticate.called
    # The re-authentication's own reads were sent and answered, not left waiting.
    assert any(url.endswith("/secure-config") for url in sent)
    assert any("/auth/loginProfile/" in url for url in sent)


def test_a_re_authentication_whose_reads_succeed_still_reads_them(monkeypatch, fresh_refresh_lock):
    """Control: a nested read the API accepts is not refused for being nested."""
    client, _ = _client_with_saved_credentials(monkeypatch)
    sent = []

    def request(method, url, **kwargs):
        sent.append(url)
        if url.endswith("/database") and len([u for u in sent if u.endswith("/database")]) == 1:
            return _response(AUTHORIZER_DENY, 403)
        if url.endswith("/secure-config"):
            return _response({"featuresEnabled": "AUTHPROVIDER_COGNITO"}, 200)
        return _response({"Items": []}, 200)

    monkeypatch.setattr(client.session, "request", request)

    outcome = _run_with_deadline(lambda: client.get("/database", include_auth=True))

    assert "error" not in outcome, outcome
    assert outcome["result"].json() == {"Items": []}
    client.profile_manager.save_feature_switches.assert_called_once_with(
        {"featuresEnabled": "AUTHPROVIDER_COGNITO"}
    )


def test_the_refresh_flag_is_cleared_when_the_refresh_ends(monkeypatch, fresh_refresh_lock):
    client, _ = _client_with_saved_credentials(monkeypatch)
    monkeypatch.setattr(client, "_do_refresh_token", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        client._try_refresh_token()
    assert getattr(api_client_module._REFRESH_IN_PROGRESS, "active", False) is False
