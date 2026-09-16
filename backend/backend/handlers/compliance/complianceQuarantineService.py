#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Quarantine Service handler.

- GET    /compliance/quarantine                                   — list quarantined assets (paged)
- POST   /compliance/quarantine/{databaseId}/{assetId}/release    — release an asset
- POST   /compliance/quarantine/{databaseId}/{assetId}/exception  — grant an exception
- DELETE /compliance/quarantine/{databaseId}/{assetId}/exception  — revoke an exception

Quarantined assets are the asset-state rows with complianceState "quarantined". The listing
reads one page of them through the ComplianceStateIndex GSI (complianceState, databaseId) and
returns a Base64 NextToken wrapping the index's LastEvaluatedKey while more rows remain.

An exception moves a quarantined asset to the "exception" state and is scoped to the schema name
and version it was granted against (`exceptionSchemaName`, `exceptionSchemaVersion`): the
evaluation store keeps the asset out of quarantine while both still match and clears the
exception once a different schema or a newer version is evaluated. Revoking clears the exception
fields and returns the asset to the state of its last evaluation.
"""

import base64
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
    EvaluationVerdict,
    GrantExceptionRequestModel,
    ReleaseQuarantineRequestModel,
)

logger = safeLogger(service_name="ComplianceQuarantineService")

claims_and_roles = {}

COMPLIANCE_EVALUATION_OBJECT_TYPE = "complianceEvaluation"

# Bound on the quarantine reason kept on the asset-state row (the failed rules' messages).
MAX_QUARANTINE_REASON_LENGTH = 1024
DEFAULT_QUARANTINE_REASON = "Compliance evaluation failed"

# The GSI the listing pages: PK complianceState, SK databaseId, projection ALL. A page token is the
# index's LastEvaluatedKey, which carries the index keys plus the table keys.
COMPLIANCE_STATE_INDEX = "ComplianceStateIndex"
COMPLIANCE_STATE_INDEX_KEY_ATTRIBUTES = ("complianceState", "databaseId", "assetId")

# Page bounds for the quarantine listing. The Casbin list filter applies to the page after the
# read, so an authz-filtered page may be empty while NextToken is present.
DEFAULT_LIST_PAGE_SIZE = 100
MAX_LIST_PAGE_SIZE = 500
INVALID_PAGINATION_TOKEN_MESSAGE = "Invalid pagination token"

# Tables are resolved (and clients built) once by the shared store at import.
asset_state_table = store.asset_state_table
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
        elif method == "DELETE":
            return handle_delete_request(event)
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
    query_params = event.get("queryStringParameters", {}) or {}
    if API_COMPLIANCE_QUARANTINE.matches(path):
        return list_quarantined(event, query_params)
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


def handle_delete_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_QUARANTINE_EXCEPTION.matches(path):
        return revoke_exception(event, path_params.get("databaseId"), path_params.get("assetId"))
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
# Paging helpers
#######################

def _page_arguments(event, query_params):
    """(page_size, exclusive_start_key, None) from `maxItems` / `startingToken`, or
    (None, None, response) when either is malformed."""
    try:
        page_size = int(query_params.get("maxItems", str(DEFAULT_LIST_PAGE_SIZE)))
    except (TypeError, ValueError):
        return None, None, validation_error(body={"message": "maxItems must be an integer"}, event=event)
    page_size = max(1, min(page_size, MAX_LIST_PAGE_SIZE))

    exclusive_start_key = None
    starting_token = query_params.get("startingToken")
    if starting_token:
        exclusive_start_key = _decode_key_token(starting_token)
        if exclusive_start_key is None:
            return None, None, validation_error(
                body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)
    return page_size, exclusive_start_key, None


def _decode_key_token(token):
    """The ComplianceStateIndex key a token carries, or None when the token is malformed — not
    Base64 JSON, not an object, or missing one of the index's key attributes (a key from another
    listing would otherwise reach DynamoDB and fail the request as an internal error)."""
    try:
        decoded = json.loads(base64.b64decode(token, validate=True).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    if any(not isinstance(decoded.get(name), str) for name in COMPLIANCE_STATE_INDEX_KEY_ATTRIBUTES):
        return None
    return {name: decoded[name] for name in COMPLIANCE_STATE_INDEX_KEY_ATTRIBUTES}


def _encode_key_token(last_evaluated_key):
    return base64.b64encode(json.dumps(last_evaluated_key, default=str).encode("utf-8")).decode("utf-8")


#######################
# Business logic
#######################

def list_quarantined(event, query_params):
    """One page of quarantined assets, filtered to those the caller may GET, with asset names.

    Access path: one ComplianceStateIndex query (complianceState = quarantined) bounded by
    `maxItems` and resumed from `startingToken`; `NextToken` is present while more rows remain.
    """
    page_size, exclusive_start_key, error = _page_arguments(event, query_params)
    if error:
        return error

    query_kwargs = {
        "IndexName": COMPLIANCE_STATE_INDEX,
        "KeyConditionExpression": Key("complianceState").eq(engine.STATE_QUARANTINED),
        "Limit": page_size,
    }
    if exclusive_start_key:
        query_kwargs["ExclusiveStartKey"] = exclusive_start_key
    response = asset_state_table.query(**query_kwargs)

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    allowed = []
    for item in response.get("Items", []):
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        if casbin_enforcer and casbin_enforcer.enforce(
                _evaluation_object(item.get("databaseId"), engine.STATE_QUARANTINED), "GET"):
            item["assetName"] = _asset_name(item.get("databaseId"), item.get("assetId"))
            allowed.append(item)

    result = {"quarantinedAssets": allowed}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = _encode_key_token(response["LastEvaluatedKey"])
    return success(body=result)


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

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
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
    """Grant an exception for a quarantined asset: the state becomes `exception`, the quarantine
    reason is cleared, and the grant is scoped to the asset's bound schema at its current version."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
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
    schema_name = item.get("schemaName") or None
    schema_version = _current_schema_version(schema_name)

    store.update_asset_state(database_id, asset_id, {
        "complianceState": engine.STATE_EXCEPTION,
        "exceptionGranted": True,
        "exceptionReason": request.reason,
        "exceptionGrantedBy": actor,
        "exceptionGrantedAt": now,
        "exceptionSchemaName": schema_name,
        "exceptionSchemaVersion": schema_version,
        "quarantineReason": None,
        "updatedAt": now,
    })

    store.write_audit(
        database_id, asset_id,
        event_type="exception_granted",
        actor=actor,
        schema_name=schema_name,
        details={"reason": request.reason, "schemaVersion": schema_version},
        previous_state=engine.STATE_QUARANTINED,
        new_state=engine.STATE_EXCEPTION,
    )

    return success(body={
        "message": f"Exception granted for {database_id}:{asset_id}",
        "reason": request.reason,
        "grantedBy": actor,
        "complianceState": engine.STATE_EXCEPTION,
    })


def revoke_exception(event, database_id, asset_id):
    """Revoke an active exception. The exception fields are cleared and the asset returns to the
    state its last evaluation's verdict maps to — re-quarantined, with the quarantine reason and
    the subscriber notification, when that verdict was quarantined — or to `pending_evaluation`
    when the asset has no recorded evaluation."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce(database_id, "DELETE", compliance_state=engine.STATE_EXCEPTION):
        return authorization_error()

    item = store.get_compliance_record(database_id, asset_id)
    if not item:
        return general_error(body={"message": "Asset not found in compliance tracking"}, event=event)
    if item.get("exceptionGranted") is not True:
        return general_error(body={"message": "No active exception"}, event=event)

    actor = claims_and_roles["tokens"][0]
    now = datetime.now(timezone.utc).isoformat()
    previous_state = item.get("complianceState")
    last_evaluation_id = item.get("lastEvaluationId") or None
    evaluation = store.get_evaluation(last_evaluation_id) if last_evaluation_id else None
    if evaluation is not None and evaluation.get("verdict") not in store.VERDICT_BEARING:
        # An error or still-pending last evaluation decides no state; fall back to the newest one that did.
        evaluation = store.latest_verdict_evaluation(database_id, asset_id)

    if evaluation:
        new_state = engine.verdict_to_state(_verdict_of(evaluation))
        rule_results = engine.rule_results_from_json(evaluation.get("ruleResults", "[]"))
    else:
        new_state = engine.STATE_PENDING_EVALUATION
        rule_results = []
    schema_name = (evaluation or {}).get("schemaName") or item.get("schemaName") or ""

    updates = {
        "complianceState": new_state,
        "quarantineReason": None,
        "updatedAt": now,
        **engine.cleared_exception_fields(),
    }
    if new_state == engine.STATE_QUARANTINED:
        updates["quarantineReason"] = _quarantine_reason(rule_results)
    store.update_asset_state(database_id, asset_id, updates)

    store.write_audit(
        database_id, asset_id,
        event_type="exception_revoked",
        actor=actor,
        schema_name=item.get("exceptionSchemaName") or None,
        evaluation_id=last_evaluation_id,
        details={"exceptionSchemaVersion": engine.schema_version_number(
            item.get("exceptionSchemaVersion"))},
        previous_state=previous_state,
        new_state=new_state,
    )

    if new_state == engine.STATE_QUARANTINED:
        _notify_quarantine(database_id, asset_id, schema_name,
                           engine.failed_rule_names(rule_results))

    return success(body={
        "message": "Exception revoked",
        "databaseId": database_id,
        "assetId": asset_id,
        "complianceState": new_state,
    })


def _current_schema_version(schema_name):
    """The current `internalVersion` of a schema, or None when the asset has no bound schema or the
    schema no longer exists."""
    if not schema_name:
        return None
    latest = store.load_schema_item(schema_name)
    if not latest:
        return None
    return engine.schema_version_number(latest.get("internalVersion", 1))


def _verdict_of(evaluation):
    """The verdict recorded on an evaluation row; an unrecognized value reads as `error`."""
    try:
        return EvaluationVerdict(evaluation.get("verdict"))
    except ValueError:
        return EvaluationVerdict.error


def _quarantine_reason(rule_results):
    """The failed rules' messages kept on the asset-state row while the asset is quarantined."""
    reason = "; ".join(engine.violations(rule_results)) or DEFAULT_QUARANTINE_REASON
    return reason[:MAX_QUARANTINE_REASON_LENGTH]


def _notify_quarantine(database_id, asset_id, schema_name, failed_rules):
    """Tell the asset's subscribers about a quarantine; a notification failure is logged and does not
    undo the state change it reports."""
    try:
        from handlers.compliance.complianceNotifications import notify_quarantine
        notify_quarantine(database_id, asset_id, schema_name, failed_rules)
    except Exception as e:
        logger.exception(f"Failed sending quarantine notification: {e}")
