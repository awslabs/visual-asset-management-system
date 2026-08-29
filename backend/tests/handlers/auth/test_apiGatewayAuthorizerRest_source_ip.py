# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Client-IP attribution in the gateway authorization audit record (FIX-048).

The audit writer reads the caller IP from ``requestContext.http.sourceIp``. A REST
authorizer event carries no ``requestContext.http`` block — the TCP peer lives at
``requestContext.identity.sourceIp`` — so every record logged ``sourceIp: unknown``.

These tests drive ``lambda_handler`` with REST-shaped events and assert on the event as
the audit writer receives it, for each fronting mode a single deployment can serve
(direct execute-api, ALB, CloudFront), plus the spoofing and allow/deny controls.
"""
import copy
import os
import re

import pytest
from unittest.mock import patch

from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest

METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/database/db1"

# The IP the audit writer would attribute the decision to. Mirrors the read in
# backend/customLogging/auditLogging.py::log_authorization_gateway, including its default.
AUDIT_UNKNOWN = "unknown"

# Real path of the audit module, so the coupling above can be checked rather than assumed.
_AUDIT_LOGGING_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "backend", "customLogging", "auditLogging.py"
    )
)


def _audit_source_ip(event):
    """Read the source IP exactly as log_authorization_gateway does."""
    return event.get("requestContext", {}).get("http", {}).get("sourceIp", AUDIT_UNKNOWN)


def _rest_event(source_ip="203.0.113.7", headers=None):
    """A REST (v1) REQUEST-authorizer event: no requestContext.http block."""
    return {
        "type": "REQUEST",
        "methodArn": METHOD_ARN,
        "resource": "/database/{databaseId}",
        "path": "/database/db1",
        "httpMethod": "GET",
        "headers": dict(headers or {}, Authorization="Bearer good"),
        "pathParameters": {"databaseId": "db1"},
        "queryStringParameters": None,
        "requestContext": {
            "stage": "prod",
            "identity": {"sourceIp": source_ip, "userAgent": "unit-test"},
        },
    }


class _AuditRecorder:
    """Stand-in for log_authorization_gateway that captures what it was handed."""

    def __init__(self):
        self.calls = []

    def __call__(self, event, authorized, failure_reason=None):
        self.calls.append(
            {
                "event": copy.deepcopy(event),
                "authorized": authorized,
                "failureReason": failure_reason,
            }
        )

    @property
    def event(self):
        assert len(self.calls) == 1, f"expected exactly one audit call, got {len(self.calls)}"
        return self.calls[0]["event"]


def _run(event, *, fronted="none", auth_result=None):
    """Invoke the authorizer with authentication stubbed, returning (policy, recorder)."""
    auth_result = auth_result or {"authorized": True, "context": {"sub": "u1"}, "reason": None}
    recorder = _AuditRecorder()
    with patch.object(rest, "authenticate_request", return_value=auth_result), patch.object(
        rest, "log_authorization_gateway", recorder
    ), patch.object(rest, "API_FRONTED", fronted):
        policy = rest.lambda_handler(event, None)
    return policy, recorder


@pytest.mark.unit
class TestAuditSourceIpAttribution:
    """The audit record names the caller, for every fronting mode."""

    def test_rest_shaped_event_yields_the_real_source_ip(self):
        # No requestContext.http block at all — the shape API Gateway REST actually sends.
        event = _rest_event(source_ip="203.0.113.7")
        assert "http" not in event["requestContext"]

        _, recorder = _run(event)

        assert _audit_source_ip(recorder.event) == "203.0.113.7"
        assert _audit_source_ip(recorder.event) != AUDIT_UNKNOWN

    def test_direct_api_gateway_request_uses_the_tcp_peer(self):
        # Existing integrations call the execute-api URL directly: no front, no headers.
        _, recorder = _run(_rest_event(source_ip="198.51.100.42"), fronted="none")
        assert _audit_source_ip(recorder.event) == "198.51.100.42"

    def test_direct_request_against_a_cloudfront_deployment_still_attributes_the_caller(self):
        # A CloudFront deployment must keep authorizing (and auditing) direct callers, whose
        # request carries no front-injected header at all.
        _, recorder = _run(_rest_event(source_ip="198.51.100.42"), fronted="cloudfront")
        assert _audit_source_ip(recorder.event) == "198.51.100.42"

    def test_alb_deployment_uses_the_tcp_peer(self):
        # The ALB redirects to execute-api, so the client is the peer; headers are not trusted.
        event = _rest_event(source_ip="198.51.100.42", headers={"X-Forwarded-For": "10.0.0.9"})
        _, recorder = _run(event, fronted="alb")
        assert _audit_source_ip(recorder.event) == "198.51.100.42"

    def test_cloudfront_request_records_the_viewer_not_the_edge(self):
        # Behind CloudFront the peer is an edge IP and the viewer is the left-hand XFF entry.
        event = _rest_event(
            source_ip="192.0.2.55",
            headers={"X-Forwarded-For": "198.51.100.10, 192.0.2.55"},
        )
        _, recorder = _run(event, fronted="cloudfront")
        assert _audit_source_ip(recorder.event) == "198.51.100.10"

    def test_cloudfront_viewer_address_header_records_the_viewer(self):
        # CloudFront-Viewer-Address is preferred when forwarded; the port is stripped.
        event = _rest_event(
            source_ip="192.0.2.55",
            headers={"CloudFront-Viewer-Address": "198.51.100.10:41234"},
        )
        _, recorder = _run(event, fronted="cloudfront")
        assert _audit_source_ip(recorder.event) == "198.51.100.10"

    def test_forged_forwarding_headers_are_ignored_off_cloudfront(self):
        # On a directly reachable endpoint every header is caller-controlled, so a claimed
        # address must never displace the unforgeable TCP peer in the record.
        event = _rest_event(
            source_ip="198.51.100.42",
            headers={
                "X-Forwarded-For": "10.0.0.1",
                "CloudFront-Viewer-Address": "10.0.0.2:443",
            },
        )
        _, recorder = _run(event, fronted="none")
        assert _audit_source_ip(recorder.event) == "198.51.100.42"

    def test_denied_request_names_the_ip_the_decision_was_made_on(self):
        # An "IP address not authorized" record is useless without the IP it refers to.
        event = _rest_event(source_ip="198.51.100.42")
        _, recorder = _run(
            event,
            auth_result={
                "authorized": False,
                "context": None,
                "reason": "IP address not authorized",
            },
        )
        assert recorder.calls[0]["authorized"] is False
        assert recorder.calls[0]["failureReason"] == "IP address not authorized"
        assert _audit_source_ip(recorder.event) == "198.51.100.42"

    def test_immediate_peer_stays_in_the_event_echo(self):
        # The audit entry appends the whole event; identity.sourceIp is what a reader
        # consults to see the unforgeable hop, so it must not be rewritten.
        event = _rest_event(
            source_ip="192.0.2.55",
            headers={"X-Forwarded-For": "198.51.100.10, 192.0.2.55"},
        )
        _, recorder = _run(event, fronted="cloudfront")
        assert recorder.event["requestContext"]["identity"]["sourceIp"] == "192.0.2.55"
        assert _audit_source_ip(recorder.event) == "198.51.100.10"

    def test_v2_shaped_event_keeps_its_source_ip(self):
        # Nothing blanks an already-populated http.sourceIp when there is no identity block.
        event = {
            "methodArn": METHOD_ARN,
            "headers": {"Authorization": "Bearer good"},
            "requestContext": {
                "http": {"path": "/database/db1", "method": "GET", "sourceIp": "203.0.113.9"}
            },
        }
        _, recorder = _run(event)
        assert _audit_source_ip(recorder.event) == "203.0.113.9"

    def test_missing_source_ip_degrades_rather_than_crashing(self):
        # Nothing to attribute: the record carries no address, and the request still resolves.
        event = _rest_event()
        event["requestContext"]["identity"] = {}
        policy, recorder = _run(event)
        assert _audit_source_ip(recorder.event) in (None, AUDIT_UNKNOWN)
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"


@pytest.mark.unit
class TestAuditWriterCoupling:
    """The assertion above is only meaningful while the writer reads that field."""

    def test_audit_writer_still_reads_request_context_http_source_ip(self):
        with open(_AUDIT_LOGGING_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        assert "def log_authorization_gateway(" in source
        pattern = re.compile(
            r"""\.get\(\s*['"]http['"]\s*,[^)]*\)\s*\.get\(\s*['"]sourceIp['"]"""
        )
        assert pattern.search(source), (
            "log_authorization_gateway no longer reads requestContext.http.sourceIp; "
            "the authorizer populates that field for it"
        )


@pytest.mark.unit
class TestAuthorizationOutcomeUnchanged:
    """Positive control: this runs ahead of every authenticated route."""

    def test_allow_policy_and_context_preserved(self):
        policy, recorder = _run(_rest_event())
        stmt = policy["policyDocument"]["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Action"] == "execute-api:Invoke"
        assert stmt["Resource"] == "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/*"
        assert policy["context"]["sub"] == "u1"
        assert policy["principalId"] == "u1"
        assert recorder.calls[0]["authorized"] is True

    def test_deny_policy_preserved(self):
        policy, _ = _run(
            _rest_event(source_ip="8.8.8.8"),
            auth_result={"authorized": False, "context": None, "reason": "IP"},
        )
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Deny"
        assert "context" not in policy

    def test_ignored_path_allow_establishes_no_identity(self):
        policy, _ = _run(
            _rest_event(),
            auth_result={
                "authorized": True,
                "context": None,
                "reason": None,
                "ignoredPath": True,
            },
        )
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert "context" not in policy
        assert policy["principalId"] == "user"

    def test_client_ip_resolution_failure_does_not_change_the_outcome(self):
        # A raise inside the audit-IP population would otherwise be caught by the handler's
        # outer except and turn every authenticated request into a Deny.
        recorder = _AuditRecorder()
        with patch.object(
            rest, "authenticate_request",
            return_value={"authorized": True, "context": {"sub": "u1"}, "reason": None},
        ), patch.object(rest, "log_authorization_gateway", recorder), patch.object(
            rest, "resolve_client_ip", side_effect=RuntimeError("boom")
        ):
            policy = rest.lambda_handler(_rest_event(), None)
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert policy["context"]["sub"] == "u1"
        assert len(recorder.calls) == 1
