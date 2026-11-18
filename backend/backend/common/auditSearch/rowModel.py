# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The row an audit search returns: one parsed audit record, redacted and encoded.

A parser (``common/auditSearch/parsers``) builds an :class:`AuditRow`; every string in it has passed
through ``redaction`` already. The row is the only thing the filters, the contains search, the page
response and the export read: the raw record text and the request echo never leave the parser.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# parseStatus: how much of the record could be read.
PARSE_PARSED = "parsed"
PARSE_PARTIAL = "partial"
PARSE_UNPARSED = "unparsed"
PARSE_UNSUPPORTED_VERSION = "unsupportedVersion"
PARSE_STATUSES = (PARSE_PARSED, PARSE_PARTIAL, PARSE_UNPARSED, PARSE_UNSUPPORTED_VERSION)

# formatVersion: which record format the row was read from.
FORMAT_LEGACY = "legacy"
FORMAT_CONTRACT_V1 = "contract-v1"

OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_DENIED = "denied"
OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_DENIED)

# The ID keys a record carries at the top level, besides databaseId, assetId and filePath, which the
# row names. Mirrors AUDIT_ID_FIELDS in customLogging/auditLogging.py; a test holds the two together.
OTHER_ID_KEYS = (
    "versionId", "assetVersionId", "pipelineId", "templateId", "workflowId", "executionId",
    "executionGroupId", "assetLinkId", "commentId", "tagId", "tagTypeId", "metadataSchemaId",
    "subscriptionId", "roleName", "constraintId", "targetUserId", "uploadId", "triggerType",
    "sessionId", "targetDatabaseId", "targetAssetId", "targetFilePath", "schemaName", "cascadeId",
    "evaluationId",
)


@dataclass
class AuditRow:
    """One parsed audit record. Strings are redacted and encoded; an absent fact is None."""

    timestamp: Optional[str] = None
    groupKey: Optional[str] = None
    groupLabel: Optional[str] = None
    prefix: Optional[str] = None
    action: Optional[str] = None
    legacyType: Optional[str] = None
    entity: Dict[str, Any] = field(default_factory=dict)
    user: Optional[str] = None
    onBehalfOf: Optional[str] = None
    authMethod: Optional[str] = None
    apiKeyId: Optional[str] = None
    crossCall: Optional[bool] = None
    outcome: Optional[str] = None
    reason: Optional[str] = None
    databaseId: Optional[str] = None
    assetId: Optional[str] = None
    filePath: Optional[str] = None
    ids: Dict[str, Any] = field(default_factory=dict)
    request: Dict[str, Any] = field(default_factory=dict)
    bytes: Optional[int] = None
    fileCount: Optional[int] = None
    itemCount: Optional[int] = None
    durationMs: Optional[int] = None
    requestParameters: Optional[Any] = None
    details: Optional[Any] = None
    changes: Optional[Any] = None
    previous: Optional[Any] = None
    truncated: bool = False
    formatVersion: str = FORMAT_LEGACY
    parseStatus: str = PARSE_PARSED
    probe: bool = False
    recordId: Optional[str] = None
    # The masked prefix text of a record that could not be read in full; None for a parsed row.
    preview: Optional[str] = None
    # The user named in the record prefix, kept apart from actor.userId for the user filter.
    prefixUser: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """The row as returned and exported: a fixed key set, absent facts as None."""
        return {
            "timestamp": self.timestamp, "groupKey": self.groupKey, "groupLabel": self.groupLabel,
            "prefix": self.prefix, "action": self.action, "legacyType": self.legacyType,
            "entity": dict(self.entity), "user": self.user, "onBehalfOf": self.onBehalfOf,
            "authMethod": self.authMethod, "apiKeyId": self.apiKeyId, "crossCall": self.crossCall,
            "outcome": self.outcome, "reason": self.reason, "databaseId": self.databaseId,
            "assetId": self.assetId, "filePath": self.filePath, "ids": dict(self.ids),
            "request": dict(self.request), "bytes": self.bytes, "fileCount": self.fileCount,
            "itemCount": self.itemCount, "durationMs": self.durationMs,
            "requestParameters": self.requestParameters, "details": self.details,
            "changes": self.changes, "previous": self.previous, "truncated": self.truncated,
            "formatVersion": self.formatVersion, "parseStatus": self.parseStatus,
            "probe": self.probe, "recordId": self.recordId, "preview": self.preview,
        }


def dedupe_rows(rows: Iterable[AuditRow]) -> Tuple[List[AuditRow], int]:
    """
    The rows without a repeated ``recordId``, and the number removed.

    Delivery is at least once, so one record can arrive twice. A row with no ``recordId`` (a legacy
    record) is never a duplicate. The first row of an id is kept.
    """
    seen: Set[Tuple[Optional[str], str]] = set()
    kept: List[AuditRow] = []
    removed = 0
    for row in rows:
        if row.recordId:
            marker = (row.groupKey, row.recordId)
            if marker in seen:
                removed += 1
                continue
            seen.add(marker)
        kept.append(row)
    return kept, removed
