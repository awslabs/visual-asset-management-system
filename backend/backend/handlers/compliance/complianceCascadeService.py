#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Cascade Service handler.

- GET  /compliance/cascades                       — list cascades awaiting approval
- POST /compliance/cascades                       — create a cascade
- GET  /compliance/cascades/{cascadeId}           — get a cascade
- POST /compliance/cascades/{cascadeId}/approve   — approve a cascade and start it
- POST /compliance/cascades/{cascadeId}/reject    — reject a cascade

Cascade table (PK cascadeId; GSI StateIndex on state/createdAt).

A cascade awaiting approval past its `approvalTimeoutAt` is aborted with abortReason
"approval timed out" wherever it is read: the listing (which then omits it), a get, an approve or
reject (refused), and the trigger's check for a pending cascade.

An automatic cascade (opened by the trigger) holds a marker row on its parent while it awaits
approval (store.cascade_pending_marker); approving, rejecting or timing it out releases it. A
cascade created here is not automatic: it opens regardless of a pending one and claims no marker.

A cascade runs in the executor Lambda (complianceCascadeExecutor), invoked asynchronously once
the row is in the `executing` state; the request returns 202 and the client observes completion
through GET /compliance/cascades/{cascadeId} (`state` executing -> completed | aborted).

A cascade read or listing is authorized on the parent's database, but its descendants may sit in
other databases. Each descendant is returned only to a reader who may read that asset's compliance
record (`_NodeVisibility`); those in a database the reader may not read are counted in
`unauthorizedNodes`, without their ids, names or verdicts.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.apiRoutes import (
    API_COMPLIANCE_CASCADE_APPROVE,
    API_COMPLIANCE_CASCADE_BY_ID,
    API_COMPLIANCE_CASCADE_REJECT,
    API_COMPLIANCE_CASCADES,
)
from common.compliance import listing
from common.compliance.archivedAssets import ArchivedAssets, flush_denials, is_asset_database
from common.compliance.knownDatabase import database_not_found
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

# Message returned when an approve or reject finds the cascade past its approval timeout; the
# cascade is aborted with abortReason "approval timed out" (store.expire_timed_out_cascade).
CASCADE_TIMED_OUT_MESSAGE = "Cascade approval timed out; the cascade was aborted"

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
    finally:
        # The denials of this request's archived-asset checks for listings and counts.
        flush_denials()


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

def _cascade_object(cascade_id, database_id):
    """The Tier-2 object of a cascade: its id and the database of its parent (trigger) asset."""
    return {"object__type": COMPLIANCE_CASCADE_OBJECT_TYPE, "cascadeId": cascade_id or "",
            "databaseId": database_id or ""}


def _enforce_cascade(cascade_id, database_id, action):
    """Tier-2 check on a cascade object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce(_cascade_object(cascade_id, database_id), action)


def _caller_enforcer():
    """The caller's CasbinEnforcer, or None for a request without tokens."""
    return CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None


def _evaluable_database_ids():
    """The databases the caller may evaluate (`complianceEvaluation` POST), recorded on a cascade
    when it is started by a caller (approved, or created without approval). The executor evaluates
    only the descendants in these databases and records the others as skipped (not authorized):
    it runs without the caller's identity, so the caller's reach is fixed when the caller acts and
    a descendant is never evaluated with the system identity instead."""
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    # A soft-deleted database (`<databaseId>#deleted`) holds no compliance records to evaluate.
    return [database_id for database_id in store.all_database_ids()
            if is_asset_database(database_id)
            and casbin_enforcer.enforce(_evaluation_object(database_id), "POST")]


def _read_authorized_cascade(event, cascade_id, cascade_action, evaluation_action, not_found_message):
    """(cascade, None) when the cascade exists and the caller passes both of its checks: the
    cascade object and the evaluation object of its parent's database. Otherwise (None, response):
    403, or `not_found_message` for a cascade that does not exist and a caller who may act on
    cascades in general (an absent cascade is checked with no database, which a role scoped to
    databases does not match, so such a caller is refused rather than told it is absent). For a
    cascade whose parent asset is archived, both checks use the parent's stored database id
    (`<databaseId>#deleted`), as the asset routes decide the archived asset itself."""
    cascade = cascade_table.get_item(Key={"cascadeId": cascade_id}).get("Item")
    database_id = (cascade or {}).get("triggeredByDatabaseId", "")
    if not _enforce_cascade(cascade_id, database_id, cascade_action):
        return None, authorization_error()
    if not cascade:
        return None, general_error(body={"message": not_found_message}, event=event)
    # The live cascade check passed above; an archived parent is checked on its stored id and
    # with the asset GET on its archived row.
    if not ArchivedAssets(store.asset_table, _caller_enforcer()).decide(
            database_id, cascade.get("triggeredByAssetId", ""),
            lambda stored_id: ((stored_id == database_id
                                or _enforce_cascade(cascade_id, stored_id, cascade_action))
                               and _enforce_evaluation(stored_id, evaluation_action))):
        return None, authorization_error()
    return cascade, None


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


class _CascadeChecks:
    """The pair of checks a cascade route makes (the evaluation object of the parent's database and
    the cascade object), asked of the engine as few times as one listing request needs.

    The evaluation object names nothing but the database id: it is asked once per database id and
    action. The cascade object names the cascade id too: once per database id and action as well
    when none of the caller's policy lines reads `cascadeId` (`CasbinEnforcer.policy_object_fields`,
    asked once), else once per cascade. The evaluation check is asked first, so the cascades of a
    database the caller may not read are refused without a cascade check each."""

    def __init__(self, casbin_enforcer):
        self._enforcer = casbin_enforcer
        self._answers = {}
        self._per_cascade = None

    def _reads_cascade_id(self):
        if self._per_cascade is None:
            try:
                answer = self._enforcer.policy_object_fields(["cascadeId"])
            except Exception:
                answer = None
            # No usable answer: decide per cascade, which is always right.
            self._per_cascade = (not isinstance(answer, (set, frozenset))
                                 or "cascadeId" in answer)
        return self._per_cascade

    def _ask(self, key, obj, action):
        if key not in self._answers:
            self._answers[key] = bool(self._enforcer) and bool(self._enforcer.enforce(obj, action))
        return self._answers[key]

    def allowed(self, cascade_id, database_id, action):
        """Whether the caller passes both checks on a cascade of `database_id` (a live id, or the
        parent's stored id `<databaseId>#deleted`)."""
        if not self._ask(("evaluation", database_id, action),
                         _evaluation_object(database_id), action):
            return False
        cascade_key = cascade_id if self._enforcer and self._reads_cascade_id() else None
        return self._ask(("cascade", cascade_key, database_id, action),
                         _cascade_object(cascade_id, database_id), action)


def _validate_cascade_id(cascade_id):
    return validate({"cascadeId": {"value": cascade_id, "validator": "UUID"}})


#######################
# Descendants the reader may not see
#######################

# The node status the executor records for a descendant it skipped as archived
# (`complianceCascadeExecutor.NODE_STATE_SKIPPED_ARCHIVED`; a test pins the two together). Kept here
# rather than imported so this Lambda does not load the executor.
NODE_STATE_SKIPPED_ARCHIVED = "skipped_archived"
# The cache key of the archive-visibility decision (the evaluation GET on a descendant database's
# stored id), so one request asks it once per database.
ARCHIVE_VISIBILITY_CACHE_KEY = "evaluation-GET"


def _decoded(raw, default):
    """A JSON-encoded cascade attribute decoded (the executor stores them as JSON strings), or
    `default` when it is absent or unreadable."""
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _encoded_like(raw, value):
    """`value` in the encoding `raw` was stored in: a JSON string for a string attribute."""
    return json.dumps(value) if isinstance(raw, str) else value


# The response attribute that counts the descendants in a database the reader may not read. They
# are left out of every other attribute and counted here only, without their ids, as the asset
# links count the linked assets a reader may not see (`unauthorizedCounts`).
UNAUTHORIZED_NODES_ATTRIBUTE = "unauthorizedNodes"

# What the reader is shown of one descendant (`_NodeVisibility.outcome`).
NODE_VISIBLE = "visible"
# An archived asset the reader may not see: left out and not counted, as the asset listing does
# not show that reader the archived asset.
NODE_HIDDEN = "hidden"
# A descendant in a database whose compliance the reader may not read: left out and counted.
NODE_UNAUTHORIZED = "unauthorized"


class _NodeVisibility:
    """The reader's decisions on the descendants of the cascades one request returns, made by the
    engine and cached for the request: a cascade, or a page of them, asks each question once per
    database id, however many descendants it holds.

    A descendant is shown when the reader passes the decision a direct compliance read of that
    asset's record makes:
    - the evaluation GET on the descendant's database (`_evaluation_object`, the object the
      overview of that database is read with), asked once per database id;
    - and, for an asset that is archived now, the archived-asset decision (`ArchivedAssets.decide`
      in its listing form: the database's archived rows are read once, and only when the reader's
      answer depends on them).
    A descendant the executor skipped as archived (`skipped_archived`) is decided as the archived
    asset it was (`ArchivedAssets.archived_asset_visible`).

    The parent asset is not a descendant: a reader who passed the cascade's own checks sees it."""

    def __init__(self, casbin_enforcer, archived):
        self._enforcer = casbin_enforcer
        self._archived = archived
        self._evaluation_reads = {}

    def evaluation_read(self, database_id):
        """The evaluation GET on a database id (a live id, or an archive's stored id
        `<databaseId>#deleted`), asked of the engine once per id of the request."""
        if database_id not in self._evaluation_reads:
            self._evaluation_reads[database_id] = bool(self._enforcer) and bool(
                self._enforcer.enforce(_evaluation_object(database_id), "GET"))
        return self._evaluation_reads[database_id]

    def outcome(self, key, skipped_archived):
        """NODE_VISIBLE, NODE_HIDDEN or NODE_UNAUTHORIZED for the descendant `databaseId:assetId`."""
        database_id, _, asset_id = key.partition(":")
        if skipped_archived:
            visible = bool(self._enforcer) and self._archived.archived_asset_visible(
                database_id, asset_id, self.evaluation_read,
                cache_key=ARCHIVE_VISIBILITY_CACHE_KEY)
            return NODE_VISIBLE if visible else NODE_HIDDEN
        if not self.evaluation_read(database_id):
            return NODE_UNAUTHORIZED
        if not self._archived.decide(database_id, asset_id, self.evaluation_read,
                                     cache_key=ARCHIVE_VISIBILITY_CACHE_KEY, listing=True):
            return NODE_HIDDEN
        return NODE_VISIBLE


def _nodes_for_reader(item, visibility):
    """The cascade row as the reader may see it (`_NodeVisibility`). A descendant the reader may
    not see is left out of `nodes`, `executionOrder`, `nodeNames` and `results`, and out of
    `totalNodes`. Those in a database the reader may not read are counted in `unauthorizedNodes`
    (0 when there are none); an archived one the reader may not see is not counted."""
    nodes = _decoded(item.get("nodes"), {})
    results = _decoded(item.get("results"), [])
    order = _decoded(item.get("executionOrder"), [])
    names = _decoded(item.get("nodeNames"), {})
    nodes = nodes if isinstance(nodes, dict) else {}
    results = results if isinstance(results, list) else []
    order = order if isinstance(order, list) else []
    names = names if isinstance(names, dict) else {}
    result_nodes = [entry.get("node") for entry in results
                    if isinstance(entry, dict) and isinstance(entry.get("node"), str)]
    skipped_archived = {key for key, status in nodes.items() if status == NODE_STATE_SKIPPED_ARCHIVED}
    skipped_archived |= {entry.get("node") for entry in results
                         if isinstance(entry, dict)
                         and entry.get("status") == NODE_STATE_SKIPPED_ARCHIVED
                         and isinstance(entry.get("node"), str)}
    keys = (set(nodes) | {key for key in order if isinstance(key, str)} | set(result_nodes)
            | set(names))

    hidden = set()
    unauthorized = 0
    for key in sorted(keys):
        outcome = visibility.outcome(key, key in skipped_archived)
        if outcome != NODE_VISIBLE:
            hidden.add(key)
        if outcome == NODE_UNAUTHORIZED:
            unauthorized += 1

    out = dict(item)
    out[UNAUTHORIZED_NODES_ATTRIBUTE] = unauthorized
    if not hidden:
        return out
    if "nodes" in item:
        out["nodes"] = _encoded_like(item["nodes"], {
            key: status for key, status in nodes.items() if key not in hidden})
    if "results" in item:
        out["results"] = _encoded_like(item["results"], [
            entry for entry in results
            if not (isinstance(entry, dict) and entry.get("node") in hidden)])
    if "executionOrder" in item:
        out["executionOrder"] = _encoded_like(item["executionOrder"], [
            key for key in order if key not in hidden])
    if "nodeNames" in item:
        out["nodeNames"] = _encoded_like(item["nodeNames"], {
            key: name for key, name in names.items() if key not in hidden})
    if item.get("totalNodes") is not None:
        counted = set(nodes) | {key for key in order if isinstance(key, str)}
        out["totalNodes"] = max(0, item["totalNodes"] - len(hidden & counted))
    return out


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
    if not ArchivedAssets(store.asset_table, _caller_enforcer()).decide(
            request.databaseId, request.assetId,
            lambda stored_id: (_enforce_cascade(cascade_id, stored_id, "POST")
                               and _enforce_evaluation(stored_id, "POST"))):
        return authorization_error()

    asset_item = store.get_asset_item(request.databaseId, request.assetId)
    if not asset_item:
        return general_error(body={"message": "Asset not found"}, event=event)
    asset_name = asset_item.get("assetName") if isinstance(asset_item, dict) else None

    actor = claims_and_roles["tokens"][0]
    now = datetime.now(timezone.utc)
    reason = request.reason or "manual trigger"
    state = CASCADE_STATE_PENDING_APPROVAL if request.requireApproval else CASCADE_STATE_EXECUTING

    item = {
        "cascadeId": cascade_id,
        "state": state,
        "triggeredByDatabaseId": request.databaseId,
        "triggeredByAssetId": request.assetId,
        "triggeredByAssetName": asset_name or "",
        "searchText": " ".join(part for part in (listing.normalized_text(asset_name),
                                                 listing.normalized_text(request.assetId)) if part),
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
    else:
        # Started by this caller now: its descendants are evaluated within the caller's reach.
        item["authorizedDatabaseIds"] = json.dumps(_evaluable_database_ids())
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
    cascade, refused = _read_authorized_cascade(
        event, cascade_id, "POST", "POST", "Cascade not found or not in pending_approval state")
    if refused:
        return refused
    if store.cascade_approval_timed_out(cascade):
        store.expire_timed_out_cascade(cascade_table, cascade)
        return general_error(body={"message": CASCADE_TIMED_OUT_MESSAGE}, event=event)

    actor = claims_and_roles["tokens"][0]
    reason = request.reason or "approved"
    now = datetime.now(timezone.utc).isoformat()

    try:
        cascade_table.update_item(
            Key={"cascadeId": cascade_id},
            UpdateExpression=(
                "SET #s = :state, approvedBy = :actor, approvedAt = :now, approvalReason = :reason,"
                " authorizedDatabaseIds = :authorized"
            ),
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":state": CASCADE_STATE_EXECUTING,
                ":actor": actor,
                ":now": now,
                ":reason": reason,
                ":pending": CASCADE_STATE_PENDING_APPROVAL,
                # The approver's reach: the executor evaluates descendants in these databases only.
                ":authorized": json.dumps(_evaluable_database_ids()),
            },
            # Still pending and not past its timeout: a cascade that times out between the read
            # above and this write is not started.
            ConditionExpression=(
                "attribute_exists(cascadeId) AND #s = :pending AND "
                "(attribute_not_exists(approvalTimeoutAt) OR approvalTimeoutAt > :now)"
            ),
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return general_error(
            body={"message": "Cascade not found or not in pending_approval state"}, event=event)
    # The cascade left pending_approval: release its parent's claim, so the parent's next
    # evaluation can open a new automatic cascade.
    store.clear_cascade_pending_marker(cascade_table, cascade)

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
    cascade, refused = _read_authorized_cascade(
        event, cascade_id, "POST", "POST", "Cascade not found or not in pending_approval state")
    if refused:
        return refused
    if store.cascade_approval_timed_out(cascade):
        store.expire_timed_out_cascade(cascade_table, cascade)
        return general_error(body={"message": CASCADE_TIMED_OUT_MESSAGE}, event=event)

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
            # Still pending and not past its timeout: a cascade that times out between the read
            # above and this write is not started.
            ConditionExpression=(
                "attribute_exists(cascadeId) AND #s = :pending AND "
                "(attribute_not_exists(approvalTimeoutAt) OR approvalTimeoutAt > :now)"
            ),
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return general_error(
            body={"message": "Cascade not found or not in pending_approval state"}, event=event)
    # The cascade left pending_approval: release its parent's claim, so the parent's next
    # evaluation can open a new automatic cascade.
    store.clear_cascade_pending_marker(cascade_table, cascade)

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
    """One cascade, with only the descendants the caller may see (`_nodes_for_reader`): those in a
    database whose compliance the caller may not read are counted in `unauthorizedNodes`, and an
    archived one the caller may not see is left out."""
    (valid, message) = _validate_cascade_id(cascade_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    item, refused = _read_authorized_cascade(event, cascade_id, "GET", "GET", "Cascade not found")
    if refused:
        return refused
    item = store.expire_timed_out_cascade(cascade_table, item)
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    item = _nodes_for_reader(item, _NodeVisibility(
        casbin_enforcer, ArchivedAssets(store.asset_table, casbin_enforcer)))
    body = {key: value for key, value in item.items() if key not in PRIVATE_CASCADE_ATTRIBUTES}
    # The trigger (parent) asset's name, as the listing rows carry it; the children's names are
    # in `nodeNames`, recorded by the executor with the nodes.
    body["assetName"] = item.get("triggeredByAssetName", "")
    body["permissions"] = _cascade_permissions(casbin_enforcer, item)
    return success(body=body)


# The states the cascade listing may be asked for (`state`); pending approval by default.
CASCADE_LISTING_STATES = ("pending_approval", "executing", "completed", "aborted")
CASCADE_STATE_INDEX_KEYS = ("cascadeId", "state", "createdAt")
DEFAULT_CASCADE_PAGE_SIZE = 100
# Stored on a cascade row for the service and the executor, never returned: the listing's search
# column, and the starting caller's evaluable databases (which databases another caller may
# evaluate is not the reader's to learn).
PRIVATE_CASCADE_ATTRIBUTES = ("searchText", "authorizedDatabaseIds")


def list_pending_cascades(event):
    """One page of the cascades in a state (pending approval by default) the caller may GET, newest
    first (StateIndex GSI, partition `state`).

    Query parameters: `state`, `databaseId` (the trigger asset's database), `text`
    (case-insensitive contains on the trigger asset's name or id), `maxItems` (1..200) and
    `startingToken`. `databaseId` and `text` are a FilterExpression on the state's partition; the
    page is filled across at most `listing.DEFAULT_READ_BUDGET` reads with the rows the caller may
    GET (the cascade object AND the compliance evaluation object of the trigger asset's database —
    the pair every single-cascade route enforces, each asked once per database id of the request,
    `_CascadeChecks`). `NextToken` resumes after the last row returned, or after the last row read
    when the budget ended the page first.
    A pending cascade past its `approvalTimeoutAt` is aborted and not listed. Each row carries
    `databaseId` / `assetId` / `assetName` (the trigger asset) beside the `triggeredBy*` attributes
    it is stored with, and only the descendants the caller may see, as in the single cascade
    (`_nodes_for_reader`, with `unauthorizedNodes`). A `databaseId` that names no database answers
    `400` "Database not found"
    to a caller allowed the listing's checks on that id (`store.database_known`).
    """
    params = event.get("queryStringParameters") or {}
    state = params.get("state") or CASCADE_STATE_PENDING_APPROVAL
    database_id = params.get("databaseId") or None
    text = listing.normalized_text(params.get("text")) or None
    if state not in CASCADE_LISTING_STATES:
        return validation_error(body={"message": "state must be one of " + ", ".join(
            CASCADE_LISTING_STATES)}, event=event)
    (valid, message) = validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True,
                       "optional": True},
        "text": {"value": text, "validator": "STRING_256", "optional": True},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)
    page_size = listing.page_size_of(params.get("maxItems"), DEFAULT_CASCADE_PAGE_SIZE)
    if page_size is None:
        return validation_error(body={"message": "maxItems must be an integer"}, event=event)
    start_key = None
    if params.get("startingToken"):
        start_key = listing.decode_token(params["startingToken"], CASCADE_STATE_INDEX_KEYS)
        if start_key is None:
            return validation_error(body={"message": "Invalid pagination token"}, event=event)

    query_kwargs = {
        "IndexName": "StateIndex",
        "KeyConditionExpression": Key("state").eq(state),
        "ScanIndexForward": False,
    }
    conditions = []
    if database_id:
        conditions.append(Attr("triggeredByDatabaseId").eq(database_id))
    if text:
        conditions.append(Attr("searchText").contains(text)
                          | Attr("triggeredByAssetId").contains(text))
    if conditions:
        expression = conditions[0]
        for condition in conditions[1:]:
            expression = expression & condition
        query_kwargs["FilterExpression"] = expression

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    # A database filter naming no database is not an empty page. Checked only for a caller allowed
    # the listing's pair of checks on that database (a cascade of it, and its evaluation object):
    # any other caller gets the page it got before, whether the database exists or not.
    if (database_id and casbin_enforcer
            and casbin_enforcer.enforce(_cascade_object("", database_id), "GET")
            and casbin_enforcer.enforce(_evaluation_object(database_id), "GET")
            and not store.database_known(database_id)):
        return database_not_found(event)
    # A cascade of an archived parent asset is listed only for a caller allowed on the parent's
    # stored database id; the archived ids are read once per database, and only when that decides.
    archived = ArchivedAssets(store.asset_table, casbin_enforcer)
    # Both checks asked once per database id of the request, not once per row (`_CascadeChecks`).
    checks = _CascadeChecks(casbin_enforcer)

    def listable(item):
        # A cascade past its approval timeout is aborted here and leaves the queue, whoever reads
        # it: the pending partition holds only cascades that can still be approved.
        if store.cascade_approval_timed_out(item):
            store.expire_timed_out_cascade(cascade_table, item)
            return False
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        return bool(casbin_enforcer) and archived.decide(
            item.get("triggeredByDatabaseId", ""), item.get("triggeredByAssetId", ""),
            lambda stored_id: checks.allowed(item.get("cascadeId"), stored_id, "GET"),
            listing=True)

    rows, next_key, _ = listing.query_page(
        cascade_table, query_kwargs, page_size, CASCADE_STATE_INDEX_KEYS, start_key=start_key,
        filtered=bool(conditions), accept=listable)
    # One set of decisions for the page: each descendant database is asked once, whichever rows
    # hold it.
    visibility = _NodeVisibility(casbin_enforcer, archived)
    body = {"cascades": [dict(_cascade_row(_nodes_for_reader(item, visibility)),
                              permissions=_cascade_permissions(casbin_enforcer, item, archived,
                                                               checks))
                         for item in rows]}
    if next_key:
        body["NextToken"] = listing.encode_token(next_key)
    return success(body=body)


def _cascade_permissions(casbin_enforcer, item, archived=None, checks=None):
    """The caller's allowed actions on one cascade, computed server-side: `approve` (approve or
    reject) needs POST on the cascade object and on the evaluation object of its parent's database
    (the parent's stored id when it is archived), and applies to a cascade still pending approval
    only. `archived` and `checks` are the request's `ArchivedAssets` and `_CascadeChecks`, reused
    across a listing's rows."""
    if item.get("state") != CASCADE_STATE_PENDING_APPROVAL:
        return {"approve": False}
    listing_rows = archived is not None
    archived = archived or ArchivedAssets(store.asset_table, casbin_enforcer)
    checks = checks or _CascadeChecks(casbin_enforcer)
    approve = archived.decide(
        item.get("triggeredByDatabaseId", ""), item.get("triggeredByAssetId", ""),
        lambda stored_id: checks.allowed(item.get("cascadeId"), stored_id, "POST"),
        listing=listing_rows)
    return {"approve": bool(approve)}


def _cascade_row(item):
    """A cascade row for the listing: the stored attributes plus `databaseId` / `assetId` naming the
    trigger asset."""
    row = {
        **item,
        "databaseId": item.get("triggeredByDatabaseId", ""),
        "assetId": item.get("triggeredByAssetId", ""),
        "assetName": item.get("triggeredByAssetName", ""),
    }
    for attribute in PRIVATE_CASCADE_ATTRIBUTES:
        row.pop(attribute, None)
    return row
