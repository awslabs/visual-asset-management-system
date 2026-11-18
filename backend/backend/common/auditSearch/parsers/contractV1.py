# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The contract version 1 parser (``auditVersion: 1``, ``docs/developer/audit-logging.md`` "Action
Records").

It reads the payload's documented top-level keys and nothing else: ``action``, ``outcome``,
``reason``, ``eventTime``, ``actor``, ``request``, ``entity``, the ID keys, the counts, the four value
sections, ``truncated``, ``legacy`` and ``legacyType``. A key found anywhere else in the payload (inside
``changes``, ``requestParameters`` or ``details``, or in the name of an asset) is a value, never a
fact about the record. The request echo is not read.
"""

from typing import Any, Dict

from common.auditActionTypes import AuditLogGroup, KIND_READ, KIND_SEARCH
from common.auditActions import audit_spec_for_action
from common.auditSearch import redaction
from common.auditSearch.parsers.base import (
    Record,
    action_name,
    base_row,
    id_fields,
    int_scalar,
    masked_preview,
    outcome_name,
    text_scalar,
    timestamp_of,
)
from common.auditSearch.rowModel import FORMAT_CONTRACT_V1, PARSE_PARSED, AuditRow

AUDIT_VERSION = 1


def _search_text_mode(action: Any, legacy_type: Any) -> str:
    """How much typed text the sections of this record hide: all of it for a search, the text filters
    of a registered read, none otherwise."""
    if action == redaction.SEARCH_ACTION or legacy_type == redaction.SEARCH_ACTION:
        return "search"
    spec = audit_spec_for_action(action) if isinstance(action, str) else None
    if spec is not None and spec.kind in (KIND_READ, KIND_SEARCH):
        return "read"
    return "none"


def parse(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    """Read a record whose payload has ``auditVersion: 1``. ``record.payload`` is a dict."""
    payload: Dict[str, Any] = record.payload or {}
    row = base_row(record, group, timestamp_of(payload, timestamp))
    row.formatVersion = FORMAT_CONTRACT_V1
    row.parseStatus = PARSE_PARSED
    row.recordId = text_scalar(payload.get("recordId"), 128)
    row.action = action_name(payload.get("action"))
    row.outcome = outcome_name(payload.get("outcome"))
    row.reason = text_scalar(payload.get("reason"), 128)

    actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
    row.user = text_scalar(actor.get("userId"), 256) or row.prefixUser
    row.onBehalfOf = text_scalar(actor.get("onBehalfOf"), 256)
    row.authMethod = text_scalar(actor.get("authMethod"), 64)
    row.apiKeyId = text_scalar(actor.get("apiKeyId"), 256)
    row.crossCall = actor["crossCall"] if isinstance(actor.get("crossCall"), bool) else None

    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    for key in ("requestId", "method", "route", "sourceIp", "lambdaRequestId"):
        value = text_scalar(request.get(key), 256)
        if value is not None:
            row.request[key] = value

    entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
    entity_type, entity_id = text_scalar(entity.get("type"), 64), text_scalar(entity.get("id"), 1024)
    if entity_type is not None or entity_id is not None:
        row.entity = {"type": entity_type, "id": entity_id}

    id_fields(payload, row)
    row.bytes = int_scalar(payload.get("bytes"))
    row.fileCount = int_scalar(payload.get("fileCount"))
    row.itemCount = int_scalar(payload.get("itemCount"))
    row.durationMs = int_scalar(payload.get("durationMs"))

    legacy = payload.get("legacy")
    legacy_type = payload.get("legacyType")
    if isinstance(legacy_type, str):
        row.legacyType = text_scalar(legacy_type, 128)

    sections = {key: payload.get(key) for key in ("changes", "previous", "requestParameters", "details")}
    obj = payload.get("obj")
    if isinstance(obj, dict):
        # The object a denial or an authorization check named; it is part of the authorization
        # record's meaning, so it is shown with the details.
        details = sections.get("details")
        sections["details"] = {**(details if isinstance(details, dict) else {}), "obj": obj}
    if isinstance(legacy, dict) and legacy:
        details = sections.get("details")
        sections["details"] = {**(details if isinstance(details, dict) else {}), "legacy": legacy}
    redacted, section_truncated = redaction.redact_sections(
        sections, search_text=_search_text_mode(row.action, row.legacyType))
    row.changes = redacted.get("changes")
    row.previous = redacted.get("previous")
    row.requestParameters = redacted.get("requestParameters")
    row.details = redacted.get("details")
    row.truncated = bool(payload.get("truncated") is True or section_truncated)
    return row


def unsupported(record: Record, group: AuditLogGroup, timestamp: Any, version: Any) -> AuditRow:
    """The row of a record whose ``auditVersion`` no adapter reads: kept, with its masked prefix."""
    from common.auditSearch.rowModel import PARSE_UNSUPPORTED_VERSION
    row = base_row(record, group, timestamp)
    row.parseStatus = PARSE_UNSUPPORTED_VERSION
    row.preview = masked_preview(record)
    row.details = {"auditVersion": version if isinstance(version, (int, str)) and not isinstance(version, bool)
                   and len(str(version)) <= 16 else "unknown"}
    row.details = redaction.redact_value(row.details)[0]
    return row
