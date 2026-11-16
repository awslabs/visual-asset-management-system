"""
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

call_login_profile must not wrap auth failures from _make_request as APIError.
"""

import pytest
from unittest.mock import MagicMock, patch

from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    APIError,
    AuthenticationError,
    OverrideTokenError,
    PermissionDeniedError,
    TokenExpiredError,
)


def _client():
    pm = MagicMock()
    pm.is_override_token.return_value = True
    pm.load_auth_profile.return_value = {"access_token": "tok", "user_id": "u"}
    return APIClient("https://vams.example.com/api", profile_manager=pm), pm


@pytest.mark.parametrize(
    "raised",
    [
        PermissionDeniedError("Access forbidden"),
        OverrideTokenError("Override token authentication failed"),
        TokenExpiredError("Override token has expired"),
        AuthenticationError("Authentication failed"),
    ],
)
def test_call_login_profile_rethrows_auth_failures_as_authentication_error(raised):
    client, pm = _client()
    with patch.object(client, "get", side_effect=raised):
        with pytest.raises(AuthenticationError) as exc_info:
            client.call_login_profile("user@example.com")
    assert not isinstance(exc_info.value, APIError)
    pm.delete_auth_profile.assert_called_once()


def test_call_login_profile_still_wraps_unexpected_errors_as_api_error():
    client, pm = _client()
    with patch.object(client, "get", side_effect=RuntimeError("boom")):
        with pytest.raises(APIError, match="Failed to call login profile API"):
            client.call_login_profile("user@example.com")
    pm.delete_auth_profile.assert_not_called()
