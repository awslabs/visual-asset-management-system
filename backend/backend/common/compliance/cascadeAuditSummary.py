# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The archived descendants in a cascade's audit entries.

A cascade does not evaluate a descendant that is archived when it reaches it (node status
`skipped_archived`). How many it skipped is a count of a database's archived assets, which only a
caller who may see that database's archive may learn (`ArchivedAssets.archive_visible`, the
decision the compliance reads make on an archived asset's record). So the `cascade_completed`
audit entry keeps them out of the summary every reader shares:

- `summary` (node count per status) and `nodesEvaluated` count the other descendants only;
- `skippedArchivedByDatabase` records the archived ones per database (absent when there were none).

The subscriber notification carries the shared `summary` alone: one message goes to every
subscriber, so it cannot be filtered per reader.

When the audit trail is read, `details_for_reader` gives each reader the entry it may see: for
each database whose archive the reader may see, its count goes back into `summary`
(`skipped_archived`), `nodesEvaluated` and `skippedArchivedByDatabase`; the others are left out.
An entry written before this shape (its `summary` counts every archived descendant, with no
per-database record) keeps its `skipped_archived` count only for a reader who may see the archive
of the cascade's parent database.

The cascade an evaluation of a parent opens (`cascade_auto_triggered`) is held to the same rule.
Archiving keeps an asset's links, so its direct children can include archived assets:

- `childCount` counts the children that are not archived (`opened_details`);
- `archivedChildrenByDatabase` records the archived ones per database (absent when there were none);
- the approval notification counts the children in `childCount` only.

When the audit trail is read, a database's archived children go back into `childCount` and
`archivedChildrenByDatabase` only for a reader who may see that database's archive. An entry written
before this shape counts every child in `childCount` and cannot be told apart from one without
archived children, so it is returned as it is (such entries exist only on test deployments).
"""

import json
from numbers import Number
from typing import Any, Callable, Dict, List, Optional, Tuple

# The audit event a finished cascade writes (`complianceCascadeExecutor.execute_cascade`).
CASCADE_COMPLETED_EVENT = "cascade_completed"
# The node status of a descendant the executor skipped as archived
# (`complianceCascadeExecutor.NODE_STATE_SKIPPED_ARCHIVED`; a test pins the two together). Kept
# here rather than imported so the audit service does not load the executor.
SKIPPED_ARCHIVED_STATUS = "skipped_archived"
# The `details` key that records those descendants per database.
ARCHIVED_BY_DATABASE_KEY = "skippedArchivedByDatabase"
# The audit event the evaluation of a parent writes when it opens a cascade
# (`complianceTrigger.check_and_trigger_cascade`), and the `details` key that records its archived
# direct children per database.
CASCADE_OPENED_EVENT = "cascade_auto_triggered"
ARCHIVED_CHILDREN_KEY = "archivedChildrenByDatabase"


def _database_of(node: Any) -> str:
    """The database id of a `databaseId:assetId` node key ("" for a malformed one)."""
    return node.split(":", 1)[0] if isinstance(node, str) else ""


def completion_details(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The `details` of a finished cascade's audit entry, from the executor's per-node results:
    `nodesEvaluated` and `summary` (count per status, in first-seen order) of every descendant but
    those skipped as archived, and `skippedArchivedByDatabase` (count per database) for those."""
    summary: Dict[str, int] = {}
    archived: Dict[str, int] = {}
    for result in results:
        status = result.get("status", "unknown")
        if status == SKIPPED_ARCHIVED_STATUS:
            database_id = _database_of(result.get("node"))
            archived[database_id] = archived.get(database_id, 0) + 1
            continue
        summary[status] = summary.get(status, 0) + 1
    details: Dict[str, Any] = {"nodesEvaluated": sum(summary.values()), "summary": summary}
    if archived:
        details[ARCHIVED_BY_DATABASE_KEY] = archived
    return details


def opened_details(reason: str, children: List[Tuple[str, str]],
                   is_archived: Callable[[str, str], bool]) -> Dict[str, Any]:
    """The `details` of the audit entry of a cascade an evaluation opened: `reason`, `childCount`
    the direct children (`(databaseId, assetId)` pairs) that are not archived, and
    `archivedChildrenByDatabase` (count per database) for those `is_archived(databaseId, assetId)`
    says are."""
    count = 0
    archived: Dict[str, int] = {}
    for database_id, asset_id in children:
        if is_archived(database_id, asset_id):
            archived[database_id] = archived.get(database_id, 0) + 1
        else:
            count += 1
    details: Dict[str, Any] = {"reason": reason, "childCount": count}
    if archived:
        details[ARCHIVED_CHILDREN_KEY] = archived
    return details


def _count(value: Any) -> int:
    """A stored count as an int; 0 for anything that is not a non-negative number."""
    if isinstance(value, bool) or not isinstance(value, Number):
        return 0
    return max(0, int(value))


def _decoded(raw: Any) -> Optional[Dict[str, Any]]:
    """An entry's `details` (stored JSON-encoded) as a dict, or None when it is not one."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _with_archived(details: Dict[str, Any], count: int) -> Dict[str, Any]:
    """`details` with `count` archived descendants added to `summary` and `nodesEvaluated`
    (negative to take them out); a `skipped_archived` bucket that reaches 0 is dropped."""
    out = dict(details)
    summary = details.get("summary")
    if isinstance(summary, dict):
        summary = dict(summary)
        remaining = _count(summary.pop(SKIPPED_ARCHIVED_STATUS, 0)) + count
        if remaining > 0:
            summary[SKIPPED_ARCHIVED_STATUS] = remaining
        out["summary"] = summary
    if "nodesEvaluated" in details:
        out["nodesEvaluated"] = max(0, _count(details["nodesEvaluated"]) + count)
    return out


def _visible_by_database(details: Dict[str, Any], key: str,
                         archive_visible: Callable[[str], bool]) -> Dict[str, int]:
    """The per-database archived counts under `key` of the databases whose archive the reader may
    see; unreadable counts count nothing."""
    by_database = details.get(key)
    by_database = by_database if isinstance(by_database, dict) else {}
    return {database_id: _count(count) for database_id, count in by_database.items()
            if _count(count) > 0 and archive_visible(database_id)}


def _opened_for_reader(details: Dict[str, Any],
                       archive_visible: Callable[[str], bool]) -> Optional[Dict[str, Any]]:
    """A `cascade_auto_triggered` entry's `details` for one reader, or None to leave it as it is
    (no per-database record: no archived child, or written before the record existed)."""
    if ARCHIVED_CHILDREN_KEY not in details:
        return None
    visible = _visible_by_database(details, ARCHIVED_CHILDREN_KEY, archive_visible)
    out = dict(details)
    out.pop(ARCHIVED_CHILDREN_KEY)
    if "childCount" in details:
        out["childCount"] = _count(details["childCount"]) + sum(visible.values())
    if visible:
        out[ARCHIVED_CHILDREN_KEY] = visible
    return out


def details_for_reader(entry: Dict[str, Any], archive_visible: Callable[[str], bool]) -> Dict[str, Any]:
    """The audit entry as one reader may see it. `archive_visible(database_id)` is whether the
    reader may see that database's archive. Only a `cascade_completed` and a
    `cascade_auto_triggered` entry are changed: their archived descendants (children) are counted
    only for the databases whose archive the reader may see (module docstring). Any other entry,
    and one whose `details` cannot be read, is returned as it is."""
    event_type = entry.get("eventType")
    if event_type not in (CASCADE_COMPLETED_EVENT, CASCADE_OPENED_EVENT):
        return entry
    raw = entry.get("details")
    details = _decoded(raw)
    if details is None:
        return entry

    if event_type == CASCADE_OPENED_EVENT:
        out = _opened_for_reader(details, archive_visible)
        if out is None:
            return entry
    elif ARCHIVED_BY_DATABASE_KEY in details:
        visible = _visible_by_database(details, ARCHIVED_BY_DATABASE_KEY, archive_visible)
        out = _with_archived(details, sum(visible.values()))
        out.pop(ARCHIVED_BY_DATABASE_KEY)
        if visible:
            out[ARCHIVED_BY_DATABASE_KEY] = visible
    else:
        # Written before the archived descendants were recorded per database: the shared summary
        # counts all of them, under the cascade's parent database as far as the entry tells.
        summary = details.get("summary")
        archived = _count(summary.get(SKIPPED_ARCHIVED_STATUS)) if isinstance(summary, dict) else 0
        if (not isinstance(summary, dict) or SKIPPED_ARCHIVED_STATUS not in summary
                or archive_visible(entry.get("databaseId") or "")):
            return entry
        out = _with_archived(details, -archived)

    return dict(entry, details=json.dumps(out) if isinstance(raw, str) else out)
