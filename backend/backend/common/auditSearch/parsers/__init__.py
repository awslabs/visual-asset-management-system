# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Record parsers, dispatched by ``auditVersion``.

``parse_record(head_text, group)`` is the one entry point. It never raises: a record that cannot be
read becomes a row with ``parseStatus`` ``partial``, ``unparsed`` or ``unsupportedVersion`` that keeps
its masked prefix, so a search shows that a record exists without trusting what it could not read.

Dispatch:

1. A payload with ``auditVersion`` goes to the adapter registered for that version in
   :data:`CONTRACT_PARSERS`. A version with no adapter is ``unsupportedVersion``.
2. A payload without ``auditVersion`` goes to the group's legacy adapter, named by
   ``AuditLogGroup.legacy_parser`` and registered in ``legacy.LEGACY_PARSERS``.
3. A record whose payload does not decode is ``partial`` when it was cut (the cap marker, or a message
   longer than the text read) and ``unparsed`` otherwise.

A new record format is a new adapter in this package, an entry in ``CONTRACT_PARSERS`` or a new name in
``LEGACY_PARSERS``, and a fixture in the parser tests. The registry contract test fails for a group
whose ``legacy_parser`` has no adapter and for an ``auditVersion`` the writer emits that has none.
"""

from typing import Any, Callable, Dict, Optional

from common.auditActionTypes import AuditLogGroup
from common.auditSearch.parsers import contractV1
from common.auditSearch.parsers.base import Record, base_row, masked_preview, split_record
from common.auditSearch.parsers.legacy import LEGACY_PARSERS
from common.auditSearch.rowModel import (
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    PARSE_PARTIAL,
    PARSE_UNPARSED,
    AuditRow,
)

# The adapter of each ``auditVersion`` the writer emits.
CONTRACT_PARSERS: Dict[int, Callable[[Record, AuditLogGroup, Any], AuditRow]] = {
    contractV1.AUDIT_VERSION: contractV1.parse,
}


def _unread(record: Record, group: AuditLogGroup, timestamp: Any, status: str) -> AuditRow:
    row = base_row(record, group, timestamp)
    row.parseStatus = status
    row.preview = masked_preview(record)
    prefix = record.prefix
    if prefix is not None and prefix.kind in ("authorized", "authenticated"):
        # The prefix alone says whether an authorization or an authentication succeeded.
        allowed = prefix.value == "True"
        row.outcome = OUTCOME_SUCCESS if allowed else (OUTCOME_DENIED if prefix.kind == "authorized"
                                                       else OUTCOME_FAILED)
    return row


def parse_record(head_text: Any, group: AuditLogGroup, timestamp: Optional[str] = None,
                 message_length: Optional[int] = None) -> AuditRow:
    """
    Parse one audit record into a row. Never raises.

    ``head_text`` is the first bytes of the message (``substr(@message, 0, HEAD_BYTES)``),
    ``timestamp`` the ingestion time, and ``message_length`` the full message length
    (``strlen(@message)``), which marks a record cut by the head bound as partial.
    """
    try:
        text = head_text if isinstance(head_text, str) else ""
        record = split_record(text, message_length)
        if record.prefix is None:
            return _unread(record, group, timestamp, PARSE_PARTIAL if record.partial else PARSE_UNPARSED)
        payload = record.payload
        if payload is None:
            return _unread(record, group, timestamp, PARSE_PARTIAL if record.partial else PARSE_UNPARSED)
        if "auditVersion" in payload:
            version = payload["auditVersion"]
            adapter = CONTRACT_PARSERS.get(version) if isinstance(version, int) and not isinstance(version, bool) \
                else None
            if adapter is None:
                return contractV1.unsupported(record, group, timestamp, version)
            return adapter(record, group, timestamp)
        legacy = LEGACY_PARSERS.get(group.legacy_parser)
        if legacy is None:
            return _unread(record, group, timestamp, PARSE_UNPARSED)
        return legacy(record, group, timestamp)
    except Exception:
        # Hostile or unexpected input must not fail a search: the row is kept, unread.
        try:
            return _unread(Record(None, None, None, "", False), group, timestamp, PARSE_UNPARSED)
        except Exception:
            return AuditRow(groupKey=getattr(group, "key", None), parseStatus=PARSE_UNPARSED)
