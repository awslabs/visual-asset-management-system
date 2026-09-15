# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ignored-path bypass is an exact match against IGNORED_PATHS (FIX-002 / S2-BACKEND-001).

The bypass runs before any token handling, so anything it matches is reachable
anonymously. Two greedy routes exist — GET/HEAD
``/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`` and
``.../auxiliaryPreviewAssets/stream/{proxy+}`` — which means an unauthenticated caller can
put arbitrary segments at the tail of a real request path. These tests pin both halves: the
two configured ignored paths still bypass (bootstrap of the SPA depends on it), and a
crafted path whose tail spells an ignored path does not.
"""
import pytest
from unittest.mock import patch
from backend.backend.common.auth import authorizerCore as core

# The deployed value of CUSTOM_AUTHORIZER_IGNORED_PATHS (infra/config/config.ts).
IGNORED = ["/api/amplify-config", "/api/version"]

# The greedy {proxy+} routes that make a crafted tail reachable (common/apiRoutes.py).
DOWNLOAD_STREAM = "/database/db1/assets/a1/download/stream"
AUX_PREVIEW_STREAM = "/database/db1/assets/a1/auxiliaryPreviewAssets/stream"


def _evt(path, source_ip="203.0.113.7", auth=None):
    return {
        "requestContext": {"identity": {"sourceIp": source_ip}},
        "path": path,
        "httpMethod": "GET",
        "headers": {"Authorization": auth} if auth else {},
    }


@pytest.mark.unit
class TestIgnoredPathExactMatch:
    """is_path_ignored must accept only the configured paths, verbatim."""

    @pytest.mark.parametrize("path", IGNORED)
    def test_configured_paths_are_ignored(self, path):
        # Positive control: the real anonymous routes still bypass authentication.
        with patch.object(core, "IGNORED_PATHS", IGNORED):
            assert core.is_path_ignored(path) is True

    @pytest.mark.parametrize("prefix", [DOWNLOAD_STREAM, AUX_PREVIEW_STREAM])
    @pytest.mark.parametrize("ignored", IGNORED)
    def test_greedy_proxy_tail_is_not_ignored(self, prefix, ignored):
        with patch.object(core, "IGNORED_PATHS", IGNORED):
            assert core.is_path_ignored(prefix + ignored) is False

    @pytest.mark.parametrize("path", [
        "/foo/notapi/version",          # partial segment
        "/api/versionx",                # partial segment
        "/api/version/extra",           # ignored path as a prefix
        "/api/api/version",             # stage prefix does not reach the authorizer
        "/API/VERSION",                 # case must match
        "",                             # unresolvable path
    ])
    def test_near_miss_paths_are_not_ignored(self, path):
        with patch.object(core, "IGNORED_PATHS", IGNORED):
            assert core.is_path_ignored(path) is False

    def test_entry_without_leading_slash_still_matches(self):
        # Configuration entries are normalized to one leading slash, not loosened.
        with patch.object(core, "IGNORED_PATHS", ["api/version"]):
            assert core.is_path_ignored("/api/version") is True
            assert core.is_path_ignored("/database/db1/assets/a1/download/stream/api/version") is False


@pytest.mark.unit
class TestMethodArnPathHasNoStagePrefix:
    """API Gateway strips the stage, so an exact comparison is the correct shape."""

    def test_path_from_method_arn_excludes_stage(self):
        arn = "arn:aws:execute-api:us-west-2:111:abc/api/GET/api/version"
        assert core._path_from_method_arn(arn) == "/api/version"

    def test_path_from_method_arn_keeps_greedy_tail(self):
        arn = ("arn:aws:execute-api:us-west-2:111:abc/api/GET"
               "/database/db1/assets/a1/download/stream/api/version")
        assert core._path_from_method_arn(arn) == \
            "/database/db1/assets/a1/download/stream/api/version"


@pytest.mark.unit
class TestAuthenticateRequestIgnoredPath:
    """End-to-end through authenticate_request: the bypass and the crafted path."""

    @pytest.mark.parametrize("path", IGNORED)
    def test_ignored_path_authorized_without_authorization_header(self, path):
        # Positive control (top-level "path" event shape).
        with patch.object(core, "IGNORED_PATHS", IGNORED):
            res = core.authenticate_request(_evt(path), fronted="none")
        assert res["authorized"] is True

    @pytest.mark.parametrize("path", IGNORED)
    def test_ignored_path_authorized_from_method_arn_only(self, path):
        # Positive control (REST REQUEST-authorizer shape: methodArn, no "path").
        evt = {
            "type": "REQUEST",
            "methodArn": f"arn:aws:execute-api:us-west-2:111:abc123/api/GET{path}",
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
            "headers": {},
        }
        with patch.object(core, "IGNORED_PATHS", IGNORED):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is True

    @pytest.mark.parametrize("prefix", [DOWNLOAD_STREAM, AUX_PREVIEW_STREAM])
    @pytest.mark.parametrize("ignored", IGNORED)
    def test_crafted_greedy_path_denied_without_authorization_header(self, prefix, ignored):
        with patch.object(core, "IGNORED_PATHS", IGNORED):
            res = core.authenticate_request(_evt(prefix + ignored), fronted="none")
        assert res["authorized"] is False
        assert res["context"] is None

    @pytest.mark.parametrize("prefix", [DOWNLOAD_STREAM, AUX_PREVIEW_STREAM])
    def test_crafted_greedy_path_denied_with_junk_bearer(self, prefix):
        # The exploit only needs an Authorization header present so API Gateway invokes the
        # authorizer; its value is never verifiable, so the request must be denied.
        path = prefix + "/api/version"
        with patch.object(core, "IGNORED_PATHS", IGNORED), \
             patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "verify_cognito_jwt", return_value=None):
            res = core.authenticate_request(_evt(path, auth="Bearer JUNK"), fronted="none")
        assert res["authorized"] is False

    @pytest.mark.parametrize("prefix", [DOWNLOAD_STREAM, AUX_PREVIEW_STREAM])
    def test_crafted_greedy_path_denied_from_method_arn_only(self, prefix):
        evt = {
            "type": "REQUEST",
            "methodArn": f"arn:aws:execute-api:us-west-2:111:abc123/api/GET{prefix}/api/version",
            "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
            "headers": {},
        }
        with patch.object(core, "IGNORED_PATHS", IGNORED):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is False

    def test_legitimate_stream_request_still_reaches_token_verification(self):
        # Control for over-tightening: a real streamed download is not short-circuited by
        # the ignored-path check — it is authorized on its verified token.
        claims = {"sub": "u1", "cognito:username": "u1"}
        with patch.object(core, "IGNORED_PATHS", IGNORED), \
             patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "verify_cognito_jwt", return_value=claims) as verify, \
             patch.object(core, "_lookup_user_roles", return_value=["admin"]), \
             patch.object(core, "resolve_mfa_enabled", return_value=False):
            res = core.authenticate_request(
                _evt(DOWNLOAD_STREAM + "/model.glb", auth="Bearer good.jwt.token"),
                fronted="none",
            )
        verify.assert_called_once()
        assert res["authorized"] is True
        assert res["context"]["sub"] == "u1"
