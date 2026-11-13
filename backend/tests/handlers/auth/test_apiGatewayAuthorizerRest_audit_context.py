# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the REST authorizer hands the audit records, and that it leaves the decision alone.

* The authorizer passes the context it built from the verified token (or API key) to the
  authorization audit record, so an allowed record names the verified user; a denial passes none
  that names a user.
* On a CloudFront deployment, an allowed request that carries an accepted origin verification
  value gets the value's digest in the policy context (audit only); any other request does not,
  and the effect, resource and principal are the same either way.
"""
import pytest
from unittest.mock import patch

from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest

METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/database/db1"
VERIFIED = {"sub": "0000-sub", "cognito:username": "alice", "vams:roles": '["admin"]',
            "vams:mfaEnabled": "true"}
DIGEST_KEY = "vams:originVerifyDigest"


def _evt(origin_value=None):
    headers = {"Authorization": "Bearer a.b.c"}
    if origin_value is not None:
        headers["x-vams-origin-verify"] = origin_value
    return {
        "type": "REQUEST",
        "methodArn": METHOD_ARN,
        "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
        "headers": headers,
        "path": "/database/db1", "httpMethod": "GET",
    }


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, event, authorized, failure_reason=None, token_audience=None, verified_context=None):
        self.calls.append({"authorized": authorized, "verified_context": verified_context})


def _run(result, event=None, fronted="none", valid=lambda value: False):
    recorder = _Recorder()
    origin_verify = rest.origin_verify_audit_context.__globals__["originVerify"]
    with patch.object(rest, "authenticate_request", return_value=result), \
            patch.object(rest, "log_authorization_gateway", recorder), \
            patch.object(rest, "API_FRONTED", fronted), \
            patch.object(origin_verify, "is_valid_origin_verify_value", valid):
        return rest.lambda_handler(event or _evt(), None), recorder


@pytest.mark.unit
class TestVerifiedContextReachesTheAuditRecord:
    def test_an_allow_passes_the_verified_context(self):
        _, recorder = _run({"authorized": True, "context": dict(VERIFIED), "reason": None})
        assert recorder.calls == [{"authorized": True, "verified_context": VERIFIED}]

    def test_a_denial_passes_no_context(self):
        _, recorder = _run({"authorized": False, "context": None, "reason": "IP not allowed",
                            "failure": "forbidden"})
        assert recorder.calls == [{"authorized": False, "verified_context": None}]


@pytest.mark.unit
class TestOriginVerifyDigestInTheContext:
    def test_a_cloudfront_request_with_an_accepted_value_carries_the_digest(self):
        policy, _ = _run({"authorized": True, "context": dict(VERIFIED), "reason": None},
                         event=_evt("accepted-value"), fronted="cloudfront",
                         valid=lambda value: value == "accepted-value")
        digest = policy["context"][DIGEST_KEY]
        assert len(digest) == 64 and "accepted-value" not in digest

    @pytest.mark.parametrize("origin_value, fronted", [
        ("forged-value", "cloudfront"), (None, "cloudfront"), ("accepted-value", "none"),
        ("accepted-value", "alb"),
    ])
    def test_any_other_request_carries_none_and_the_policy_is_the_same(self, origin_value, fronted):
        result = {"authorized": True, "context": dict(VERIFIED), "reason": None}
        policy, _ = _run(result, event=_evt(origin_value), fronted=fronted,
                         valid=lambda value: value == "accepted-value" and fronted == "cloudfront")
        assert DIGEST_KEY not in policy["context"]
        with_digest, _ = _run(result, event=_evt("accepted-value"), fronted="cloudfront",
                              valid=lambda value: value == "accepted-value")
        assert policy["policyDocument"] == with_digest["policyDocument"]
        assert policy["principalId"] == with_digest["principalId"] == "0000-sub"
        assert {k: v for k, v in with_digest["context"].items() if k != DIGEST_KEY} == policy["context"]

    def test_a_failing_check_leaves_the_allow_unchanged(self):
        def broken(value):
            raise RuntimeError("secrets manager unavailable")

        policy, _ = _run({"authorized": True, "context": dict(VERIFIED), "reason": None},
                         event=_evt("accepted-value"), fronted="cloudfront", valid=broken)
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert DIGEST_KEY not in policy["context"]
