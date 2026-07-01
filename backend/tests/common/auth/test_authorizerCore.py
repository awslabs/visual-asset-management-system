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

    def test_ignored_path_suffix_match_with_stage_prefix(self):
        # If the authorizer event path carries a stage prefix, the suffix match still
        # recognizes the ignored path.
        evt = _evt(path="/api/api/version", auth=None)
        with patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is True

    def test_ip_denied_blocks_even_for_ignored_path(self):
        # IP restriction applies to anonymous/ignored routes too: a disallowed IP is denied
        # before the ignored-path check grants access.
        evt = _evt(path="/api/version", source_ip="8.8.8.8", auth=None)
        with patch.object(core, "ALLOWED_IP_RANGES", [["203.0.113.0", "203.0.113.255"]]), \
             patch.object(core, "IGNORED_PATHS", ["/api/version"]):
            res = core.authenticate_request(evt, fronted="none")
        assert res["authorized"] is False
