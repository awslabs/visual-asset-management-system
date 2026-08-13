"""FMM Quarantine Service handler.

Manages quarantine operations:
- GET /compliance/quarantine — List quarantined assets
- POST /compliance/quarantine/{databaseId}/{assetId}/release — Release
- POST /compliance/quarantine/{databaseId}/{assetId}/exception — Grant exception
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr
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
logger = safeLogger(service_name="FMMQuarantineService")

claims_and_roles = {}

try:
    compliance_table_name = os.environ["FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["FMM_AUDIT_STORAGE_TABLE_NAME"]
    asset_links_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

compliance_table = dynamodb.Table(compliance_table_name)
audit_table = dynamodb.Table(audit_table_name)
asset_links_table = dynamodb.Table(asset_links_table_name)
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

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if http_method == "GET" and "/quarantine" in path and not database_id:
            response = list_quarantined()
        elif http_method == "POST" and "/release" in path:
            body = json.loads(event.get("body", "{}"))
            response = release_quarantine(database_id, asset_id, body)
        elif http_method == "POST" and "/exception" in path:
            body = json.loads(event.get("body", "{}"))
            response = grant_exception(database_id, asset_id, body)
        else:
            response["statusCode"] = 405
            response["body"] = json.dumps({"message": "Method not allowed"})

    except Exception as e:
        logger.exception("Unhandled error in FMM Quarantine Service")
        response = internal_error(body={"message": str(e)})

    return response


def list_quarantined():
    """List all quarantined assets."""
    obj = {
        "object__type": "complianceEvaluation",
        "complianceState": "quarantined",
        "databaseId": "",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "GET"):
            return authorization_error()

    response = compliance_table.scan(
        FilterExpression=Attr("complianceState").eq("quarantined"),
    )
    items = response.get("Items", [])

    for item in items:
        try:
            resp = asset_table.get_item(
                Key={"databaseId": item["databaseId"], "assetId": item["assetId"]},
                ProjectionExpression="assetName",
            )
            asset = resp.get("Item")
            if asset:
                item["assetName"] = asset.get("assetName", "")
        except Exception:
            pass

    return success(body={"quarantinedAssets": items})


def release_quarantine(database_id, asset_id, body):
    """Release an asset from quarantine."""
    if not database_id or not asset_id:
        return validation_error(body={"message": "databaseId and assetId are required"})

    obj = {
        "object__type": "complianceEvaluation",
        "databaseId": database_id,
        "complianceState": "quarantined",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    actor = claims_and_roles.get("sub", "system")
    reason = body.get("reason", "released via API")
    now = datetime.now(timezone.utc).isoformat()

    current = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = current.get("Item")
    if not item:
        return validation_error(body={"message": "Asset not found in compliance tracking"})

    if item.get("complianceState") != "quarantined":
        return validation_error(
            body={"message": f"Asset is not quarantined (state: {item.get('complianceState')})"}
        )

    compliance_table.update_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        UpdateExpression=(
            "SET complianceState = :state, "
            "quarantineReason = :none, "
            "exceptionGranted = :false, "
            "updatedAt = :now"
        ),
        ExpressionAttributeValues={
            ":state": "compliant",
            ":none": None,
            ":false": False,
            ":now": now,
        },
    )

    write_audit(
        database_id, asset_id,
        event_type="quarantine_released",
        actor=actor,
        details={"reason": reason},
        previous_state="quarantined",
        new_state="compliant",
    )

    return success(body={
        "message": f"Asset {database_id}:{asset_id} released from quarantine",
    })


def grant_exception(database_id, asset_id, body):
    """Grant an exception for a quarantined asset."""
    if not database_id or not asset_id:
        return validation_error(body={"message": "databaseId and assetId are required"})

    obj = {
        "object__type": "complianceEvaluation",
        "databaseId": database_id,
        "complianceState": "quarantined",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    reason = body.get("reason")
    if not reason:
        return validation_error(body={"message": "reason is required for exception grant"})

    actor = claims_and_roles.get("sub", "system")
    now = datetime.now(timezone.utc).isoformat()

    current = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = current.get("Item")
    if not item:
        return validation_error(body={"message": "Asset not found in compliance tracking"})

    if item.get("complianceState") != "quarantined":
        return validation_error(
            body={"message": (
                f"Cannot grant exception: asset is not quarantined "
                f"(state: {item.get('complianceState')})"
            )}
        )

    compliance_table.update_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        UpdateExpression=(
            "SET complianceState = :state, "
            "exceptionGranted = :true, "
            "exceptionReason = :reason, "
            "exceptionGrantedBy = :actor, "
            "exceptionGrantedAt = :now, "
            "updatedAt = :now"
        ),
        ExpressionAttributeValues={
            ":state": "compliant",
            ":true": True,
            ":reason": reason,
            ":actor": actor,
            ":now": now,
        },
    )

    write_audit(
        database_id, asset_id,
        event_type="exception_granted",
        actor=actor,
        details={"reason": reason},
        previous_state="quarantined",
        new_state="compliant",
    )

    return success(body={
        "message": f"Exception granted for {database_id}:{asset_id}",
        "reason": reason,
        "grantedBy": actor,
    })


def write_audit(database_id, asset_id, event_type, actor,
                details=None, previous_state=None, new_state=None):
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
            "previousState": previous_state,
            "newState": new_state,
        }
    )
