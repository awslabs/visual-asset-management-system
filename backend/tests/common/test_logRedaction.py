# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for common.logRedaction — credential redaction of returned log text."""

import pytest

from backend.backend.common.logRedaction import (
    redact_log_text,
    redact_log_events,
    REDACTED,
)


@pytest.mark.unit
class TestRedactLogText:
    def test_none_and_non_string_passthrough(self):
        assert redact_log_text(None) is None
        assert redact_log_text("") == ""
        assert redact_log_text(123) == 123

    def test_plain_text_unchanged(self):
        text = "Pipeline started processing 3 input files at /data/foo.e57"
        assert redact_log_text(text) == text

    def test_json_authorization_redacted(self):
        text = '{"authorization": "Bearer abc.def.ghi", "status": "ok"}'
        out = redact_log_text(text)
        assert "abc.def.ghi" not in out
        assert REDACTED in out
        assert '"status": "ok"' in out

    def test_bare_key_value_redacted(self):
        text = "SessionToken=FQoGZXIvYXdzEED//// status=running"
        out = redact_log_text(text)
        assert "FQoGZXIvYXdzEED" not in out
        assert "status=running" in out

    def test_bearer_token_redacted(self):
        text = "calling api with Authorization: Bearer eyJhbGciOiJ.payloadpart.sig"
        out = redact_log_text(text)
        assert "payloadpart" not in out
        assert REDACTED in out

    def test_aws_access_key_id_redacted(self):
        text = "assumed role with key AKIAIOSFODNN7EXAMPLE for the job"
        out = redact_log_text(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert REDACTED in out

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.s5H5ZoQ5abc-DEF_123"
        text = f"token issued: {jwt}"
        out = redact_log_text(text)
        assert jwt not in out
        assert REDACTED in out

    def test_secret_access_key_json(self):
        text = '{"Credentials": {"SecretAccessKey": "wJalrXUtnFEMI/K7MDENG", "Expiration": "2026"}}'
        out = redact_log_text(text)
        assert "wJalrXUtnFEMI/K7MDENG" not in out
        assert "Expiration" in out


@pytest.mark.unit
class TestRedactLogEvents:
    def test_non_list_passthrough(self):
        assert redact_log_events(None) is None
        assert redact_log_events("nope") == "nope"

    def test_event_messages_redacted(self):
        events = [
            {"timestamp": 1, "message": "ok, nothing secret"},
            {"timestamp": 2, "message": 'authorization: "Bearer secrettoken123"'},
            {"timestamp": 3},  # no message key
        ]
        out = redact_log_events(events)
        assert out[0]["message"] == "ok, nothing secret"
        assert "secrettoken123" not in out[1]["message"]
        assert REDACTED in out[1]["message"]
        assert out[1]["timestamp"] == 2
        assert out[2] == {"timestamp": 3}

    def test_does_not_mutate_input(self):
        events = [{"message": "SessionToken=abc123def"}]
        redact_log_events(events)
        # Original list entry is untouched (new dicts returned).
        assert events[0]["message"] == "SessionToken=abc123def"
