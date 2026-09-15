# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Cognito user-management methods read the API's real refusal for a disabled Cognito: a 400.

The handler refuses every `/user/cognito*` call with `VAMSGeneralErrorResponse("Cognito user management
is not available")` when the deployment has Cognito switched off -- an ordinary 400, the same status a
malformed request gets. The CLI methods used to map a 503 to "Cognito not enabled" (a status the handler
never returned) and every 400 to "Invalid ... parameters", and their `except HTTPError` blocks were dead
anyway because the request layer wrapped the error first. These arms drive the real request layer (only
`session.request` is faked) so the mapping that reaches a user is the one asserted.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from vamscli.utils.api_client import APIClient, _is_cognito_unavailable
from vamscli.utils.exceptions import (
    APIError,
    CognitoUserAlreadyExistsError,
    CognitoUserNotFoundError,
    CognitoUserOperationError,
    InvalidCognitoUserDataError,
)

DISABLED = "VAMS General Error: Cognito user management is not available"
MISCONFIGURED = "VAMS General Error: Cognito configuration error"


def _client():
    pm = MagicMock()
    pm.is_override_token.return_value = False
    pm.load_auth_profile.return_value = {}
    pm.is_token_expired.return_value = False
    return APIClient("https://x.execute-api.us-east-1.amazonaws.com/api", profile_manager=pm)


def _response(status_code, body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = json.dumps(body).encode()
    resp.headers = {}
    resp.json.return_value = body
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    return resp


CALLS = {
    "list": lambda c: c.list_cognito_users(),
    "create": lambda c: c.create_cognito_user({"email": "a@example.com"}),
    "update": lambda c: c.update_cognito_user("u1", {"email": "b@example.com"}),
    "delete": lambda c: c.delete_cognito_user("u1"),
    "reset": lambda c: c.reset_cognito_user_password("u1", confirm_reset=True),
}


class TestTheDisabledFeatureMessageIsToldApartFromBadInput:
    @pytest.mark.parametrize("op", sorted(CALLS))
    @pytest.mark.parametrize("message", [DISABLED, MISCONFIGURED])
    def test_a_400_saying_cognito_is_off_is_reported_as_not_enabled(self, op, message):
        client = _client()
        with patch.object(client.session, "request", return_value=_response(400, {"message": message})):
            with pytest.raises(CognitoUserOperationError) as excinfo:
                CALLS[op](client)
        assert str(excinfo.value).startswith("Cognito not enabled:"), str(excinfo.value)
        assert message in str(excinfo.value)

    @pytest.mark.parametrize(
        "op,expected_type,expected_prefix",
        [
            ("list", CognitoUserOperationError, "Invalid list parameters:"),
            ("create", InvalidCognitoUserDataError, "Invalid user data:"),
            ("update", InvalidCognitoUserDataError, "Invalid update data:"),
            ("delete", CognitoUserOperationError, "Invalid delete request:"),
            ("reset", InvalidCognitoUserDataError, "Invalid reset request:"),
        ],
    )
    def test_any_other_400_keeps_its_input_error_mapping(self, op, expected_type, expected_prefix):
        # CONTROL for the arm above: the guard must not swallow ordinary validation failures.
        client = _client()
        with patch.object(client.session, "request", return_value=_response(400, {"message": "email is required"})):
            with pytest.raises(expected_type) as excinfo:
                CALLS[op](client)
        assert str(excinfo.value).startswith(expected_prefix), str(excinfo.value)
        assert "Cognito not enabled" not in str(excinfo.value)

    def test_a_duplicate_user_400_is_still_the_already_exists_error(self):
        client = _client()
        with patch.object(client.session, "request",
                          return_value=_response(400, {"message": "User already exists"})):
            with pytest.raises(CognitoUserAlreadyExistsError):
                CALLS["create"](client)

    @pytest.mark.parametrize("op", ["update", "delete", "reset"])
    def test_the_handlers_400_user_not_found_is_the_not_found_error(self, op):
        """What the handler actually sends for a missing user is a 400 carrying "User not found" (it
        wraps Cognito's UserNotFoundException in VAMSGeneralErrorResponse), so the not-found mapping
        has to key on the message; the 404 arm below is the spec's shape and stays for completeness."""
        client = _client()
        with patch.object(client.session, "request",
                          return_value=_response(400, {"message": "VAMS General Error: User not found"})):
            with pytest.raises(CognitoUserNotFoundError) as excinfo:
                CALLS[op](client)
        assert "u1" in str(excinfo.value)

    @pytest.mark.parametrize("op", ["update", "delete", "reset"])
    def test_a_404_is_the_not_found_error(self, op):
        # The handlers are live now (raise_http_errors), so the 404 arm is reachable too.
        client = _client()
        with patch.object(client.session, "request", return_value=_response(404, {"message": "User not found"})):
            with pytest.raises(CognitoUserNotFoundError):
                CALLS[op](client)

    @pytest.mark.parametrize("op", sorted(CALLS))
    def test_a_503_is_an_ordinary_server_failure_not_a_disabled_feature(self, op):
        """The status the handler never returns must not be read as 'Cognito not enabled' any more: a
        503 is API Gateway or Lambda trouble, and telling the user to enable Cognito would be wrong."""
        client = _client()
        with patch.object(client.session, "request", return_value=_response(503, {"message": "Service Unavailable"})):
            with pytest.raises(APIError) as excinfo:
                CALLS[op](client)
        assert not isinstance(excinfo.value, CognitoUserOperationError)
        assert "Cognito not enabled" not in str(excinfo.value)


class TestTheMessagePredicate:
    @pytest.mark.parametrize("message", [DISABLED, MISCONFIGURED, DISABLED.upper(), "  cognito user management is not available  "])
    def test_both_disabled_messages_match_case_insensitively(self, message):
        assert _is_cognito_unavailable(message) is True

    @pytest.mark.parametrize("message", ["email is required", "User already exists", "", None])
    def test_other_messages_do_not(self, message):
        assert _is_cognito_unavailable(message) is False


def test_the_cognito_methods_map_no_503_and_all_opt_into_their_own_handlers():
    """Durable guard (root CLAUDE.md Rule 13): both constructs are one-line edits a later change could
    reintroduce -- a 503 branch that promises a status the handler does not send, or a Cognito call that
    drops `raise_http_errors=True` and silently turns its whole `except HTTPError` block back into dead
    code."""
    import inspect
    import vamscli.utils.api_client as mod

    src = inspect.getsource(mod)
    start = src.index("    def list_cognito_users(")
    after_reset = src.index("    def reset_cognito_user_password(")
    end = src.index("\n    def ", after_reset + 10)
    region = src[start:end]
    assert region.count("def ") == 5, "the Cognito method region did not resolve to the five methods"
    assert "status_code == 503" not in region
    assert region.count("raise_http_errors=True") == 5
    assert region.count("_is_cognito_unavailable(error_message)") == 5
    assert region.count("'user not found' in error_message.lower()") == 3
