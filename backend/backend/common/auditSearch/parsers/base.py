# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
What every record parser shares: the prefix, the payload, the request echo and the row builder.

A record is one line: a bracketed prefix, a JSON payload and, usually, ``--- [event: {...}]``. The
parse is positional. The prefix is read from the start of the line and ends at the payload, which is
decoded with a raw JSON decoder from the first character after ``[mfaEnabled: ...] ``. Nothing is
found by searching the line for a marker, so text inside a name, a path or a body (``] [user: admin]``,
``--- [event: ...]``, a quoted key) cannot become a prefix segment, the user or the echo.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from common.auditActionTypes import AuditLogGroup
from common.auditSearch import redaction
from common.auditSearch.rowModel import OTHER_ID_KEYS, OUTCOMES, AuditRow, normalize_time

TRUNCATION_MARKER = " ...[truncated]"
ECHO_SEPARATOR = " --- [event: "
PREVIEW_CHARS = 256

_DECODER = json.JSONDecoder()
_LABEL_AND_SEGMENT = re.compile(
    r"\A\[(?P<label>[A-Z][A-Z0-9-]{0,31})\]"
    r"(?:\[(?P<kind>type|authorized|authenticated): (?P<value>[^\]]*)\])?"
    r"(?: \[probe: (?P<probe>[^\]]*)\])? \[user: "
)
_MFA_SEGMENT = re.compile(r"\] \[mfaEnabled: (True|False)\] ")
_PREFIX_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_ACTION_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_ISO_TIME = re.compile(r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z\Z")
_ROLES_SEPARATOR = "] [roles: "


@dataclass
class Prefix:
    """The bracketed prefix of a record."""

    label: str
    kind: Optional[str]          # "type", "authorized" or "authenticated"
    value: Optional[str]         # the segment value; "True"/"False" for the flags
    probe: Optional[str]
    user: str
    roles: Any
    mfa_enabled: bool
    payload_start: int


@dataclass
class Record:
    """A record split into prefix, payload and echo. ``payload`` is None when it is not a JSON object."""

    prefix: Optional[Prefix]
    payload: Optional[Dict[str, Any]]
    payload_end: Optional[int]
    text: str
    partial: bool


def decode_prefix_text(value: str) -> str:
    """Decode the ``\\uXXXX`` escapes the writer puts in a bracketed segment value."""
    return _PREFIX_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), value)


def parse_prefix(text: str) -> Optional[Prefix]:
    """Read the prefix from the start of ``text``; None when the text does not start with one."""
    match = _LABEL_AND_SEGMENT.match(text)
    if not match:
        return None
    user_start = match.end()
    roles_marker = text.find(_ROLES_SEPARATOR, user_start)
    if roles_marker < 0:
        return None
    user = decode_prefix_text(text[user_start:roles_marker])
    roles_start = roles_marker + len(_ROLES_SEPARATOR)
    try:
        roles, roles_end = _DECODER.raw_decode(text, roles_start)
    except ValueError:
        return None
    mfa = _MFA_SEGMENT.match(text, roles_end)
    if not mfa:
        return None
    return Prefix(label=match.group("label"), kind=match.group("kind"), value=match.group("value"),
                  probe=match.group("probe"), user=user, roles=roles,
                  mfa_enabled=mfa.group(1) == "True", payload_start=mfa.end())


def split_record(text: str, message_length: Optional[int] = None) -> Record:
    """Split a record into prefix and payload by the documented parsing rule. Never raises."""
    try:
        prefix = parse_prefix(text)
        cut = (message_length is not None and message_length > len(text)) or text.endswith(TRUNCATION_MARKER)
        if prefix is None:
            return Record(None, None, None, text, cut)
        payload = None
        payload_end = None
        if text.startswith("{", prefix.payload_start):
            try:
                decoded, payload_end = _DECODER.raw_decode(text, prefix.payload_start)
                if isinstance(decoded, dict):
                    payload = decoded
            except (ValueError, RecursionError):
                payload = None
        return Record(prefix, payload, payload_end, text, cut)
    except Exception:
        return Record(None, None, None, text, False)


def echo_request_facts(record: Record) -> Dict[str, Any]:
    """
    The request facts of a legacy record's echo, from an allow-list of paths only.

    The echo is read only when it starts exactly where the payload ended. The path, headers, query
    string and body of the echo are never read or returned: ``requestId``, ``method``, ``route`` (the
    template, never the concrete path) and ``sourceIp``.
    """
    if record.payload_end is None or not record.text.startswith(ECHO_SEPARATOR, record.payload_end):
        return {}
    start = record.payload_end + len(ECHO_SEPARATOR)
    if not record.text.startswith("{", start):
        return {}
    try:
        echo, _ = _DECODER.raw_decode(record.text, start)
    except (ValueError, RecursionError):
        return {}
    if not isinstance(echo, dict):
        return {}
    context = echo.get("requestContext") if isinstance(echo.get("requestContext"), dict) else {}
    http = context.get("http") if isinstance(context.get("http"), dict) else {}
    identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
    route = (echo.get("resource") or context.get("resourcePath") or context.get("routeKey")
             or echo.get("routeKey"))
    if isinstance(route, str) and " " in route:
        route = route.split(" ", 1)[1]
    facts = {
        "requestId": context.get("requestId"),
        "method": http.get("method") or echo.get("httpMethod") or context.get("httpMethod"),
        "route": route,
        "sourceIp": http.get("sourceIp") or identity.get("sourceIp"),
    }
    return {key: text_scalar(value, 256) for key, value in facts.items() if isinstance(value, str)}


def text_scalar(value: Any, limit: int = 1024) -> Optional[str]:
    """A string value, redacted, bounded and encoded; None for anything that is not a string."""
    if not isinstance(value, str):
        return None
    return redaction.safe_text(value, limit)


def int_scalar(value: Any) -> Optional[int]:
    """An integer value; None for anything else (a bool is not a count)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def action_name(value: Any) -> Optional[str]:
    """An action name when it has the registry's shape, else None."""
    return value if isinstance(value, str) and _ACTION_NAME.match(value) else None


def outcome_name(value: Any) -> Optional[str]:
    """An outcome when it is one of ``success``, ``failed`` and ``denied``, else None."""
    return value if isinstance(value, str) and value in OUTCOMES else None


def event_time(value: Any) -> Optional[str]:
    """An ISO 8601 UTC time as written, else None."""
    return value if isinstance(value, str) and _ISO_TIME.match(value) else None


def base_row(record: Record, group: AuditLogGroup, timestamp: Optional[str]) -> AuditRow:
    """A row with what every record carries: group, prefix, prefix user and probe marker."""
    row = AuditRow(timestamp=normalize_time(text_scalar(timestamp, 64)), groupKey=group.key, groupLabel=group.label)
    prefix = record.prefix
    if prefix is not None:
        row.prefix = prefix.label
        row.prefixUser = text_scalar(prefix.user, 256)
        row.user = row.prefixUser
        row.probe = prefix.probe is not None
        if prefix.kind == "type":
            row.legacyType = text_scalar(prefix.value, 128)
    return row


def id_fields(payload: Dict[str, Any], row: AuditRow) -> None:
    """Copy the top-level ID keys of the payload into the row. Nested keys are never read."""
    row.databaseId = text_scalar(payload.get("databaseId"), 256)
    row.assetId = text_scalar(payload.get("assetId"), 256)
    row.filePath = text_scalar(payload.get("filePath"), 1024)
    for key in OTHER_ID_KEYS:
        value = text_scalar(payload.get(key), 1024)
        if value is not None:
            row.ids[key] = value


def masked_preview(record: Record) -> Optional[str]:
    """
    A short preview of a record that could not be read in full: its prefix only, redacted and encoded.

    The payload and the echo are never part of a preview, because the parts that were not read are the
    parts that were not redacted.
    """
    prefix = record.prefix
    if prefix is None:
        return None
    head = record.text[:prefix.payload_start]
    return redaction.safe_text(head, PREVIEW_CHARS)


def timestamp_of(payload: Optional[Dict[str, Any]], fallback: Optional[str]) -> Optional[str]:
    """The record's own ``eventTime`` when it has one, else the ingestion time."""
    if payload:
        own = event_time(payload.get("eventTime"))
        if own:
            return own
    return fallback


def split_sections(payload: Dict[str, Any], keys: Tuple[str, ...]) -> Dict[str, Any]:
    """The named top-level sections of a payload that hold a value."""
    return {key: payload[key] for key in keys if key in payload and payload[key] is not None}
