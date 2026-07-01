# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest
from unittest.mock import patch
from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest

METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/database/db1"


def _evt(source_ip="203.0.113.7", auth="Bearer good"):
    return {
        "methodArn": METHOD_ARN,
        "requestContext": {"identity": {"sourceIp": source_ip}},
        "headers": {"Authorization": auth} if auth else {},
        "path": "/database/db1", "httpMethod": "GET",
    }


@pytest.mark.unit
class TestRestAuthorizer:
    def test_wildcard_resource(self):
        assert rest._wildcard_resource(METHOD_ARN) == \
            "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/*/*"

    def test_allow_returns_allow_policy_with_context(self):
        with patch.object(rest, "authenticate_request",
                          return_value={"authorized": True, "context": {"sub": "u1"}, "reason": None}):
            out = rest.lambda_handler(_evt(), None)
        stmt = out["policyDocument"]["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Resource"].endswith("/prod/*/*")
        assert out["context"]["sub"] == "u1"
        assert out["principalId"]

    def test_deny_returns_deny_policy(self):
        with patch.object(rest, "authenticate_request",
                          return_value={"authorized": False, "context": None, "reason": "IP"}):
            out = rest.lambda_handler(_evt(source_ip="8.8.8.8"), None)
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Deny"
