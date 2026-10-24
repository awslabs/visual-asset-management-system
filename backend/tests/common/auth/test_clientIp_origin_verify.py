# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Client-address resolution on a CloudFront deployment reads the forwarding headers only from a
request that carries the origin verification value CloudFront adds (issue #714).

CloudFront adds ``x-vams-origin-verify`` to every request it forwards to the REST API, with the
``current`` value of the deployment's origin verification secret; while a rotation propagates, an
edge may still send the ``previous`` value, or already send the pending one. Any other request,
including one sent to the execute-api endpoint or the API custom domain, resolves to
``identity.sourceIp``. The IP allow-list and the audit record both use this address.

Events follow the REST REQUEST-authorizer input API Gateway delivers for a request CloudFront
forwarded: ``headers`` and ``multiValueHeaders`` carrying ``CloudFront-Viewer-Address``,
``X-Forwarded-For`` (viewer, then edge) and the origin custom header under the lower-case name
CloudFront is configured with. Addresses are documentation ranges; the edge address is a placeholder.
"""
import sys

import pytest

from backend.backend.common.auth.clientIp import resolve_client_ip, is_ip_authorized

CURRENT = "currentOriginVerifyValue0123456789abcdef"
PREVIOUS = "previousOriginVerifyValue0123456789abcde"
PENDING = "pendingOriginVerifyValue0123456789abcdefg"
SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams-origin-verify-AbCdEf"

EDGE = "192.0.2.55"  # TCP peer: the CloudFront edge that forwarded the request
VIEWER = "198.51.100.10"
ALLOWED = [["198.51.100.0", "198.51.100.255"]]


def _origin_verify_module():
    """The module object resolve_client_ip actually reads."""
    return resolve_client_ip.__globals__["originVerify"]


@pytest.fixture
def origin_verify(monkeypatch):
    """Accept CURRENT, PREVIOUS and PENDING, as during a rotation; returns a call counter."""
    module = _origin_verify_module()
    calls = {"count": 0}

    def fetch(secret_arn):
        calls["count"] += 1
        assert secret_arn == SECRET_ARN
        return (CURRENT, PREVIOUS, PENDING)

    monkeypatch.setenv(module.SECRET_ARN_ENV, SECRET_ARN)
    monkeypatch.setattr(module, "_fetch_accepted_values", fetch)
    monkeypatch.setitem(module._cache, "values", ())
    monkeypatch.setitem(module._cache, "fetched_at", None)
    return calls


def _cloudfront_event(origin_verify_value=None, header_name="x-vams-origin-verify"):
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer eyJ.placeholder.token",
        "CloudFront-Is-Desktop-Viewer": "true",
        "CloudFront-Viewer-Address": VIEWER + ":50314",
        "CloudFront-Viewer-Country": "US",
        "Host": "abc123.execute-api.us-east-1.amazonaws.com",
        "Via": "2.0 0123456789abcdef.cloudfront.net (CloudFront)",
        "X-Amz-Cf-Id": "placeholder-request-id==",
        "X-Forwarded-For": f"{VIEWER}, {EDGE}",
        "X-Forwarded-Port": "443",
        "X-Forwarded-Proto": "https",
    }
    if origin_verify_value is not None:
        headers[header_name] = origin_verify_value
    return {
        "type": "REQUEST",
        "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/api/GET/database",
        "resource": "/database",
        "path": "/database",
        "httpMethod": "GET",
        "headers": headers,
        "multiValueHeaders": {k: [v] for k, v in headers.items()},
        "queryStringParameters": None,
        "requestContext": {
            "resourcePath": "/database",
            "httpMethod": "GET",
            "stage": "api",
            "domainName": "abc123.execute-api.us-east-1.amazonaws.com",
            "identity": {"sourceIp": EDGE, "userAgent": "Amazon CloudFront"},
        },
    }


@pytest.mark.unit
class TestFixtureIsLoadBearing:
    def test_fixture_patches_the_module_resolve_client_ip_reads(self):
        assert _origin_verify_module() is sys.modules["common.auth.originVerify"]


@pytest.mark.unit
class TestValidValueIsTrusted:
    @pytest.mark.parametrize("value", [CURRENT, PREVIOUS, PENDING], ids=["current", "previous", "pending"])
    def test_accepted_value_resolves_the_viewer(self, origin_verify, value):
        client_ip = resolve_client_ip(_cloudfront_event(value), fronted="cloudfront")
        assert client_ip == VIEWER
        assert is_ip_authorized(client_ip, ALLOWED) is True

    def test_header_name_is_matched_case_insensitively(self, origin_verify):
        event = _cloudfront_event(CURRENT, header_name="X-Vams-Origin-Verify")
        assert resolve_client_ip(event, fronted="cloudfront") == VIEWER

    def test_x_forwarded_for_is_used_when_the_viewer_address_is_absent(self, origin_verify):
        event = _cloudfront_event(CURRENT)
        del event["headers"]["CloudFront-Viewer-Address"]
        assert resolve_client_ip(event, fronted="cloudfront") == VIEWER


@pytest.mark.unit
class TestOtherRequestsUseTheTcpPeer:
    @pytest.mark.parametrize(
        "value",
        [None, "", "wrongValue0123456789", CURRENT.upper(), CURRENT + " ", CURRENT[:-1]],
        ids=["missing", "empty", "wrong", "different-case", "trailing-space", "truncated"],
    )
    def test_unaccepted_value_resolves_the_peer(self, origin_verify, value):
        client_ip = resolve_client_ip(_cloudfront_event(value), fronted="cloudfront")
        assert client_ip == EDGE
        assert is_ip_authorized(client_ip, ALLOWED) is False

    def test_non_ascii_value_resolves_the_peer(self, origin_verify):
        assert resolve_client_ip(_cloudfront_event("vérification"), fronted="cloudfront") == EDGE

    def test_secret_read_failure_resolves_the_peer(self, monkeypatch, origin_verify):
        def failing_fetch(secret_arn):
            raise RuntimeError("AccessDeniedException")

        monkeypatch.setattr(_origin_verify_module(), "_fetch_accepted_values", failing_fetch)
        assert resolve_client_ip(_cloudfront_event(CURRENT), fronted="cloudfront") == EDGE

    def test_no_configured_secret_resolves_the_peer(self, monkeypatch, origin_verify):
        monkeypatch.delenv(_origin_verify_module().SECRET_ARN_ENV)
        assert resolve_client_ip(_cloudfront_event(CURRENT), fronted="cloudfront") == EDGE
        assert origin_verify["count"] == 0

    def test_missing_header_does_not_read_the_secret(self, origin_verify):
        assert resolve_client_ip(_cloudfront_event(None), fronted="cloudfront") == EDGE
        assert origin_verify["count"] == 0


@pytest.mark.unit
class TestNonCloudFrontDeploymentsUnchanged:
    @pytest.mark.parametrize("fronted", ["alb", "none"])
    def test_valid_value_does_not_enable_forwarding_headers(self, origin_verify, fronted):
        assert resolve_client_ip(_cloudfront_event(CURRENT), fronted=fronted) == EDGE
        assert origin_verify["count"] == 0
