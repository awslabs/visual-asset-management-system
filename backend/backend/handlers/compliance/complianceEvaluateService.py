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
from datetime import datetime, timedelta, timezone

from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
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
from common.compliance.archivedAssets import (
    ArchivedAssets, withheld_state_counts,
)
from common.dynamodb import query_all_items
from common.validators import validate
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance.complianceTrigger import (
    EVALUATION_TARGET_ARCHIVED, EVALUATION_TARGET_REMOVED, automatic_evaluation_target,
    check_and_trigger_cascade,
)
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
    GLOBAL_DATABASE_ID,
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

# Bound on the assets one sweep evaluates synchronously inside the API Lambda timeout. A sweep
# that reaches more is continued by the next call with the response's `NextToken`
# (`_sweep_candidates`).
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
        request = parse(_parse_body(event), model=EvaluateDatabaseRequestModel)
        return evaluate_database(event, path_params.get("databaseId"), request.startingToken)
    if API_COMPLIANCE_SWEEP_SCHEMA.matches(path):
        request = parse(_parse_body(event), model=SweepSchemaRequestModel)
        return sweep_schema(event, path_params.get("schemaName"), request.startingToken)
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


def _enforce_asset_evaluation(database_id, asset_id, action, compliance_state=""):
    """Tier-2 check on the compliance evaluation object of one asset, with the database id the
    asset is stored under: `<databaseId>#deleted` for an archived asset, so the engine's
    archived-record rule applies to its compliance record as it applies to the asset itself
    (`ArchivedAssets.decide`)."""
    return ArchivedAssets(asset_table).decide(
        database_id, asset_id,
        lambda stored_id: _enforce_evaluation(stored_id, action, compliance_state))


def _enforce_schema(schema_name, database_id, action):
    """Tier-2 check on a compliance schema object in its scope (`databaseId`: a database id or
    GLOBAL). Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce({
        "object__type": COMPLIANCE_SCHEMA_OBJECT_TYPE,
        "complianceSchemaName": schema_name,
        "databaseId": database_id or GLOBAL_DATABASE_ID,
    }, action)


def _validate_database_and_asset(database_id, asset_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID"},
    })


# The cache key of the archive-visibility decision (the evaluation GET on a database's stored id),
# so one request asks it once per database.
ARCHIVE_VISIBILITY_CACHE_KEY = "evaluation-GET"


def _archive_visible(archived, database_id):
    """Whether the caller may see the archived assets of a database: the evaluation GET on its
    stored id `<databaseId>#deleted`, the decision the overview and the record reads make on an
    archived asset's record (`ArchivedAssets`), asked once per database of the request."""
    return archived.archive_visible(
        database_id, lambda stored_id: _enforce_evaluation(stored_id, "GET"),
        cache_key=ARCHIVE_VISIBILITY_CACHE_KEY)


def _hidden_archived(archived, database_id, asset_id):
    """Whether an asset is archived in a database whose archive the caller may not see. Such an
    asset is counted nowhere in the response of a sweep or of Evaluate All (only the operator log
    counts it), as the asset listing does not show it to that caller. The database's archived ids
    are read (keys only, once per request) only for a caller refused its archive."""
    return (not _archive_visible(archived, database_id)
            and asset_id in archived.archived_ids(database_id))


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
    if not _enforce_asset_evaluation(database_id, asset_id, "POST"):
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


def sweep_schema(event, schema_name, starting_token=None):
    """Evaluate the assets whose state row is bound to `schema_name` (SchemaNameIndex GSI) and whose
    database the caller may evaluate; the rest are counted as `skipped` (and in
    `skippedNotAuthorized`, the reason they were skipped) and never listed. An archived asset is
    not evaluated either (its row sits in the archive and cannot be read): it is counted in
    `skipped` and `skippedArchived`, and its compliance record is left as it is. Only the archived
    assets the caller may see are counted (`_archive_visible`): an archived asset of a database
    whose archive the caller may not see is counted nowhere in the response (`skippedNotAuthorized`
    and `assetsRemaining` included). The caller needs read access to the schema in its scope: a
    database's compliance administrator may sweep a GLOBAL schema, and the sweep then reaches the
    assets of the databases that administrator may evaluate only. Every evaluation runs as the
    caller, never with the system identity.

    One call evaluates at most MAX_SWEEP_ASSETS assets and stops launching once
    SWEEP_TIME_BUDGET_SECONDS has elapsed. A sweep is continued across calls: while assets remain,
    the response carries `NextToken`, and a call that passes it back as `startingToken` evaluates
    only the assets not evaluated since the sweep started (`_sweep_candidates`), so the calls
    advance and the sweep ends; `assetsRemaining` counts the assets still to evaluate after this
    call. `pending_evaluation` rows go first, then the least recently evaluated. A record whose
    asset no longer exists is not evaluated: it is removed as the asset's permanent deletion
    removes it and counted under `removedOrphans`. An asset whose
    evaluation found a newer one of it already started (a trigger evaluation running at the same
    time) is listed with verdict `superseded` and `superseded: true` and counted under
    `superseded`: its state is that evaluation's, and the listed `evaluationId` names no evaluation
    record."""
    started_at = monotonic()
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)
    sweep_started_at, token_ok = _sweep_started_at(starting_token)
    if not token_ok:
        return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    schema_item = store.load_schema_item(schema_name)
    # An absent schema is checked in the GLOBAL scope, so a caller without schema read access is
    # refused rather than told whether the name exists.
    if not _enforce_schema(schema_name, (schema_item or {}).get("databaseId"), "GET"):
        return authorization_error()
    if schema_item is None:
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

    bound_assets, evaluated_earlier = _sweep_candidates(
        _bound_assets(schema_name), sweep_started_at, _asset_key)
    actor = claims_and_roles["tokens"][0]

    triggered = []
    skipped = 0
    skipped_archived = 0
    hidden_archived = 0
    superseded = 0
    removed_orphans = 0
    beyond_cap = 0
    beyond_budget = 0
    budget_spent = False
    archived = ArchivedAssets(asset_table)
    for asset in bound_assets:
        # A database the caller may not evaluate is skipped whatever the bounds: its assets are
        # never this caller's to evaluate, so they are not counted as remaining either.
        database_id = asset["databaseId"]
        if database_id not in database_allowed:
            database_allowed[database_id] = _enforce_evaluation(database_id, "POST")
        # An archived asset the caller may not see is counted nowhere in the response, whatever
        # the reason it is not evaluated (only the operator log counts it).
        if _hidden_archived(archived, database_id, asset["assetId"]):
            hidden_archived += 1
            continue
        if not database_allowed[database_id]:
            skipped += 1
            continue
        if len(triggered) >= MAX_SWEEP_ASSETS:
            beyond_cap += 1
            continue
        if not budget_spent and monotonic() - started_at > SWEEP_TIME_BUDGET_SECONDS:
            budget_spent = True
        if budget_spent:
            beyond_budget += 1
            continue
        target = automatic_evaluation_target(database_id, asset["assetId"], detected_by="sweep")
        if target == EVALUATION_TARGET_REMOVED:
            removed_orphans += 1
            continue
        if target == EVALUATION_TARGET_ARCHIVED:
            # Archived in a database whose archive the caller sees, or since the archive was read.
            if _archive_visible(archived, database_id):
                skipped_archived += 1
            else:
                hidden_archived += 1
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
                f"superseded by a newer evaluation), {skipped} skipped as not authorized, "
                f"{skipped_archived + hidden_archived} skipped as archived ({hidden_archived} in "
                f"an archive the caller may not see, not in the response), {remaining} remaining "
                f"({beyond_cap} beyond the cap, {beyond_budget} beyond the time budget), "
                f"{evaluated_earlier} evaluated earlier in this sweep, {removed_orphans} records of deleted "
                f"assets removed")
    body = {
        "message": f"Sweep triggered for {len(triggered)} assets",
        "schemaName": schema_name,
        "assetsTriggered": triggered,
        "assetsRemaining": remaining,
        "skipped": skipped + skipped_archived,
        "skippedNotAuthorized": skipped,
        "skippedArchived": skipped_archived,
        "superseded": superseded,
        "removedOrphans": removed_orphans,
    }
    if remaining:
        body["NextToken"] = _sweep_token(sweep_started_at)
    return success(body=body)


def evaluate_database(event, database_id, starting_token=None):
    """Evaluate every tracked asset of one database — every asset-state row of it, each against
    the schema its row binds (the database binding or the asset's own override) — with the sweep's
    bounds and continuation: at most MAX_SWEEP_ASSETS evaluations and no launch after
    SWEEP_TIME_BUDGET_SECONDS per call, `pending_evaluation` rows first and then the least recently
    evaluated, a `NextToken` while assets remain that the next call passes back as `startingToken`
    to evaluate only the assets not evaluated since the evaluation started, and `assetsRemaining`
    the tracked assets still to evaluate after this call (`_sweep_candidates`). A record whose
    asset no longer exists is removed rather than evaluated, as in a sweep (`removedOrphans`), and
    an archived asset is skipped with its record left as it is (`skipped`, `skippedArchived`). As
    in a sweep, only the archived assets the caller may see are counted: when the caller may not
    see the database's archive, its archived assets are counted nowhere in the response
    (`withoutSchema` and `assetsRemaining` included).

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
    sweep_started_at, token_ok = _sweep_started_at(starting_token)
    if not token_ok:
        return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)

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
    removed_orphans = 0
    skipped_archived = 0
    hidden_archived = 0
    without_schema = 0
    beyond_cap = 0
    beyond_budget = 0
    budget_spent = False
    archived = ArchivedAssets(asset_table)
    rows, evaluated_earlier = _sweep_candidates(
        _database_rows(database_id), sweep_started_at, lambda row: row.get("assetId", ""))
    for row in rows:
        if _hidden_archived(archived, database_id, row.get("assetId", "")):
            hidden_archived += 1
            continue
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
        target = automatic_evaluation_target(database_id, row["assetId"], detected_by="sweep")
        if target == EVALUATION_TARGET_REMOVED:
            removed_orphans += 1
            continue
        if target == EVALUATION_TARGET_ARCHIVED:
            # Archived in a database whose archive the caller sees, or since the archive was read.
            if _archive_visible(archived, database_id):
                skipped_archived += 1
            else:
                hidden_archived += 1
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
                f"time budget), {evaluated_earlier} evaluated earlier in this evaluation, "
                f"{removed_orphans} records of deleted assets removed, "
                f"{skipped_archived + hidden_archived} archived assets skipped "
                f"({hidden_archived} in an archive the caller may not see, not in the response)")
    body = {
        "message": f"Evaluation triggered for {len(triggered)} assets",
        "databaseId": database_id,
        "assetsTriggered": triggered,
        "assetsRemaining": remaining,
        "superseded": superseded,
        "registered": registered,
        "withoutSchema": without_schema,
        "skipped": skipped_archived,
        "skippedArchived": skipped_archived,
        "removedOrphans": removed_orphans,
    }
    if remaining:
        body["NextToken"] = _sweep_token(sweep_started_at)
    return success(body=body)


def _database_rows(database_id):
    """Every asset-state row of a database (read to exhaustion)."""
    return query_all_items(asset_state_table, KeyConditionExpression=Key("databaseId").eq(database_id))


def _bound_assets(schema_name):
    """Every state row bound to `schema_name` (its SchemaNameIndex partition, read to exhaustion)."""
    return query_all_items(
        asset_state_table,
        IndexName="SchemaNameIndex",
        KeyConditionExpression=Key("schemaName").eq(schema_name),
    )


# The field of a sweep's continuation token: the instant its first call started.
SWEEP_TOKEN_FIELD = "sweepStartedAt"


def _parse_instant(value):
    """An ISO-8601 instant as an aware datetime (UTC when it names no zone), or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    return instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)


def _sweep_started_at(starting_token):
    """`(started_at, ok)`: the instant the sweep a token continues started, or now for a sweep's
    first call (no token). `ok` is False for a malformed token, or one naming a future instant."""
    now = datetime.now(timezone.utc)
    if not starting_token:
        return now, True
    try:
        decoded = json.loads(base64.b64decode(starting_token, validate=True).decode("utf-8"))
    except (ValueError, TypeError):
        return None, False
    started_at = _parse_instant(decoded.get(SWEEP_TOKEN_FIELD)) if isinstance(decoded, dict) else None
    if started_at is None or started_at > now + timedelta(minutes=5):
        return None, False
    return started_at, True


def _sweep_token(started_at):
    return base64.b64encode(json.dumps(
        {SWEEP_TOKEN_FIELD: started_at.isoformat()}).encode("utf-8")).decode("utf-8")


def _sweep_candidates(rows, started_at, key):
    """The rows a sweep call still has to evaluate, in the order it evaluates them.

    A sweep is a walk over consecutive calls: the first call starts it (`started_at` is then now),
    and each call that leaves rows unreached returns a token naming that instant. Every evaluation
    records its start on the row (`lastEvaluatedAt`) before it evaluates anything, so a row evaluated
    since the sweep started (by an earlier call of it, or by an evaluation of the trigger meanwhile)
    is done and left out; the rest is what remains, and each call shrinks it. Order:
    `pending_evaluation` rows first, then the least recently evaluated (never evaluated first), then
    `key`, so even calls that do not pass the token work through the rows instead of repeating the
    same ones. Returns `(candidates, evaluated since the sweep started)`."""
    candidates = []
    evaluated = 0
    for row in rows:
        last = _parse_instant(row.get("lastEvaluatedAt"))
        if last is not None and last >= started_at:
            evaluated += 1
        else:
            candidates.append(row)
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    candidates.sort(key=lambda row: (
        row.get("complianceState") != engine.STATE_PENDING_EVALUATION,
        _parse_instant(row.get("lastEvaluatedAt")) or oldest,
        key(row),
    ))
    return candidates, evaluated


def _asset_key(state_row):
    return (state_row["databaseId"], state_row["assetId"])


def get_evaluations(event, database_id, asset_id, query_params):
    """Evaluation history of an asset, newest first, externally paged (AssetIndex GSI)."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_asset_evaluation(database_id, asset_id, "GET"):
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
    if not _enforce_asset_evaluation(database_id, asset_id, "GET",
                                     (item or {}).get("complianceState", "")):
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


def overview_summary(database_id, withheld=frozenset()):
    """`(summary, totalAssets, reads)`: per-state counts and the evaluation-error overlay of a
    database, without reading the rows. Each state is one `Select=COUNT` query on the
    ComplianceStateIndex partition of that state narrowed to the database (its sort key), so the
    reads grow with the database's index entries, never with the item sizes, and no row is
    returned; the overlay is one COUNT over the database's partition filtered on
    `lastEvaluationStatus`. Exact at the time of the read. The records of the `withheld` (archived)
    assets are read by key and left out of every count."""
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
    if withheld:
        withheld_counts, withheld_errors = withheld_state_counts(
            asset_state_table, database_id, withheld, OVERVIEW_STATES,
            engine.EVALUATION_STATUS_ERROR)
        for state in OVERVIEW_STATES:
            summary[state] = max(0, summary[state] - withheld_counts[state])
        errors = max(0, errors - withheld_errors)
        reads += -(-len(withheld) // 100)
    summary[OVERVIEW_ERROR_BUCKET] = errors
    return summary, sum(summary[state] for state in OVERVIEW_STATES), reads


def _without(withheld, accept=None):
    """An `accept` for `listing.query_page` that drops the rows of the `withheld` asset ids and
    otherwise applies `accept`."""
    def accept_row(row):
        if row.get("assetId") in withheld:
            return False
        return accept is None or accept(row)
    return accept_row


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
    """`(query kwargs, token key attributes, filtered, accept)` for the overview's filters: a state
    filter reads the ComplianceStateIndex partition of that state narrowed to the database;
    `sort=name` reads the DatabaseAssetNameIndex; otherwise the database's partition of the table,
    in assetId order. The other filters are a FilterExpression on that read, which never names a
    key attribute of the table or index read (DynamoDB refuses it); `accept` (or None) is the part
    of the text filter matched in code (`listing.asset_text_filter`)."""
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
    accept = None
    if filters["schemaName"]:
        conditions.append(Attr("schemaName").eq(filters["schemaName"]))
    if filters["schemaSource"]:
        conditions.append(Attr("schemaSource").eq(filters["schemaSource"]))
    if filters["evaluationError"] == "true":
        conditions.append(Attr("lastEvaluationStatus").eq(engine.EVALUATION_STATUS_ERROR))
    elif filters["evaluationError"] == "false":
        conditions.append(Attr("lastEvaluationStatus").ne(engine.EVALUATION_STATUS_ERROR))
    if filters["text"]:
        # A row written before the search attribute existed is matched on its id, in code.
        condition, accept = listing.asset_text_filter(filters["text"])
        conditions.append(condition)
    if conditions:
        expression = conditions[0]
        for condition in conditions[1:]:
            expression = expression & condition
        kwargs["FilterExpression"] = expression
    return kwargs, keys, bool(conditions), accept


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
    # `sort=name` without a state reads DatabaseAssetNameIndex. While DynamoDB backfills that index
    # (the update that adds it to an existing table returns before the backfill ends) a read of it
    # is refused, so the page is answered in the default order and flagged.
    name_index = filters["sort"] == "name" and not filters["state"]
    name_sort_unavailable = False
    kwargs, keys, filtered, accept = _overview_query(database_id, filters)
    # An archived asset keeps its record under the live database id. A caller refused the
    # database's archived id (`<databaseId>#deleted`) does not see the records of its archived
    # assets, in the rows or in the counts, as the asset listing does not show them.
    withheld = ArchivedAssets(asset_table).withheld(
        database_id, lambda stored_id: _enforce_evaluation(stored_id, "GET"))
    read_limit = None
    if withheld:
        accept = _without(withheld, accept)
        if not filtered:
            # At most len(withheld) rows are dropped, so a page plus that many fills in one read.
            read_limit = min(page_size + len(withheld), listing.FILTERED_READ_LIMIT)
    start_key = None
    if query_params.get("startingToken"):
        start_key = listing.decode_token(query_params["startingToken"], keys)
        if start_key is None and name_index:
            # A listing that began in the default order, during the backfill, continues in it.
            start_key = listing.decode_token(query_params["startingToken"], listing.BASE_STATE_KEYS)
            name_sort_unavailable = start_key is not None
        if start_key is None:
            return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)

    rows = next_key = None
    if not name_sort_unavailable:
        try:
            rows, next_key, _ = listing.query_page(asset_state_table, kwargs, page_size, keys,
                                                   start_key=start_key, filtered=filtered,
                                                   accept=accept, read_limit=read_limit)
        except ClientError as error:
            if not (name_index and _index_backfilling(error)):
                raise
            logger.warning(f"{listing.DATABASE_ASSET_NAME_INDEX} is backfilling; answering the "
                           f"overview of {database_id} in assetId order")
            name_sort_unavailable = True
            if start_key is not None:
                start_key = {name: start_key[name] for name in listing.BASE_STATE_KEYS}
    if name_sort_unavailable:
        kwargs, keys, filtered, accept = _overview_query(database_id, dict(filters, sort="assetId"))
        if withheld:
            accept = _without(withheld, accept)
        rows, next_key, _ = listing.query_page(asset_state_table, kwargs, page_size, keys,
                                               start_key=start_key, filtered=filtered,
                                               accept=accept, read_limit=read_limit)
    if filters["state"] and filters["sort"] == "name":
        rows.sort(key=lambda row: row.get("assetNameSort") or row.get("assetId", ""))
    for row in rows:
        row.setdefault("assetName", "")
        row.pop("assetNameSort", None)
        row.pop("assetSearchText", None)

    summary, total, _ = overview_summary(database_id, withheld)
    body = {
        "databaseId": database_id,
        "totalAssets": total,
        "summary": summary,
        "assets": rows,
    }
    if name_sort_unavailable:
        # The rows are in assetId order: the name index is still being built.
        body["nameSortUnavailable"] = True
    if next_key:
        body["NextToken"] = listing.encode_token(next_key)
    return success(body=body)


def _index_backfilling(error):
    """Whether a ClientError is DynamoDB refusing a read of a global secondary index it is still
    backfilling ("Cannot read from backfilling global secondary index")."""
    details = error.response.get("Error", {}) if hasattr(error, "response") else {}
    return (details.get("Code") == "ValidationException"
            and "backfilling" in str(details.get("Message", "")).lower())


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


