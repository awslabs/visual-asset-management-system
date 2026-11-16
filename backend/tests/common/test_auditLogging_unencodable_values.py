# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An audit record is still written when its payload or event echo holds a value JSON cannot encode.

A handler that passes a DynamoDB item (Decimal numbers) or a set into an audit call made
json.dumps raise. On the event echo that exception reached the writer's outer handler, so the
whole record was dropped; on a dict payload the record fell back to Python's repr. Both are now
serialized with default=str: the value is written as its string form and the record keeps its
JSON shape.
"""

import decimal
import json
import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("AUDIT_LOG_ACTIONS", "test-auditActions")

from backend.backend.customLogging import auditLogging  # noqa: E402
from backend.backend.customLogging.logger import mask_sensitive_data  # noqa: E402

_ECHO_MARKER = " --- [event: "


def _write(event, messages=("[ACTIONS][type: x] [user: u]",)):
    """Run the batch writer with a mocked CloudWatch client and the real masker; return the client."""
    logs = MagicMock()
    with patch.object(auditLogging, "cloudwatch_logs", logs), \
            patch.object(auditLogging, "mask_sensitive_data", mask_sensitive_data):
        auditLogging._write_batch_to_cloudwatch("test-auditActions", list(messages), event)
    return logs


@pytest.mark.unit
class TestUnencodableValues:
    def test_an_echo_with_a_non_json_value_is_still_written(self):
        """A DynamoDB Decimal or a set in the event made the echo raise, so the whole record was dropped."""
        event = {"headers": {"x-note": "a"},
                 "body": {"count": decimal.Decimal("3"), "tags": {"only"}}}
        logs = _write(event)
        assert logs.put_log_events.called, "the record was dropped"
        message = logs.put_log_events.call_args.kwargs["logEvents"][0]["message"]
        echo = message[message.index(_ECHO_MARKER) + len(_ECHO_MARKER):-1]
        assert json.loads(echo) == {"headers": {"x-note": "a"},
                                    "body": {"count": "3", "tags": "{'only'}"}}

    def test_every_entry_of_a_batch_is_written(self):
        event = {"body": {"size": decimal.Decimal("12.5")}}
        logs = _write(event, messages=[f"[ACTIONS][type: x] [user: u{i}]" for i in range(3)])
        written = [e for call in logs.put_log_events.call_args_list for e in call.kwargs["logEvents"]]
        assert len(written) == 3

    def test_a_payload_with_a_non_json_value_keeps_its_json_shape(self):
        message = auditLogging._format_log_message(
            "[ACTIONS][type: x]", {"user": "alice"}, {"size": decimal.Decimal("12.5"), "ok": True})
        payload = json.loads(message[message.index("{"):])
        assert payload == {"size": "12.5", "ok": True}

    def test_a_json_payload_and_echo_are_unchanged(self):
        """Values JSON already encodes are written exactly as before."""
        message = auditLogging._format_log_message(
            "[ACTIONS][type: x]", {"user": "alice", "roles": ["admin"]}, {"assetId": "a1", "n": 2})
        assert message == '[ACTIONS][type: x] [user: alice] [roles: ["admin"]] [mfaEnabled: False] ' \
                          '{"assetId": "a1", "n": 2}'
        logs = _write({"pathParameters": {"assetId": "a1"}})
        written = logs.put_log_events.call_args.kwargs["logEvents"][0]["message"]
        assert written.endswith(_ECHO_MARKER + '{"pathParameters": {"assetId": "a1"}}]')
