# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The REST authorizer hands the accepted token audience to the audit record.

A token issued to an additional app client (for example one registered for a remote MCP server)
must be distinguishable from a web app call in the authorization audit record. The authorizer
passes the audience authenticate_request reported, and nothing for a denial or an API key.
"""
import pytest
from unittest.mock import patch

from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest

METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/database/db1"


def _evt():
    return {
        "type": "REQUEST",
        "methodArn": METHOD_ARN,
        "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
        "headers": {"Authorization": "Bearer a.b.c"},
        "path": "/database/db1", "httpMethod": "GET",
    }


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, event, authorized, reason=None, token_audience=None):
        self.calls.append({"authorized": authorized, "reason": reason, "tokenAudience": token_audience})


@pytest.mark.unit
class TestTokenAudienceAudit:
    @pytest.mark.parametrize("result, expected", [
        ({"authorized": True, "context": {"sub": "u1"}, "reason": None,
          "tokenAudience": "mcpclient0001"}, "mcpclient0001"),
        ({"authorized": True, "context": {"sub": "u1"}, "reason": None}, None),
        ({"authorized": False, "context": None, "reason": "Token verification failed",
          "failure": rest.FAILURE_UNAUTHENTICATED}, None),
    ], ids=["additional-client", "api-key-or-no-audience", "denied"])
    def test_the_reported_audience_reaches_the_audit_record(self, result, expected):
        recorder = _Recorder()
        with patch.object(rest, "authenticate_request", return_value=result), \
             patch.object(rest, "log_authorization_gateway", recorder):
            try:
                rest.lambda_handler(_evt(), None)
            except Exception as e:  # the unauthenticated case raises "Unauthorized" for a 401
                assert str(e) == "Unauthorized"
        assert recorder.calls == [{"authorized": result["authorized"], "reason": result["reason"],
                                   "tokenAudience": expected}]

    def test_the_audience_is_not_added_to_the_policy_context(self):
        result = {"authorized": True, "context": {"sub": "u1", "aud": "mcpclient0001"},
                  "reason": None, "tokenAudience": "mcpclient0001"}
        with patch.object(rest, "authenticate_request", return_value=result), \
             patch.object(rest, "log_authorization_gateway", _Recorder()):
            out = rest.lambda_handler(_evt(), None)
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert "tokenAudience" not in out["context"]
