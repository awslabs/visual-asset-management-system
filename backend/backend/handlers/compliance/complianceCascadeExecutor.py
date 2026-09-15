#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Cascade Executor.

Discovers downstream children via DAG traversal, computes topological order,
and executes compliance evaluation on each child in dependency order.
"""

import json
import os
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from customLogging.logger import safeLogger

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceCascadeExecutor")

try:
    cascade_table_name = os.environ["COMPLIANCE_CASCADE_STORAGE_TABLE_NAME"]
    asset_links_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    audit_table_name = os.environ["COMPLIANCE_AUDIT_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

cascade_table = dynamodb.Table(cascade_table_name)
asset_links_table = dynamodb.Table(asset_links_table_name)
audit_table = dynamodb.Table(audit_table_name)


def discover_children(
    database_id: str, asset_id: str
) -> List[Dict[str, str]]:
    """Find all direct children of an asset via the fromAssetGSI.

    Returns list of dicts with databaseId and assetId for each child.
    """
    asset_key = f"{database_id}:{asset_id}"
    response = asset_links_table.query(
        IndexName="fromAssetGSI",
        KeyConditionExpression=Key("fromAssetDatabaseId:fromAssetId").eq(
            asset_key
        ),
    )
    children = []
    for item in response.get("Items", []):
        child_db = item.get("toAssetDatabaseId")
        child_asset = item.get("toAssetId")
        if child_db and child_asset:
            children.append({"databaseId": child_db, "assetId": child_asset})
    return children


def discover_all_descendants(
    database_id: str, asset_id: str
) -> List[Dict[str, str]]:
    """BFS traversal to find all downstream descendants in the DAG.

    Returns list of all descendants (not including the source asset).
    """
    visited: Set[str] = set()
    queue: deque = deque()
    descendants: List[Dict[str, str]] = []

    source_key = f"{database_id}:{asset_id}"
    visited.add(source_key)
    queue.append((database_id, asset_id))

    while queue:
        current_db, current_asset = queue.popleft()
        children = discover_children(current_db, current_asset)
        for child in children:
            child_key = f"{child['databaseId']}:{child['assetId']}"
            if child_key not in visited:
                visited.add(child_key)
                descendants.append(child)
                queue.append((child['databaseId'], child['assetId']))

    return descendants


def topological_sort(
    source_database_id: str,
    source_asset_id: str,
    descendants: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Compute execution order using Kahn's algorithm (BFS topological sort).

    Returns descendants ordered so parents are processed before children.
    """
    all_keys = {
        f"{d['databaseId']}:{d['assetId']}" for d in descendants
    }
    source_key = f"{source_database_id}:{source_asset_id}"
    all_keys.add(source_key)

    in_degree: Dict[str, int] = {k: 0 for k in all_keys}
    adj: Dict[str, List[str]] = {k: [] for k in all_keys}

    for node in descendants:
        node_key = f"{node['databaseId']}:{node['assetId']}"
        parents = _get_parent_keys(node["databaseId"], node["assetId"])
        for parent_key in parents:
            if parent_key in all_keys:
                adj[parent_key].append(node_key)
                in_degree[node_key] += 1

    queue: deque = deque()
    for key, degree in in_degree.items():
        if degree == 0:
            queue.append(key)

    sorted_keys: List[str] = []
    while queue:
        current = queue.popleft()
        sorted_keys.append(current)
        for neighbor in adj.get(current, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    result = []
    for key in sorted_keys:
        if key == source_key:
            continue
        db_id, asset_id = key.split(":", 1)
        result.append({"databaseId": db_id, "assetId": asset_id})

    return result


def execute_cascade(cascade_id: str) -> Dict[str, Any]:
    """Execute an approved cascade: evaluate children in topological order.

    Updates cascade record with progress and results.
    """
    cascade = cascade_table.get_item(
        Key={"cascadeId": cascade_id}
    ).get("Item")

    if not cascade:
        logger.error(f"Cascade {cascade_id} not found")
        return {"error": "Cascade not found"}

    if cascade.get("state") != "executing":
        logger.info(f"Cascade {cascade_id} not in executing state")
        return {"error": "Cascade not in executing state"}

    source_db = cascade["triggeredByDatabaseId"]
    source_asset = cascade["triggeredByAssetId"]

    descendants = discover_all_descendants(source_db, source_asset)

    if not descendants:
        _complete_cascade(cascade_id, [], "completed")
        return {"cascadeId": cascade_id, "status": "completed", "evaluated": 0}

    execution_order = topological_sort(source_db, source_asset, descendants)

    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression=(
            "SET executionOrder = :order, "
            "nodes = :nodes, "
            "totalNodes = :total"
        ),
        ExpressionAttributeValues={
            ":order": json.dumps(
                [f"{n['databaseId']}:{n['assetId']}" for n in execution_order]
            ),
            ":nodes": json.dumps({
                f"{n['databaseId']}:{n['assetId']}": "pending"
                for n in execution_order
            }),
            ":total": len(execution_order),
        },
    )

    from common.compliance.evaluationEngine import evaluate_asset as run_evaluation

    results = []
    for node in execution_order:
        node_key = f"{node['databaseId']}:{node['assetId']}"
        schema_name = _get_asset_schema(node["databaseId"], node["assetId"])

        if not schema_name:
            _update_node_state(cascade_id, node_key, "skipped")
            results.append({"node": node_key, "status": "skipped"})
            continue

        _update_node_state(cascade_id, node_key, "evaluating")

        try:
            eval_result = run_evaluation(
                node["databaseId"], node["assetId"],
                schema_name, "cascade",
            )
            verdict = eval_result.get("verdict", "error")
            _update_node_state(cascade_id, node_key, verdict)
            results.append({"node": node_key, "status": verdict})
        except Exception as e:
            logger.exception(f"Cascade evaluation failed for {node_key}: {e}")
            _update_node_state(cascade_id, node_key, "error")
            results.append({"node": node_key, "status": "error"})

    _complete_cascade(cascade_id, results, "completed")

    _write_audit(
        source_db, source_asset,
        event_type="cascade_completed",
        actor="system",
        details={
            "cascadeId": cascade_id,
            "nodesEvaluated": len(results),
        },
    )

    try:
        from handlers.compliance.complianceNotifications import notify_cascade_completed

        results_summary: Dict[str, int] = {}
        for r in results:
            status = r.get("status", "unknown")
            results_summary[status] = results_summary.get(status, 0) + 1
        notify_cascade_completed(
            source_db, source_asset, cascade_id, results_summary
        )
    except Exception as e:
        logger.exception(f"Failed sending cascade completion notification: {e}")

    return {
        "cascadeId": cascade_id,
        "status": "completed",
        "evaluated": len(results),
        "results": results,
    }


def _get_parent_keys(database_id: str, asset_id: str) -> List[str]:
    """Get composite keys of all parents of an asset."""
    asset_key = f"{database_id}:{asset_id}"
    response = asset_links_table.query(
        IndexName="toAssetGSI",
        KeyConditionExpression=Key("toAssetDatabaseId:toAssetId").eq(
            asset_key
        ),
    )
    parents = []
    for item in response.get("Items", []):
        parent_db = item.get("fromAssetDatabaseId")
        parent_asset = item.get("fromAssetId")
        if parent_db and parent_asset:
            parents.append(f"{parent_db}:{parent_asset}")
    return parents


def _get_asset_schema(database_id: str, asset_id: str) -> str:
    """Look up the compliance schema for an asset."""
    compliance_table_name = os.environ.get(
        "COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME"
    )
    if not compliance_table_name:
        return ""
    compliance_table = dynamodb.Table(compliance_table_name)
    response = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = response.get("Item")
    if item:
        return item.get("schemaName", "")
    return ""


def _update_node_state(cascade_id: str, node_key: str, state: str):
    """Update the state of a single node within a cascade."""
    cascade = cascade_table.get_item(
        Key={"cascadeId": cascade_id}
    ).get("Item")
    if not cascade:
        return

    nodes = json.loads(cascade.get("nodes", "{}"))
    nodes[node_key] = state
    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression="SET nodes = :nodes",
        ExpressionAttributeValues={":nodes": json.dumps(nodes)},
    )


def _complete_cascade(
    cascade_id: str, results: List[Dict], final_state: str
):
    """Mark cascade as completed."""
    now = datetime.now(timezone.utc).isoformat()
    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression=(
            "SET #s = :state, completedAt = :now, "
            "results = :results"
        ),
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={
            ":state": final_state,
            ":now": now,
            ":results": json.dumps(results),
        },
    )


def _write_audit(
    database_id: str,
    asset_id: str,
    event_type: str,
    actor: str,
    details: Dict[str, Any] = None,
):
    """Write an audit log entry."""
    now = datetime.now(timezone.utc).isoformat()
    audit_table.put_item(
        Item={
            "entryId": str(uuid.uuid4()),
            "databaseId:assetId": f"{database_id}:{asset_id}",
            "timestamp": now,
            "eventType": event_type,
            "databaseId": database_id,
            "assetId": asset_id,
            "actor": actor,
            "details": json.dumps(details or {}),
        }
    )
