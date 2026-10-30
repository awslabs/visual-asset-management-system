#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Cascade Executor Lambda.

Invoked asynchronously by the cascade service with `{"cascadeId": "<uuid>"}` once a cascade row
is in the `executing` state. Discovers the trigger asset's downstream descendants through the
asset-link DAG, orders them topologically (parents before children) and evaluates each against
its bound schema, recording per-node progress on the cascade row. The row always ends in a
terminal state: `completed` with `completedAt` and `results`, or `aborted` with `abortReason`.
"""

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3
from botocore.config import Config

from common.compliance.cascadeAuditSummary import completion_details, other_database_node_count
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from customLogging.logger import safeLogger
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance.complianceTrigger import (
    EVALUATION_TARGET_ARCHIVED, EVALUATION_TARGET_REMOVED, automatic_evaluation_target,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceCascadeExecutor")

CASCADE_STATE_EXECUTING = "executing"
CASCADE_STATE_COMPLETED = "completed"
CASCADE_STATE_ABORTED = "aborted"

# Recorded as `abortReason` when the run raises out of execute_cascade.
EXECUTION_FAILED_ABORT_REASON = "Cascade execution failed"

NODE_STATE_PENDING = "pending"
NODE_STATE_EVALUATING = "evaluating"
# A node that is not evaluated: it has no bound schema, or it no longer exists (its compliance
# record is then removed, as its permanent delete removes it).
NODE_STATE_SKIPPED = "skipped"
# A node whose evaluation did not run because a newer evaluation of the same asset had already
# started (the run_evaluation result carries `superseded: true`): like `skipped`, the node is
# done and the asset's state is that newer evaluation's to settle.
NODE_STATE_SUPERSEDED = store.SUPERSEDED_VERDICT
NODE_STATE_ERROR = "error"
# A descendant in a database the caller who started the cascade (approver, or creator of a cascade
# without approval) may not evaluate, or whose asset that caller's asset rules refuse: it is not
# evaluated, with that caller's reach or any other.
NODE_STATE_SKIPPED_NOT_AUTHORIZED = "skipped_not_authorized"
# A descendant that is archived when the executor reaches it (archiving keeps an asset's links, so
# the cascade still discovers it): it is not evaluated, and nothing is written to its compliance
# record or history (`complianceTrigger.automatic_evaluation_target`, as the trigger and the
# sweeps decide it).
NODE_STATE_SKIPPED_ARCHIVED = "skipped_archived"

# Bound on the descendants one cascade evaluates (`store.MAX_CASCADE_NODES`); a DAG larger than
# this is truncated, so a runaway graph cannot exhaust the Lambda timeout.
MAX_CASCADE_NODES = store.MAX_CASCADE_NODES

try:
    cascade_table_name = get_table_name(ResourceKeys.COMPLIANCE_CASCADE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

cascade_table = dynamodb.Table(cascade_table_name)


#######################
# Lambda handler
#######################

def lambda_handler(event, context) -> Dict[str, Any]:
    """Run the cascade named by the event. An event that does not carry a UUID `cascadeId` is
    rejected without a write; a run that raises leaves the row `aborted` rather than `executing`."""
    cascade_id = _cascade_id_from_event(event)
    (valid, message) = validate({"cascadeId": {"value": cascade_id, "validator": "UUID"}})
    if not valid:
        logger.error(f"Cascade executor event rejected: {message}")
        return {"error": "Invalid cascade executor event"}

    try:
        return execute_cascade(cascade_id)
    except Exception as e:
        logger.exception(f"Cascade {cascade_id} execution failed: {e}")
        abort_cascade(cascade_id, EXECUTION_FAILED_ABORT_REASON)
        return {"cascadeId": cascade_id, "status": CASCADE_STATE_ABORTED,
                "error": EXECUTION_FAILED_ABORT_REASON}


def _cascade_id_from_event(event) -> Any:
    """The event's `cascadeId`, or None for an event that is not a dict; validate() rejects both
    None and a non-string value."""
    return event.get("cascadeId") if isinstance(event, dict) else None


def abort_cascade(cascade_id: str, reason: str) -> None:
    """Mark a still-executing cascade aborted with the reason; the terminal state clients read
    back. A row that already reached a terminal state (a failure after the completion write) is
    left as it is."""
    try:
        cascade_table.update_item(
            Key={"cascadeId": cascade_id},
            UpdateExpression="SET #s = :state, abortReason = :reason, completedAt = :now",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":state": CASCADE_STATE_ABORTED,
                ":reason": reason,
                ":now": datetime.now(timezone.utc).isoformat(),
                ":executing": CASCADE_STATE_EXECUTING,
            },
            ConditionExpression="#s = :executing",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info(f"Cascade {cascade_id} is no longer executing; abort not recorded")


#######################
# Descendant discovery
#######################


def _node_key(database_id: str, asset_id: str) -> str:
    return f"{database_id}:{asset_id}"


# Descendant discovery is shared with the cascade service, which decides at a cascade's start which
# descendants the starting caller may act on (`store.discover_all_descendants`).
discover_children = store.discover_children
discover_all_descendants = store.discover_all_descendants


def topological_sort(
    source_database_id: str,
    source_asset_id: str,
    descendants: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Descendants ordered so every parent precedes its children (Kahn's algorithm over the
    edges among the discovered nodes)."""
    all_keys = {_node_key(d["databaseId"], d["assetId"]) for d in descendants}
    source_key = _node_key(source_database_id, source_asset_id)
    all_keys.add(source_key)

    in_degree: Dict[str, int] = {k: 0 for k in all_keys}
    adjacency: Dict[str, List[str]] = {k: [] for k in all_keys}

    for node in descendants:
        node_key = _node_key(node["databaseId"], node["assetId"])
        for parent_key in _parent_keys(node["databaseId"], node["assetId"]):
            if parent_key in all_keys:
                adjacency[parent_key].append(node_key)
                in_degree[node_key] += 1

    queue: deque = deque(key for key, degree in in_degree.items() if degree == 0)
    ordered: List[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for neighbor in adjacency.get(current, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    result = []
    for key in ordered:
        if key == source_key:
            continue
        db_id, asset_id = key.split(":", 1)
        result.append({"databaseId": db_id, "assetId": asset_id})
    return result


def _parent_keys(database_id: str, asset_id: str) -> List[str]:
    """Composite keys of an asset's parents: `parentChild` link rows where the asset is the `to`
    side (`store.is_cascade_link`)."""
    parents = []
    for item in store.get_parent_links(database_id, asset_id):
        if not store.is_cascade_link(item):
            continue
        parent_db = item.get("fromAssetDatabaseId")
        parent_asset = item.get("fromAssetId")
        if parent_db and parent_asset:
            parents.append(_node_key(parent_db, parent_asset))
    return parents


def execute_cascade(cascade_id: str) -> Dict[str, Any]:
    """Execute an approved cascade: evaluate the source's descendants in topological order and
    record progress and results on the cascade row."""
    cascade = cascade_table.get_item(Key={"cascadeId": cascade_id}).get("Item")
    if not cascade:
        logger.error(f"Cascade {cascade_id} not found")
        return {"cascadeId": cascade_id, "error": "Cascade not found"}
    if cascade.get("state") != CASCADE_STATE_EXECUTING:
        logger.info(f"Cascade {cascade_id} is not in the executing state")
        return {"cascadeId": cascade_id, "error": "Cascade not in executing state"}

    source_db = cascade["triggeredByDatabaseId"]
    source_asset = cascade["triggeredByAssetId"]

    descendants = discover_all_descendants(source_db, source_asset)
    if not descendants:
        _complete_cascade(cascade_id, [])
        return {"cascadeId": cascade_id, "status": CASCADE_STATE_COMPLETED, "evaluated": 0,
                "results": []}

    execution_order = topological_sort(source_db, source_asset, descendants)
    nodes = {_node_key(n["databaseId"], n["assetId"]): NODE_STATE_PENDING for n in execution_order}
    authorized = _authorized_database_ids(cascade)
    authorized_assets = _authorized_asset_keys(cascade)

    # Each node's compliance record is read once, up front, so the names are recorded with the
    # nodes; the evaluation loop takes the bound schema from the same read. A node outside the
    # starting caller's reach (its database, or, for a caller whose asset rules read asset fields,
    # the asset itself) is not read: it is neither evaluated nor named.
    records = {}
    for node in execution_order:
        node_key = _node_key(node["databaseId"], node["assetId"])
        if ((authorized is None or node["databaseId"] in authorized)
                and (authorized_assets is None or node_key in authorized_assets)):
            records[node_key] = store.get_compliance_record(node["databaseId"], node["assetId"]) or {}
    node_names = {key: record.get("assetName") or "" for key, record in records.items()}

    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression=("SET executionOrder = :order, nodes = :nodes, totalNodes = :total,"
                          " nodeNames = :names"),
        ExpressionAttributeValues={
            ":order": json.dumps(list(nodes.keys())),
            ":nodes": json.dumps(nodes),
            ":total": len(execution_order),
            ":names": json.dumps(node_names),
        },
    )

    results = []
    for node in execution_order:
        node_key = _node_key(node["databaseId"], node["assetId"])
        if node_key not in records:
            nodes[node_key] = NODE_STATE_SKIPPED_NOT_AUTHORIZED
            _write_nodes(cascade_id, nodes)
            results.append({"node": node_key, "status": NODE_STATE_SKIPPED_NOT_AUTHORIZED})
            continue
        schema_name = records[node_key].get("schemaName", "")
        if not schema_name:
            nodes[node_key] = NODE_STATE_SKIPPED
            _write_nodes(cascade_id, nodes)
            results.append({"node": node_key, "status": NODE_STATE_SKIPPED})
            continue

        nodes[node_key] = NODE_STATE_EVALUATING
        _write_nodes(cascade_id, nodes)
        try:
            # Decided now, not when the cascade started: a descendant archived, or deleted, since
            # discovery (or since approval) is not evaluated.
            presence = automatic_evaluation_target(node["databaseId"], node["assetId"])
            if presence == EVALUATION_TARGET_ARCHIVED:
                logger.info(f"Cascade {cascade_id}: {node_key} is archived; not evaluated")
                nodes[node_key] = NODE_STATE_SKIPPED_ARCHIVED
                results.append({"node": node_key, "status": NODE_STATE_SKIPPED_ARCHIVED})
            elif presence == EVALUATION_TARGET_REMOVED:
                logger.info(f"Cascade {cascade_id}: {node_key} no longer exists; its record was "
                            "removed")
                nodes[node_key] = NODE_STATE_SKIPPED
                results.append({"node": node_key, "status": NODE_STATE_SKIPPED})
            else:
                evaluation = store.run_evaluation(
                    node["databaseId"], node["assetId"], schema_name, "cascade")
                node_state = _node_state_for(evaluation)
                nodes[node_key] = node_state
                results.append({"node": node_key, "status": node_state,
                                "evaluationId": evaluation.get("evaluationId")})
        except Exception as e:
            logger.exception(f"Cascade evaluation failed for {node_key}: {e}")
            nodes[node_key] = NODE_STATE_ERROR
            results.append({"node": node_key, "status": NODE_STATE_ERROR})
        _write_nodes(cascade_id, nodes)

    _complete_cascade(cascade_id, results)
    # The descendants skipped as archived are recorded per database, outside the summary and the
    # node count every reader shares: the audit service counts each only for a reader who may see
    # that archived asset, and the notification, one message for every subscriber, not at all.
    # The descendants in other databases than the parent's are recorded per database too: the audit
    # service counts their results only for a reader of that database, and the notification counts
    # them without their results.
    details = completion_details(results, source_db)

    store.write_audit(
        source_db, source_asset,
        event_type="cascade_completed",
        actor=store.SYSTEM_ACTOR,
        cascade_id=cascade_id,
        details=details,
    )

    try:
        from handlers.compliance.complianceNotifications import notify_cascade_completed

        notify_cascade_completed(source_db, source_asset, cascade_id, details["summary"],
                                 other_database_node_count(details))
    except Exception as e:
        logger.exception(f"Failed sending cascade completion notification: {e}")

    return {
        "cascadeId": cascade_id,
        "status": CASCADE_STATE_COMPLETED,
        "evaluated": len(results),
        "results": results,
    }


def _authorized_database_ids(cascade: Dict[str, Any]) -> Any:
    """The databases whose descendants this cascade may evaluate, as recorded when a caller started
    it; None for a cascade without the record (one started before it was kept), whose descendants
    are all evaluated as before. An unreadable record authorizes nothing."""
    raw = cascade.get("authorizedDatabaseIds")
    if raw is None:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        logger.warning("Cascade authorizedDatabaseIds is unreadable; no descendant is evaluated")
        return set()
    return set(value) if isinstance(value, (list, tuple, set)) else set()


def _authorized_asset_keys(cascade: Dict[str, Any]) -> Any:
    """The descendants (`databaseId:assetId`) this cascade may evaluate, as recorded when a caller
    whose asset rules read asset fields started it (the asset GET on each one's row, decided with
    that caller's rights); None when the cascade has no such record, and then the databases decide
    alone (`_authorized_database_ids`). A descendant discovered after the start is not in the
    record. An unreadable record authorizes nothing."""
    raw = cascade.get("authorizedAssetKeys")
    if raw is None:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        logger.warning("Cascade authorizedAssetKeys is unreadable; no descendant is evaluated")
        return set()
    return set(value) if isinstance(value, (list, tuple, set)) else set()


def _node_state_for(evaluation: Dict[str, Any]) -> str:
    """The node state an evaluation result records: `superseded` for a run a newer evaluation of
    the asset superseded, otherwise the verdict (`error` when the result carries none)."""
    if evaluation.get("superseded"):
        return NODE_STATE_SUPERSEDED
    return evaluation.get("verdict", NODE_STATE_ERROR)


def _write_nodes(cascade_id: str, nodes: Dict[str, str]) -> None:
    """Store the per-node state map on the cascade row."""
    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression="SET nodes = :nodes",
        ExpressionAttributeValues={":nodes": json.dumps(nodes)},
    )


def _complete_cascade(cascade_id: str, results: List[Dict[str, Any]]) -> None:
    """Mark the cascade completed with its results."""
    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression="SET #s = :state, completedAt = :now, results = :results",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={
            ":state": CASCADE_STATE_COMPLETED,
            ":now": datetime.now(timezone.utc).isoformat(),
            ":results": json.dumps(results),
        },
    )
