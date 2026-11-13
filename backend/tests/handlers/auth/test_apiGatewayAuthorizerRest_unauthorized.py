# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""401 versus 403 from the REST authorizer (issue #1212).

API Gateway answers a Lambda authorizer that raises Exception("Unauthorized") with 401, a
Deny policy with 403, and any other exception with 500. These tests pin:

* an unauthenticated result raises exactly "Unauthorized", and the handler's own catch-all
  does not swallow it back into a Deny;
* the audit record is written, with the internal reason, before the 401 is raised;
* every other denial (forbidden, a result without a failure kind) and any unexpected error
  stay a Deny policy, so the default is still deny;
* the 401 carries nothing from the internal reason.
"""
import pytest
from unittest.mock import patch

from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest

METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/database/db1"

UNAUTH = rest.FAILURE_UNAUTHENTICATED


def _evt(auth="Bearer expired.jwt.token"):
    return {
        "type": "REQUEST",
        "methodArn": METHOD_ARN,
        "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
        "headers": {"Authorization": auth} if auth else {},
        "path": "/database/db1", "httpMethod": "GET",
    }


class _Recorder:
    def __init__(self, order=None):
        self.calls = []
        self.order = order

    def __call__(self, event, authorized, reason=None, token_audience=None):
        self.calls.append((authorized, reason))
        if self.order is not None:
            self.order.append("audit")


def _run(result, recorder=None):
    recorder = recorder or _Recorder()
    with patch.object(rest, "authenticate_request", return_value=result), \
         patch.object(rest, "log_authorization_gateway", recorder):
        return rest.lambda_handler(_evt(), None), recorder


@pytest.mark.unit
class TestUnauthenticatedRaises:
    RESULT = {"authorized": False, "context": None,
              "reason": "Token verification failed", "failure": UNAUTH}

    def test_raises_exactly_unauthorized(self):
        with pytest.raises(Exception) as exc:
            _run(self.RESULT)
        # API Gateway maps only this exact message to 401; anything else is a 500.
        assert str(exc.value) == "Unauthorized"
        assert type(exc.value) is Exception

    def test_audit_record_is_written_before_the_raise(self):
        order = []
        recorder = _Recorder(order)

        def _authenticate(event):
            order.append("authenticate")
            return self.RESULT

        with patch.object(rest, "authenticate_request", side_effect=_authenticate), \
             patch.object(rest, "log_authorization_gateway", recorder):
            with pytest.raises(Exception, match="^Unauthorized$"):
                rest.lambda_handler(_evt(), None)
        assert order == ["authenticate", "audit"]
        assert recorder.calls == [(False, "Token verification failed")]

    def test_internal_reason_is_not_in_the_raised_message(self):
        result = dict(self.RESULT, reason="API key is disabled")
        with pytest.raises(Exception) as exc:
            _run(result)
        assert "disabled" not in str(exc.value)

    def test_raise_is_not_swallowed_by_the_catch_all(self):
        # If the raise sat inside the try, the outer except would return a Deny policy
        # instead; a returned value here would mean the 401 was lost.
        try:
            out, _ = _run(self.RESULT)
        except Exception as e:
            assert str(e) == "Unauthorized"
        else:
            pytest.fail(f"expected a raise, got a policy: {out}")

    def test_audit_failure_falls_back_to_deny_not_a_500(self):
        # The audit write sits inside the handler's try; an error there must still answer
        # the request, and conservatively (Deny), never with an unmapped exception.
        def _broken(*args, **kwargs):
            raise RuntimeError("log sink down")

        with patch.object(rest, "authenticate_request", return_value=self.RESULT), \
             patch.object(rest, "log_authorization_gateway", _broken):
            out = rest.lambda_handler(_evt(), None)
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@pytest.mark.unit
class TestEverythingElseIsDeny:
    @pytest.mark.parametrize("result", [
        {"authorized": False, "context": None, "reason": "IP address not authorized",
         "failure": "forbidden"},
        {"authorized": False, "context": None, "reason": "No roles", "failure": "forbidden"},
        {"authorized": False, "context": None, "reason": "legacy shape"},     # no kind
        {"authorized": False, "context": None, "reason": "x", "failure": None},
        {"authorized": False, "context": None, "reason": "x", "failure": "something-new"},
    ], ids=["ip", "no-roles", "no-kind", "kind-none", "unknown-kind"])
    def test_non_unauthenticated_denials_return_deny(self, result):
        out, recorder = _run(result)
        stmt = out["policyDocument"]["Statement"][0]
        assert stmt["Effect"] == "Deny"
        assert recorder.calls == [(False, result["reason"])]
        assert "context" not in out

    def test_unexpected_error_returns_deny(self):
        with patch.object(rest, "authenticate_request", side_effect=RuntimeError("boom")):
            out = rest.lambda_handler(_evt(), None)
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Deny"

    def test_allow_is_unchanged(self):
        out, _ = _run({"authorized": True, "context": {"sub": "u1"}, "reason": None})
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert out["context"]["sub"] == "u1"

    def test_ignored_path_allow_is_unchanged(self):
        with patch.object(rest, "IGNORED_PATHS", ["/api/version"]):
            out, _ = _run({"authorized": True, "context": None, "reason": None,
                           "ignoredPath": True})
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert "context" not in out


@pytest.mark.unit
class TestEndToEndThroughTheRealCore:
    """The handler and the real authenticate_request together, for the main cases."""

    def _handle(self, evt, **core_patches):
        core = rest.authenticate_request.__globals__
        recorder = _Recorder()
        patches = [patch.dict(core, core_patches)] if core_patches else []
        for p in patches:
            p.start()
        try:
            with patch.object(rest, "log_authorization_gateway", recorder):
                return rest.lambda_handler(evt, None), recorder
        finally:
            for p in patches:
                p.stop()

    def test_missing_header_is_a_401(self):
        with pytest.raises(Exception, match="^Unauthorized$"):
            self._handle(_evt(auth=None), ALLOWED_IP_RANGES=[], IGNORED_PATHS=[])

    def test_rejected_cognito_token_is_a_401(self):
        with pytest.raises(Exception, match="^Unauthorized$"):
            self._handle(_evt(auth="Bearer not-a-jwt"), ALLOWED_IP_RANGES=[],
                         IGNORED_PATHS=[], AUTH_MODE="cognito", USER_POOL_ID="pool-1",
                         APP_CLIENT_ID="app-client",
                         COGNITO_BASE_URL="https://cognito-idp.test")

    def test_blocked_ip_is_a_deny(self):
        out, recorder = self._handle(
            _evt(auth=None), ALLOWED_IP_RANGES=[["198.51.100.0", "198.51.100.255"]],
            IGNORED_PATHS=[])
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Deny"
        assert recorder.calls == [(False, "IP address not authorized")]

    def test_misconfigured_auth_mode_is_a_deny(self):
        out, _ = self._handle(_evt(), ALLOWED_IP_RANGES=[], IGNORED_PATHS=[], AUTH_MODE="")
        assert out["policyDocument"]["Statement"][0]["Effect"] == "Deny"
