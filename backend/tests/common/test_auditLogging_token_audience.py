# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The authorization audit record names the accepted token audience (issue #1328).

log_authorization_gateway records the Cognito app client id / external IdP audience a verified
token was issued to, so a call made through an additional client (for example a remote MCP
server) is distinguishable from a web app call. Only on a success: a denial records no audience,
and the field never carries token content.
"""

import json
import os

import pytest
from unittest.mock import patch

os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from backend.backend.customLogging import auditLogging  # noqa: E402


def _record(authorized, reason=None, token_audience=None):
    written = []
    with patch.object(auditLogging, "_audit_log_group", return_value="test-auditAuthorization"), \
         patch.object(auditLogging, "_write_to_cloudwatch",
                      side_effect=lambda group, message, event: written.append(message)):
        auditLogging.log_authorization_gateway(
            {"headers": {"Authorization": "Bearer secret.jwt.value"},
             "requestContext": {"http": {"sourceIp": "203.0.113.7"}}},
            authorized, reason, token_audience=token_audience)
    assert len(written) == 1
    return written[0]


def _custom_data(message):
    return json.loads(message[message.index("{"):])


@pytest.mark.unit
class TestAuthorizationAuditTokenAudience:
    def test_a_success_records_the_audience(self):
        data = _custom_data(_record(True, token_audience="mcpclient0001"))
        assert data["tokenAudience"] == "mcpclient0001"

    def test_a_success_without_an_audience_records_none(self):
        assert "tokenAudience" not in _custom_data(_record(True))

    def test_a_denial_never_records_an_audience(self):
        data = _custom_data(_record(False, "Token verification failed", token_audience="x"))
        assert "tokenAudience" not in data
        assert data["failureReason"] == "Token verification failed"

    def test_the_record_carries_no_token_content(self):
        assert "secret.jwt.value" not in _record(True, token_audience="mcpclient0001")
