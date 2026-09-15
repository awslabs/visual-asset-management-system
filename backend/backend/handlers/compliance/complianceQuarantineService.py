#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Quarantine Service handler.

- GET  /compliance/quarantine                                   — list quarantined assets
- POST /compliance/quarantine/{databaseId}/{assetId}/release    — release an asset
- POST /compliance/quarantine/{databaseId}/{assetId}/exception  — grant an exception

Quarantined assets are the asset-state rows with complianceState "quarantined". The listing
reads them per schema through the SchemaNameIndex GSI (schemaName, complianceState); the schema
names come from the schema table.
"""

import json
from datetime import datetime, timezone

from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key

from common.apiRoutes import (
    API_COMPLIANCE_QUARANTINE,
    API_COMPLIANCE_QUARANTINE_EXCEPTION,
    API_COMPLIANCE_QUARANTINE_RELEASE,
)
from common.compliance import evaluationEngine as engine
from common.dynamodb import query_all_items
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
from models.compliance import GrantExceptionRequestModel, ReleaseQuarantineRequestModel

logger = safeLogger(service_name="ComplianceQuarantineService")

claims_and_roles = {}

COMPLIANCE_EVALUATION_OBJECT_TYPE = "complianceEvaluation"

# Tables are resolved (and clients built) once by the shared store at import.
asset_state_table = store.asset_state_table
schema_table = store.schema_table
asset_table = store.asset_table


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
    if API_COMPLIANCE_QUARANTINE.matches(path):
        return list_quarantined(event)
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_post_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_QUARANTINE_RELEASE.matches(path):
        request = parse(_parse_body(event), model=ReleaseQuarantineRequestModel)
        return release_quarantine(event, path_params.get("databaseId"),
                                  path_params.get("assetId"), request)
    if API_COMPLIANCE_QUARANTINE_EXCEPTION.matches(path):
        request = parse(_parse_body(event), model=GrantExceptionRequestModel)
        return grant_exception(event, path_params.get("databaseId"),
                               path_params.get("assetId"), request)
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

def _evaluation_object(database_id, compliance_state):
    return {
        "object__type": COMPLIANCE_EVALUATION_OBJECT_TYPE,
        "databaseId": database_id or "",
        "complianceState": compliance_state or "",
    }


def _enforce(database_id, action, compliance_state=engine.STATE_QUARANTINED):
    """Tier-2 check on a compliance evaluation object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce(
        _evaluation_object(database_id, compliance_state), action)


def _validate_database_and_asset(database_id, asset_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID"},
    })


#######################
# Business logic
#######################

def list_quarantined(event):
    """Every quarantined asset the caller may GET, with asset names.

    Access path: one SchemaNameIndex query (schemaName, complianceState = quarantined) per
    registered schema name, each paged to exhaustion.
    """
    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None

    allowed = []
    for schema_name in _schema_names():
        rows = query_all_items(
            asset_state_table,
            IndexName="SchemaNameIndex",
            KeyConditionExpression=(
                Key("schemaName").eq(schema_name)
                & Key("complianceState").eq(engine.STATE_QUARANTINED)
            ),
        )
        for item in rows:
            # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
            if casbin_enforcer and casbin_enforcer.enforce(
                    _evaluation_object(item.get("databaseId"), engine.STATE_QUARANTINED), "GET"):
                item["assetName"] = _asset_name(item.get("databaseId"), item.get("assetId"))
                allowed.append(item)

    return success(body={"quarantinedAssets": allowed})


def _schema_names():
    """Every distinct schema name. The schema table has no constant-partition index, so the
    complete set is a key-projected scan paged to exhaustion (schemas are few)."""
    names = set()
    scan_kwargs = {"ProjectionExpression": "schemaName"}
    while True:
        response = schema_table.scan(**scan_kwargs)
        names.update(item["schemaName"] for item in response.get("Items", []) if item.get("schemaName"))
        if "LastEvaluatedKey" not in response:
            return sorted(names)
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _asset_name(database_id, asset_id):
    if not database_id or not asset_id:
        return ""
    row = asset_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        ProjectionExpression="assetName",
    ).get("Item")
    return (row or {}).get("assetName", "")


def release_quarantine(event, database_id, asset_id, request: ReleaseQuarantineRequestModel):
    """Release a quarantined asset (state becomes compliant; no exception recorded)."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if not _enforce(database_id, "POST"):
        return authorization_error()

    item = store.get_compliance_record(database_id, asset_id)
    if not item:
        return general_error(body={"message": "Asset not found in compliance tracking"}, event=event)
    if item.get("complianceState") != engine.STATE_QUARANTINED:
        return general_error(body={"message": "Asset is not quarantined"}, event=event)

    actor = claims_and_roles["tokens"][0]
    reason = request.reason or "released via API"
    now = datetime.now(timezone.utc).isoformat()

    store.update_asset_state(database_id, asset_id, {
        "complianceState": engine.STATE_COMPLIANT,
        "quarantineReason": None,
        "exceptionGranted": False,
        "updatedAt": now,
    })

    store.write_audit(
        database_id, asset_id,
        event_type="quarantine_released",
        actor=actor,
        details={"reason": reason},
        previous_state=engine.STATE_QUARANTINED,
        new_state=engine.STATE_COMPLIANT,
    )

    return success(body={"message": f"Asset {database_id}:{asset_id} released from quarantine"})


def grant_exception(event, database_id, asset_id, request: GrantExceptionRequestModel):
    """Grant an exception for a quarantined asset (state becomes compliant; exception recorded)."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if not _enforce(database_id, "POST"):
        return authorization_error()

    item = store.get_compliance_record(database_id, asset_id)
    if not item:
        return general_error(body={"message": "Asset not found in compliance tracking"}, event=event)
    if item.get("complianceState") != engine.STATE_QUARANTINED:
        return general_error(body={"message": "Cannot grant exception: asset is not quarantined"},
                             event=event)

    actor = claims_and_roles["tokens"][0]
    now = datetime.now(timezone.utc).isoformat()

    store.update_asset_state(database_id, asset_id, {
        "complianceState": engine.STATE_COMPLIANT,
        "exceptionGranted": True,
        "exceptionReason": request.reason,
        "exceptionGrantedBy": actor,
        "exceptionGrantedAt": now,
        "updatedAt": now,
    })

    store.write_audit(
        database_id, asset_id,
        event_type="exception_granted",
        actor=actor,
        details={"reason": request.reason},
        previous_state=engine.STATE_QUARANTINED,
        new_state=engine.STATE_COMPLIANT,
    )

    return success(body={
        "message": f"Exception granted for {database_id}:{asset_id}",
        "reason": request.reason,
        "grantedBy": actor,
    })
