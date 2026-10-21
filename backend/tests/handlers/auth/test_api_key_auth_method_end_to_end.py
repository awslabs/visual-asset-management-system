# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every API key, whatever it is named, reaches the handlers as an API-key caller.

The REST authorizer's API-key path marks the principal with ``vams:authMethod = apiKey`` and the
key's id in ``vams:apiKeyId`` without looking at the key's name, API Gateway hands the authorizer's
context to the handler as a flat string map, and ``request_to_claims`` reads both back. These tests
walk an ordinary automation key and a temporary session key through the real REST authorizer handler
and the real claims reader, and a JWT caller as the control, so the two ends cannot drift apart for
one kind of key.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest
from backend.backend.handlers.auth import request_to_claims

METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/api/GET/database"
ORDINARY_KEY_ID = "22222222-2222-4222-8222-222222222222"
SESSION_KEY_ID = "11111111-1111-4111-8111-111111111111"


def _record(api_key_id, name):
    return {"apiKeyId": api_key_id, "apiKeyName": name, "isActive": "true", "userId": "automation-user",
            "expiresAt": "2999-12-31T23:59:59Z"}


def _authorize(authorization, record=None, jwt_claims=None):
    """The REST authorizer's answer to one request, with the key table and the user roles doubled.

    The doubles go into the globals the authorizer functions actually run in: the core module can be
    loaded under more than one name, and only the copy that defined ``authenticate_request`` is read."""
    core_globals = rest.authenticate_request.__globals__
    event = {"methodArn": METHOD_ARN, "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
             "headers": {"Authorization": authorization}, "path": "/database", "httpMethod": "GET"}
    doubles = {
        "_lookup_api_key_by_hash": MagicMock(return_value=record),
        "_get_user_roles_table": MagicMock(return_value=MagicMock()),
        "_lookup_user_roles": MagicMock(return_value=["automation"]),
        "AUTH_MODE": "cognito",
        "verify_cognito_jwt": MagicMock(return_value=jwt_claims),
        "resolve_mfa_enabled": MagicMock(return_value=False),
    }
    assert all(name in core_globals for name in doubles), "the authorizer core no longer defines a doubled name"
    with patch.dict(core_globals, doubles), \
            patch.dict(rest.lambda_handler.__globals__, {"log_authorization_gateway": MagicMock()}):
        return rest.lambda_handler(event, None)


def _handler_claims(policy):
    """What a handler's request_to_claims returns for a request the policy allowed: API Gateway places
    the authorizer's context, with the principal id, directly under requestContext.authorizer."""
    authorizer = dict(policy["context"], principalId=policy["principalId"])
    return request_to_claims({"requestContext": {"authorizer": authorizer}})


@pytest.mark.unit
class TestEveryApiKeyIsAnApiKeyCaller:
    def test_an_ordinary_key_is_an_api_key_caller_with_its_key_id(self):
        policy = _authorize("vams_ordinary", record=_record(ORDINARY_KEY_ID, "ci-deploy"))
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        claims = _handler_claims(policy)
        assert claims["authMethod"] == "apiKey"
        assert claims["apiKeyId"] == ORDINARY_KEY_ID
        assert claims["tokens"] == ["automation-user"]
        assert claims["roles"] == ["automation"]

    def test_a_bearer_prefixed_ordinary_key_is_read_the_same_way(self):
        policy = _authorize("Bearer vams_ordinary", record=_record(ORDINARY_KEY_ID, "ci-deploy"))
        claims = _handler_claims(policy)
        assert (claims["authMethod"], claims["apiKeyId"]) == ("apiKey", ORDINARY_KEY_ID)

    def test_a_session_key_is_marked_the_same_way_as_an_ordinary_one(self):
        # The authorizer does not tell keys apart by name; a handler that must, reads the record.
        policy = _authorize("vams_session", record=_record(SESSION_KEY_ID, "temporary-session-probe-3f2a9c1e"))
        claims = _handler_claims(policy)
        assert (claims["authMethod"], claims["apiKeyId"]) == ("apiKey", SESSION_KEY_ID)

    def test_a_jwt_caller_is_not_an_api_key_caller(self):
        # Control: the same path for a signed-in user carries neither marker.
        policy = _authorize("Bearer header.payload.signature",
                            jwt_claims={"sub": "alice", "cognito:username": "alice"})
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        claims = _handler_claims(policy)
        assert claims["authMethod"] == "jwt"
        assert claims["apiKeyId"] is None
        assert claims["tokens"] == ["alice"]

    def test_a_disabled_ordinary_key_is_denied_before_any_handler(self):
        record = dict(_record(ORDINARY_KEY_ID, "ci-deploy"), isActive="false")
        policy = _authorize("vams_ordinary", record=record)
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Deny"
        assert "context" not in policy
