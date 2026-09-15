#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Evaluate Service handler.

Manages compliance evaluation operations:
- POST /compliance/evaluate/{databaseId}/{assetId} — Evaluate asset
- POST /compliance/sweep/{schemaName} — Sweep all assets for a schema
- GET /compliance/evaluations/{databaseId}/{assetId} — Get evaluation history
- GET /compliance/state/{databaseId}/{assetId} — Get compliance state
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.compliance.evaluationEngine import (
    evaluate_asset as run_evaluation,
)
from handlers.compliance.complianceTrigger import check_and_trigger_cascade
from models.common import (
    APIGatewayProxyResponseV2,
    authorization_error,
    internal_error,
    success,
    validation_error,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceEvaluateService")

claims_and_roles = {}

try:
    schema_table_name = os.environ["COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME"]
    compliance_table_name = os.environ["COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME"]
    evaluation_table_name = os.environ["COMPLIANCE_EVALUATION_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["COMPLIANCE_AUDIT_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

schema_table = dynamodb.Table(schema_table_name)
compliance_table = dynamodb.Table(compliance_table_name)
evaluation_table = dynamodb.Table(evaluation_table_name)
audit_table = dynamodb.Table(audit_table_name)
asset_table = dynamodb.Table(asset_table_name)


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE

    try:
        claims_and_roles = request_to_claims(event)
        if "statusCode" in claims_and_roles:
            return claims_and_roles

        http_method = event["requestContext"]["http"]["method"]
        path = event["requestContext"]["http"]["path"]
        path_params = event.get("pathParameters", {}) or {}
        database_id = path_params.get("databaseId")
        asset_id = path_params.get("assetId")
        schema_name = path_params.get("schemaName")

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if http_method == "POST" and "/evaluate/" in path:
            body = json.loads(event.get("body", "{}"))
            response = evaluate_asset(database_id, asset_id, body)
        elif http_method == "POST" and "/sweep/" in path:
            body = json.loads(event.get("body", "{}"))
            response = sweep_schema(schema_name, body)
        elif http_method == "GET" and "/evaluations/" in path:
            response = get_evaluations(database_id, asset_id)
        elif http_method == "GET" and "/state/" in path and database_id and not asset_id:
            response = get_database_compliance_overview(database_id)
        elif http_method == "GET" and "/state/" in path:
            response = get_compliance_state(database_id, asset_id)
        else:
            response["statusCode"] = 405
            response["body"] = json.dumps({"message": "Method not allowed"})

    except Exception as e:
        logger.exception("Unhandled error in Compliance Evaluate Service")
        response = internal_error(body={"message": str(e)})

    return response


def evaluate_asset(database_id, asset_id, body):
    """Trigger compliance evaluation for an asset.

    For vams-rules-v1 schemas, runs the evaluation engine synchronously
    (metadata + relationship rules) and returns the result. Pipeline rules
    are initiated asynchronously via workflow execution.

    For legacy JSON Schema formats, creates a pending evaluation record.
    """
    if not database_id or not asset_id:
        return validation_error(body={"message": "databaseId and assetId are required"})

    obj = {
        "object__type": "complianceEvaluation",
        "databaseId": database_id,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    asset_response = asset_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    if "Item" not in asset_response:
        return validation_error(body={"message": f"Asset {database_id}:{asset_id} not found"})

    compliance_state = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    current_state = compliance_state.get("Item", {})
    schema_name = body.get("schemaName") or current_state.get("schemaName")

    if not schema_name:
        return validation_error(body={
            "message": "No schema specified and asset has no registered schema"
        })

    actor = claims_and_roles.get("sub", "system")

    schema_body = _load_schema_body_for_check(schema_name)
    if schema_body and schema_body.get("schemaFormat") == "vams-rules-v1":
        result = run_evaluation(database_id, asset_id, schema_name, actor)
        check_and_trigger_cascade(database_id, asset_id)
        return success(body={
            "message": "Evaluation completed",
            "evaluationId": result.get("evaluationId"),
            "schemaName": schema_name,
            "verdict": result.get("verdict"),
            "complianceState": result.get("complianceState"),
            "ruleResults": result.get("ruleResults", []),
            "pipelineRulesPending": result.get("pipelineRulesPending", 0),
        })

    now = datetime.now(timezone.utc).isoformat()
    evaluation_id = str(uuid.uuid4())

    evaluation_item = {
        "evaluationId": evaluation_id,
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": schema_name,
        "evaluatedAt": now,
        "actor": actor,
        "status": "pending",
        "datasetPath": body.get("datasetPath", f"s3://{database_id}/{asset_id}"),
    }
    evaluation_table.put_item(Item=evaluation_item)

    existing_source = current_state.get("schemaSource", "database")
    compliance_table.update_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        UpdateExpression=(
            "SET schemaName = :schema, "
            "complianceState = :state, "
            "lastEvaluationId = :evalId, "
            "lastEvaluationAt = :now, "
            "updatedAt = :now, "
            "schemaSource = if_not_exists(schemaSource, :source)"
        ),
        ExpressionAttributeValues={
            ":schema": schema_name,
            ":state": "pending_evaluation",
            ":evalId": evaluation_id,
            ":now": now,
            ":source": existing_source,
        },
    )

    write_audit(
        database_id=database_id,
        asset_id=asset_id,
        event_type="compliance_check",
        actor=actor,
        schema_name=schema_name,
        evaluation_id=evaluation_id,
        details={"status": "pending", "trigger": "api"},
    )

    return success(body={
        "message": "Evaluation triggered",
        "evaluationId": evaluation_id,
        "schemaName": schema_name,
    })


def _load_schema_body_for_check(schema_name):
    """Load schema body to determine format for routing."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None
    body = items[0].get("schemaBody", "{}")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None
    return body


def sweep_schema(schema_name, body):
    """Trigger evaluation for all assets governed by a schema."""
    if not schema_name:
        return validation_error(body={"message": "schemaName is required"})

    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": schema_name,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    response = compliance_table.query(
        IndexName="SchemaNameIndex",
        KeyConditionExpression=Key("schemaName").eq(schema_name),
    )
    assets = response.get("Items", [])

    triggered = []
    for asset in assets:
        result = evaluate_asset(
            asset["databaseId"],
            asset["assetId"],
            {"schemaName": schema_name},
        )
        triggered.append({
            "databaseId": asset["databaseId"],
            "assetId": asset["assetId"],
        })

    return success(body={
        "message": f"Sweep triggered for {len(triggered)} assets",
        "schemaName": schema_name,
        "assetsTriggered": triggered,
    })


def get_evaluations(database_id, asset_id):
    """Get evaluation history for an asset."""
    obj = {
        "object__type": "complianceEvaluation",
        "databaseId": database_id,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "GET"):
            return authorization_error()

    response = evaluation_table.query(
        IndexName="AssetIndex",
        KeyConditionExpression=Key("databaseId:assetId").eq(
            f"{database_id}:{asset_id}"
        ),
        ScanIndexForward=False,
        Limit=50,
    )
    return success(body={"evaluations": response.get("Items", [])})


def get_compliance_state(database_id, asset_id):
    """Get current compliance state for an asset."""
    obj = {
        "object__type": "complianceEvaluation",
        "databaseId": database_id,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "GET"):
            return authorization_error()

    response = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = response.get("Item")
    if not item:
        return success(body={
            "databaseId": database_id,
            "assetId": asset_id,
            "complianceState": "unknown",
            "schemaName": None,
            "schemaSource": None,
        })
    return success(body=item)


def _batch_get_asset_names(database_id, asset_ids):
    """Look up asset names for a list of asset IDs. Returns {assetId: assetName}."""
    names = {}
    if not asset_ids:
        return names

    for asset_id in asset_ids:
        try:
            resp = asset_table.get_item(
                Key={"databaseId": database_id, "assetId": asset_id},
                ProjectionExpression="assetId, assetName",
            )
            item = resp.get("Item")
            if item:
                names[asset_id] = item.get("assetName", "")
        except Exception:
            pass

    return names


def get_database_compliance_overview(database_id):
    """Get compliance overview for all assets in a database."""
    if not database_id:
        return validation_error(body={"message": "databaseId is required"})

    obj = {
        "object__type": "complianceEvaluation",
        "databaseId": database_id,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "GET"):
            return authorization_error()

    response = compliance_table.query(
        KeyConditionExpression=Key("databaseId").eq(database_id),
    )
    items = response.get("Items", [])

    asset_ids = [item["assetId"] for item in items if "assetId" in item]
    asset_names = _batch_get_asset_names(database_id, asset_ids)

    summary = {
        "compliant": 0,
        "non_compliant": 0,
        "pending_evaluation": 0,
        "quarantined": 0,
        "unknown": 0,
    }
    for item in items:
        state = item.get("complianceState", "unknown")
        if state in summary:
            summary[state] += 1
        else:
            summary["unknown"] += 1
        item["assetName"] = asset_names.get(item.get("assetId"), "")

    return success(body={
        "databaseId": database_id,
        "totalAssets": len(items),
        "summary": summary,
        "assets": items,
    })


def write_audit(database_id, asset_id, event_type, actor, schema_name=None,
                evaluation_id=None, cascade_id=None, details=None):
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
            "schemaName": schema_name,
            "evaluationId": evaluation_id,
            "cascadeId": cascade_id,
            "details": json.dumps(details or {}),
        }
    )
