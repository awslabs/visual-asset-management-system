#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Evaluate Service handler.

- POST /compliance/evaluate/{databaseId}/{assetId}     — evaluate an asset
- POST /compliance/evaluate/{databaseId}               — evaluate every tracked asset of a database
- POST /compliance/sweep/{schemaName}                  — evaluate every asset bound to a schema
- GET  /compliance/evaluations/{databaseId}/{assetId}  — evaluation history of an asset
- GET  /compliance/state/{databaseId}/{assetId}        — compliance state of an asset
- GET  /compliance/state/{databaseId}                  — compliance overview of a database

Evaluation runs through `handlers/compliance/complianceEvaluationStore.run_evaluation`
(metadata, relationship, file-structure, version-coverage and asset-attribute rules
synchronously; pipeline rules launch workflow executions that the workflow callback finalizes).
"""

import base64
import json
import time

from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from common.apiRoutes import (
    API_COMPLIANCE_EVALUATE_ASSET,
    API_COMPLIANCE_EVALUATE_DATABASE,
    API_COMPLIANCE_EVALUATIONS_ASSET,
    API_COMPLIANCE_STATE_ASSET,
    API_COMPLIANCE_STATE_DATABASE,
    API_COMPLIANCE_SWEEP_SCHEMA,
)
from common.compliance import evaluationEngine as engine
from common.compliance import listing
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
    EvaluateDatabaseRequestModel,
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
MAX_OVERVIEW_PAGE_SIZE = listing.MAX_LISTING_PAGE_SIZE
INVALID_PAGINATION_TOKEN_MESSAGE = "Invalid pagination token"
# The attributes a LastEvaluatedKey of the evaluation table's AssetIndex carries (table key plus
# index keys); a token missing one belongs to another listing and never reaches DynamoDB.
EVALUATION_ASSET_INDEX_KEY_ATTRIBUTES = ("evaluationId", "databaseId:assetId", "evaluatedAt")
# The overview `summary` key counting assets whose latest evaluation errored (`lastEvaluationStatus`
# of `error`) — an overlay on the state buckets, not a compliance state.
OVERVIEW_ERROR_BUCKET = "error"
# The asset-state table's (complianceState, databaseId) index.
COMPLIANCE_STATE_INDEX = "ComplianceStateIndex"

# Bound on the assets one sweep evaluates synchronously inside the API Lambda timeout. The bound
# rows are ordered `pending_evaluation` first; a schema bound to more assets sweeps the first
# MAX_SWEEP_ASSETS of that order and reports the remainder.
MAX_SWEEP_ASSETS = 200

# Wall-clock budget for launching a sweep's synchronous per-asset evaluations. API Gateway closes a
# synchronous integration after 29 seconds, so the launches (each pipeline rule is an execute-workflow
# invoke) must fit under it with room for the index reads and the response: once the budget is spent
# the sweep launches nothing more and reports the unreached assets in `assetsRemaining`.
SWEEP_TIME_BUDGET_SECONDS = 20

# Clock for the sweep budget: monotonic, so a wall-clock adjustment cannot stretch or shrink it.
monotonic = time.monotonic

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
    if API_COMPLIANCE_EVALUATE_DATABASE.matches(path):
        parse(_parse_body(event), model=EvaluateDatabaseRequestModel)
        return evaluate_database(event, path_params.get("databaseId"))
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
        # The database binding governs every asset of the database, whatever its auto-evaluation
        # says: an asset it does not record yet is registered under it and evaluated against it.
        schema_name = (store.get_database_item(database_id) or {}).get("complianceSchemaName")
        if schema_name:
            store.register_under_database_binding(database_id, asset_id, schema_name)
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
    that could not run (including one a newer evaluation of the asset superseded, which carries an
    `error`), or whose every rule errored (verdict `error`), changes no asset state and so opens no
    cascade."""
    result = store.run_evaluation(database_id, asset_id, schema_name, actor)
    if not result.get("error") and result.get("verdict") != EvaluationVerdict.error.value:
        check_and_trigger_cascade(database_id, asset_id)
    return result


def sweep_schema(event, schema_name):
    """Evaluate the assets whose state row is bound to `schema_name` (SchemaNameIndex GSI) and whose
    database the caller may evaluate; the rest are counted as `skipped` and never listed.

    Rows in `pending_evaluation` are evaluated first, then the rest of the bound rows. One call
    evaluates at most MAX_SWEEP_ASSETS assets and stops launching once SWEEP_TIME_BUDGET_SECONDS has
    elapsed; `assetsRemaining` counts the bound assets this call did not reach under either bound,
    and tells the caller to call again. An asset whose evaluation found a newer one of it already
    started (a trigger evaluation running at the same time) is listed with verdict `superseded` and
    `superseded: true` and counted under `superseded`: its state is that evaluation's, and the
    listed `evaluationId` names no evaluation record."""
    started_at = monotonic()
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_schema(schema_name, "POST"):
        return authorization_error()

    if store.load_schema_item(schema_name) is None:
        return general_error(body={"message": "Schema not found"}, event=event)

    # The evaluation object depends only on the database, so one verdict serves every asset in it.
    database_allowed = {}
    # A database binding governs every asset of the database: an asset it does not record yet (one
    # whose creation event was not processed) is registered under it first, in each bound database
    # the caller may evaluate, so the sweep reaches it.
    registered = 0
    for database_id in store.databases_bound_to_schema(schema_name):
        if database_id not in database_allowed:
            database_allowed[database_id] = _enforce_evaluation(database_id, "POST")
        if database_allowed[database_id]:
            registered += store.register_unrecorded_assets(database_id, schema_name)

    bound_assets = _bound_assets_pending_first(schema_name)
    actor = claims_and_roles["tokens"][0]

    triggered = []
    skipped = 0
    superseded = 0
    beyond_cap = 0
    beyond_budget = 0
    budget_spent = False
    for asset in bound_assets:
        if len(triggered) >= MAX_SWEEP_ASSETS:
            beyond_cap += 1
            continue
        if not budget_spent and monotonic() - started_at > SWEEP_TIME_BUDGET_SECONDS:
            budget_spent = True
        if budget_spent:
            beyond_budget += 1
            continue
        database_id = asset["databaseId"]
        if database_id not in database_allowed:
            database_allowed[database_id] = _enforce_evaluation(database_id, "POST")
        if not database_allowed[database_id]:
            skipped += 1
            continue
        result = run_evaluation_for_asset(database_id, asset["assetId"], schema_name, actor)
        entry = {
            "databaseId": database_id,
            "assetId": asset["assetId"],
            "evaluationId": result.get("evaluationId"),
            "verdict": result.get("verdict"),
        }
        if result.get("superseded"):
            entry["superseded"] = True
            superseded += 1
        triggered.append(entry)

    remaining = beyond_cap + beyond_budget
    logger.info(f"Sweep of schema {schema_name}: {registered} registered under a database binding")
    logger.info(f"Sweep of schema {schema_name}: {len(triggered)} triggered ({superseded} "
                f"superseded by a newer evaluation), {skipped} skipped, {remaining} remaining "
                f"({beyond_cap} beyond the cap, {beyond_budget} beyond the time budget)")
    return success(body={
        "message": f"Sweep triggered for {len(triggered)} assets",
        "schemaName": schema_name,
        "assetsTriggered": triggered,
        "assetsRemaining": remaining,
        "skipped": skipped,
        "superseded": superseded,
    })


def evaluate_database(event, database_id):
    """Evaluate every tracked asset of one database — every asset-state row of it, each against
    the schema its row binds (the database binding or the asset's own override) — with the sweep's
    bounds: `pending_evaluation` rows first, at most MAX_SWEEP_ASSETS evaluations, no launch after
    SWEEP_TIME_BUDGET_SECONDS, `assetsRemaining` the tracked assets not reached.

    A database binding governs every asset of the database, so an asset of a bound database that
    has no compliance record yet is registered under the binding first (`registered`). A tracked
    asset whose row names no schema (an on-demand evaluation against a named schema in a database
    without a binding records one) cannot be evaluated without a schema and is counted under
    `withoutSchema`. An asset whose evaluation found a newer one of it already started is listed
    with verdict `superseded` and counted under `superseded`, as in a sweep.

    Authorized on the database's compliance evaluation object, like the evaluation of one asset;
    no other database is read or evaluated."""
    started_at = monotonic()
    (valid, message) = validate({
        "databaseId": {"value": database_id, "validator": "ID"},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_evaluation(database_id, "POST"):
        return authorization_error()

    db_item = store.get_database_item(database_id)
    if not db_item:
        return general_error(body={"message": "Database not found"}, event=event)

    registered = 0
    if db_item.get("complianceSchemaName"):
        registered = store.register_unrecorded_assets(database_id, db_item["complianceSchemaName"])

    actor = claims_and_roles["tokens"][0]
    triggered = []
    superseded = 0
    without_schema = 0
    beyond_cap = 0
    beyond_budget = 0
    budget_spent = False
    for row in _database_rows_pending_first(database_id):
        schema_name = row.get("schemaName")
        if not schema_name:
            without_schema += 1
            continue
        if len(triggered) >= MAX_SWEEP_ASSETS:
            beyond_cap += 1
            continue
        if not budget_spent and monotonic() - started_at > SWEEP_TIME_BUDGET_SECONDS:
            budget_spent = True
        if budget_spent:
            beyond_budget += 1
            continue
        result = run_evaluation_for_asset(database_id, row["assetId"], schema_name, actor)
        entry = {
            "databaseId": database_id,
            "assetId": row["assetId"],
            "schemaName": schema_name,
            "evaluationId": result.get("evaluationId"),
            "verdict": result.get("verdict"),
        }
        if result.get("superseded"):
            entry["superseded"] = True
            superseded += 1
        triggered.append(entry)

    remaining = beyond_cap + beyond_budget
    logger.info(f"Evaluation of database {database_id}: {registered} registered, {len(triggered)} "
                f"triggered ({superseded} superseded), {without_schema} without a schema, "
                f"{remaining} remaining ({beyond_cap} beyond the cap, {beyond_budget} beyond the "
                f"time budget)")
    return success(body={
        "message": f"Evaluation triggered for {len(triggered)} assets",
        "databaseId": database_id,
        "assetsTriggered": triggered,
        "assetsRemaining": remaining,
        "superseded": superseded,
        "registered": registered,
        "withoutSchema": without_schema,
    })


def _database_rows_pending_first(database_id):
    """Every asset-state row of a database (read to exhaustion), the `pending_evaluation` rows
    first and the rest after, each in asset-id order."""
    rows = query_all_items(asset_state_table, KeyConditionExpression=Key("databaseId").eq(database_id))
    pending = [r for r in rows if r.get("complianceState") == engine.STATE_PENDING_EVALUATION]
    rest = [r for r in rows if r.get("complianceState") != engine.STATE_PENDING_EVALUATION]
    return pending + rest


def _bound_assets_pending_first(schema_name):
    """The state rows bound to `schema_name`, each once: the `pending_evaluation` rows first (the GSI
    sorts on `complianceState`, so a plain partition read would place them after every verdict-bearing
    state), then the rest of the partition."""
    pending = query_all_items(
        asset_state_table,
        IndexName="SchemaNameIndex",
        KeyConditionExpression=(Key("schemaName").eq(schema_name)
                                & Key("complianceState").eq(engine.STATE_PENDING_EVALUATION)),
    )
    seen = {_asset_key(row) for row in pending}
    rest = [row for row in query_all_items(
        asset_state_table,
        IndexName="SchemaNameIndex",
        KeyConditionExpression=Key("schemaName").eq(schema_name),
    ) if _asset_key(row) not in seen]
    return pending + rest


def _asset_key(state_row):
    return (state_row["databaseId"], state_row["assetId"])


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
        exclusive_start_key = _decode_key_token(starting_token, EVALUATION_ASSET_INDEX_KEY_ATTRIBUTES)
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


# Filters of the database overview (GET /compliance/state/{databaseId}).
OVERVIEW_STATES = (
    engine.STATE_COMPLIANT, engine.STATE_NON_COMPLIANT, engine.STATE_PENDING_EVALUATION,
    engine.STATE_QUARANTINED, engine.STATE_EXCEPTION, engine.STATE_UNKNOWN,
)
OVERVIEW_SCHEMA_SOURCES = ("database", "asset")
OVERVIEW_SORTS = ("assetId", "name")


def overview_summary(database_id):
    """`(summary, totalAssets, reads)`: per-state counts and the evaluation-error overlay of a
    database, without reading the rows. Each state is one `Select=COUNT` query on the
    ComplianceStateIndex partition of that state narrowed to the database (its sort key), so the
    reads grow with the database's index entries, never with the item sizes, and no row is
    returned; the overlay is one COUNT over the database's partition filtered on
    `lastEvaluationStatus`. Exact at the time of the read."""
    summary = {}
    reads = 0
    for state in OVERVIEW_STATES:
        count, spent = listing.count_items(asset_state_table, {
            "IndexName": COMPLIANCE_STATE_INDEX,
            "KeyConditionExpression": (Key("complianceState").eq(state)
                                       & Key("databaseId").eq(database_id)),
        })
        summary[state] = count
        reads += spent
    errors, spent = listing.count_items(asset_state_table, {
        "KeyConditionExpression": Key("databaseId").eq(database_id),
        "FilterExpression": Attr("lastEvaluationStatus").eq(engine.EVALUATION_STATUS_ERROR),
    })
    reads += spent
    summary[OVERVIEW_ERROR_BUCKET] = errors
    return summary, sum(summary[state] for state in OVERVIEW_STATES), reads


def _overview_filters(event, query_params):
    """`(filters, None)` from the overview's query parameters, or `(None, response)` when one is
    not an accepted value."""
    state = query_params.get("state") or None
    schema_name = query_params.get("schemaName") or None
    schema_source = query_params.get("schemaSource") or None
    evaluation_error = query_params.get("evaluationError")
    text = listing.normalized_text(query_params.get("text"))
    sort = query_params.get("sort") or "assetId"
    if state is not None and state not in OVERVIEW_STATES:
        return None, validation_error(body={"message": "state must be one of " + ", ".join(
            OVERVIEW_STATES)}, event=event)
    if schema_source is not None and schema_source not in OVERVIEW_SCHEMA_SOURCES:
        return None, validation_error(body={"message": "schemaSource must be database or asset"},
                                      event=event)
    if evaluation_error not in (None, "", "true", "false"):
        return None, validation_error(body={"message": "evaluationError must be true or false"},
                                      event=event)
    if sort not in OVERVIEW_SORTS:
        return None, validation_error(body={"message": "sort must be assetId or name"}, event=event)
    (valid, message) = validate({
        "schemaName": {"value": schema_name, "validator": "ID", "optional": True},
        "text": {"value": text, "validator": "STRING_256", "optional": True},
    })
    if not valid:
        return None, validation_error(body={"message": message}, event=event)
    return {"state": state, "schemaName": schema_name, "schemaSource": schema_source,
            "evaluationError": evaluation_error or None, "text": text or None,
            "sort": sort}, None


def _overview_query(database_id, filters):
    """`(query kwargs, token key attributes, filtered)` for the overview's filters: a state filter
    reads the ComplianceStateIndex partition of that state narrowed to the database; `sort=name`
    reads the DatabaseAssetNameIndex; otherwise the database's partition of the table, in assetId
    order. The other filters are a FilterExpression on that read."""
    if filters["state"]:
        kwargs = {"IndexName": COMPLIANCE_STATE_INDEX,
                  "KeyConditionExpression": (Key("complianceState").eq(filters["state"])
                                             & Key("databaseId").eq(database_id))}
        keys = listing.COMPLIANCE_STATE_INDEX_KEYS
    elif filters["sort"] == "name":
        kwargs = {"IndexName": listing.DATABASE_ASSET_NAME_INDEX,
                  "KeyConditionExpression": Key("databaseId").eq(database_id)}
        keys = listing.DATABASE_ASSET_NAME_INDEX_KEYS
    else:
        kwargs = {"KeyConditionExpression": Key("databaseId").eq(database_id)}
        keys = listing.BASE_STATE_KEYS
    conditions = []
    if filters["schemaName"]:
        conditions.append(Attr("schemaName").eq(filters["schemaName"]))
    if filters["schemaSource"]:
        conditions.append(Attr("schemaSource").eq(filters["schemaSource"]))
    if filters["evaluationError"] == "true":
        conditions.append(Attr("lastEvaluationStatus").eq(engine.EVALUATION_STATUS_ERROR))
    elif filters["evaluationError"] == "false":
        conditions.append(Attr("lastEvaluationStatus").ne(engine.EVALUATION_STATUS_ERROR))
    if filters["text"]:
        # A row written before the search attribute existed is matched on its id.
        conditions.append(Attr("assetSearchText").contains(filters["text"])
                          | Attr("assetId").contains(filters["text"]))
    if conditions:
        expression = conditions[0]
        for condition in conditions[1:]:
            expression = expression & condition
        kwargs["FilterExpression"] = expression
    return kwargs, keys, bool(conditions)


def get_database_compliance_overview(event, database_id, query_params):
    """One page of a database's asset-state rows, filtered and sorted on the server, with the
    per-state counts of the whole database.

    Query parameters: `maxItems` (1..200) and `startingToken`; `state`, `schemaName`,
    `schemaSource` (`database` | `asset`), `evaluationError` (`true` | `false`), `text`
    (case-insensitive contains on the asset name or id) and `sort` (`assetId`, the default, or
    `name`). Each filter combination reads with a key condition (`_overview_query`) and fills the
    page across at most `listing.DEFAULT_READ_BUDGET` reads; `NextToken` resumes after the last row
    returned. Every row carries `assetName`, kept on the row (no lookup per row).

    `summary` has one bucket per compliance state (each row counted in exactly one) plus an
    `error` overlay: the rows whose `lastEvaluationStatus` is `error` — assets whose latest
    evaluation could not run or could not evaluate any rule. `error` is not a state, so an asset in
    it is also counted in its state bucket and the state buckets alone sum to `totalAssets`. The
    counts are of the whole database, whatever the filters (`overview_summary`).
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

    page_size = listing.page_size_of(query_params.get("maxItems"), DEFAULT_OVERVIEW_PAGE_SIZE)
    if page_size is None:
        return validation_error(body={"message": "maxItems must be an integer"}, event=event)
    filters, error = _overview_filters(event, query_params)
    if error:
        return error
    kwargs, keys, filtered = _overview_query(database_id, filters)
    start_key = None
    if query_params.get("startingToken"):
        start_key = listing.decode_token(query_params["startingToken"], keys)
        if start_key is None:
            return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)

    rows, next_key, _ = listing.query_page(asset_state_table, kwargs, page_size, keys,
                                           start_key=start_key, filtered=filtered)
    if filters["state"] and filters["sort"] == "name":
        rows.sort(key=lambda row: row.get("assetNameSort") or row.get("assetId", ""))
    for row in rows:
        row.setdefault("assetName", "")
        row.pop("assetNameSort", None)
        row.pop("assetSearchText", None)

    summary, total, _ = overview_summary(database_id)
    body = {
        "databaseId": database_id,
        "totalAssets": total,
        "summary": summary,
        "assets": rows,
    }
    if next_key:
        body["NextToken"] = listing.encode_token(next_key)
    return success(body=body)


def _decode_key_token(token, key_attributes):
    """The index key a Base64 JSON token carries, or None when the token is malformed — not Base64
    JSON, not an object, or missing a string value for one of `key_attributes` (a key from another
    listing would otherwise reach DynamoDB and fail the request as an internal error)."""
    try:
        decoded = json.loads(base64.b64decode(token, validate=True).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    if any(not isinstance(decoded.get(name), str) for name in key_attributes):
        return None
    return {name: decoded[name] for name in key_attributes}


