# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ignored-path check through the REST API custom domain (issue #714).

With `app.api.apiGatewayRest.customDomain` enabled, base path ``api`` maps to the ``api``
stage, so the anonymous ``/api/version`` route is reached at
``https://{domain}/api/api/version``. The event API Gateway hands the REST authorizer then
carries ``path`` "/api/api/version" (the base path kept), while ``methodArn``
("{apiId}/{stage}/{verb}/{resource path}") names the stage and the stage-relative path
"/api/version", as it does through execute-api. Before the fix the check read ``path`` and
denied both anonymous routes on the custom domain.

The check now reads the methodArn first (server-generated, and the ARN the returned policy
is evaluated against), and a proxy-shaped event without one has its ``path`` made
stage-relative only when ``requestContext.domainName`` is the configured custom domain.
The comparison itself stays the exact match pinned in
test_authorizerCore_ignored_path_exact.py; the REST authorizer's scoped Allow for the
custom-domain methodArn is checked in tests/handlers/auth/test_apiGatewayAuthorizerRest.py.

Event layout follows the REST REQUEST-authorizer input documented by API Gateway and the
proxy events captured on a sandbox deployment; host, API id and account are placeholders.
"""
import pytest
from unittest.mock import patch

from backend.backend.common.auth import authorizerCore as core

IGNORED = ["/api/amplify-config", "/api/version"]
CUSTOM_HOST = "api.vams.example.com"
EXECUTE_API_HOST = "abc123.execute-api.us-east-1.amazonaws.com"
API_STAGE_ARN = "arn:aws:execute-api:us-east-1:123456789012:abc123/api"


def _authorizer_event(url_path, route, domain_name, auth=None, method="GET"):
    """A REST REQUEST-authorizer event.

    ``url_path`` is what the event's ``path`` carries (base path kept on the custom
    domain); ``route`` is the stage-relative path API Gateway puts in the methodArn.
    """
    return {
        "type": "REQUEST",
        "methodArn": f"{API_STAGE_ARN}/{method}{route}",
        "resource": route,
        "path": url_path,
        "httpMethod": method,
        "headers": dict({"Host": domain_name}, **({"Authorization": auth} if auth else {})),
        "queryStringParameters": None,
        "pathParameters": None,
        "requestContext": {
            "resourcePath": route,
            "httpMethod": method,
            "path": "/api" + route if domain_name == EXECUTE_API_HOST else url_path,
            "stage": "api",
            "domainName": domain_name,
            "apiId": "abc123",
            "accountId": "123456789012",
            "identity": {"sourceIp": "203.0.113.7"},
        },
    }


def _proxy_shaped_event(url_path, domain_name):
    """The same request without a methodArn, as a proxy-shaped event carries it."""
    evt = _authorizer_event(url_path, "/unused", domain_name)
    del evt["methodArn"]
    return evt


@pytest.fixture(autouse=True)
def custom_domain_env(monkeypatch):
    """The environment the CDK sets on API Lambdas when the custom domain is enabled."""
    monkeypatch.setenv("API_CUSTOM_DOMAIN_NAME", CUSTOM_HOST)
    monkeypatch.setenv("API_CUSTOM_DOMAIN_BASE_PATH", "api")


def _authenticate(evt):
    with patch.object(core, "IGNORED_PATHS", IGNORED):
        return core.authenticate_request(evt, fronted="none")


@pytest.mark.unit
class TestCustomDomainIgnoredPaths:
    @pytest.mark.parametrize("route", IGNORED)
    def test_custom_domain_anonymous_routes_are_ignored(self, route):
        res = _authenticate(_authorizer_event("/api" + route, route, CUSTOM_HOST))
        assert res["authorized"] is True
        assert res["ignoredPath"] is True

    @pytest.mark.parametrize("route", IGNORED)
    def test_execute_api_anonymous_routes_are_still_ignored(self, route):
        res = _authenticate(_authorizer_event(route, route, EXECUTE_API_HOST))
        assert res["authorized"] is True
        assert res["ignoredPath"] is True

    def test_stage_prefixed_path_on_execute_api_is_not_ignored(self):
        # https://{apiId}.execute-api.../api/api/api/version would be the "/api/api/version"
        # route of the stage: not an ignored path.
        res = _authenticate(
            _authorizer_event("/api/api/version", "/api/api/version", EXECUTE_API_HOST)
        )
        assert res["authorized"] is False
        assert res["context"] is None

    def test_custom_domain_route_still_requires_a_token(self):
        res = _authenticate(_authorizer_event("/api/database", "/database", CUSTOM_HOST))
        assert res["authorized"] is False
        assert res["reason"] == "Token missing or invalid format"

    def test_custom_domain_greedy_tail_is_not_ignored(self):
        route = "/database/db1/assets/a1/download/stream/api/version"
        res = _authenticate(_authorizer_event("/api" + route, route, CUSTOM_HOST))
        assert res["authorized"] is False

    def test_method_arn_decides_over_a_disagreeing_path(self):
        # The methodArn is what the policy is evaluated against; a path that spells an
        # ignored route cannot bypass authentication for the route the methodArn names.
        res = _authenticate(_authorizer_event("/api/version", "/database", EXECUTE_API_HOST))
        assert res["authorized"] is False


@pytest.mark.unit
class TestCustomDomainIgnoredPathsWithoutMethodArn:
    """Proxy-shaped events: the top-level path is made stage-relative by the shared helper."""

    @pytest.mark.parametrize("route", IGNORED)
    def test_custom_domain_path_is_stripped(self, route):
        res = _authenticate(_proxy_shaped_event("/api" + route, CUSTOM_HOST))
        assert res["authorized"] is True

    def test_stage_prefixed_path_on_execute_api_is_not_ignored(self):
        res = _authenticate(_proxy_shaped_event("/api/api/version", EXECUTE_API_HOST))
        assert res["authorized"] is False

    def test_custom_domain_path_is_not_stripped_without_the_environment(self, monkeypatch):
        monkeypatch.delenv("API_CUSTOM_DOMAIN_NAME")
        res = _authenticate(_proxy_shaped_event("/api/api/version", CUSTOM_HOST))
        assert res["authorized"] is False

    def test_normalized_path_is_not_stripped_twice(self):
        # requestContext.http.path is already stage-relative after normalize_event; a
        # second strip would turn "/api/version" into "/version".
        evt = _proxy_shaped_event("/api/api/version", CUSTOM_HOST)
        evt["requestContext"]["http"] = {"path": "/api/version", "method": "GET"}
        res = _authenticate(evt)
        assert res["authorized"] is True
