"""Test doubles for the asset status tests: a recorded session and token refresh for APIClient."""

import json
from unittest.mock import MagicMock

import requests

from vamscli.utils.api_client import APIClient

BASE = "https://abc123.execute-api.us-west-2.amazonaws.com/api"

HANDLER_DENY = {"message": "Not Authorized"}


def _response(body=None, status=403, headers=None, content_type="application/json"):
    """A requests.Response as the session returns it: a dict or list is sent as JSON, a str as is."""
    response = requests.Response()
    response.status_code = status
    response.reason = "Forbidden" if status == 403 else "OK"
    response.url = BASE + "/database"
    if body is None:
        response._content = b""
    elif isinstance(body, str):
        response._content = body.encode("utf-8")
    else:
        response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = content_type
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def _client(override_token=False, token_expired=False):
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = override_token
    profile_manager.is_token_expired.return_value = token_expired
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
