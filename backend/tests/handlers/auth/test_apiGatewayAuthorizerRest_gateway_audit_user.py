# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The authorizer's audit record names the user of an allowed request (issue #1437).

``log_authorization_gateway`` used to read the user from ``event["context"]``, a key an authorizer
event never carries, so every allowed record read ``[user: unknown]``. The authorizer now hands the
context it built from the verified token or API key to the writer. These tests drive
``lambda_handler`` with authentication stubbed and read the message the real writer formats.
"""
import json
import os
from unittest.mock import patch

import pytest

# The resolver reads an environment override before any SSM lookup, which keeps the writer offline.
os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from backend.backend.customLogging import auditLogging  # noqa: E402
from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest  # noqa: E402

METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/database/db1"
RAW_TOKEN = "test-header.test-payload.test-signature"


def _event():
    return {
        "methodArn": METHOD_ARN,
        "headers": {"Authorization": f"Bearer {RAW_TOKEN}"},
        "requestContext": {"identity": {"sourceIp": "198.51.100.9"}},
    }


def _allowed_context(user="alice", roles=("viewer",), mfa="true"):
    return {
        "sub": "11111111-2222-3333-4444-555555555555",
        "vams:tokens": json.dumps([user]),
        "vams:roles": json.dumps(list(roles)),
        "vams:mfaEnabled": mfa,
    }


def _run(auth_result):
    """Run the handler; return (policy, the formatted audit messages the writer produced)."""
    messages = []

    def capture(log_group_name, message, event):
        messages.append(message)

    # The authorizer module binds the test-suite mock of customLogging; run the real writer here.
    with patch.object(rest, "authenticate_request", return_value=auth_result), patch.object(
        rest, "log_authorization_gateway", auditLogging.log_authorization_gateway
    ), patch.object(
        auditLogging, "_audit_log_group", return_value="test-auditAuthorization"
    ), patch.object(auditLogging, "_write_to_cloudwatch", side_effect=capture):
        policy = rest.lambda_handler(_event(), None)
    return policy, messages


@pytest.mark.unit
class TestGatewayAuditNamesTheUser:
    def test_allowed_record_names_user_roles_and_mfa(self):
        policy, messages = _run({"authorized": True, "context": _allowed_context(), "reason": None})
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert len(messages) == 1
        message = messages[0]
        assert "[AUTHORIZATION][authorized: True]" in message
        assert "[user: alice]" in message
        assert '[roles: ["viewer"]]' in message
        assert "[mfaEnabled: True]" in message
        assert "[user: unknown]" not in message

    def test_user_is_the_one_the_authorizer_set_in_vams_tokens(self):
        # The claims fix makes vams:tokens the username; a differing sub must not name the user.
        _, messages = _run(
            {"authorized": True, "context": _allowed_context(user="bob"), "reason": None}
        )
        assert "[user: bob]" in messages[0]
        assert "11111111-2222-3333-4444-555555555555" not in messages[0].split("{")[0]

    def test_api_key_context_names_the_key_owner(self):
        context = {"vams:tokens": json.dumps(["svc-user"]), "vams:roles": json.dumps(["r1", "r2"])}
        _, messages = _run({"authorized": True, "context": context, "reason": None})
        assert "[user: svc-user]" in messages[0]
        assert '[roles: ["r1", "r2"]]' in messages[0]
        assert "[mfaEnabled: False]" in messages[0]

    def test_denied_record_keeps_unknown_even_if_a_context_is_present(self):
        _, messages = _run(
            {"authorized": False, "context": _allowed_context(), "reason": "Token verification failed"}
        )
        assert "[AUTHORIZATION][authorized: False]" in messages[0]
        assert "[user: unknown]" in messages[0]
        assert "alice" not in messages[0].split("{")[0]
        assert "[roles: []]" in messages[0]

    def test_ignored_path_allow_has_no_identity(self):
        _, messages = _run({"authorized": True, "context": None, "reason": None, "ignoredPath": True})
        assert "[authorized: True]" in messages[0]
        assert "[user: unknown]" in messages[0]

    def test_no_token_content_reaches_the_record(self):
        _, messages = _run({"authorized": True, "context": _allowed_context(), "reason": None})
        assert RAW_TOKEN not in messages[0]
        assert "Bearer" not in messages[0]
