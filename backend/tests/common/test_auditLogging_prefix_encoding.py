# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audit record prefix values are encoded, and payload and event echo are single-line ASCII JSON.

An audit record is one line: bracketed prefix segments ([ACTIONS][type: ...] [user: ...]
[roles: [...]] [mfaEnabled: ...]), the JSON payload, then the `--- [event: ...]` echo. A reader
splits the prefix on its brackets, so a value placed inside a segment must not carry a bracket, a
line break, or a character that changes how the line reads without being visible. Every such
character is written as \\uXXXX; a value made only of the characters a user id may contain
(common/validators.py userid_pattern) is written unchanged, so existing records and queries over
them keep their exact text.
"""

import decimal
import json
import os
import re
import sys

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("AUDIT_LOG_ACTIONS", "test-auditActions")
os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")
os.environ.setdefault("AUDIT_LOG_AUTHCHANGES", "test-auditAuthChanges")

from backend.backend.customLogging import auditLogging  # noqa: E402
from backend.backend.customLogging.logger import mask_sensitive_data  # noqa: E402
from backend.backend.common.validators import userid_pattern  # noqa: E402

# A user id built to close the user segment, add segments of its own and start a second record line.
BRACKETED_USER = 'alice] [roles: ["admin"]] [mfaEnabled: True]\n[AUTHORIZATION] [user: admin\x07'

# Characters a prefix value must never carry raw.
UNSAFE_CHARS = ["[", "]", "\\", "\r", "\n", "\t", "\x00", "\x07", "\x1b", "\x7f", "\x85", "\x9b",
                "\u00ad", "\u061c", "\u180e", "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
                "\u2028", "\u2029", "\u202a", "\u202e", "\u2060", "\u2066", "\u2069", "\ufeff"]

_ESCAPE = re.compile(r"\\u([0-9a-f]{4})")


def _decode(text):
    """Reverse the prefix encoding."""
    return _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def _segment(message, label):
    """The value of the first [label: value] segment, read the way a bracket-splitting reader does."""
    start = message.index(f"[{label}: ") + len(label) + 3
    return message[start:message.index("]", start)]


def _captured(call):
    """Run an audit call with the single-entry writer captured; return the formatted messages."""
    written = []
    with patch.object(auditLogging, "_write_to_cloudwatch",
                      side_effect=lambda group, message, event: written.append(message)):
        call()
    return written


def _event_for(user, roles=None):
    return {"headers": {}, "requestContext": {"http": {"method": "GET", "path": "/x"}},
            "_claims": {"tokens": [user], "roles": roles or [], "mfaEnabled": False}}


def _fake_claims(event):
    return dict(event["_claims"])


@pytest.mark.unit
class TestPrefixValueEncoding:
    def test_a_bracketed_user_id_stays_inside_its_own_segment(self):
        message = auditLogging._format_log_message("[ACTIONS][type: x]",
                                                   {"user": BRACKETED_USER, "roles": [], "mfaEnabled": False},
                                                   {"k": "v"})
        assert "\n" not in message and "\r" not in message and "\x07" not in message
        # One user segment, one roles segment, one mfaEnabled segment, one AUTHORIZATION marker: none.
        assert message.count("[user: ") == 1
        assert message.count("[roles: ") == 1
        assert message.count("[mfaEnabled: ") == 1
        assert "[AUTHORIZATION]" not in message
        # The bracket-delimited value read back is the whole id, which decodes to the original.
        assert _decode(_segment(message, "user")) == BRACKETED_USER
        assert message.startswith("[ACTIONS][type: x] [user: alice\\u005d \\u005broles: ")

    @pytest.mark.parametrize("char", UNSAFE_CHARS, ids=lambda c: "U+%04X" % ord(c))
    def test_every_unsafe_character_is_escaped(self, char):
        encoded = auditLogging._encode_prefix_value(f"a{char}b")
        assert encoded == "a\\u%04xb" % ord(char)
        assert char not in encoded or char == "\\"
        assert _decode(encoded) == f"a{char}b"

    def test_every_user_id_character_is_left_unchanged(self):
        """Exhaustive over the code space: each character userid_pattern admits is written as is,
        so a record for any valid user id is byte-identical to what the formatter always wrote."""
        allowed = re.compile(userid_pattern[1:-1].replace("{3,256}", ""))
        admitted = [chr(code) for code in range(sys.maxunicode + 1)
                    if (code < 0xD800 or code > 0xDFFF) and allowed.fullmatch(chr(code))]
        # Control: the walk covered the Unicode word characters, not only ASCII.
        assert len(admitted) > 100000
        changed = [hex(ord(char)) for char in admitted if auditLogging._encode_prefix_value(char) != char]
        assert changed == []

    @pytest.mark.parametrize("user", ["alice@example.com", "first.last+tag@example.org",
                                      "svc_account-01", "j\u00fcrgen.m\u00fcller", "\u674e\u96f7_01"])
    def test_realistic_user_ids_are_byte_identical(self, user):
        assert re.fullmatch(userid_pattern, user)
        message = auditLogging._format_log_message(
            "[ACTIONS][type: assetCreate]", {"user": user, "roles": ["admin", "viewer"], "mfaEnabled": True},
            {"databaseId": "db1"})
        legacy = (f"[ACTIONS][type: assetCreate] [user: {user}] "
                  f"[roles: {json.dumps(['admin', 'viewer'])}] [mfaEnabled: True] "
                  f"{json.dumps({'databaseId': 'db1'})}")
        assert message == legacy

    def test_a_role_name_with_brackets_keeps_the_roles_segment_closed_and_decodable(self):
        roles = ["admin", 'x"] [user: root']
        message = auditLogging._format_log_message("[ACTIONS][type: x]",
                                                   {"user": "alice", "roles": roles, "mfaEnabled": False}, None)
        assert message.count("[user: ") == 1
        roles_text = message[message.index("[roles: ") + 8:message.index(" [mfaEnabled: ")]
        # The array's own brackets and the segment's closing bracket, nothing else.
        assert roles_text.endswith("]]") and roles_text.count("[") == 1 and roles_text.count("]") == 2
        assert json.loads(roles_text[:-1]) == roles

    @pytest.mark.parametrize("logger_name,label", [
        ("log_actions", "ACTIONS"), ("log_auth_changes", "AUTHCHANGES")])
    def test_the_secondary_type_is_encoded(self, logger_name, label):
        with patch.object(auditLogging, "request_to_claims", _fake_claims):
            written = _captured(lambda: getattr(auditLogging, logger_name)(
                _event_for("alice"), "x] [user: admin\nnext", {"k": 1}))
        assert len(written) == 1
        message = written[0]
        assert message.startswith(
            f"[{label}][type: x\\u005d \\u005buser: admin\\u000anext] [user: alice] ")
        assert message.count("[user: ") == 1
        assert "\n" not in message

    def test_the_gateway_record_encodes_the_verified_subject(self):
        event = {"context": {"sub": BRACKETED_USER, "mfaEnabled": "false"},
                 "requestContext": {"http": {"sourceIp": "203.0.113.7"}}}
        written = _captured(lambda: auditLogging.log_authorization_gateway(event, True, None))
        assert len(written) == 1
        assert written[0].count("[user: ") == 1
        assert "\n" not in written[0] and "[AUTHORIZATION] [user: admin" not in written[0]

    def test_the_probe_denial_record_encodes_its_values_and_keeps_the_legacy_text(self):
        claims = {"tokens": ["alice"], "roles": ["viewer"], "mfaEnabled": False}
        plain = auditLogging.format_authorization_denial_record(
            claims, "GET /auth/routes/api/allowed", {"action": "GET", "obj": {"route__path": "/x"}})
        assert plain == ("[AUTHORIZATION][authorized: False] [probe: GET /auth/routes/api/allowed] "
                         "[user: alice] [roles: [\"viewer\"]] [mfaEnabled: False] "
                         + json.dumps({"action": "GET", "obj": {"route__path": "/x"}}))
        bracketed = auditLogging.format_authorization_denial_record(
            {"tokens": [BRACKETED_USER], "roles": []}, "GET /x", {"action": "GET"})
        assert bracketed.count("[user: ") == 1 and "\n" not in bracketed


@pytest.mark.unit
class TestPayloadAndEchoSerialization:
    def test_a_string_payload_is_serialized_as_json_on_one_line(self):
        message = auditLogging._format_log_message(
            "[ERRORS][type: x]", {"user": "alice"}, "line one\n[AUTHORIZATION] [user: admin]\u202e")
        assert "\n" not in message and "\u202e" not in message
        payload = message[message.index("[mfaEnabled: False] ") + len("[mfaEnabled: False] "):]
        assert json.loads(payload) == "line one\n[AUTHORIZATION] [user: admin]\u202e"

    def test_a_payload_with_a_non_json_value_keeps_its_json_shape(self):
        message = auditLogging._format_log_message(
            "[ACTIONS][type: x]", {"user": "alice"}, {"size": decimal.Decimal("12.5"), "note": "a\u2028b"})
        payload = json.loads(message[message.index("{"):])
        assert payload == {"size": "12.5", "note": "a\u2028b"}
        assert "\u2028" not in message
        assert message.isascii()

    def test_an_echo_with_a_non_json_value_is_still_written_as_ascii(self):
        """A DynamoDB Decimal or a set in the event made the echo raise, so the whole record was dropped."""
        logs = MagicMock()
        event = {"headers": {"x-note": "a\u2028[event: x]\u200b"},
                 "body": {"count": decimal.Decimal("3")}}
        with patch.object(auditLogging, "cloudwatch_logs", logs), \
                patch.object(auditLogging, "mask_sensitive_data", mask_sensitive_data):
            auditLogging._write_batch_to_cloudwatch("test-auditActions", ["[ACTIONS][type: x] [user: u]"], event)
        assert logs.put_log_events.called, "the record was dropped"
        message = logs.put_log_events.call_args.kwargs["logEvents"][0]["message"]
        assert message.isascii()
        echo = message[message.index(" --- [event: ") + len(" --- [event: "):-1]
        assert json.loads(echo) == {"headers": {"x-note": "a\u2028[event: x]\u200b"},
                                    "body": {"count": "3"}}
