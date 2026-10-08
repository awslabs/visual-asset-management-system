#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Cascade Service handler.

- GET  /compliance/cascades                       — list cascades awaiting approval
- POST /compliance/cascades                       — create a cascade
- GET  /compliance/cascades/{cascadeId}           — get a cascade
- POST /compliance/cascades/{cascadeId}/approve   — approve a cascade and start it
- POST /compliance/cascades/{cascadeId}/reject    — reject a cascade

Cascade table (PK cascadeId; GSI StateIndex on state/createdAt).

A cascade runs in the executor Lambda (complianceCascadeExecutor), invoked asynchronously once
the row is in the `executing` state; the request returns 202 and the client observes completion
through GET /compliance/cascades/{cascadeId} (`state` executing -> completed | aborted).
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.apiRoutes import (
    API_COMPLIANCE_CASCADE_APPROVE,
    API_COMPLIANCE_CASCADE_BY_ID,
    API_COMPLIANCE_CASCADE_REJECT,
    API_COMPLIANCE_CASCADES,
)
from common.dynamodb import query_all_items
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from handlers.compliance import complianceEvaluationStore as store
from models.common import (
    APIGatewayProxyResponseV2,
    VAMSGeneralErrorResponse,
    authorization_error,
    general_error,
    internal_error,
    success,
    validation_error,
    validation_error_message,
)
from models.compliance import (
    ApproveCascadeRequestModel,
    CreateCascadeRequestModel,
    RejectCascadeRequestModel,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)
logger = safeLogger(service_name="ComplianceCascadeService")

claims_and_roles = {}

COMPLIANCE_CASCADE_OBJECT_TYPE = "complianceCascade"
COMPLIANCE_EVALUATION_OBJECT_TYPE = "complianceEvaluation"

CASCADE_STATE_PENDING_APPROVAL = "pending_approval"
CASCADE_STATE_EXECUTING = "executing"
CASCADE_STATE_ABORTED = "aborted"

# A cascade awaiting approval expires after this many hours.
CASCADE_APPROVAL_TIMEOUT_HOURS = 24

# Message returned when the executor Lambda could not be invoked; the row is aborted with the reason.
CASCADE_START_FAILED_MESSAGE = "Cascade could not be started"
CASCADE_START_FAILED_ABORT_REASON = "Cascade executor could not be invoked"

try:
    cascade_table_name = get_table_name(ResourceKeys.COMPLIANCE_CASCADE_STORAGE_TABLE)
    # The executor Lambda that runs a cascade; set on the cascade service Lambda only.
    cascade_executor_function_name = os.environ.get("COMPLIANCE_CASCADE_EXECUTOR_FUNCTION_NAME", "")
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

cascade_table = dynamodb.Table(cascade_table_name)


#######################
# Lambda handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        method = event["requestContext"]["http"]["method"]

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if method == "GET":
            return handle_get_request(event)
        elif method == "POST":
            return handle_post_request(event)
        else:
            return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)


#######################
# Method handlers
#######################

def handle_get_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_CASCADE_BY_ID.matches(path):
        return get_cascade(event, path_params.get("cascadeId"))
    if API_COMPLIANCE_CASCADES.matches(path):
        return list_pending_cascades(event)
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_post_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_CASCADE_APPROVE.matches(path):
        request = parse(_parse_body(event), model=ApproveCascadeRequestModel)
        return approve_cascade(event, path_params.get("cascadeId"), request)
    if API_COMPLIANCE_CASCADE_REJECT.matches(path):
        request = parse(_parse_body(event), model=RejectCascadeRequestModel)
        return reject_cascade(event, path_params.get("cascadeId"), request)
    if API_COMPLIANCE_CASCADES.matches(path):
        request = parse(_parse_body(event), model=CreateCascadeRequestModel)
        return create_cascade(event, request)
    return validation_error(body={"message": "Method not allowed"}, event=event)


def _parse_body(event):
    raw = event.get("body") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise VAMSGeneralErrorResponse("Invalid JSON in request body")


#######################
# Authorization helpers
#######################

def _cascade_object(cascade_id):
    return {"object__type": COMPLIANCE_CASCADE_OBJECT_TYPE, "cascadeId": cascade_id or ""}


def _enforce_cascade(cascade_id, action):
    """Tier-2 check on a cascade object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce(_cascade_object(cascade_id), action)


def _evaluation_object(database_id, compliance_state=""):
    return {
        "object__type": COMPLIANCE_EVALUATION_OBJECT_TYPE,
        "databaseId": database_id,
        "complianceState": compliance_state or "",
    }


def _enforce_evaluation(database_id, action, compliance_state=""):
    """Tier-2 check on the compliance evaluation object of the cascade's trigger database: a cascade
    evaluates that database's downstream assets, so the caller must be allowed to evaluate there.
    Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce(
        _evaluation_object(database_id, compliance_state), action)


def _validate_cascade_id(cascade_id):
    return validate({"cascadeId": {"value": cascade_id, "validator": "UUID"}})


#######################
# Executor hand-off
#######################

def _start_cascade(cascade_id):
    """Hand an `executing` cascade to the executor Lambda; the invocation returns before it runs."""
    lambda_client.invoke(
        FunctionName=cascade_executor_function_name,
        InvocationType="Event",
        Payload=json.dumps({"cascadeId": cascade_id}),
    )


def _abort_cascade(cascade_id, reason):
    """Mark a still-executing cascade aborted with the reason; the terminal state clients read back.
    A row the executor already moved to a terminal state is left as it is."""
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


def _start_or_abort(event, cascade_id, started_body):
    """Invoke the executor for a cascade already in the `executing` state. A failed invocation
    leaves no cascade `executing` with nothing running it: the row is aborted and the caller told."""
    try:
        _start_cascade(cascade_id)
    except Exception as e:
        logger.exception(f"Cascade {cascade_id} executor invocation failed: {e}")
        _abort_cascade(cascade_id, CASCADE_START_FAILED_ABORT_REASON)
        return general_error(body={"message": CASCADE_START_FAILED_MESSAGE}, event=event)
    return success(status_code=202, body=started_body)


#######################
# Business logic
#######################

def create_cascade(event, request: CreateCascadeRequestModel):
    """Create a cascade for the downstream assets of `databaseId:assetId`."""
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    cascade_id = str(uuid.uuid4())
    if not _enforce_cascade(cascade_id, "POST"):
        return authorization_error()
    if not _enforce_evaluation(request.databaseId, "POST"):
        return authorization_error()

    if not store.get_asset_item(request.databaseId, request.assetId):
        return general_error(body={"message": "Asset not found"}, event=event)

    actor = claims_and_roles["tokens"][0]
    now = datetime.now(timezone.utc)
    reason = request.reason or "manual trigger"
    state = CASCADE_STATE_PENDING_APPROVAL if request.requireApproval else CASCADE_STATE_EXECUTING

    item = {
        "cascadeId": cascade_id,
        "state": state,
        "triggeredByDatabaseId": request.databaseId,
        "triggeredByAssetId": request.assetId,
        "triggerReason": reason,
        "createdAt": now.isoformat(),
        "actor": actor,
        "requireApproval": request.requireApproval,
        "nodes": json.dumps({}),
        "executionOrder": json.dumps([]),
    }
    if request.requireApproval:
        item["approvalTimeoutAt"] = (
            now + timedelta(hours=CASCADE_APPROVAL_TIMEOUT_HOURS)).isoformat()
    cascade_table.put_item(Item=item)

    store.write_audit(
        request.databaseId, request.assetId,
        event_type="cascade_triggered",
        actor=actor,
        cascade_id=cascade_id,
        details={"reason": reason, "requireApproval": request.requireApproval},
    )

    body = {"message": "Cascade created", "cascadeId": cascade_id, "state": state}
    if request.requireApproval:
        return success(body=body)
    return _start_or_abort(event, cascade_id, body)


def approve_cascade(event, cascade_id, request: ApproveCascadeRequestModel):
    """Approve a pending cascade and start it."""
    (valid, message) = _validate_cascade_id(cascade_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_cascade(cascade_id, "POST"):
        return authorization_error()

    cascade = cascade_table.get_item(Key={"cascadeId": cascade_id}).get("Item")
    if not cascade:
        return general_error(
            body={"message": "Cascade not found or not in pending_approval state"}, event=event)
    if not _enforce_evaluation(cascade.get("triggeredByDatabaseId", ""), "POST"):
        return authorization_error()

    actor = claims_and_roles["tokens"][0]
    reason = request.reason or "approved"
    now = datetime.now(timezone.utc).isoformat()

    try:
        cascade_table.update_item(
            Key={"cascadeId": cascade_id},
            UpdateExpression=(
                "SET #s = :state, approvedBy = :actor, approvedAt = :now, approvalReason = :reason"
            ),
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":state": CASCADE_STATE_EXECUTING,
                ":actor": actor,
                ":now": now,
                ":reason": reason,
                ":pending": CASCADE_STATE_PENDING_APPROVAL,
            },
            ConditionExpression="attribute_exists(cascadeId) AND #s = :pending",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return general_error(
            body={"message": "Cascade not found or not in pending_approval state"}, event=event)

    store.write_audit(
        cascade.get("triggeredByDatabaseId", ""),
        cascade.get("triggeredByAssetId", ""),
        event_type="cascade_approved",
        actor=actor,
        cascade_id=cascade_id,
        details={"reason": reason},
    )

    return _start_or_abort(event, cascade_id, {
        "message": "Cascade approved",
        "cascadeId": cascade_id,
        "state": CASCADE_STATE_EXECUTING,
    })


def reject_cascade(event, cascade_id, request: RejectCascadeRequestModel):
    """Reject a pending cascade."""
    (valid, message) = _validate_cascade_id(cascade_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_cascade(cascade_id, "POST"):
        return authorization_error()

    cascade = cascade_table.get_item(Key={"cascadeId": cascade_id}).get("Item")
    if not cascade:
        return general_error(
            body={"message": "Cascade not found or not in pending_approval state"}, event=event)
    if not _enforce_evaluation(cascade.get("triggeredByDatabaseId", ""), "POST"):
        return authorization_error()

    actor = claims_and_roles["tokens"][0]
    reason = request.reason or "rejected"
    now = datetime.now(timezone.utc).isoformat()

    try:
        cascade_table.update_item(
            Key={"cascadeId": cascade_id},
            UpdateExpression=(
                "SET #s = :state, rejectedBy = :actor, rejectedAt = :now, "
                "rejectionReason = :reason, completedAt = :now"
            ),
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":state": CASCADE_STATE_ABORTED,
                ":actor": actor,
                ":now": now,
                ":reason": reason,
                ":pending": CASCADE_STATE_PENDING_APPROVAL,
            },
            ConditionExpression="attribute_exists(cascadeId) AND #s = :pending",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return general_error(
            body={"message": "Cascade not found or not in pending_approval state"}, event=event)

    store.write_audit(
        cascade.get("triggeredByDatabaseId", ""),
        cascade.get("triggeredByAssetId", ""),
        event_type="cascade_rejected",
        actor=actor,
        cascade_id=cascade_id,
        details={"reason": reason},
    )

    return success(body={"message": "Cascade rejected", "cascadeId": cascade_id})


def get_cascade(event, cascade_id):
    """One cascade."""
    (valid, message) = _validate_cascade_id(cascade_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_cascade(cascade_id, "GET"):
        return authorization_error()

    item = cascade_table.get_item(Key={"cascadeId": cascade_id}).get("Item")
    if not item:
        return general_error(body={"message": "Cascade not found"}, event=event)
    if not _enforce_evaluation(item.get("triggeredByDatabaseId", ""), "GET"):
        return authorization_error()
    return success(body=item)


def list_pending_cascades(event):
    """Every cascade awaiting approval the caller may GET (StateIndex GSI, newest first).

    A row is listed only when the caller may GET both the cascade object and the compliance
    evaluation object of the trigger asset's database — the same pair every single-cascade route
    enforces. Each row carries `databaseId` / `assetId` (the trigger asset) beside the
    `triggeredBy*` attributes it is stored with.
    """
    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None

    rows = query_all_items(
        cascade_table,
        IndexName="StateIndex",
        KeyConditionExpression=Key("state").eq(CASCADE_STATE_PENDING_APPROVAL),
        ScanIndexForward=False,
    )
    allowed = []
    for item in rows:
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        if casbin_enforcer and casbin_enforcer.enforce(_cascade_object(item.get("cascadeId")), "GET") \
                and casbin_enforcer.enforce(
                    _evaluation_object(item.get("triggeredByDatabaseId", "")), "GET"):
            allowed.append(_cascade_row(item))
    return success(body={"cascades": allowed})


def _cascade_row(item):
    """A cascade row for the listing: the stored attributes plus `databaseId` / `assetId` naming the
    trigger asset."""
    return {
        **item,
        "databaseId": item.get("triggeredByDatabaseId", ""),
        "assetId": item.get("triggeredByAssetId", ""),
    }
