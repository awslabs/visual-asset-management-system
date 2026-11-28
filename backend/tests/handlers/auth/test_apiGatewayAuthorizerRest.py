# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import fnmatch

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
        # A single trailing "*" after the stage matches METHOD + the full (multi-segment)
        # resource path, so an authorizer policy cached on the Authorization header applies to
        # every path on the API+stage. A "*/*" form would only match single-segment paths.
        assert rest._wildcard_resource(METHOD_ARN) == \
            "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/*"

    def test_wildcard_resource_matches_regardless_of_path_depth(self):
        # Every method/path on the same api+stage collapses to one cacheable resource.
        deep_arn = ("arn:aws:execute-api:us-east-1:123456789012:abc123/prod/POST/"
                    "database/db1/assets/a1/uploads/u1/complete")
        assert rest._wildcard_resource(deep_arn) == rest._wildcard_resource(METHOD_ARN)

    def test_allow_returns_allow_policy_with_context(self):
        with patch.object(rest, "authenticate_request",
                          return_value={"authorized": True, "context": {"sub": "u1"}, "reason": None}):
            out = rest.lambda_handler(_evt(), None)
        stmt = out["policyDocument"]["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Resource"].endswith("/prod/*")
        assert out["context"]["sub"] == "u1"
        assert out["principalId"]

    def test_deny_returns_deny_policy(self):
        with patch.object(rest, "authenticate_request",
                          return_value={"authorized": False, "context": None, "reason": "IP"}):
            out = rest.lambda_handler(_evt(source_ip="8.8.8.8"), None)
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@pytest.mark.unit
class TestCustomDomainIgnoredPathPolicy:
    """Through the REST API custom domain (base path ``api`` mapped to the ``api`` stage) the
    anonymous routes are at https://{domain}/api/api/version. The event's ``path`` then keeps
    the base path, but the methodArn API Gateway generates names the stage and the
    stage-relative route, as through execute-api, so the scoped ignored-path Allow still
    matches it and needs no change (issue #714)."""

    STAGE_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/api"
    IGNORED = ["/api/amplify-config", "/api/version"]
    IGNORED_RESULT = {"authorized": True, "context": None, "reason": None, "ignoredPath": True}

    @pytest.mark.parametrize("route", IGNORED)
    def test_allow_resource_matches_the_custom_domain_method_arn(self, route):
        evt = {
            "type": "REQUEST",
            "methodArn": f"{self.STAGE_ARN}/GET{route}",
            "resource": route,
            "path": "/api" + route,
            "httpMethod": "GET",
            "headers": {"Host": "api.vams.example.com"},
            "requestContext": {
                "stage": "api",
                "domainName": "api.vams.example.com",
                "identity": {"sourceIp": "203.0.113.7"},
            },
        }
        with patch.object(rest, "IGNORED_PATHS", self.IGNORED), \
             patch.object(rest, "authenticate_request", return_value=self.IGNORED_RESULT), \
             patch.object(rest, "log_authorization_gateway"):
            out = rest.lambda_handler(evt, None)
        stmt = out["policyDocument"]["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Resource"] == [f"{self.STAGE_ARN}/*/api/amplify-config",
                                    f"{self.STAGE_ARN}/*/api/version"]
        # IAM "*" matches any run of characters, which fnmatchcase reproduces here.
        assert any(fnmatch.fnmatchcase(evt["methodArn"], r) for r in stmt["Resource"])
        assert "context" not in out
