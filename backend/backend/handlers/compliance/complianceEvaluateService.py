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
from common.compliance import executionFailureAccess as failure_access
from common.compliance import listing
from common.compliance.assetAccess import (
    AssetAccess, flush_denials, withheld_state_counts,
)
from common.compliance.knownDatabase import database_not_found
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
    finally:
        # The denials of this request's asset checks (`AssetAccess`).
        flush_denials()


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


def _caller_enforcer():
    """The caller's CasbinEnforcer, or None for a request without tokens."""
    return CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None


def _asset_access():
    """The request's asset decisions (`AssetAccess`), with the caller's enforcer."""
    return AssetAccess(asset_table, _caller_enforcer())


def _execution_reads():
    """The request's execution-read decisions (`executionFailureAccess`), with the caller's
    enforcer: which failure reasons of the rule results and quarantine records it returns the
    caller may read."""
    return failure_access.new_scope(_caller_enforcer())


def _enforce_asset_evaluation(database_id, asset_id, action, compliance_state="", access=None):
    """Tier-2 check on the compliance evaluation object of one asset, and, for a caller whose
    policy has asset rules, the asset GET the asset routes make on the asset's row as stored. For
    an archived asset the evaluation object is also decided with its stored database id
    `<databaseId>#deleted` (`AssetAccess.decide`; `access` is the request's, when it has one)."""
    return (access or _asset_access()).decide(
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


def _archived_visible(access, database_id, asset_id):
    """Whether the caller may see an archived asset: the evaluation GET on its stored id
    `<databaseId>#deleted` (asked once per database of the request) and, for a caller whose policy
    has asset rules, the asset GET on its archived row, the decision the overview and the record
    reads make on an archived asset's record (`AssetAccess.archived_asset_visible`)."""
    return access.archived_asset_visible(
        database_id, asset_id, lambda stored_id: _enforce_evaluation(stored_id, "GET"),
        cache_key=ARCHIVE_VISIBILITY_CACHE_KEY)


def _hidden_archived(access, database_id, asset_id):
    """Whether an asset is archived and the caller may not see it. Such an asset is counted
    nowhere in the response of a sweep or of Evaluate All (only the operator log counts it), as the
    asset listing does not show it to that caller (`AssetAccess.withholds`)."""
    return access.withholds(
        database_id, asset_id, lambda stored_id: _enforce_evaluation(stored_id, "GET"),
        cache_key=ARCHIVE_VISIBILITY_CACHE_KEY)


def _live_not_readable(access, database_id, asset_id):
    """Whether an asset is live and the caller's asset rules refuse the asset GET on its live row
    (`AssetAccess.live_denied`). A sweep and Evaluate All do not evaluate it and count it in
    `skippedNotAuthorized`, as an asset of a database the caller may not evaluate."""
    return access.live_denied(database_id, asset_id)


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
        "ruleResults": failure_access.rule_results_for_caller(
            _execution_reads(), result.get("ruleResults", [])),
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
    `skippedNotAuthorized`, the reason they were skipped) and never listed. So is a live asset the
    caller may not read: a caller whose policy has asset rules needs the asset GET on its row, as
    the asset routes ask it (`_live_not_readable`); for a caller whose rules read an asset's own
    fields a record whose asset has no row is skipped the same way. An archived asset is
    not evaluated either (its row sits in the archive and cannot be read): it is counted in
    `skipped` and `skippedArchived`, and its compliance record is left as it is. Only the archived
    assets the caller may see are counted (`_archived_visible`): an archived asset the caller may
    not see (its database's archive, or the asset itself by the asset GET) is counted nowhere in
    the response (`skippedNotAuthorized`
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
    archived = _asset_access()
    # The live rows of a database's candidates are read together, when a decision first needs one
    # (a caller whose asset rules read asset fields only), and only for a database the caller may
    # evaluate.
    candidates_by_database = {}
    for asset in bound_assets:
        candidates_by_database.setdefault(asset["databaseId"], []).append(asset["assetId"])
    expected_databases = set()
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
        if database_id not in expected_databases:
            expected_databases.add(database_id)
            archived.expect((database_id, asset_id)
                            for asset_id in candidates_by_database[database_id])
        # A live asset the caller may not read is skipped as not authorized, as the asset routes
        # refuse it.
        if _live_not_readable(archived, database_id, asset["assetId"]):
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
            # Archived and visible to the caller, or archived since the archive was read.
            if _archived_visible(archived, database_id, asset["assetId"]):
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
                f"archived assets the caller may not see, not in the response), {remaining} remaining "
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
    in a sweep, only the archived assets the caller may see are counted (`_archived_visible`): an
    archived asset the caller may not see is counted nowhere in the response (`withoutSchema` and
    `assetsRemaining` included). A live asset the caller may not read (a caller whose policy has
    asset rules needs the asset GET on its row) is not evaluated and is counted in `skipped` and
    `skippedNotAuthorized`; for a caller whose rules read an asset's own fields, so is a record
    whose asset has no row (it is not removed then: `removedOrphans` counts only the records the
    caller may read).

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
    skipped_not_authorized = 0
    without_schema = 0
    beyond_cap = 0
    beyond_budget = 0
    budget_spent = False
    archived = _asset_access()
    rows, evaluated_earlier = _sweep_candidates(
        _database_rows(database_id), sweep_started_at, lambda row: row.get("assetId", ""))
    if archived.reads_asset_rows():
        # Decided on each asset's own row: the database's live rows are read once, projected, and
        # after its records, so every record read names an asset whose row was written before it
        # (the trigger records an asset after its row is written).
        archived.live_rows(database_id)
    for row in rows:
        if _hidden_archived(archived, database_id, row.get("assetId", "")):
            hidden_archived += 1
            continue
        # A live asset the caller may not read is not evaluated, as the asset routes refuse it.
        if _live_not_readable(archived, database_id, row.get("assetId", "")):
            skipped_not_authorized += 1
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
            # Archived and visible to the caller, or archived since the archive was read.
            if _archived_visible(archived, database_id, row["assetId"]):
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
                f"{skipped_not_authorized} skipped as assets the caller may not read, "
                f"{skipped_archived + hidden_archived} archived assets skipped "
                f"({hidden_archived} the caller may not see, not in the response)")
    body = {
        "message": f"Evaluation triggered for {len(triggered)} assets",
        "databaseId": database_id,
        "assetsTriggered": triggered,
        "assetsRemaining": remaining,
        "superseded": superseded,
        "registered": registered,
        "withoutSchema": without_schema,
        "skipped": skipped_archived + skipped_not_authorized,
        "skippedNotAuthorized": skipped_not_authorized,
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
    reads = _execution_reads()
    result = {"evaluations": [failure_access.evaluation_for_caller(reads, evaluation)
                              for evaluation in response.get("Items", [])]}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = base64.b64encode(
            json.dumps(response["LastEvaluatedKey"], default=str).encode("utf-8")).decode("utf-8")
    return success(body=result)


def get_compliance_state(event, database_id, asset_id):
    """The compliance state row of an asset ("unknown" when it has none). An archived asset is not
    evaluated while it is archived: a caller allowed to read its record gets it with
    `complianceState` `archived` and the stored state as `recordedComplianceState`, which applies
    again once the asset is unarchived."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    item = store.get_compliance_record(database_id, asset_id)
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    archived = _asset_access()
    if not _enforce_asset_evaluation(database_id, asset_id, "GET",
                                     (item or {}).get("complianceState", ""), archived):
        return authorization_error()

    if not item:
        item = {
            "databaseId": database_id,
            "assetId": asset_id,
            "complianceState": engine.STATE_UNKNOWN,
            "schemaName": None,
            "schemaSource": None,
        }
    if archived.is_archived(database_id, asset_id):
        item = dict(item, complianceState=engine.STATE_ARCHIVED,
                    recordedComplianceState=item["complianceState"])
    return success(body=failure_access.state_record_for_caller(_execution_reads(), item))


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
    `lastEvaluationStatus`. Exact at the time of the read. The records of the `withheld` assets
    (archived, and live ones the caller may not read) are read by key, 100 a BatchGetItem, and left
    out of every count, so the counts, `totalAssets` and the share of compliant assets cover the
    live assets the caller may read only."""
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


def overview_summary_readable(database_id, readable):
    """`(summary, totalAssets, records, reads)` for a caller whose asset rules read an asset's own
    fields: the per-state counts and the evaluation-error overlay of the records of the `readable`
    assets only (`AssetAccess.live_withheld`: the live assets whose rows the caller may read), so an
    archived asset, a refused one and a record with no asset row count nowhere. Each state reads
    its ComplianceStateIndex partition narrowed to the database projected on `assetId`, and the
    overlay the database's error records the same way: the items and the pages `overview_summary`'s
    COUNT queries read (`listing.query_ids`), and no record by key. `records` is how many records
    were read in all, so the listing knows how many rows it leaves out."""
    summary = {}
    reads = 0
    records = 0
    for state in OVERVIEW_STATES:
        ids, spent = listing.query_ids(asset_state_table, {
            "IndexName": COMPLIANCE_STATE_INDEX,
            "KeyConditionExpression": (Key("complianceState").eq(state)
                                       & Key("databaseId").eq(database_id)),
        })
        summary[state] = sum(1 for asset_id in ids if asset_id in readable)
        records += len(ids)
        reads += spent
    error_ids, spent = listing.query_ids(asset_state_table, {
        "KeyConditionExpression": Key("databaseId").eq(database_id),
        "FilterExpression": Attr("lastEvaluationStatus").eq(engine.EVALUATION_STATUS_ERROR),
    })
    reads += spent
    summary[OVERVIEW_ERROR_BUCKET] = sum(1 for asset_id in error_ids if asset_id in readable)
    return summary, sum(summary[state] for state in OVERVIEW_STATES), records, reads


def _without(withheld, accept=None, readable=None):
    """An `accept` for `listing.query_page` that drops the rows of the `withheld` asset ids, and,
    with `readable`, those of every asset not among them, and otherwise applies `accept`."""
    def accept_row(row):
        if row.get("assetId") in withheld:
            return False
        if readable is not None and row.get("assetId") not in readable:
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

    The rows and the counts cover the assets the caller may read: an archived asset is left out
    for every caller, and so is a live asset whose row the caller's asset rules refuse the asset
    GET on (`AssetAccess.live_withheld`); a caller refused the asset GET on the database's assets
    gets an empty overview with zero counts. For a caller whose asset rules read an asset's own
    fields, a record whose asset has no row (left by a permanently deleted asset until it is
    removed) is left out too, and the counts are of the readable assets' records
    (`overview_summary_readable`).

    An id that names no database (`store.database_known`) answers `400` "Database not found",
    after the authorization check on the id, rather than an empty overview.
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
    # An archived asset keeps its record under the live database id, and it is not evaluated while
    # it is archived. Its record is left out of the rows and the counts, for every caller, as the
    # asset listing leaves archived assets out (`AssetAccess.left_out`). A live asset the caller's
    # asset rules refuse is left out as well, as the asset listing leaves it out
    # (`AssetAccess.live_withheld`): every asset of the database, or those whose rows are refused.
    # For a caller whose rules read an asset's own fields only the readable live assets are kept
    # (`readable`): that leaves out the archived ones, the refused ones and a record whose asset
    # has no row, which has no fields to decide on.
    access = _asset_access()
    live_withheld = access.live_withheld(database_id)
    readable = live_withheld.readable
    if readable is None:
        withheld = access.left_out(database_id) | live_withheld.ids
    else:
        withheld = frozenset()
    read_limit = None
    if withheld or readable is not None:
        accept = _without(withheld, accept, readable)
        if withheld and not filtered:
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
    # A well-formed request the caller may make of this id, so the answer may say whether the id
    # names a database: one that names none is not an empty overview.
    if not store.database_known(database_id):
        return database_not_found(event)
    if live_withheld.everything:
        # The asset GET refuses every asset of the database: no row and no count is the caller's.
        summary = dict.fromkeys(OVERVIEW_STATES, 0)
        summary[OVERVIEW_ERROR_BUCKET] = 0
        return success(body={"databaseId": database_id, "totalAssets": 0, "summary": summary,
                             "assets": []})

    readable_summary = None
    if readable is not None:
        # The counts first: they say how many records the rows leave out, so an unfiltered page
        # plus that many fills in one read.
        readable_summary = overview_summary_readable(database_id, readable)
        dropped = readable_summary[2] - readable_summary[1]
        if dropped and not filtered:
            read_limit = min(page_size + dropped, listing.FILTERED_READ_LIMIT)

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
        if withheld or readable is not None:
            accept = _without(withheld, accept, readable)
        rows, next_key, _ = listing.query_page(asset_state_table, kwargs, page_size, keys,
                                               start_key=start_key, filtered=filtered,
                                               accept=accept, read_limit=read_limit)
    if filters["state"] and filters["sort"] == "name":
        rows.sort(key=lambda row: row.get("assetNameSort") or row.get("assetId", ""))
    for row in rows:
        row.setdefault("assetName", "")
        row.pop("assetNameSort", None)
        row.pop("assetSearchText", None)
    reads = _execution_reads()
    rows = [failure_access.state_record_for_caller(reads, row) for row in rows]

    if readable_summary is not None:
        summary, total = readable_summary[0], readable_summary[1]
    else:
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


