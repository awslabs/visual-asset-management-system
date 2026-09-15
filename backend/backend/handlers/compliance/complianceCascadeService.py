#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Cascade Service handler.

Manages cascade execution operations:
- POST /compliance/cascades — Create a new cascade
- POST /compliance/cascades/{cascadeId}/approve — Approve cascade
- POST /compliance/cascades/{cascadeId}/reject — Reject cascade
- GET /compliance/cascades/{cascadeId} — Get cascade status
- GET /compliance/cascades — List pending cascades
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import (
    APIGatewayProxyResponseV2,
    authorization_error,
    internal_error,
    success,
    validation_error,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceCascadeService")

claims_and_roles = {}

try:
    cascade_table_name = os.environ["COMPLIANCE_CASCADE_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["COMPLIANCE_AUDIT_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

cascade_table = dynamodb.Table(cascade_table_name)
audit_table = dynamodb.Table(audit_table_name)

CASCADE_APPROVAL_TIMEOUT_HOURS = 24


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
        cascade_id = path_params.get("cascadeId")

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if http_method == "POST" and "/approve" in path:
            body = json.loads(event.get("body", "{}"))
            response = approve_cascade(cascade_id, body)
        elif http_method == "POST" and "/reject" in path:
            body = json.loads(event.get("body", "{}"))
            response = reject_cascade(cascade_id, body)
        elif http_method == "POST" and not cascade_id:
            body = json.loads(event.get("body", "{}"))
            response = create_cascade(body)
        elif http_method == "GET" and cascade_id:
            response = get_cascade(cascade_id)
        elif http_method == "GET":
            response = list_pending_cascades()
        else:
            response["statusCode"] = 405
            response["body"] = json.dumps({"message": "Method not allowed"})

    except Exception as e:
        logger.exception("Unhandled error in Compliance Cascade Service")
        response = internal_error(body={"message": str(e)})

    return response


def create_cascade(body):
    """Create a new cascade execution."""
    triggered_by_db = body.get("databaseId")
    triggered_by_asset = body.get("assetId")
    trigger_reason = body.get("reason", "manual trigger")
    require_approval = body.get("requireApproval", True)

    if not triggered_by_db or not triggered_by_asset:
        return validation_error(body={"message": "databaseId and assetId are required"})

    obj = {
        "object__type": "complianceCascade",
        "cascadeId": "",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    actor = claims_and_roles.get("sub", "system")
    now = datetime.now(timezone.utc)
    cascade_id = str(uuid.uuid4())

    state = "pending_approval" if require_approval else "executing"
    timeout_at = (now + timedelta(hours=CASCADE_APPROVAL_TIMEOUT_HOURS)).isoformat()

    item = {
        "cascadeId": cascade_id,
        "state": state,
        "triggeredByDatabaseId": triggered_by_db,
        "triggeredByAssetId": triggered_by_asset,
        "triggerReason": trigger_reason,
        "createdAt": now.isoformat(),
        "actor": actor,
        "requireApproval": require_approval,
        "approvalTimeoutAt": timeout_at if require_approval else None,
        "nodes": json.dumps(body.get("nodes", {})),
        "executionOrder": json.dumps(body.get("executionOrder", [])),
    }
    cascade_table.put_item(Item=item)

    write_audit(
        triggered_by_db, triggered_by_asset,
        event_type="cascade_triggered",
        actor=actor,
        cascade_id=cascade_id,
        details={"reason": trigger_reason, "requireApproval": require_approval},
    )

    return success(body={
        "message": "Cascade created",
        "cascadeId": cascade_id,
        "state": state,
    })


def approve_cascade(cascade_id, body):
    """Approve a pending cascade and begin execution."""
    if not cascade_id:
        return validation_error(body={"message": "cascadeId is required"})

    obj = {
        "object__type": "complianceCascade",
        "cascadeId": cascade_id,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    actor = claims_and_roles.get("sub", "system")
    reason = body.get("reason", "approved")
    now = datetime.now(timezone.utc).isoformat()

    try:
        cascade_table.update_item(
            Key={"cascadeId": cascade_id},
            UpdateExpression=(
                "SET #s = :state, approvedBy = :actor, "
                "approvedAt = :now, approvalReason = :reason"
            ),
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":state": "executing",
                ":actor": actor,
                ":now": now,
                ":reason": reason,
                ":pending": "pending_approval",
            },
            ConditionExpression="attribute_exists(cascadeId) AND #s = :pending",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return validation_error(
            body={"message": "Cascade not found or not in pending_approval state"}
        )

    cascade = cascade_table.get_item(
        Key={"cascadeId": cascade_id}
    ).get("Item")
    if cascade:
        write_audit(
            cascade.get("triggeredByDatabaseId", ""),
            cascade.get("triggeredByAssetId", ""),
            event_type="cascade_approved",
            actor=actor,
            cascade_id=cascade_id,
            details={"reason": reason},
        )

    from handlers.compliance.complianceCascadeExecutor import execute_cascade

    result = execute_cascade(cascade_id)
    logger.info(f"Cascade {cascade_id} execution completed: {result}")

    return success(body={
        "message": "Cascade approved and executed",
        "cascadeId": cascade_id,
        "result": result,
    })


def reject_cascade(cascade_id, body):
    """Reject a pending cascade."""
    if not cascade_id:
        return validation_error(body={"message": "cascadeId is required"})

    obj = {
        "object__type": "complianceCascade",
        "cascadeId": cascade_id,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    actor = claims_and_roles.get("sub", "system")
    reason = body.get("reason", "rejected")
    now = datetime.now(timezone.utc).isoformat()

    cascade_table.update_item(
        Key={"cascadeId": cascade_id},
        UpdateExpression=(
            "SET #s = :state, rejectedBy = :actor, "
            "rejectedAt = :now, rejectionReason = :reason, "
            "completedAt = :now"
        ),
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={
            ":state": "aborted",
            ":actor": actor,
            ":now": now,
            ":reason": reason,
            ":pending": "pending_approval",
        },
        ConditionExpression="attribute_exists(cascadeId) AND #s = :pending",
    )

    return success(body={"message": "Cascade rejected", "cascadeId": cascade_id})


def get_cascade(cascade_id):
    """Get cascade status."""
    obj = {
        "object__type": "complianceCascade",
        "cascadeId": cascade_id,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "GET"):
            return authorization_error()

    response = cascade_table.get_item(Key={"cascadeId": cascade_id})
    item = response.get("Item")
    if not item:
        return {
            "statusCode": 404,
            "body": json.dumps({"message": f"Cascade '{cascade_id}' not found"}),
            "headers": {"Content-Type": "application/json"},
        }
    return success(body=item)


def list_pending_cascades():
    """List cascades awaiting approval."""
    obj = {
        "object__type": "complianceCascade",
        "cascadeId": "",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "GET"):
            return authorization_error()

    response = cascade_table.query(
        IndexName="StateIndex",
        KeyConditionExpression=Key("state").eq("pending_approval"),
        ScanIndexForward=False,
    )
    return success(body={"cascades": response.get("Items", [])})


def write_audit(database_id, asset_id, event_type, actor,
                cascade_id=None, details=None):
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
            "cascadeId": cascade_id,
            "details": json.dumps(details or {}),
        }
    )
