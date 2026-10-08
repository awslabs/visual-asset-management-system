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
from typing import Any, Dict, List, Set

import boto3
from botocore.config import Config

from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from customLogging.logger import safeLogger
from handlers.compliance import complianceEvaluationStore as store

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
NODE_STATE_SKIPPED = "skipped"
NODE_STATE_ERROR = "error"

# Bound on the descendants one cascade evaluates; a DAG larger than this is truncated and the
# omission recorded on the cascade row, so a runaway graph cannot exhaust the Lambda timeout.
MAX_CASCADE_NODES = 500

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


def discover_children(database_id: str, asset_id: str) -> List[Dict[str, str]]:
    """Direct children of an asset (link rows where the asset is the `from` side)."""
    children = []
    for item in store.get_child_links(database_id, asset_id):
        child_db = item.get("toAssetDatabaseId")
        child_asset = item.get("toAssetId")
        if child_db and child_asset:
            children.append({"databaseId": child_db, "assetId": child_asset})
    return children


def discover_all_descendants(database_id: str, asset_id: str) -> List[Dict[str, str]]:
    """Every downstream descendant of an asset (breadth-first; the source is excluded),
    bounded by MAX_CASCADE_NODES."""
    visited: Set[str] = {_node_key(database_id, asset_id)}
    queue: deque = deque([(database_id, asset_id)])
    descendants: List[Dict[str, str]] = []

    while queue and len(descendants) < MAX_CASCADE_NODES:
        current_db, current_asset = queue.popleft()
        for child in discover_children(current_db, current_asset):
            child_key = _node_key(child["databaseId"], child["assetId"])
            if child_key in visited:
                continue
            visited.add(child_key)
            descendants.append(child)
            queue.append((child["databaseId"], child["assetId"]))
            if len(descendants) >= MAX_CASCADE_NODES:
                logger.warning(f"Cascade descendant discovery stopped at the {MAX_CASCADE_NODES}-node bound")
                break
    return descendants


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
    """Composite keys of an asset's parents (link rows where the asset is the `to` side)."""
    parents = []
    for item in store.get_parent_links(database_id, asset_id):
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

    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression="SET executionOrder = :order, nodes = :nodes, totalNodes = :total",
        ExpressionAttributeValues={
            ":order": json.dumps(list(nodes.keys())),
            ":nodes": json.dumps(nodes),
            ":total": len(execution_order),
        },
    )

    results = []
    for node in execution_order:
        node_key = _node_key(node["databaseId"], node["assetId"])
        schema_name = (store.get_compliance_record(node["databaseId"], node["assetId"]) or {}).get(
            "schemaName", "")
        if not schema_name:
            nodes[node_key] = NODE_STATE_SKIPPED
            _write_nodes(cascade_id, nodes)
            results.append({"node": node_key, "status": NODE_STATE_SKIPPED})
            continue

        nodes[node_key] = NODE_STATE_EVALUATING
        _write_nodes(cascade_id, nodes)
        try:
            evaluation = store.run_evaluation(
                node["databaseId"], node["assetId"], schema_name, "cascade")
            verdict = evaluation.get("verdict", NODE_STATE_ERROR)
            nodes[node_key] = verdict
            results.append({"node": node_key, "status": verdict,
                            "evaluationId": evaluation.get("evaluationId")})
        except Exception as e:
            logger.exception(f"Cascade evaluation failed for {node_key}: {e}")
            nodes[node_key] = NODE_STATE_ERROR
            results.append({"node": node_key, "status": NODE_STATE_ERROR})
        _write_nodes(cascade_id, nodes)

    _complete_cascade(cascade_id, results)

    store.write_audit(
        source_db, source_asset,
        event_type="cascade_completed",
        actor=store.SYSTEM_ACTOR,
        cascade_id=cascade_id,
        details={"nodesEvaluated": len(results)},
    )

    try:
        from handlers.compliance.complianceNotifications import notify_cascade_completed

        summary: Dict[str, int] = {}
        for result in results:
            status = result.get("status", "unknown")
            summary[status] = summary.get(status, 0) + 1
        notify_cascade_completed(source_db, source_asset, cascade_id, summary)
    except Exception as e:
        logger.exception(f"Failed sending cascade completion notification: {e}")

    return {
        "cascadeId": cascade_id,
        "status": CASCADE_STATE_COMPLETED,
        "evaluated": len(results),
        "results": results,
    }


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
