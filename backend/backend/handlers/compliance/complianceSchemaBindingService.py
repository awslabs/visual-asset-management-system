#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Schema Binding Service handler.

- GET    /compliance/bind/{databaseId}            — database binding and asset overrides (paged)
- PUT    /compliance/bind/{databaseId}            — bind a schema to a database
- DELETE /compliance/bind/{databaseId}            — remove the database binding
- PUT    /compliance/bind/{databaseId}/{assetId}  — bind a schema to an asset (override)
- DELETE /compliance/bind/{databaseId}/{assetId}  — remove the asset override

A database binding lives on the database row (`complianceSchemaName`, `complianceAutoEval`,
`complianceSchemaUpdatedAt`); asset state rows carry `schemaName` + `schemaSource`
("database" or "asset"). An asset-level binding always overrides the database binding: a
database rebinding touches only rows with schemaSource "database", and removing an asset
override falls the asset back to the database schema (or removes its row when there is none).

Binding and unbinding are authorized on three objects: the `complianceBinding` object of the
database (`PUT` binds, `DELETE` unbinds; an asset-level binding uses the asset's database), the
compliance schema being bound (or currently bound) with `GET` in the schema's scope (its
`databaseId`, a database id or GLOBAL), and a `GET` on the target — the `database` row
for database paths, the `asset` row for asset paths — so ABAC constraints on the target's
attributes still apply. A binding writes only compliance records, never the database or asset row,
so it needs no write permission on either: a role that may edit a database's assets cannot bind a
schema to them, and a role that may bind one cannot edit them. Reading the bindings is authorized on
a `GET` of the database and of its bound schema.

Only a schema whose body is a vams-rules-v1 document can be bound (both paths); a `legacy` row —
one the schema listing reports with `schemaFormat: legacy` — is refused with LEGACY_SCHEMA_MESSAGE.

A binding change never lifts a quarantine without a record (D118). Binding a new schema to a
database, removing the binding, setting an asset override or clearing one keeps every quarantined
asset it reaches `quarantined` (its download block with it, under `quarantineBlocksDownload`) and
marks it awaiting re-evaluation (`store.AWAITING_REEVALUATION_*`) until an evaluation under the
binding now in force decides it; the other assets move to `pending_evaluation`, or lose their
record when no binding applies, as before. Every asset whose record the change touches gets its
own `schema_binding_applied_to_asset` audit entry (previous state, new state, schema), beside the
database-level entry. With automatic evaluation on, the change queues one evaluation message per
affected asset on the compliance trigger's queue, which evaluates them the way automatic
evaluation does (`complianceTrigger.automatic_evaluation_target`, the decision Evaluate All and
the sweeps use), in its bounded batches; with it off nothing is queued and a sweep or Evaluate All
evaluates them. Re-binding the schema already bound with the same automatic-evaluation setting,
or an asset to the override it already has, changes nothing and answers `200` with
`changed: false`; changing only automatic evaluation updates the flag (and, turned on, queues the
assets still pending).

A database binding change (bind, change, removal, and automatic evaluation turned on with assets
pending) is applied to the asset records in the background (D161, `complianceBindingApply`): the
request saves the binding, audits it naming the caller and answers `202` with the number of assets
the change goes through (`applyStatus` `applying`, `applyId`, `assetCount`, `apply`); the
compliance job Lambda applies it page by page. While it is applied, another binding change of
the database answers `409`; a stalled change resumes when the same change is requested again.
The binding read reports the last change's `apply` (status, counts, times). An asset-level
override is one record and is written in the request, as before.
"""

import base64
import json
import os
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.apiRoutes import API_COMPLIANCE_BIND_ASSET, API_COMPLIANCE_BIND_DATABASE
from common.compliance import evaluationEngine as engine
from common.compliance import databaseRetirement
from common.compliance import listing
from common.compliance.assetAccess import AssetAccess, archived_database_id, flush_denials
from common.dynamodb import query_all_items
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from common.compliance.routeAudit import (
    audit_outcome, created_value, failure_outcome, request_parameters, response_count,
)
from customLogging.auditLogging import audit_request, log_action
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from handlers.compliance import complianceBindingApply as apply
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
from models.compliance import GLOBAL_DATABASE_ID, BindSchemaRequestModel

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceSchemaBindingService")
# Queues a database binding change's first message on the compliance job queue, where the job
# Lambda applies it, and an asset override's evaluation on the compliance trigger's queue (both set
# by complianceFunctions.ts); unset, nothing is queued.
sqs_client = boto3.client("sqs", config=retry_config)
trigger_queue_url = os.environ.get("COMPLIANCE_TRIGGER_QUEUE_URL", "")
job_queue_url = os.environ.get("COMPLIANCE_JOB_QUEUE_URL", "")

claims_and_roles = {}

COMPLIANCE_SCHEMA_OBJECT_TYPE = "complianceSchema"
COMPLIANCE_BINDING_OBJECT_TYPE = "complianceBinding"
DATABASE_OBJECT_TYPE = "database"
ASSET_OBJECT_TYPE = "asset"
SCHEMA_SOURCE_DATABASE = "database"
SCHEMA_SOURCE_ASSET = "asset"
STATE_PENDING_EVALUATION = "pending_evaluation"
STATE_QUARANTINED = "quarantined"
STATE_UNKNOWN = "unknown"
# The per-asset audit entry of a binding change (D118), and the database-level entry of a change of
# automatic evaluation alone.
AUDIT_BINDING_APPLIED_TO_ASSET = "schema_binding_applied_to_asset"
AUDIT_BINDING_AUTO_EVAL_CHANGED = "schema_binding_auto_eval_changed"
REASON_SCHEMA_BOUND, REASON_SCHEMA_UNBOUND, REASON_OVERRIDE_SET, REASON_OVERRIDE_CLEARED = \
    store.AWAITING_REEVALUATION_REASONS
# The binding a kept-quarantined record loses when no schema applies to it any more (a database
# unbind, or an override cleared with no database binding): it then names no schema (D127).
UNBOUND_RECORD_FIELDS = apply.UNBOUND_RECORD_FIELDS
# The one client-visible message for a bind that names a schema whose stored body is not a
# vams-rules-v1 document (a `legacy` row in the schema listing). It names no schema.
LEGACY_SCHEMA_MESSAGE = "Schema body must be a vams-rules-v1 document"

# Page bounds for the asset-override listing. The full set is read (its count is reported), then
# offset-sliced to one page in assetId order; the token carries the offset of the next page.
DEFAULT_LIST_PAGE_SIZE = 100
MAX_LIST_PAGE_SIZE = 500
INVALID_PAGINATION_TOKEN_MESSAGE = "Invalid pagination token"

try:
    asset_state_table_name = get_table_name(ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE)
    schema_table_name = get_table_name(ResourceKeys.COMPLIANCE_SCHEMA_STORAGE_TABLE)
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

asset_state_table = dynamodb.Table(asset_state_table_name)
schema_table = dynamodb.Table(schema_table_name)
database_table = dynamodb.Table(database_table_name)
asset_table = dynamodb.Table(asset_table_name)


#######################
# Lambda handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    # The request's audit context: its action records are written together when it ends.
    with audit_request(event, context):
        return _handle_request(event)


def _handle_request(event) -> APIGatewayProxyResponseV2:
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
            log_action(event, **failure_outcome(403))
            return authorization_error()

        if method == "GET":
            return handle_get_request(event)
        elif method == "PUT":
            return handle_put_request(event)
        elif method == "DELETE":
            return handle_delete_request(event)
        else:
            return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        log_action(event, **failure_outcome(400))
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        log_action(event, **failure_outcome(400))
        return general_error(body={"message": str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        log_action(event, **failure_outcome(500))
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
    if API_COMPLIANCE_BIND_DATABASE.matches(path):
        response = get_bindings(event, path_params.get("databaseId"), query_params)
        log_action(event, "complianceBindingGet", **audit_outcome(response),
                   database_id=path_params.get("databaseId"),
                   request_parameters=request_parameters(query_params, ("maxItems", "startingToken")),
                   item_count=response_count(response, "assetOverrides"))
        return response
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_put_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_BIND_ASSET.matches(path):
        request = parse(_parse_body(event), model=BindSchemaRequestModel)
        response = bind_schema_to_asset(event, path_params.get("databaseId"),
                                        path_params.get("assetId"), request)
        log_action(event, "complianceBindingSet", **audit_outcome(response),
                   database_id=path_params.get("databaseId"), asset_id=path_params.get("assetId"),
                   entity_id=path_params.get("assetId"), changes={"schemaName": request.schemaName})
        return response
    if API_COMPLIANCE_BIND_DATABASE.matches(path):
        request = parse(_parse_body(event), model=BindSchemaRequestModel)
        response = bind_schema_to_database(event, path_params.get("databaseId"), request)
        log_action(event, "complianceBindingSet", **audit_outcome(response),
                   database_id=path_params.get("databaseId"),
                   changes={"schemaName": request.schemaName,
                            "complianceAutoEval": request.complianceAutoEval},
                   **_apply_started(response))
        return response
    return validation_error(body={"message": "Method not allowed"}, event=event)


def _apply_started(response):
    """`details.applyId` of the background apply a database binding change started (D197), from the
    service's answer, so the record joins the apply the binding read shows; nothing when the change
    started none (an unchanged binding, a refusal)."""
    apply_id = created_value(response, "applyId")
    return {"details": {"applyId": apply_id}} if apply_id is not None else {}


def handle_delete_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_BIND_ASSET.matches(path):
        response = unbind_schema_from_asset(event, path_params.get("databaseId"),
                                            path_params.get("assetId"))
        log_action(event, "complianceBindingDelete", **audit_outcome(response),
                   database_id=path_params.get("databaseId"), asset_id=path_params.get("assetId"),
                   entity_id=path_params.get("assetId"))
        return response
    if API_COMPLIANCE_BIND_DATABASE.matches(path):
        response = unbind_schema_from_database(event, path_params.get("databaseId"))
        log_action(event, "complianceBindingDelete", **audit_outcome(response),
                   database_id=path_params.get("databaseId"), **_apply_started(response))
        return response
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

def _enforce_schema_read(schema_name, database_id, schema_row=None):
    """Tier-2 GET check on the schema a binding names, in the schema's own scope (its `databaseId`:
    a database id or GLOBAL). Binding, unbinding and reading a binding each need read access to the
    schema involved; the write itself is authorized on the database's `complianceBinding` object.
    With no schema (a database or asset without a binding) the database operated on stands in as
    the scope. `schema_row` is the schema's latest version when the caller has already read it.
    Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    if not schema_name:
        scope = database_id
    else:
        row = schema_row if schema_row is not None else _latest_schema_row(schema_name)
        # An absent schema is checked in the GLOBAL scope, so a caller without schema read access
        # is refused rather than told whether the name exists.
        scope = (row or {}).get("databaseId") or GLOBAL_DATABASE_ID
    return CasbinEnforcer(claims_and_roles).enforce({
        "object__type": COMPLIANCE_SCHEMA_OBJECT_TYPE,
        "complianceSchemaName": schema_name or "",
        "databaseId": scope or "",
    }, "GET")


def _latest_schema_row(schema_name):
    """A schema's latest version row, or None."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def _enforce_binding(database_id, action):
    """Tier-2 check on the `complianceBinding` object of a database: `PUT` binds a schema in it (to
    the database or to one of its assets), `DELETE` unbinds one. Fails closed on an empty token
    list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce({
        "object__type": COMPLIANCE_BINDING_OBJECT_TYPE,
        "databaseId": database_id or "",
    }, action)


def _enforce_database(db_item, database_id, action):
    """Tier-2 check on the database a binding targets. The stored row is annotated and evaluated as
    stored (so ABAC constraints on its attributes apply); when there is none, the requested id
    stands in so an unauthorized caller is refused rather than told the database is absent. Fails
    closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    database = dict(db_item) if db_item else {"databaseId": database_id}
    database["object__type"] = DATABASE_OBJECT_TYPE
    return CasbinEnforcer(claims_and_roles).enforce(database, action)


def _enforce_asset(asset_item, database_id, asset_id, action):
    """Tier-2 check on the asset a binding targets, shaped like `_enforce_database`: the stored
    asset row (tags, assetType, assetName, ...) annotated as an `asset`, or the requested ids when
    the row is absent. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    asset = dict(asset_item) if asset_item else {"databaseId": database_id, "assetId": asset_id}
    asset["object__type"] = ASSET_OBJECT_TYPE
    return CasbinEnforcer(claims_and_roles).enforce(asset, action)


def _asset_as_stored(database_id, asset_id):
    """`(asset row, database id it is stored under)`, looked up as the asset routes look up an
    archived asset: the live row, else the archived row under `<databaseId>#deleted`, else
    `(None, database_id)`. The binding and asset checks of an override then use that id, so the
    engine's archived-record rule applies to an archived asset's override as to the asset."""
    item = store.get_asset_item(database_id, asset_id)
    if item:
        return item, database_id
    archived_id = archived_database_id(database_id)
    item = store.get_asset_item(archived_id, asset_id)
    if item:
        return item, archived_id
    return None, database_id


def _validate_database(database_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
    })


def _validate_database_and_asset(database_id, asset_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID"},
    })


#######################
# Paging helpers
#######################

def _page_arguments(event, query_params):
    """(page_size, offset, None) from `maxItems` / `startingToken`, or (None, None, response) when
    either is malformed. The token is Base64 JSON `{"offset": n}`."""
    try:
        page_size = int(query_params.get("maxItems", str(DEFAULT_LIST_PAGE_SIZE)))
    except (TypeError, ValueError):
        return None, None, validation_error(body={"message": "maxItems must be an integer"}, event=event)
    page_size = max(1, min(page_size, MAX_LIST_PAGE_SIZE))

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


#######################
# Business logic
#######################

def resolved_auto_eval(requested, db_item):
    """The `complianceAutoEval` a database binding stores: the requested value when the request
    names one; otherwise the stored value of an existing binding (a database row that names a
    schema; a stored value that is not `true` reads as off, as the trigger reads it), and `true`
    for a new binding."""
    if requested is not None:
        return bool(requested)
    if (db_item or {}).get("complianceSchemaName"):
        return db_item.get("complianceAutoEval") is True
    return True


def bind_schema_to_database(event, database_id, request: BindSchemaRequestModel):
    """Bind a schema to a database, or change its schema. The request saves the binding on the
    database row, writes the database-level audit entry naming the caller and answers `202` at
    once with the number of assets the change goes through; the job Lambda then applies it to every
    asset record in the background (`complianceBindingApply`). `complianceAutoEval` is the
    request's when it names one; when it is omitted, a database that already has a binding keeps
    its stored value (so changing the schema never turns automatic evaluation on or off) and a new
    binding defaults it to true. While a change of the database is being applied, another answers
    `409`; repeating a stalled change resumes it."""
    (valid, message) = _validate_database(database_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_binding(database_id, "PUT"):
        return authorization_error()
    schema_row = _latest_schema_row(request.schemaName)
    if not _enforce_schema_read(request.schemaName, database_id, schema_row):
        return authorization_error()
    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item")
    if not _enforce_database(db_item, database_id, "GET"):
        return authorization_error()
    if store.database_moving(database_id):
        return databaseRetirement.moving_response(event)

    refused = _schema_bindable(event, request.schemaName, database_id, schema_row)
    if refused is not None:
        return refused

    if not db_item:
        return general_error(body={"message": "Database not found"}, event=event)

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles["tokens"][0]
    old_schema = db_item.get("complianceSchemaName")
    auto_eval = resolved_auto_eval(request.complianceAutoEval, db_item)

    running = _change_while_applying(event, database_id, apply.OPERATION_BIND,
                                     request.schemaName, auto_eval, actor)
    if running is not None:
        return running

    if old_schema == request.schemaName:
        return _rebind_same_schema(event, database_id, request.schemaName, db_item, auto_eval,
                                   actor)

    operation = apply.OPERATION_CHANGE if old_schema else apply.OPERATION_BIND
    record = apply.begin(database_id, operation, request.schemaName, old_schema, actor,
                         apply.count_assets(database_id, operation), auto_eval)
    if record is None:
        # Another request's change began between the read and this one.
        return apply.applying_response(event, apply.get_record(database_id))
    try:
        database_table.update_item(
            Key={"databaseId": database_id},
            UpdateExpression=(
                "SET complianceSchemaName = :schema,"
                " complianceSchemaUpdatedAt = :now,"
                " complianceAutoEval = :autoEval"
            ),
            ExpressionAttributeValues={
                ":schema": request.schemaName,
                ":now": now,
                ":autoEval": auto_eval,
            },
        )
    except Exception:
        apply.finish(record, apply.STATUS_FAILED, reason=apply.FAILED_NOT_SAVED, audit=False)
        raise

    store.write_audit(
        database_id, "*",
        event_type="schema_bound_to_database",
        actor=actor,
        schema_name=request.schemaName,
        details={"previousSchema": old_schema, "complianceAutoEval": auto_eval,
                 "applyId": record["applyId"], "assetCount": record["assetCount"]},
    )
    record = _queue_first_message(record)

    return _accepted(record, {
        "message": (f"Schema '{request.schemaName}' bound to database '{database_id}'; the "
                    f"binding is being applied to {record['assetCount']} assets"),
        "schemaName": request.schemaName,
        "databaseId": database_id,
        "complianceAutoEval": auto_eval,
        "changed": True,
    })


def _accepted(record, body):
    """The `202` of a binding change applied in the background: the request's answer plus the
    apply (`applyStatus`, `applyId`, `assetCount`, and `apply` as the binding read shows it)."""
    view = apply.answer(record)
    return success(status_code=202, body={
        **body,
        "applyStatus": view["status"],
        "applyId": view["applyId"],
        "assetCount": view["assetCount"],
        "apply": view,
    })


def _queue_first_message(record):
    """Queue the message that runs an apply (begun or resumed); when it cannot be queued, the apply
    is recorded as having none, so it reads stalled at once and the same request resumes it.
    Returns the record as it now stands."""
    if apply.send_message(sqs_client, job_queue_url, record):
        return record
    return apply.message_not_queued(record)


def _change_while_applying(event, database_id, operation, schema_name, auto_eval, actor):
    """The answer to a binding change of a database whose last change is still being applied:
    when that apply is stalled and the request repeats its change, the apply resumes from its
    cursor (`202`); otherwise `409` (`complianceBindingApply.applying_response`). A change whose
    records are all written but whose evaluations stopped being queued resumes the same way when
    the request repeats it; any other request goes on (it replaces that change). None when the
    request goes on."""
    running = apply.applying_record(database_id)
    if running is None:
        current = apply.current_record(database_id)
        if (current is None
                or apply.evaluation_status(current) != apply.EVALUATIONS_STALLED
                or not apply.same_change(current, operation, schema_name, auto_eval)):
            return None
        running = current
    if apply.is_stalled(running) and apply.same_change(running, operation, schema_name, auto_eval):
        resumed = apply.resume(running, actor)
        if resumed is None:
            return apply.applying_response(event, apply.get_record(database_id))
        resumed = _queue_first_message(resumed)
        logger.info(f"Binding apply {resumed['applyId']} of {database_id} resumed")
        body = {"message": (f"The binding change of database '{database_id}' resumes from where "
                            "it stopped"),
                "databaseId": database_id, "changed": True, "resumed": True}
        if resumed.get("operation") == apply.OPERATION_UNBIND:
            body["previousSchema"] = resumed.get("previousSchema")
        else:
            body.update(schemaName=resumed.get("schemaName"),
                        complianceAutoEval=bool(resumed.get("complianceAutoEval")))
        return _accepted(resumed, body)
    return apply.applying_response(event, running)


def _rebind_same_schema(event, database_id, schema_name, db_item, auto_eval, actor):
    """A bind naming the schema the database already has. With the same automatic-evaluation
    setting nothing changes and nothing is recorded; with another, only the flag changes (one
    database-level audit entry). Turning it on queues the evaluations of the assets still pending
    or awaiting re-evaluation in the background, as a binding change is applied (`202`); with none
    pending, or turning it off, the request answers `200`."""
    changed = (db_item.get("complianceAutoEval") is True) != auto_eval
    if not changed:
        return success(body={
            "message": (f"Schema '{schema_name}' is already bound to database '{database_id}' "
                        f"with automatic evaluation {'on' if auto_eval else 'off'}; nothing "
                        "changed"),
            "schemaName": schema_name,
            "databaseId": database_id,
            "complianceAutoEval": auto_eval,
            "changed": False,
            "assetsPendingEvaluation": 0,
            "assetsKeptQuarantined": 0,
            "evaluationsQueued": 0,
        })
    pending = apply.count_assets(database_id, apply.OPERATION_EVALUATE_PENDING) if auto_eval else 0
    record = None
    if pending:
        record = apply.begin(database_id, apply.OPERATION_EVALUATE_PENDING, schema_name, None,
                             actor, pending, auto_eval)
        if record is None:
            return apply.applying_response(event, apply.get_record(database_id))
    try:
        database_table.update_item(
            Key={"databaseId": database_id},
            UpdateExpression="SET complianceAutoEval = :autoEval",
            ExpressionAttributeValues={":autoEval": auto_eval},
        )
    except Exception:
        if record is not None:
            apply.finish(record, apply.STATUS_FAILED, reason=apply.FAILED_NOT_SAVED, audit=False)
        raise
    details = {"complianceAutoEval": auto_eval}
    details.update({"applyId": record["applyId"], "assetCount": pending} if record
                   else {"evaluationsQueued": 0})
    store.write_audit(
        database_id, "*",
        event_type=AUDIT_BINDING_AUTO_EVAL_CHANGED,
        actor=actor,
        schema_name=schema_name,
        details=details,
    )
    body = {
        "message": (f"Automatic evaluation turned {'on' if auto_eval else 'off'} for database "
                    f"'{database_id}'; the asset records are unchanged"),
        "schemaName": schema_name,
        "databaseId": database_id,
        "complianceAutoEval": auto_eval,
        "changed": True,
    }
    if record is None:
        return success(body={**body, "assetsPendingEvaluation": 0, "assetsKeptQuarantined": 0,
                             "evaluationsQueued": 0})
    record = _queue_first_message(record)
    body["message"] += f"; the evaluations of {pending} pending assets are being queued"
    return _accepted(record, body)


def unbind_schema_from_database(event, database_id):
    """Remove the database binding. The request removes it from the database row, writes the
    database-level audit entry naming the caller and answers `202` at once with the number of
    records the removal goes through; the job Lambda then removes the records that inherited the
    binding (keeping a quarantined one, marked) in the background (`complianceBindingApply`).
    While a change of the database is being applied, another answers `409`; repeating a stalled
    removal resumes it."""
    (valid, message) = _validate_database(database_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce_binding(database_id, "DELETE"):
        return authorization_error()
    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item")
    if not _enforce_database(db_item, database_id, "GET"):
        return authorization_error()
    old_schema = (db_item or {}).get("complianceSchemaName")
    if not _enforce_schema_read(old_schema, database_id):
        return authorization_error()
    if store.database_moving(database_id):
        return databaseRetirement.moving_response(event)

    if not db_item:
        return general_error(body={"message": "Database not found"}, event=event)

    actor = claims_and_roles["tokens"][0]
    running = _change_while_applying(event, database_id, apply.OPERATION_UNBIND, None, None, actor)
    if running is not None:
        return running

    if not old_schema:
        return success(body={
            "message": "Database has no schema binding to remove",
            "databaseId": database_id,
        })

    record = apply.begin(database_id, apply.OPERATION_UNBIND, None, old_schema, actor,
                         apply.count_assets(database_id, apply.OPERATION_UNBIND),
                         db_item.get("complianceAutoEval") is True)
    if record is None:
        return apply.applying_response(event, apply.get_record(database_id))
    try:
        database_table.update_item(
            Key={"databaseId": database_id},
            UpdateExpression=(
                "REMOVE complianceSchemaName, complianceSchemaUpdatedAt, complianceAutoEval"
            ),
        )
    except Exception:
        apply.finish(record, apply.STATUS_FAILED, reason=apply.FAILED_NOT_SAVED, audit=False)
        raise

    store.write_audit(
        database_id, "*",
        event_type="schema_unbound_from_database",
        actor=actor,
        schema_name=old_schema,
        details={"applyId": record["applyId"], "assetCount": record["assetCount"]},
    )
    record = _queue_first_message(record)

    return _accepted(record, {
        "message": (f"Schema binding removed from database '{database_id}'; the removal is being "
                    f"applied to {record['assetCount']} assets"),
        "databaseId": database_id,
        "previousSchema": old_schema,
        "changed": True,
    })


def bind_schema_to_asset(event, database_id, asset_id, request: BindSchemaRequestModel):
    """Bind a schema directly to an asset, overriding the database binding.

    The record's write is conditional on the record as read (`store.revision_condition`, D159);
    when it changed meanwhile the record is read again and the override decided on it as it is
    now (quarantined or not, already bound or not). The audit entry follows the write that
    landed."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    # The asset is read by (database_id, asset_id), so the path's database is the asset's database;
    # an archived asset is decided with the id it is stored under.
    asset_item, stored_id = _asset_as_stored(database_id, asset_id)
    if not _enforce_binding(stored_id, "PUT"):
        return authorization_error()
    schema_row = _latest_schema_row(request.schemaName)
    if not _enforce_schema_read(request.schemaName, database_id, schema_row):
        return authorization_error()
    if not _enforce_asset(asset_item, stored_id, asset_id, "GET"):
        return authorization_error()
    if store.database_moving(database_id):
        return databaseRetirement.moving_response(event)

    refused = _schema_bindable(event, request.schemaName, database_id, schema_row)
    if refused is not None:
        return refused

    # An archived asset cannot be evaluated, so it takes no new binding.
    if not asset_item or stored_id != database_id:
        return general_error(body={"message": "Asset not found"}, event=event)

    actor = claims_and_roles["tokens"][0]

    for _attempt in range(store.STATE_WRITE_ATTEMPTS):
        existing = store.get_compliance_record(database_id, asset_id, consistent_read=True) or {}
        if (existing.get("schemaSource") == SCHEMA_SOURCE_ASSET
                and existing.get("schemaName") == request.schemaName):
            return success(body={
                "message": f"Schema '{request.schemaName}' is already bound to the asset; "
                           "nothing changed",
                "databaseId": database_id,
                "assetId": asset_id,
                "schemaName": request.schemaName,
                "schemaSource": SCHEMA_SOURCE_ASSET,
                "changed": False,
                "complianceState": existing.get("complianceState"),
                "evaluationsQueued": 0,
            })
        previous_state = existing.get("complianceState")
        # The time of the write, taken after the reads it depends on (D155).
        now = datetime.now(timezone.utc).isoformat()
        updates = {
            "schemaName": request.schemaName,
            "schemaSource": SCHEMA_SOURCE_ASSET,
            "updatedAt": now,
            **listing.asset_name_attributes(asset_id, (asset_item or {}).get("assetName")),
        }
        if previous_state == STATE_QUARANTINED:
            # Its quarantine, reason and download block hold until a new evaluation decides.
            updates.update(_awaiting(now, request.schemaName, REASON_OVERRIDE_SET))
            new_state = STATE_QUARANTINED
        else:
            updates["complianceState"] = STATE_PENDING_EVALUATION
            new_state = STATE_PENDING_EVALUATION
        try:
            replaced = store.update_asset_state(database_id, asset_id, updates,
                                                computed_from=existing, return_old=True)
        except Exception as e:
            if not store.is_conditional_check_failure(e):
                raise
            continue
        break
    else:
        return general_error(body={"message": store.RECORD_CHANGED_MESSAGE}, event=event)
    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item") or {}
    queued = (queue_evaluations(database_id, [asset_id])
              if db_item.get("complianceAutoEval") is True else 0)

    store.write_audit(
        database_id, asset_id,
        event_type="schema_bound_to_asset",
        actor=actor,
        schema_name=request.schemaName,
        previous_state=store.state_replaced(replaced, existing) or STATE_UNKNOWN,
        new_state=new_state,
        details={
            "previousSchema": existing.get("schemaName"),
            "previousSource": existing.get("schemaSource"),
            "awaitingReevaluation": new_state == STATE_QUARANTINED,
            "evaluationsQueued": queued,
        },
    )

    return success(body={
        "message": f"Schema '{request.schemaName}' bound to asset",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": request.schemaName,
        "schemaSource": SCHEMA_SOURCE_ASSET,
        "changed": True,
        "complianceState": new_state,
        "evaluationsQueued": queued,
    })


def unbind_schema_from_asset(event, database_id, asset_id):
    """Remove an asset's schema override, falling back to the database binding.

    The record's write (or its delete, when no database binding applies and the asset is not
    quarantined) is conditional on the record as read (`store.revision_condition`, D159); when it
    changed meanwhile the record is read again and the removal decided on it as it is now. The
    audit entry follows the write that landed."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    # The asset is read by (database_id, asset_id), so the path's database is the asset's database;
    # an archived asset is decided with the id it is stored under.
    asset_item, stored_id = _asset_as_stored(database_id, asset_id)
    if not _enforce_binding(stored_id, "DELETE"):
        return authorization_error()
    if not _enforce_asset(asset_item, stored_id, asset_id, "GET"):
        return authorization_error()
    existing = store.get_compliance_record(database_id, asset_id, consistent_read=True)
    current_schema = (existing or {}).get("schemaName", "")
    if not _enforce_schema_read(current_schema, database_id):
        return authorization_error()
    if store.database_moving(database_id):
        return databaseRetirement.moving_response(event)

    actor = claims_and_roles["tokens"][0]

    db_item = None
    for attempt in range(store.STATE_WRITE_ATTEMPTS):
        if attempt:
            existing = store.get_compliance_record(database_id, asset_id, consistent_read=True)
            if (existing or {}).get("schemaName", "") != current_schema:
                # Another override replaced the one read: it is decided on its own schema.
                current_schema = (existing or {}).get("schemaName", "")
                if not _enforce_schema_read(current_schema, database_id):
                    return authorization_error()
        if not existing or existing.get("schemaSource") != SCHEMA_SOURCE_ASSET:
            return success(body={
                "message": "Asset has no explicit schema override to remove",
                "databaseId": database_id,
                "assetId": asset_id,
            })
        if db_item is None:
            db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item") or {}
        db_schema = db_item.get("complianceSchemaName")
        previous_state = existing.get("complianceState")
        # The time of the write, taken after the reads it depends on (D155).
        now = datetime.now(timezone.utc).isoformat()
        try:
            if previous_state == STATE_QUARANTINED:
                # Kept quarantined until an evaluation under the database binding (or a release)
                # decides.
                updates = {"updatedAt": now, **_awaiting(now, db_schema, REASON_OVERRIDE_CLEARED)}
                if db_schema:
                    updates.update(schemaName=db_schema, schemaSource=SCHEMA_SOURCE_DATABASE)
                # With no database binding no schema applies: the record names none, so no reader
                # shows the removed override as the asset's schema.
                replaced = store.update_asset_state(
                    database_id, asset_id, updates,
                    remove_fields=() if db_schema else (store.AWAITING_REEVALUATION_SCHEMA,
                                                        *UNBOUND_RECORD_FIELDS),
                    computed_from=existing, return_old=True)
                new_state = STATE_QUARANTINED
            elif db_schema:
                replaced = store.update_asset_state(database_id, asset_id, {
                    "schemaName": db_schema,
                    "schemaSource": SCHEMA_SOURCE_DATABASE,
                    "complianceState": STATE_PENDING_EVALUATION,
                    "updatedAt": now,
                }, computed_from=existing, return_old=True)
                new_state = STATE_PENDING_EVALUATION
            else:
                replaced = store.delete_asset_state(database_id, asset_id, existing,
                                                     table=asset_state_table)
                new_state = STATE_UNKNOWN
        except Exception as e:
            if not store.is_conditional_check_failure(e):
                raise
            continue
        break
    else:
        return general_error(body={"message": store.RECORD_CHANGED_MESSAGE}, event=event)
    queued = (queue_evaluations(database_id, [asset_id])
              if db_schema and db_item.get("complianceAutoEval") is True else 0)

    store.write_audit(
        database_id, asset_id,
        event_type="schema_unbound_from_asset",
        actor=actor,
        schema_name=current_schema,
        previous_state=store.state_replaced(replaced, existing) or STATE_UNKNOWN,
        new_state=new_state,
        details={"fallbackSchema": db_schema,
                 "awaitingReevaluation": new_state == STATE_QUARANTINED,
                 "evaluationsQueued": queued},
    )

    return success(body={
        "message": "Asset schema override removed",
        "databaseId": database_id,
        "assetId": asset_id,
        "fallbackSchema": db_schema,
        "schemaSource": SCHEMA_SOURCE_DATABASE if db_schema else None,
        "complianceState": new_state,
        "evaluationsQueued": queued,
    })


def get_bindings(event, database_id, query_params):
    """The database binding plus one page of the asset-level overrides in the database.

    The overrides are read to exhaustion (the response reports their total), ordered by assetId and
    offset-sliced to `maxItems`; `NextToken` is present while more remain.
    """
    (valid, message) = _validate_database(database_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item")
    if not _enforce_database(db_item, database_id, "GET"):
        return authorization_error()
    db_schema = (db_item or {}).get("complianceSchemaName")
    if not _enforce_schema_read(db_schema, database_id):
        return authorization_error()

    if not db_item:
        return general_error(body={"message": "Database not found"}, event=event)

    page_size, offset, error = _page_arguments(event, query_params)
    if error:
        return error

    # The override of an archived asset is listed only for a caller who passes the listing's own
    # database check on the stored id (`<databaseId>#deleted`) as well, the listing's check made
    # the way the engine decides an archived record, and, for a caller whose policy has asset
    # rules, the asset GET on the archived row. The override of a live asset the caller's asset
    # rules refuse is not listed either (`AssetAccess.live_withheld`), nor, for a caller whose
    # rules read an asset's own fields, that of an asset with no row.
    enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    # An archived row of an earlier, deleted database with the same id is not the database's asset.
    access = AssetAccess(asset_table, enforcer, databaseRetirement.Presenter(store.evaluation_table))
    withheld = access.withheld(
        database_id, lambda stored_id: _enforce_database(
            dict(db_item, databaseId=stored_id), stored_id, "GET"))
    live_withheld = access.live_withheld(database_id)
    withheld = withheld | live_withheld.ids
    asset_overrides = []
    for item in _database_asset_state_rows(database_id):
        if item.get("assetId") in withheld:
            continue
        if (live_withheld.leaves_out(item.get("assetId"))
                and item.get("assetId") not in access.archived_ids(database_id)):
            # A live asset the caller may not read (every live asset of the database, or one with
            # no row decided on its own fields); only the archived assets decided above may be
            # listed.
            continue
        if item.get("schemaSource") == SCHEMA_SOURCE_ASSET:
            asset_overrides.append({
                "assetId": item["assetId"],
                "schemaName": item.get("schemaName"),
                "complianceState": item.get("complianceState"),
            })
    asset_overrides.sort(key=lambda override: override["assetId"])

    # The database's last binding change, while it is applied in the background and after.
    last_apply = apply.answer(apply.current_record(database_id))
    body = {
        "databaseId": database_id,
        "databaseSchema": db_schema,
        "complianceAutoEval": bool(db_item.get("complianceAutoEval", False)),
        "applyStatus": last_apply["status"] if last_apply else None,
        "apply": last_apply,
        "assetOverrides": asset_overrides[offset:offset + page_size],
        "assetOverrideCount": len(asset_overrides),
    }
    if offset + page_size < len(asset_overrides):
        body["NextToken"] = _encode_offset_token(offset + page_size)
    return success(body=body)


#######################
# Table helpers
#######################

def _database_asset_state_rows(database_id):
    """Every asset-state row in a database, paged to exhaustion."""
    return query_all_items(
        asset_state_table, KeyConditionExpression=Key("databaseId").eq(database_id))


def _awaiting(now, schema_name, reason):
    """The re-evaluation marker of a quarantined record a binding change reached
    (`complianceBindingApply.awaiting`)."""
    return apply.awaiting(now, schema_name, reason)


def queue_evaluations(database_id, asset_ids):
    """Queue one evaluation message per asset on the compliance trigger's queue
    (`complianceBindingApply.queue_evaluations`, with this function's queue client). Returns the
    count queued."""
    return apply.queue_evaluations(database_id, asset_ids, sqs_client, trigger_queue_url)


def _schema_visibility(schema_row, database_id):
    """(visible, exists, is_vams_rules) of a schema's latest version row (None when absent):
    whether it exists, whether it is GLOBAL or scoped to `database_id`, and whether its body is a
    vams-rules-v1 document (a legacy body cannot be evaluated, so it cannot be bound)."""
    if not schema_row:
        return False, False, False
    schema_db_id = schema_row.get("databaseId", GLOBAL_DATABASE_ID)
    is_vams_rules = engine.schema_format(schema_row.get("schemaBody")) == engine.VAMS_RULES_V1
    return schema_db_id in (GLOBAL_DATABASE_ID, database_id), True, is_vams_rules


def _schema_bindable(event, schema_name, database_id, schema_row):
    """The 400 that refuses binding `schema_name` in `database_id` — absent, scoped to another
    database, or not a vams-rules-v1 document — or None when it may be bound. `schema_row` is the
    schema's latest version (None when absent), read once for this and the authorization check."""
    visible, exists, is_vams_rules = _schema_visibility(schema_row, database_id)
    if not exists:
        return general_error(body={"message": "Schema not found"}, event=event)
    if not visible:
        return general_error(body={
            "message": "Schema is not available for this database. Only GLOBAL or "
                       "database-scoped schemas can be assigned.",
        }, event=event)
    if not is_vams_rules:
        logger.info(f"Schema '{schema_name}' is not a vams-rules-v1 document; binding refused")
        return general_error(body={"message": LEGACY_SCHEMA_MESSAGE}, event=event)
    return None
