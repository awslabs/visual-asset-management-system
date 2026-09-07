# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import patch
from backend.backend.common.auth import authorizerCore as core


def _evt(path="/database", source_ip="203.0.113.7", auth="Bearer good.jwt.token", headers=None):
    h = {"Authorization": auth}
    if headers:
        h.update(headers)
    return {
        "requestContext": {"identity": {"sourceIp": source_ip},
                           "http": {"path": path}},
        "path": path, "httpMethod": "GET",
        "headers": h,
    }


@pytest.mark.unit
class TestAuthenticateRequest:
    def test_ignored_path_authorized_without_token(self):
        evt = _evt(path="/api/version", auth=None)
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is True

    def test_ip_denied_blocks(self):
        evt = _evt(source_ip="8.8.8.8")
        with patch.object(core, "ALLOWED_IP_RANGES", [["203.0.113.0", "203.0.113.255"]]):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is False

    def test_ip_check_uses_client_ip_when_fronted(self):
        # sourceIp is the CloudFront hop (disallowed); real client (allowed) is in XFF.
        evt = _evt(source_ip="130.176.0.1",
                   headers={"X-Forwarded-For": "203.0.113.7, 130.176.0.1"},
                   auth=None)
        with patch.object(core, "ALLOWED_IP_RANGES", [["203.0.113.0", "203.0.113.255"]]), \
             patch.object(core, "IGNORED_PATHS", ["/database"]):
            res = core.authenticate_request(evt, fronted="cloudfront")
        assert res["authorized"] is True  # client IP allowed, not the proxy IP

    def test_ignored_path_resolved_from_method_arn_only(self):
        # REST REQUEST-authorizer event: no requestContext.http, no top-level path,
        # only methodArn. The ignored path must still be resolved (anonymous route works).
        evt = {
            "type": "REQUEST",
            "methodArn": "arn:aws:execute-api:us-west-2:111:abc123/api/GET/api/version",
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
            "headers": {},
        }
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is True

    def test_ignored_path_match_is_exact_not_a_suffix(self):
        # This test previously asserted the opposite, on the premise that a REST
        # REQUEST-authorizer event can carry a stage prefix ("/api/api/version" when the
        # stage is "api"). That premise is wrong: API Gateway strips the stage before the
        # authorizer sees the path, which test_path_from_method_arn_strips_the_stage pins
        # directly. Matching on a suffix to tolerate a shape that never arrives is what let
        # a greedy "{proxy+}" route be reached anonymously, so a doubled path must NOT be
        # treated as an ignored path.
        evt = _evt(path="/api/api/version", auth=None)
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is False

    def test_path_from_method_arn_strips_the_stage(self):
        # The control for the test above: it establishes that exact matching is correct
        # rather than assumed. The stage segment ("api") sits between the API id and the
        # HTTP method, and the resolved path carries only the resource path.
        arn = "arn:aws:execute-api:us-west-2:111:abc123/api/GET/api/version"
        assert core._path_from_method_arn(arn) == "/api/version"

    def test_greedy_proxy_route_cannot_borrow_an_ignored_path(self):
        # The actual exploit shape, using the real greedy route that makes it reachable
        # (GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}) rather than
        # a synthetic path.
        exploit = "/database/db1/assets/a1/download/stream/api/version"
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            assert core.is_path_ignored(exploit) is False
            res = core.authenticate_request(_evt(path=exploit, auth=None), fronted="none")
        assert res["authorized"] is False

    @pytest.mark.parametrize("path", ["/foo/notapi/version", "/api/versionx",
                                      "/api/version/extra", "/api/versio"])
    def test_partial_segment_paths_are_not_ignored(self, path):
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            assert core.is_path_ignored(path) is False

    @pytest.mark.parametrize("configured", ["/api/version", "api/version"])
    def test_configured_entry_matches_with_or_without_a_leading_slash(self, configured):
        # Paired positive control for the whole group: the bootstrap routes must STILL be
        # reachable anonymously. An exact-match fix that also stopped matching the real
        # ignored paths would satisfy every denial assertion above and take the app down at
        # sign-in, since /api/amplify-config is fetched before any token exists.
        with patch.object(core, "IGNORED_PATHS", [configured]):
            assert core.is_path_ignored("/api/version") is True
            res = core.authenticate_request(_evt(path="/api/version", auth=None), fronted="none")
        assert res["authorized"] is True

    def test_ip_denied_blocks_even_for_ignored_path(self):
        # IP restriction applies to anonymous/ignored routes too: a disallowed IP is denied
        # before the ignored-path check grants access.
        evt = _evt(path="/api/version", source_ip="8.8.8.8", auth=None)
        with patch.object(core, "ALLOWED_IP_RANGES", [["203.0.113.0", "203.0.113.255"]]), \
             patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is False

    def test_ignored_path_result_is_marked_as_such(self):
        # The bypass is the one authorized result that establishes no identity. The marker
        # is what lets the REST authorizer scope its Allow to the ignored paths instead of
        # emitting the API+stage wildcard for an unauthenticated caller.
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            res = core.authenticate_request(_evt(path="/api/version", auth=None), fronted="none")
        assert res["authorized"] is True
        assert res["ignoredPath"] is True

    def test_authenticated_result_is_not_marked_as_an_ignored_path(self):
        # Paired control: an authenticated Allow must not be mistaken for the bypass, or a
        # real user's policy would be narrowed to the anonymous routes.
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]), \
             patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "verify_cognito_jwt", return_value={"sub": "u1"}), \
             patch.object(core, "_lookup_user_roles", return_value=["admin"]), \
             patch.object(core, "resolve_mfa_enabled", return_value=False):
            res = core.authenticate_request(_evt(path="/database"), fronted="none")
        assert res["authorized"] is True
        assert res.get("ignoredPath") is None


class _FakeRolesTable:
    """Minimal stand-in for the user roles DynamoDB table."""

    def __init__(self, items=None):
        self._items = items if items is not None else [{"roleName": "admin"}]

    def query(self, **kwargs):
        return {"Items": self._items}


def _api_key_record(expires_at, api_key_id="k1", user_id="u1"):
    return {"apiKeyId": api_key_id, "isActive": "true", "userId": user_id,
            "expiresAt": expires_at}


def _verify_key(record):
    with patch.object(core, "_get_user_roles_table", return_value=_FakeRolesTable()), \
         patch.object(core, "_lookup_api_key_by_hash", return_value=record):
        return core.verify_api_key("vams_rawkey")


@pytest.mark.unit
class TestApiKeyExpiry:
    """API key expiry evaluation (S2-BACKEND-052).

    Expiry is the only lifetime bound on an API key — isActive is a separate manual flag —
    so every value that cannot be turned into a comparison must deny. Two stored shapes
    reach the authorizer from the supported API surface: the models accept a value that
    either ``fromisoformat`` or ``strptime('%Y-%m-%d')`` parses, so an unpadded
    ``2027-1-5`` is stored although ``fromisoformat`` alone cannot read it, and a padded
    date-only ``2026-12-31`` parses but yields a naive datetime whose comparison against
    an aware ``now`` raises. Both used to land in the warn-and-continue branch and
    authenticate forever. Every denial assertion below is paired with a control that a
    valid future expiry still authorizes, so a fix that denied every key would not pass.
    """

    @pytest.mark.parametrize("expires_at", [
        "31-12-2020",            # day-first, parsed by neither format
        "2026/12/31",            # slash separators
        "never",
        "Tue, 31 Dec 2026 23:59:59 GMT",   # RFC 1123
        "1798761599",            # epoch seconds stored as a string
    ])
    def test_unevaluable_expiry_denies(self, expires_at):
        assert core._api_key_expiry_denial(expires_at, "k1") == \
            "API key expiry could not be evaluated"

    def test_non_string_expiry_denies(self):
        # A numeric attribute has no .replace; the AttributeError must deny, not escape.
        assert core._api_key_expiry_denial(1798761599, "k1") == \
            "API key expiry could not be evaluated"

    @pytest.mark.parametrize("expires_at", [
        "2999-12-31T23:59:59Z",  # ISO datetime, UTC designator
        "2999-12-31T23:59:59+00:00",
        "2999-12-31",            # date-only: naive, read as UTC
        "2999-1-5",              # unpadded date-only: models accept it, so must evaluate
    ])
    def test_future_expiry_does_not_deny(self, expires_at):
        assert core._api_key_expiry_denial(expires_at, "k1") is None

    @pytest.mark.parametrize("expires_at", [
        "2020-12-31T23:59:59Z",
        "2020-12-31",
        "2020-1-5",
    ])
    def test_past_expiry_denies_as_expired(self, expires_at):
        assert core._api_key_expiry_denial(expires_at, "k1") == "API key has expired"

    def test_verify_api_key_denies_an_unevaluable_expiry(self):
        res = _verify_key(_api_key_record("31-12-2020"))
        assert res["denied"] is True
        assert res["reason"] == "API key expiry could not be evaluated"

    def test_verify_api_key_denies_an_expired_date_only_key(self):
        res = _verify_key(_api_key_record("2020-12-31"))
        assert res["denied"] is True
        assert res["reason"] == "API key has expired"

    def test_verify_api_key_authorizes_a_valid_future_key(self):
        # The control for both denials above: an unexpired key still authenticates, so the
        # fail-closed change cannot be satisfied by denying everything.
        res = _verify_key(_api_key_record("2999-12-31T23:59:59Z"))
        assert "denied" not in res
        assert res["sub"] == "u1"
        assert res["vams:authMethod"] == "apiKey"
        assert json.loads(res["vams:roles"]) == ["admin"]

    def test_verify_api_key_authorizes_a_future_date_only_key(self):
        # Second control, for the documented date-only format ("2026-12-31" is the example
        # in the model's own rejection message): it must evaluate, not deny outright.
        res = _verify_key(_api_key_record("2999-12-31"))
        assert "denied" not in res
        assert res["sub"] == "u1"

    @pytest.mark.parametrize("expires_at", ["", None])
    def test_verify_api_key_authorizes_a_key_with_no_expiry(self, expires_at):
        # A key created without an expiration has no time bound to evaluate.
        res = _verify_key(_api_key_record(expires_at))
        assert "denied" not in res
        assert res["sub"] == "u1"


# The deployed CUSTOM_AUTHORIZER_IGNORED_PATHS pair (infra/config/config.ts), which is also
# the set of routes registered with allowAnonymous (rest-api-gateway-construct.ts).
ANON_PATHS = ["/api/amplify-config", "/api/version"]
API_STAGE = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod"


def _rest_authorizer():
    """The REST authorizer module, imported on demand.

    This directory's conftest registers the real common.auth modules; importing a handler
    at module scope would put collection of every test in this file behind that wiring, so
    the policy-shape tests pull it in themselves.
    """
    from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest
    return rest


def _policy_for(rest, method_arn, result):
    with patch.object(rest, "IGNORED_PATHS", ANON_PATHS), \
         patch.object(rest, "authenticate_request", return_value=result):
        return rest.lambda_handler({"methodArn": method_arn}, None)


@pytest.mark.unit
class TestIgnoredPathPolicyResource:
    """Scope of the IAM policy the REST authorizer emits for the bypass (FIX-002).

    The ignored-path Allow authenticates nobody and carries no context, so granting the
    API+stage wildcard hands an unauthenticated caller a policy covering every route.
    Scoping it to the requested path alone would be wrong in the other direction: the
    anonymous routes share one authorizer keyed on the source IP, so the cached policy is
    replayed for the sibling path and would 403 it for the rest of the TTL.
    """

    IGNORED_RESULT = {"authorized": True, "context": None, "reason": None, "ignoredPath": True}
    AUTHENTICATED_RESULT = {"authorized": True, "context": {"sub": "u1"}, "reason": None}

    def test_bypass_allow_is_scoped_to_the_anonymous_paths(self):
        rest = _rest_authorizer()
        out = _policy_for(rest, f"{API_STAGE}/GET/api/version", self.IGNORED_RESULT)
        stmt = out["policyDocument"]["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Resource"] == [f"{API_STAGE}/*/api/amplify-config",
                                    f"{API_STAGE}/*/api/version"]
        assert "context" not in out          # the bypass establishes no identity

    def test_bypass_allow_is_identical_for_every_anonymous_path(self):
        # Cache safety: the anonymous authorizer's cache key is the source IP alone, so one
        # policy is served for whichever anonymous path the same caller requests next. The
        # policy must therefore not depend on which path produced it.
        rest = _rest_authorizer()
        version = _policy_for(rest, f"{API_STAGE}/GET/api/version", self.IGNORED_RESULT)
        amplify = _policy_for(rest, f"{API_STAGE}/GET/api/amplify-config", self.IGNORED_RESULT)
        assert version["policyDocument"] == amplify["policyDocument"]

    def test_authenticated_allow_keeps_the_api_stage_wildcard(self):
        # Positive control: narrowing the authenticated Allow would break the token-keyed
        # 30s cache, which is reused across every method on the API.
        rest = _rest_authorizer()
        out = _policy_for(rest, f"{API_STAGE}/GET/database/db1", self.AUTHENTICATED_RESULT)
        stmt = out["policyDocument"]["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Resource"] == f"{API_STAGE}/*"
        assert out["context"]["sub"] == "u1"

    def test_bypass_resources_keep_the_request_partition(self):
        rest = _rest_authorizer()
        gov_stage = "arn:aws-us-gov:execute-api:us-gov-west-1:111:xyz789/prod"
        out = _policy_for(rest, f"{gov_stage}/GET/api/version", self.IGNORED_RESULT)
        resources = out["policyDocument"]["Statement"][0]["Resource"]
        assert resources == [f"{gov_stage}/*/api/amplify-config",
                             f"{gov_stage}/*/api/version"]

    def test_configured_entry_without_a_leading_slash_yields_one_slash(self):
        rest = _rest_authorizer()
        with patch.object(rest, "IGNORED_PATHS", ["api/version"]), \
             patch.object(rest, "authenticate_request", return_value=self.IGNORED_RESULT):
            out = rest.lambda_handler({"methodArn": f"{API_STAGE}/GET/api/version"}, None)
        assert out["policyDocument"]["Statement"][0]["Resource"] == \
            [f"{API_STAGE}/*/api/version"]
