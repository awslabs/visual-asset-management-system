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
from common.compliance import listing
from common.compliance.archivedAssets import ArchivedAssets
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
MAX_LIST_PAGE_SIZE = listing.MAX_LISTING_PAGE_SIZE
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


def _enforce_asset(database_id, asset_id, action, compliance_state=engine.STATE_QUARANTINED):
    """`_enforce` with the database id the asset is stored under: `<databaseId>#deleted` for an
    archived asset, so the engine's archived-record rule applies to its quarantine and exception
    as it applies to the asset itself (`ArchivedAssets.decide`)."""
    return ArchivedAssets(asset_table).decide(
        database_id, asset_id,
        lambda stored_id: _enforce(stored_id, action, compliance_state))


def _validate_database_and_asset(database_id, asset_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID"},
    })


#######################
# Paging helpers
#######################

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
    """One page of quarantined assets the caller may GET, with their names.

    Access path: the ComplianceStateIndex partition `complianceState = quarantined`, narrowed to one
    database by the index's sort key when `databaseId` is given; `text` (case-insensitive contains
    on the asset name or id) is a FilterExpression. The page is filled across at most
    `listing.DEFAULT_READ_BUDGET` reads (`listing.query_page`), skipping the rows the caller may
    not GET; `NextToken` resumes after the last row returned. No per-row lookup: the name is on the
    row.
    """
    page_size = listing.page_size_of(query_params.get("maxItems"), DEFAULT_LIST_PAGE_SIZE)
    if page_size is None:
        return validation_error(body={"message": "maxItems must be an integer"}, event=event)
    database_id = query_params.get("databaseId") or None
    text = listing.normalized_text(query_params.get("text")) or None
    (valid, message) = validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True,
                       "optional": True},
        "text": {"value": text, "validator": "STRING_256", "optional": True},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)
    start_key = None
    if query_params.get("startingToken"):
        start_key = listing.decode_token(query_params["startingToken"],
                                         listing.COMPLIANCE_STATE_INDEX_KEYS)
        if start_key is None:
            return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)

    key_condition = Key("complianceState").eq(engine.STATE_QUARANTINED)
    if database_id:
        key_condition = key_condition & Key("databaseId").eq(database_id)
    query_kwargs = {"IndexName": COMPLIANCE_STATE_INDEX, "KeyConditionExpression": key_condition}
    text_accept = None
    if text:
        query_kwargs["FilterExpression"], text_accept = listing.asset_text_filter(text)

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    # An archived asset is not evaluated while it is archived, so its quarantine is left out for
    # every caller, as the asset listing leaves archived assets out (`ArchivedAssets.listed`).
    archived = ArchivedAssets(asset_table)

    def may_get(item):
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        if text_accept is not None and not text_accept(item):
            return False
        if not archived.listed(item.get("databaseId"), item.get("assetId")):
            return False
        return bool(casbin_enforcer) and archived.decide(
            item.get("databaseId"), item.get("assetId"),
            lambda stored_id: casbin_enforcer.enforce(
                _evaluation_object(stored_id, engine.STATE_QUARANTINED), "GET"),
            cache_key="GET", listing=True)

    rows, next_key, _ = listing.query_page(
        asset_state_table, query_kwargs, page_size, listing.COMPLIANCE_STATE_INDEX_KEYS,
        start_key=start_key, filtered=bool(text), accept=may_get)
    may_act = {}

    def may_post(stored_id):
        # The answer depends on the database id alone, so each id is checked once per request.
        if stored_id not in may_act:
            may_act[stored_id] = bool(casbin_enforcer.enforce(
                _evaluation_object(stored_id, engine.STATE_QUARANTINED), "POST"))
        return may_act[stored_id]

    for row in rows:
        row.setdefault("assetName", "")
        row.pop("assetNameSort", None)
        row.pop("assetSearchText", None)
        # The caller's allowed actions on the row, computed server-side: releasing and granting an
        # exception are both a POST on the quarantined evaluation object of the row's database
        # (its stored id, for an archived asset).
        act = archived.decide(row.get("databaseId"), row.get("assetId"), may_post, listing=True)
        row["permissions"] = {"release": act, "exception": act}
    result = {"quarantinedAssets": rows}
    if next_key:
        result["NextToken"] = listing.encode_token(next_key)
    return success(body=result)

def release_quarantine(event, database_id, asset_id, request: ReleaseQuarantineRequestModel):
    """Release a quarantined asset (state becomes compliant; no exception recorded)."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_asset(database_id, asset_id, "POST"):
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
    if not _enforce_asset(database_id, asset_id, "POST"):
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
    if not _enforce_asset(database_id, asset_id, "DELETE", compliance_state=engine.STATE_EXCEPTION):
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
    if evaluation is None or evaluation.get("verdict") not in store.VERDICT_BEARING:
        # A last evaluation that is gone, errored or still pending decides no state; fall back to
        # the newest one that did.
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
