# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The legacy adapters: records written without ``auditVersion``.

These are the records written before the record contract: the same
bracketed prefix and a payload whose keys depend on the writing function. One adapter per prefix
family, named by ``AuditLogGroup.legacy_parser``:

* ``authentication``: ``[AUTHENTICATION][authenticated: True|False]``;
* ``authorization``: ``[AUTHORIZATION][authorized: True|False]``, including the API authorizer's own
  record (user ``unknown``, ``sourceIp``, ``failureReason``);
* ``fileTransfer``: ``[FILEUPLOAD]``, ``[FILEDOWNLOAD]``, ``[FILEDOWNLOAD-STREAMED]``;
* ``authEvents``: ``[AUTHCHANGES][type: ...]`` and ``[AUTHOTHER][type: ...]``;
* ``errors``: ``[ERRORS][type: ...]``;
* ``actions``: ``[ACTIONS][type: ...]`` written by ``log_actions``.

A legacy payload names no action: the row's ``action`` stays None and ``legacyType`` carries the
``[type: ...]`` value. Only top-level payload keys are read as facts. The request facts come from the
request echo by an allow-list (``base.echo_request_facts``).
"""

from typing import Any, Callable, Dict

from common.auditActionTypes import AuditLogGroup
from common.auditSearch import redaction
from common.auditSearch.parsers.base import (
    Record,
    base_row,
    echo_request_facts,
    id_fields,
    int_scalar,
    outcome_name,
    text_scalar,
)
from common.auditSearch.rowModel import (
    FORMAT_LEGACY,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    PARSE_PARSED,
    AuditRow,
)

# Payload keys the row names itself; the rest of the payload is returned as the redacted details.
_CONSUMED_KEYS = frozenset({
    "databaseId", "assetId", "filePath", "sourceIp", "failureReason", "uploadDenied",
    "uploadDeniedReason", "customData", "outcome", "bytes", "fileCount", "itemCount", "durationMs",
}) | frozenset({
    "versionId", "assetVersionId", "pipelineId", "templateId", "workflowId", "executionId",
    "executionGroupId", "assetLinkId", "commentId", "tagId", "tagTypeId", "metadataSchemaId",
    "subscriptionId", "roleName", "constraintId", "targetUserId", "uploadId", "triggerType",
    "sessionId", "targetDatabaseId", "targetAssetId", "targetFilePath", "schemaName", "cascadeId",
    "evaluationId",
})

# The label and entity type of a permission-probe summary row (see :func:`authorization`).
PROBE_SUMMARY_ACTION = "permission probe (summary)"
PROBE_SUMMARY_ENTITY_TYPE = "auditLog"

_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def _common(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    row = base_row(record, group, timestamp)
    row.formatVersion = FORMAT_LEGACY
    row.parseStatus = PARSE_PARSED
    payload = record.payload or {}
    id_fields(payload, row)
    row.bytes = int_scalar(payload.get("bytes"))
    row.fileCount = int_scalar(payload.get("fileCount"))
    row.itemCount = int_scalar(payload.get("itemCount"))
    row.durationMs = int_scalar(payload.get("durationMs"))
    facts = echo_request_facts(record)
    source_ip = text_scalar(payload.get("sourceIp"), 256)
    if source_ip is not None and "sourceIp" not in facts:
        facts["sourceIp"] = source_ip
    row.request = facts
    return row


def _finish(built: AuditRow, payload: Dict[str, Any], extra: Dict[str, Any] = None) -> AuditRow:
    """Set the redacted details: the payload keys the row does not name, plus ``extra``."""
    rest = {key: value for key, value in payload.items() if key not in _CONSUMED_KEYS}
    if extra:
        rest.update(extra)
    mode = "search" if built.legacyType == redaction.SEARCH_ACTION else "none"
    redacted, truncated = redaction.redact_sections({"details": rest or None}, search_text=mode)
    built.details = redacted.get("details")
    built.truncated = truncated
    return built


def _flag(record: Record) -> bool:
    return bool(record.prefix and record.prefix.value == "True")


def authentication(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    """``[AUTHENTICATION][authenticated: True|False]``: success, or a failed sign-in."""
    payload = record.payload or {}
    row = _common(record, group, timestamp)
    row.outcome = OUTCOME_SUCCESS if _flag(record) else OUTCOME_FAILED
    return _finish(row, payload)


def authorization(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    """``[AUTHORIZATION][authorized: True|False]``: an allowed check or a denial."""
    payload = record.payload or {}
    row = _common(record, group, timestamp)
    row.outcome = OUTCOME_SUCCESS if _flag(record) else OUTCOME_DENIED
    reason = text_scalar(payload.get("failureReason"), 128)
    if reason:
        row.reason = reason
    obj = payload.get("obj")
    if row.probe and isinstance(obj, dict) and obj.get("object__type") == "auditLog":
        # The summary denial of an audit search or group listing: its object names the groups the caller
        # may NOT search (and so their number), and its action is the check's own. A viewer must learn
        # neither, so the row gets a generic label and entity and the object loses the group keys.
        row.action = PROBE_SUMMARY_ACTION
        row.entity = {"type": PROBE_SUMMARY_ENTITY_TYPE, "id": None}
        rest = {key: value for key, value in payload.items() if key not in ("action", "obj")}
        rest["obj"] = {"object__type": "auditLog"}
        return _finish(row, rest)
    method = payload.get("action")
    if isinstance(method, str) and method.upper() in _HTTP_METHODS and "method" not in row.request:
        row.request["method"] = method.upper()
    return _finish(row, {key: value for key, value in payload.items() if key != "action"})


def file_transfer(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    """``[FILEUPLOAD]`` and the download prefixes: ``uploadDenied`` makes an upload a denial."""
    payload = record.payload or {}
    row = _common(record, group, timestamp)
    denied = payload.get("uploadDenied") is True
    row.outcome = OUTCOME_DENIED if denied else OUTCOME_SUCCESS
    if denied:
        row.reason = text_scalar(payload.get("uploadDeniedReason"), 128)
    custom = payload.get("customData")
    return _finish(row, payload, {"customData": custom} if custom else None)


def auth_events(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    """``[AUTHCHANGES][type: ...]`` and ``[AUTHOTHER][type: ...]``: success unless the payload says
    otherwise."""
    payload = record.payload or {}
    row = _common(record, group, timestamp)
    row.outcome = outcome_name(payload.get("outcome")) or OUTCOME_SUCCESS
    return _finish(row, payload)


def errors(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    """``[ERRORS][type: ...]``: every record is a failure."""
    payload = record.payload or {}
    row = _common(record, group, timestamp)
    row.outcome = OUTCOME_FAILED
    return _finish(row, payload)


def actions(record: Record, group: AuditLogGroup, timestamp: Any) -> AuditRow:
    """``[ACTIONS][type: ...]`` written by ``log_actions``: success unless the payload carries one of
    the three outcomes (an AMI recipe build writes its own ``outcome`` such as ``in-progress``, which
    stays in the details)."""
    payload = record.payload or {}
    row = _common(record, group, timestamp)
    declared = outcome_name(payload.get("outcome"))
    row.outcome = declared or OUTCOME_SUCCESS
    extra: Dict[str, Any] = {}
    if declared is None and "outcome" in payload:
        extra["outcome"] = payload["outcome"]
    return _finish(row, payload, extra or None)


# The adapter of each ``AuditLogGroup.legacy_parser`` name.
LEGACY_PARSERS: Dict[str, Callable[[Record, AuditLogGroup, Any], AuditRow]] = {
    "authentication": authentication,
    "authorization": authorization,
    "fileTransfer": file_transfer,
    "authEvents": auth_events,
    "errors": errors,
    "actions": actions,
}
