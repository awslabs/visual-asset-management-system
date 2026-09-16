#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Evaluate Service handler.

- POST /compliance/evaluate/{databaseId}/{assetId}     — evaluate an asset
- POST /compliance/sweep/{schemaName}                  — evaluate every asset bound to a schema
- GET  /compliance/evaluations/{databaseId}/{assetId}  — evaluation history of an asset
- GET  /compliance/state/{databaseId}/{assetId}        — compliance state of an asset
- GET  /compliance/state/{databaseId}                  — compliance overview of a database

Evaluation runs through `handlers/compliance/complianceEvaluationStore.run_evaluation`
(metadata and relationship rules synchronously; pipeline rules launch workflow executions that
the workflow callback finalizes).
"""

import base64
import json

from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from common.apiRoutes import (
    API_COMPLIANCE_EVALUATE_ASSET,
    API_COMPLIANCE_EVALUATIONS_ASSET,
    API_COMPLIANCE_STATE_ASSET,
    API_COMPLIANCE_STATE_DATABASE,
    API_COMPLIANCE_SWEEP_SCHEMA,
)
from common.compliance import evaluationEngine as engine
from common.dynamodb import query_all_items
from common.validators import validate
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance.complianceTrigger import check_and_trigger_cascade
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
    EvaluateAssetRequestModel,
    EvaluationVerdict,
    SweepSchemaRequestModel,
)

logger = safeLogger(service_name="ComplianceEvaluateService")

claims_and_roles = {}

COMPLIANCE_EVALUATION_OBJECT_TYPE = "complianceEvaluation"
COMPLIANCE_SCHEMA_OBJECT_TYPE = "complianceSchema"

# Page size for the evaluation-history listing (newest first).
DEFAULT_EVALUATIONS_PAGE_SIZE = 50
MAX_EVALUATIONS_PAGE_SIZE = 200

# Page bounds for the database overview. The state rows are read to exhaustion (the per-state
# summary and total cover the whole database), then offset-sliced to one page in assetId order;
# the token carries the offset of the next page.
DEFAULT_OVERVIEW_PAGE_SIZE = 100
MAX_OVERVIEW_PAGE_SIZE = 500
INVALID_PAGINATION_TOKEN_MESSAGE = "Invalid pagination token"
# The overview `summary` key counting assets whose latest evaluation errored (`lastEvaluationStatus`
# of `error`) — an overlay on the state buckets, not a compliance state.
OVERVIEW_ERROR_BUCKET = "error"

# Bound on the assets one sweep evaluates synchronously inside the API Lambda timeout; a schema
# bound to more assets sweeps the first MAX_SWEEP_ASSETS and reports the remainder.
MAX_SWEEP_ASSETS = 200

# Tables are resolved (and clients built) once by the shared store at import.
asset_state_table = store.asset_state_table
evaluation_table = store.evaluation_table
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
    path_params = event.get("pathParameters", {}) or {}
    query_params = event.get("queryStringParameters", {}) or {}
    if API_COMPLIANCE_EVALUATIONS_ASSET.matches(path):
        return get_evaluations(event, path_params.get("databaseId"), path_params.get("assetId"),
                               query_params)
    if API_COMPLIANCE_STATE_ASSET.matches(path):
        return get_compliance_state(event, path_params.get("databaseId"), path_params.get("assetId"))
    if API_COMPLIANCE_STATE_DATABASE.matches(path):
        return get_database_compliance_overview(event, path_params.get("databaseId"), query_params)
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_post_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_EVALUATE_ASSET.matches(path):
        request = parse(_parse_body(event), model=EvaluateAssetRequestModel)
        return evaluate_asset(event, path_params.get("databaseId"), path_params.get("assetId"),
                              request)
    if API_COMPLIANCE_SWEEP_SCHEMA.matches(path):
        parse(_parse_body(event), model=SweepSchemaRequestModel)
        return sweep_schema(event, path_params.get("schemaName"))
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

def _enforce_evaluation(database_id, action, compliance_state=""):
    """Tier-2 check on a compliance evaluation object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce({
        "object__type": COMPLIANCE_EVALUATION_OBJECT_TYPE,
        "databaseId": database_id,
        "complianceState": compliance_state or "",
    }, action)


def _enforce_schema(schema_name, action):
    """Tier-2 check on a compliance schema object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce({
        "object__type": COMPLIANCE_SCHEMA_OBJECT_TYPE,
        "complianceSchemaName": schema_name,
    }, action)


def _validate_database_and_asset(database_id, asset_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID"},
    })


#######################
# Business logic
#######################

def evaluate_asset(event, database_id, asset_id, request: EvaluateAssetRequestModel):
    """Evaluate an asset against the requested schema (or its bound schema)."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_evaluation(database_id, "POST"):
        return authorization_error()

    if not store.get_asset_item(database_id, asset_id):
        return general_error(body={"message": "Asset not found"}, event=event)

    current_state = store.get_compliance_record(database_id, asset_id) or {}
    schema_name = request.schemaName or current_state.get("schemaName")
    if not schema_name:
        return general_error(body={
            "message": "No schema specified and the asset has no bound schema",
        }, event=event)

    result = run_evaluation_for_asset(database_id, asset_id, schema_name,
                                      claims_and_roles["tokens"][0])
    if result.get("error"):
        return general_error(body={"message": "Evaluation could not run: " + result["error"]},
                             event=event)

    return success(body={
        "message": "Evaluation completed",
        "evaluationId": result.get("evaluationId"),
        "schemaName": schema_name,
        "schemaVersion": result.get("schemaVersion"),
        "verdict": result.get("verdict"),
        "complianceState": result.get("complianceState"),
        "exceptionApplied": bool(result.get("exceptionApplied", False)),
        "hasRuleErrors": bool(result.get("hasRuleErrors", False)),
        "ruleResults": result.get("ruleResults", []),
        "pipelineRulesPending": result.get("pipelineRulesPending", 0),
    })


def run_evaluation_for_asset(database_id, asset_id, schema_name, actor):
    """One evaluation plus the downstream cascade check; shared by evaluate and sweep. An evaluation
    that could not run, or whose every rule errored (verdict `error`), changes no asset state and so
    opens no cascade."""
    result = store.run_evaluation(database_id, asset_id, schema_name, actor)
    if not result.get("error") and result.get("verdict") != EvaluationVerdict.error.value:
        check_and_trigger_cascade(database_id, asset_id)
    return result


def sweep_schema(event, schema_name):
    """Evaluate every asset whose state row is bound to `schema_name` (SchemaNameIndex GSI) and
    whose database the caller may evaluate; the rest are counted as `skipped` and never listed."""
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_schema(schema_name, "POST"):
        return authorization_error()

    if store.load_schema_item(schema_name) is None:
        return general_error(body={"message": "Schema not found"}, event=event)

    bound_assets = query_all_items(
        asset_state_table,
        IndexName="SchemaNameIndex",
        KeyConditionExpression=Key("schemaName").eq(schema_name),
    )
    actor = claims_and_roles["tokens"][0]

    # The evaluation object depends only on the database, so one verdict serves every asset in it.
    database_allowed = {}
    triggered = []
    skipped = 0
    remaining = 0
    for asset in bound_assets:
        if len(triggered) >= MAX_SWEEP_ASSETS:
            remaining += 1
            continue
        database_id = asset["databaseId"]
        if database_id not in database_allowed:
            database_allowed[database_id] = _enforce_evaluation(database_id, "POST")
        if not database_allowed[database_id]:
            skipped += 1
            continue
        result = run_evaluation_for_asset(database_id, asset["assetId"], schema_name, actor)
        triggered.append({
            "databaseId": database_id,
            "assetId": asset["assetId"],
            "evaluationId": result.get("evaluationId"),
            "verdict": result.get("verdict"),
        })

    logger.info(f"Sweep of schema {schema_name}: {len(triggered)} triggered, {skipped} skipped, "
                f"{remaining} remaining")
    return success(body={
        "message": f"Sweep triggered for {len(triggered)} assets",
        "schemaName": schema_name,
        "assetsTriggered": triggered,
        "assetsRemaining": remaining,
        "skipped": skipped,
    })


def get_evaluations(event, database_id, asset_id, query_params):
    """Evaluation history of an asset, newest first, externally paged (AssetIndex GSI)."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_evaluation(database_id, "GET"):
        return authorization_error()

    try:
        page_size = int(query_params.get("maxItems", str(DEFAULT_EVALUATIONS_PAGE_SIZE)))
    except (TypeError, ValueError):
        return validation_error(body={"message": "maxItems must be an integer"}, event=event)
    page_size = max(1, min(page_size, MAX_EVALUATIONS_PAGE_SIZE))

    query_kwargs = {
        "IndexName": "AssetIndex",
        "KeyConditionExpression": Key("databaseId:assetId").eq(f"{database_id}:{asset_id}"),
        "ScanIndexForward": False,
        "Limit": page_size,
    }
    starting_token = query_params.get("startingToken")
    if starting_token:
        exclusive_start_key = _decode_key_token(starting_token)
        if exclusive_start_key is None:
            return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)
        query_kwargs["ExclusiveStartKey"] = exclusive_start_key

    response = evaluation_table.query(**query_kwargs)
    result = {"evaluations": response.get("Items", [])}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = base64.b64encode(
            json.dumps(response["LastEvaluatedKey"], default=str).encode("utf-8")).decode("utf-8")
    return success(body=result)


def get_compliance_state(event, database_id, asset_id):
    """The compliance state row of an asset ("unknown" when it has none)."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    item = store.get_compliance_record(database_id, asset_id)
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_evaluation(database_id, "GET", (item or {}).get("complianceState", "")):
        return authorization_error()

    if not item:
        return success(body={
            "databaseId": database_id,
            "assetId": asset_id,
            "complianceState": engine.STATE_UNKNOWN,
            "schemaName": None,
            "schemaSource": None,
        })
    return success(body=item)


def get_database_compliance_overview(event, database_id, query_params):
    """Per-state counts over every asset-state row of a database, plus one page of the rows (assetId
    order) with asset names. The counts cover the whole database; `NextToken` is present while more
    rows remain.

    `summary` has one bucket per compliance state (each row counted in exactly one; a row with an
    unrecognized state counts as `unknown`) plus an `error` overlay: the number of rows whose
    `lastEvaluationStatus` is `error` — assets whose latest evaluation could not run or could not
    evaluate any rule. `error` is not a state, so an asset in it is also counted in its state bucket
    and the state buckets alone sum to `totalAssets`.
    """
    (valid, message) = validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_evaluation(database_id, "GET"):
        return authorization_error()

    page_size, offset, error = _overview_page_arguments(event, query_params)
    if error:
        return error

    items = query_all_items(
        asset_state_table, KeyConditionExpression=Key("databaseId").eq(database_id))

    summary = {
        engine.STATE_COMPLIANT: 0,
        engine.STATE_NON_COMPLIANT: 0,
        engine.STATE_PENDING_EVALUATION: 0,
        engine.STATE_QUARANTINED: 0,
        engine.STATE_EXCEPTION: 0,
        engine.STATE_UNKNOWN: 0,
    }
    evaluation_errors = 0
    for item in items:
        state = item.get("complianceState", engine.STATE_UNKNOWN)
        summary[state if state in summary else engine.STATE_UNKNOWN] += 1
        if item.get("lastEvaluationStatus") == engine.EVALUATION_STATUS_ERROR:
            evaluation_errors += 1
    summary[OVERVIEW_ERROR_BUCKET] = evaluation_errors

    items.sort(key=lambda item: item.get("assetId", ""))
    page = items[offset:offset + page_size]
    for item in page:
        item["assetName"] = _asset_name(database_id, item.get("assetId"))

    body = {
        "databaseId": database_id,
        "totalAssets": len(items),
        "summary": summary,
        "assets": page,
    }
    if offset + page_size < len(items):
        body["NextToken"] = _encode_offset_token(offset + page_size)
    return success(body=body)


def _overview_page_arguments(event, query_params):
    """(page_size, offset, None) from `maxItems` / `startingToken`, or (None, None, response) when
    either is malformed. The token is Base64 JSON `{"offset": n}`."""
    try:
        page_size = int(query_params.get("maxItems", str(DEFAULT_OVERVIEW_PAGE_SIZE)))
    except (TypeError, ValueError):
        return None, None, validation_error(body={"message": "maxItems must be an integer"}, event=event)
    page_size = max(1, min(page_size, MAX_OVERVIEW_PAGE_SIZE))

    offset = 0
    starting_token = query_params.get("startingToken")
    if starting_token:
        offset = _decode_offset_token(starting_token)
        if offset is None:
            return None, None, validation_error(
                body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)
    return page_size, offset, None


def _decode_offset_token(token):
    """The non-negative offset an offset token carries, or None when the token is malformed."""
    try:
        decoded = json.loads(base64.b64decode(token, validate=True).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    offset = decoded.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return None
    return offset


def _encode_offset_token(offset):
    return base64.b64encode(json.dumps({"offset": offset}).encode("utf-8")).decode("utf-8")


def _decode_key_token(token):
    """The DynamoDB key a Base64 JSON token carries, or None when the token is malformed."""
    try:
        decoded = json.loads(base64.b64decode(token, validate=True).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    return decoded if isinstance(decoded, dict) and decoded else None


def _asset_name(database_id, asset_id):
    """The asset's display name, or "" when the asset row is gone."""
    if not asset_id:
        return ""
    row = asset_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        ProjectionExpression="assetName",
    ).get("Item")
    return (row or {}).get("assetName", "")
