#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The one builder of compliance audit rows.

Audit row (COMPLIANCE_AUDIT_STORAGE_TABLE, PK `entryId`):
    entryId, databaseId:assetId (AssetIndex PK), timestamp (the sort key of every index),
    eventType (EventTypeIndex PK), allListPartition (AuditByDateGSI PK — the constant
    `AUDIT_LIST_PARTITION`), databaseId, assetId, actor, details (JSON), searchText (what the
    listing's `text` filter matches: lowercased asset name, asset id and actor), and the optional
    assetName (the asset's name when the event was recorded), previousState, newState, schemaName,
    evaluationId, cascadeId.

`AuditByDateGSI` is what makes the unfiltered audit listing one newest-first query, and a row the
index can see is one that carries the constant. Every writer therefore puts an item this module
built — a put that assembles its own dict is a row the global listing silently never shows, which is
why `tests/common/compliance/test_auditRecord.py` fails any `put_item` into the audit table whose
item did not come from `build_audit_item`.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# The value every audit row carries in `allListPartition`; the audit service queries
# AuditByDateGSI on it.
AUDIT_LIST_PARTITION = "audit"


def audit_search_text(asset_name: Optional[str], asset_id: Optional[str], actor: Optional[str]) -> str:
    """What an audit entry's `text` filter matches: lowercased asset name, asset id and actor."""
    return " ".join(str(part).strip().lower() for part in (asset_name, asset_id, actor)
                    if part and str(part).strip())


def build_audit_item(
    database_id: str,
    asset_id: str,
    event_type: str,
    actor: str,
    details: Optional[Dict[str, Any]] = None,
    previous_state: Optional[str] = None,
    new_state: Optional[str] = None,
    schema_name: Optional[str] = None,
    evaluation_id: Optional[str] = None,
    cascade_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    asset_name: Optional[str] = None,
) -> Dict[str, Any]:
    """One audit row, keyed for all three indexes; `timestamp` defaults to now (UTC, ISO-8601).
    Optional fields left None are absent from the row rather than written as null."""
    item = {
        "entryId": str(uuid.uuid4()),
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "eventType": event_type,
        "allListPartition": AUDIT_LIST_PARTITION,
        "databaseId": database_id,
        "assetId": asset_id,
        "actor": actor,
        "details": json.dumps(details or {}),
        "searchText": audit_search_text(asset_name, asset_id, actor),
    }
    if asset_name:
        item["assetName"] = asset_name
    if previous_state is not None:
        item["previousState"] = previous_state
    if new_state is not None:
        item["newState"] = new_state
    if schema_name is not None:
        item["schemaName"] = schema_name
    if evaluation_id is not None:
        item["evaluationId"] = evaluation_id
    if cascade_id is not None:
        item["cascadeId"] = cascade_id
    return item
