#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A database binding change, applied to the database's asset records in the background (D161).

Binding a schema to a database, changing it, removing it, and turning automatic evaluation on for
the schema in force reach every asset record of the database, which takes longer than an API
request may run on a large database. The request (`PUT` / `DELETE /compliance/bind/{databaseId}`,
`complianceSchemaBindingService`) makes every check it made before, saves the database binding,
writes the database-level audit entry naming the caller, records an **apply** (`begin`) and queues
its first message on the compliance trigger's queue (`message`), then answers `202` with the
number of assets the apply goes through (`count_assets`, a count query). The trigger applies the
change page by page (`run`), as it moves a deleted database's data (`databaseRetirement.run`).

**The apply record** lives in the evaluation table, beside the retirement record (`evaluationId`
`bindingApply#<databaseId>`). It carries no attribute any index of the table reads and no
`databaseId`, so no listing and no deleted database's move reaches it. One per database (the last
apply, kept after it ends, so `GET /compliance/bind/{databaseId}` shows its outcome):

    recordType, bindingDatabaseId, applyId, runId, operation (`bind` | `change` | `unbind` |
    `evaluatePending`), schemaName (the schema the change applies; none for `unbind`),
    previousSchema, actor (the caller's user id, for the audit entries), complianceAutoEval,
    assetCount, startedAt, lastProgressAt, finishedAt, cursor (the paging key, JSON), counts
    (`COUNT_FIELDS`), applyStatus (`applying` | `applied` | `failed`), failureReason, resumedAt,
    resumedBy, resumes.

**The message** (`APPLY_ACTION`) carries the database id, the apply id and the run id only, and
is read from a raw queue body only (`message_target`, never an SNS envelope). A run acts only while
the stored apply is `applying` with that apply id and run id, so a message can continue an apply a
request started and never start one. Everything the run applies comes from the stored apply and
the database row, read when it starts: the schema, the operation and the actor are never taken
from the message.

**A run** pages through the database's records until its deadline: the asset rows for a bind or a
change (each record decided by `bind_record`), the asset-state records for a removal
(`unbind_record`) or for queueing the pending ones (`evaluatePending`). After each page it writes
one `schema_binding_applied_to_asset` audit entry per record it changed (actor: the apply's
caller), queues the evaluations automatic evaluation calls for, and saves the cursor and the counts
on the apply record, conditional on the record still naming this apply and run. When the pages
end, the apply becomes `applied` and one `schema_binding_apply_finished` entry records the totals
(a second database-level entry: an audit entry is never rewritten). A run that ends before the
pages do returns False and its caller queues the next message; a run that fails leaves its message
to the queue's redelivery and, past the receive limit, to its dead-letter queue.

**Idempotency.** Every record a bind or a kept-quarantined removal writes carries the apply's id
(`APPLY_STAMP_FIELD`); a page applied twice (a redelivered message, a run that stopped mid-page)
finds the records it wrote stamped and writes nothing and audits nothing for them again (they are
counted from the stamp). A record deleted by a removal is not read again. Each record's write is
conditional on its revision as read (D159), so a writer that changed it meanwhile wins and the
record is decided again as it is now. A bind also leaves alone a record that already names the
apply's schema from the database binding and was written after the apply began (a new asset
registered under the new binding, or an evaluation under it): it already has the binding.

**While applying** another binding change of the database answers `409` (`APPLYING_MESSAGE`,
`applying_response`), and so does Evaluate All of it (it would evaluate the records not reached yet
under the binding before the change). An apply with no progress for `STALLED_AFTER_SECONDS` reads
`stalled`; repeating the same change resumes it from its cursor with a new run id (`resume`), and a
message of the run before is then ignored. A deleted database's move wins: a run checks the
retirement record before each record it writes and ends the apply `failed` once the move began,
and an apply that began before the database's last delete is not the live database's
(`current_record`).
"""

import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Attr, Key

from common.compliance import databaseRetirement
from common.compliance import listing
from customLogging.logger import safeLogger
from handlers.compliance import complianceEvaluationStore as store

logger = safeLogger(service_name="ComplianceBindingApply")

RECORD_PREFIX = "bindingApply#"
RECORD_TYPE = "databaseBindingApply"
# The queue message that runs (or continues) an apply.
APPLY_ACTION = "applyDatabaseBinding"

STATUS_APPLYING = "applying"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
# Not stored: how an `applying` apply with no recent progress reads (`status`).
STATUS_STALLED = "stalled"

OPERATION_BIND = "bind"
OPERATION_CHANGE = "change"
OPERATION_UNBIND = "unbind"
OPERATION_EVALUATE_PENDING = "evaluatePending"
OPERATIONS = (OPERATION_BIND, OPERATION_CHANGE, OPERATION_UNBIND, OPERATION_EVALUATE_PENDING)

# The apply id a bind or a kept-quarantined removal stamps on each record it writes (no API answer
# returns it: `listing.PRIVATE_STATE_ATTRIBUTES`).
APPLY_STAMP_FIELD = listing.BINDING_APPLY_STAMP_ATTRIBUTE
COUNT_FIELDS = ("assetsProcessed", "assetsPendingEvaluation", "assetsKeptQuarantined",
                "removedComplianceRecords", "evaluationsQueued")

PAGE_SIZE = 100
# An apply whose last progress is older than this reads `stalled`: three times the trigger's
# five-minute timeout (complianceFunctions.ts COMPLIANCE_TRIGGER_TIMEOUT), and a run saves its
# progress after every page, so a running apply never reads stalled.
STALLED_AFTER_SECONDS = 900

APPLYING_STATUS = 409
APPLYING_MESSAGE = ("The compliance schema binding of this database is still being applied; "
                    "try again when it finishes")
STALLED_MESSAGE = ("The compliance schema binding of this database stopped while it was being "
                   "applied; repeat the same change to resume it")
STALLED_REASON = "No progress for {minutes} minutes; repeat the same change to resume it"
FAILED_DATABASE_DELETED = "The database was deleted while the binding was being applied"
FAILED_BINDING_CHANGED = "The database binding no longer matches the change being applied"
FAILED_NOT_SAVED = "The binding could not be saved"

AUDIT_APPLY_FINISHED = "schema_binding_apply_finished"
AUDIT_BINDING_APPLIED_TO_ASSET = "schema_binding_applied_to_asset"

SCHEMA_SOURCE_DATABASE = "database"
SCHEMA_SOURCE_ASSET = "asset"
STATE_PENDING_EVALUATION = "pending_evaluation"
STATE_QUARANTINED = "quarantined"
STATE_UNKNOWN = "unknown"
REASON_SCHEMA_BOUND, REASON_SCHEMA_UNBOUND, REASON_OVERRIDE_SET, REASON_OVERRIDE_CLEARED = \
    store.AWAITING_REEVALUATION_REASONS
# The binding a kept-quarantined record loses when no schema applies to it any more (a database
# unbind, or an override cleared with no database binding): it then names no schema (D127).
UNBOUND_RECORD_FIELDS = ("schemaName", "schemaSource")
# SendMessageBatch takes at most ten entries.
QUEUE_BATCH = 10

# What a record's decision did (`bind_record`, `unbind_record`).
WRITTEN = "written"
# Written by this apply before (its stamp): nothing written or audited again; counted.
STAMPED = "stamped"
# Already under the apply's binding, written after the apply began by another writer.
CURRENT = "current"


def now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def record_key(database_id: str) -> Dict[str, str]:
    return {"evaluationId": f"{RECORD_PREFIX}{database_id}"}


def get_record(database_id: str) -> Optional[Dict[str, Any]]:
    """The database's apply record, read strongly consistent; None when it has none."""
    if not database_id:
        return None
    return store.evaluation_table.get_item(Key=record_key(database_id),
                                           ConsistentRead=True).get("Item")


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _of_live_generation(record: Dict[str, Any], database_id: str) -> bool:
    """Whether an apply began after the database id was last deleted (or it never was): an apply
    of a deleted generation is not the live database's."""
    deleted_at = _parse(databaseRetirement.last_deleted_at(store.evaluation_table, database_id))
    started_at = _parse(record.get("startedAt"))
    return deleted_at is None or (started_at is not None and started_at > deleted_at)


def current_record(database_id: str) -> Optional[Dict[str, Any]]:
    """The database's last apply when it belongs to the live database, else None."""
    record = get_record(database_id)
    if not record or not _of_live_generation(record, database_id):
        return None
    return record


def applying_record(database_id: str) -> Optional[Dict[str, Any]]:
    """The database's apply while it is `applying` (stalled or not), else None."""
    record = current_record(database_id)
    return record if record and record.get("applyStatus") == STATUS_APPLYING else None


def is_stalled(record: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """Whether an `applying` apply made no progress for STALLED_AFTER_SECONDS."""
    if not record or record.get("applyStatus") != STATUS_APPLYING:
        return False
    last = _parse(record.get("lastProgressAt")) or _parse(record.get("startedAt"))
    if last is None:
        return True
    return ((now or datetime.now(timezone.utc)) - last).total_seconds() > STALLED_AFTER_SECONDS


def status(record: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[str]:
    """`applying`, `stalled`, `applied` or `failed`; None without an apply."""
    if not record:
        return None
    if is_stalled(record, now):
        return STATUS_STALLED
    return record.get("applyStatus")


def _number(value: Any) -> int:
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return int(value)
    return 0


def counts_of(record: Dict[str, Any]) -> Dict[str, int]:
    stored = record.get("counts") or {}
    return {field: _number(stored.get(field)) for field in COUNT_FIELDS}


def answer(record: Optional[Dict[str, Any]], now: Optional[datetime] = None
           ) -> Optional[Dict[str, Any]]:
    """The apply as the API answers it: what it does, how far it got and how it ended. The actor,
    the run id and the cursor stay internal."""
    if not record:
        return None
    current = status(record, now)
    view: Dict[str, Any] = {
        "applyId": record.get("applyId"),
        "operation": record.get("operation"),
        "status": current,
        "schemaName": record.get("schemaName"),
        "previousSchema": record.get("previousSchema"),
        "assetCount": _number(record.get("assetCount")),
        **counts_of(record),
        "startedAt": record.get("startedAt"),
        "lastProgressAt": record.get("lastProgressAt"),
    }
    if record.get("finishedAt"):
        view["finishedAt"] = record["finishedAt"]
    if current == STATUS_FAILED and record.get("failureReason"):
        view["reason"] = record["failureReason"]
    elif current == STATUS_STALLED:
        view["reason"] = STALLED_REASON.format(minutes=STALLED_AFTER_SECONDS // 60)
    return view


def applying_response(event=None, record: Optional[Dict[str, Any]] = None):
    """The `409` of a binding change (or Evaluate All) of a database whose apply is running; a
    stalled apply says how to resume it."""
    from models.common import general_error
    message = STALLED_MESSAGE if is_stalled(record) else APPLYING_MESSAGE
    return general_error(status_code=APPLYING_STATUS, body={"message": message}, event=event)


def same_change(record: Dict[str, Any], operation: str, schema_name: Optional[str],
                auto_eval: Optional[bool] = None) -> bool:
    """Whether a request repeats the change an apply is making: a removal repeats a removal; a bind
    of a schema with an automatic-evaluation setting repeats the bind, change or
    automatic-evaluation apply of that schema with that setting."""
    if operation == OPERATION_UNBIND:
        return record.get("operation") == OPERATION_UNBIND
    return (record.get("operation") != OPERATION_UNBIND
            and record.get("schemaName") == schema_name
            and bool(record.get("complianceAutoEval")) == bool(auto_eval))


# --- Counting ---


def _count(table, **kwargs) -> int:
    total = 0
    kwargs = dict(kwargs, Select="COUNT")
    while True:
        response = table.query(**kwargs)
        total += _number(response.get("Count"))
        if "LastEvaluatedKey" not in response:
            return total
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _not_override():
    return Attr("schemaSource").not_exists() | Attr("schemaSource").ne(SCHEMA_SOURCE_ASSET)


def _pending():
    return (Attr("complianceState").eq(STATE_PENDING_EVALUATION)
            | Attr(store.AWAITING_REEVALUATION_SINCE).exists())


def count_assets(database_id: str, operation: str) -> int:
    """How many records the apply goes through, by a count query (no record is read into the
    request): the database's live assets for a bind or a change; the records that inherit the
    database binding for a removal; those still pending or awaiting re-evaluation for
    `evaluatePending`."""
    key = Key("databaseId").eq(database_id)
    if operation in (OPERATION_BIND, OPERATION_CHANGE):
        return _count(store.asset_table, KeyConditionExpression=key)
    if operation == OPERATION_UNBIND:
        return _count(store.asset_state_table, KeyConditionExpression=key,
                      FilterExpression=_not_override())
    return _count(store.asset_state_table, KeyConditionExpression=key,
                  FilterExpression=_not_override() & _pending())


# --- The apply record: begin, resume, progress, end ---


def begin(database_id: str, operation: str, schema_name: Optional[str],
          previous_schema: Optional[str], actor: str, asset_count: int, auto_eval: bool,
          now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Record a new apply (`applying`), conditional on the database having none running (an apply
    of a deleted generation does not count). Returns the record, or None when another request's
    apply is running."""
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown binding operation {operation!r}")
    started = now_iso(now)
    record = {
        **record_key(database_id),
        "recordType": RECORD_TYPE,
        "bindingDatabaseId": database_id,
        "applyId": str(uuid.uuid4()),
        "runId": str(uuid.uuid4()),
        "operation": operation,
        "actor": actor,
        "complianceAutoEval": bool(auto_eval),
        "assetCount": int(asset_count),
        "startedAt": started,
        "lastProgressAt": started,
        "counts": {field: 0 for field in COUNT_FIELDS},
        "applyStatus": STATUS_APPLYING,
        "resumes": 0,
    }
    if schema_name:
        record["schemaName"] = schema_name
    if previous_schema:
        record["previousSchema"] = previous_schema
    condition = "attribute_not_exists(evaluationId) OR applyStatus <> :applying"
    values: Dict[str, Any] = {":applying": STATUS_APPLYING}
    deleted_at = databaseRetirement.last_deleted_at(store.evaluation_table, database_id)
    if deleted_at:
        condition += " OR startedAt < :deletedAt"
        values[":deletedAt"] = deleted_at
    try:
        store.evaluation_table.put_item(Item=record, ConditionExpression=condition,
                                        ExpressionAttributeValues=values)
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return None
        raise
    return record


def resume(record: Dict[str, Any], actor: str, now: Optional[datetime] = None
           ) -> Optional[Dict[str, Any]]:
    """Resume a stalled apply from its cursor: a new run id (a message of the run before is then
    ignored), conditional on the apply being the one read and still without progress. Returns the
    record as resumed, or None when another request resumed it or a run moved it meanwhile."""
    resumed_at = now_iso(now)
    run_id = str(uuid.uuid4())
    try:
        store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]),
            UpdateExpression=("SET runId = :run, lastProgressAt = :now, resumedAt = :now, "
                              "resumedBy = :actor ADD resumes :one"),
            ConditionExpression=("applyId = :apply AND runId = :readRun AND applyStatus = "
                                 ":applying AND lastProgressAt = :readProgress"),
            ExpressionAttributeValues={
                ":run": run_id, ":now": resumed_at, ":actor": actor, ":one": 1,
                ":apply": record["applyId"], ":readRun": record.get("runId"),
                ":applying": STATUS_APPLYING, ":readProgress": record.get("lastProgressAt")})
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return None
        raise
    return dict(record, runId=run_id, lastProgressAt=resumed_at, resumedAt=resumed_at,
                resumedBy=actor, resumes=_number(record.get("resumes")) + 1)


def _owner_condition(record: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    return ("applyId = :apply AND runId = :run AND applyStatus = :applying",
            {":apply": record["applyId"], ":run": record["runId"], ":applying": STATUS_APPLYING})


def _save_progress(record: Dict[str, Any], cursor: Optional[Dict[str, Any]],
                   counts: Dict[str, int], now: Optional[datetime] = None) -> bool:
    """Save a page's cursor and the counts, conditional on the record still naming this apply and
    run. Returns False when it does not (a resume handed the apply to a new run)."""
    condition, values = _owner_condition(record)
    values.update({":counts": counts, ":now": now_iso(now)})
    expression = "SET counts = :counts, lastProgressAt = :now"
    if cursor:
        expression += ", #c = :cursor"
        values[":cursor"] = json.dumps(cursor, default=_json_default)
    else:
        expression += " REMOVE #c"
    try:
        store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]), UpdateExpression=expression,
            ConditionExpression=condition, ExpressionAttributeNames={"#c": "cursor"},
            ExpressionAttributeValues=values)
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return False
        raise
    return True


def finish(record: Dict[str, Any], outcome: str, counts: Optional[Dict[str, int]] = None,
           reason: Optional[str] = None, audit: bool = True,
           audit_database_id: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """End an apply `applied` or `failed`, conditional on the record still naming this apply and
    run; when it lands (once), write the `schema_binding_apply_finished` entry naming the apply's
    caller with the totals, under `audit_database_id` (the database id by default; a deleted
    database's apply is recorded under `<databaseId>#deleted`, where its move takes its data).
    Returns whether it landed."""
    condition, values = _owner_condition(record)
    finished_at = now_iso(now)
    counts = counts if counts is not None else counts_of(record)
    values.update({":outcome": outcome, ":now": finished_at, ":counts": counts})
    expression = ("SET applyStatus = :outcome, finishedAt = :now, lastProgressAt = :now, "
                  "counts = :counts")
    if reason:
        expression += ", failureReason = :reason"
        values[":reason"] = reason
    expression += " REMOVE #c"
    try:
        store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]), UpdateExpression=expression,
            ConditionExpression=condition, ExpressionAttributeNames={"#c": "cursor"},
            ExpressionAttributeValues=values)
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return False
        raise
    if audit:
        details = {"applyId": record["applyId"], "operation": record.get("operation"),
                   "outcome": outcome, "assetCount": _number(record.get("assetCount")),
                   "previousSchema": record.get("previousSchema"), **counts}
        if reason:
            details["reason"] = reason
        store.write_audit(audit_database_id or record["bindingDatabaseId"], "*",
                          event_type=AUDIT_APPLY_FINISHED,
                          actor=record.get("actor") or store.SYSTEM_ACTOR,
                          schema_name=record.get("schemaName") or record.get("previousSchema"),
                          details=details)
    return True


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    raise TypeError(f"Unserializable paging key value {value!r}")


# --- The message ---


def message(database_id: str, apply_id: str, run_id: str) -> Dict[str, Any]:
    """The queue message that runs (or continues) an apply (`APPLY_ACTION`)."""
    return {"vamsInternalAction": APPLY_ACTION, "databaseId": database_id, "applyId": apply_id,
            "runId": run_id}


def message_target(queue_record: Any) -> Optional[Tuple[str, str, str]]:
    """`(databaseId, applyId, runId)` of a queue record carrying `message` as its raw body, else
    None. An SNS envelope is never one (the indexer topics cannot carry the action)."""
    body = queue_record.get("body") if isinstance(queue_record, dict) else None
    try:
        payload = json.loads(body) if isinstance(body, str) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("vamsInternalAction") != APPLY_ACTION:
        return None
    fields = tuple(payload.get(name) for name in ("databaseId", "applyId", "runId"))
    if not all(isinstance(value, str) and value for value in fields):
        return None
    return fields  # type: ignore[return-value]


def send_message(sqs, queue_url: str, record: Dict[str, Any]) -> bool:
    """Queue the message of an apply's run; False (logged) when it could not be queued: the apply
    then reads stalled after STALLED_AFTER_SECONDS and the same change resumes it."""
    if not queue_url or sqs is None:
        logger.error(f"No compliance trigger queue to apply the binding of "
                     f"{record['bindingDatabaseId']} on")
        return False
    try:
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(
            message(record["bindingDatabaseId"], record["applyId"], record["runId"])))
    except Exception as e:
        logger.exception(f"Could not queue the binding apply of {record['bindingDatabaseId']}: {e}")
        return False
    return True


def queue_evaluations(database_id: str, asset_ids: List[str], sqs, queue_url: str) -> int:
    """Queue one evaluation message (`{databaseId, assetId}`) per asset on the compliance trigger's
    queue, ten to a SendMessageBatch; the trigger evaluates each as automatic evaluation does.
    Best-effort: an entry that is not queued is logged (the asset stays pending, and its next change
    or a sweep evaluates it). Returns the count queued."""
    if not queue_url or sqs is None or not asset_ids:
        return 0
    queued = 0
    ids = list(dict.fromkeys(asset_ids))
    for start in range(0, len(ids), QUEUE_BATCH):
        entries = [{"Id": str(index),
                    "MessageBody": json.dumps({"databaseId": database_id, "assetId": asset_id})}
                   for index, asset_id in enumerate(ids[start:start + QUEUE_BATCH])]
        try:
            response = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
        except Exception as e:
            logger.exception(f"Could not queue {len(entries)} evaluations of {database_id}: {e}")
            continue
        failed = response.get("Failed") or []
        if failed:
            logger.error(f"{len(failed)} evaluations of {database_id} were not queued")
        queued += len(entries) - len(failed)
    return queued


# --- One record ---


def awaiting(now: str, schema_name: Optional[str], reason: str) -> Dict[str, Any]:
    """The re-evaluation marker of a quarantined record a binding change reached."""
    marker = {store.AWAITING_REEVALUATION_SINCE: now, store.AWAITING_REEVALUATION_REASON: reason}
    if schema_name:
        marker[store.AWAITING_REEVALUATION_SCHEMA] = schema_name
    return marker


def change(asset_id: str, previous: Optional[Dict[str, Any]], new_state: str,
           asset_name: Optional[str] = None) -> Dict[str, Any]:
    """One asset a binding change reached: its state before and after, and its schema before."""
    previous = previous or {}
    return {"assetId": asset_id,
            "previousState": previous.get("complianceState") or STATE_UNKNOWN,
            "previousSchema": previous.get("schemaName"),
            "newState": new_state,
            "assetName": asset_name or previous.get("assetName")}


# The key attributes of an asset-state record, and its revision, which a binding's replacement of
# the record keeps (`_replacing_fields`).
_KEPT_ON_REPLACE = ("databaseId", "assetId", store.RECORD_REVISION_FIELD)


def _replacing_fields(existing, written):
    """The attributes of `existing` that a write of `written` removes so the record ends as
    `written` alone, as the put it replaces left it (the keys and the revision stay)."""
    return tuple(field for field in (existing or {})
                 if field not in written and field not in _KEPT_ON_REPLACE)


def _already_bound(existing: Optional[Dict[str, Any]], schema_name: str,
                   apply: Dict[str, Any]) -> Optional[str]:
    """STAMPED when this apply wrote the record; CURRENT when another writer put it under the
    apply's binding after the apply began; else None."""
    if not existing:
        return None
    if existing.get(APPLY_STAMP_FIELD) == apply.get("applyId"):
        return STAMPED
    written_at, started_at = _parse(existing.get("updatedAt")), _parse(apply.get("startedAt"))
    if (existing.get("schemaSource") == SCHEMA_SOURCE_DATABASE
            and existing.get("schemaName") == schema_name
            and written_at is not None and started_at is not None and written_at >= started_at):
        return CURRENT
    return None


def bind_record(database_id: str, asset_id: str, existing: Optional[Dict[str, Any]],
                schema_name: str, asset_name: Optional[str], apply: Optional[Dict[str, Any]] = None
                ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Apply a database binding to one asset's record, computed from `existing` (the record as
    read, None for none) and conditional on its revision (`store.revision_condition`, D159): an
    asset-level override is left alone; a quarantined record stays quarantined, marked awaiting
    re-evaluation under the schema; any other record is replaced by a `pending_evaluation` one
    under the schema (created when absent). Each record's `updatedAt`, and a quarantined record's
    marker time, are the time of its own write (D155, D158). With an `apply`, the write carries its
    stamp and a record it already reached is left alone (`_already_bound`). When the record changed
    since the read it is read again consistently and decided as it is now, up to
    `store.STATE_WRITE_ATTEMPTS` tries; after the last it is left as it is (logged).

    Returns `(outcome, change)`: `(WRITTEN, change)`, `(STAMPED, change)` (the change as the stamp
    shows it), `(CURRENT, None)`, or `(None, None)` when nothing was written."""
    for attempt in range(store.STATE_WRITE_ATTEMPTS):
        if attempt:
            existing = store.get_compliance_record(database_id, asset_id, consistent_read=True)
        if existing and existing.get("schemaSource") == SCHEMA_SOURCE_ASSET:
            return None, None
        already = _already_bound(existing, schema_name, apply) if apply else None
        if already == STAMPED:
            return STAMPED, change(asset_id, None, existing.get("complianceState")
                                   or STATE_PENDING_EVALUATION, asset_name)
        if already == CURRENT:
            return CURRENT, None
        written_at = store.now_iso()
        stamp = {APPLY_STAMP_FIELD: apply["applyId"]} if apply else {}
        if existing and existing.get("complianceState") == STATE_QUARANTINED:
            updates = {
                "schemaName": schema_name,
                "schemaSource": SCHEMA_SOURCE_DATABASE,
                "updatedAt": written_at,
                **awaiting(written_at, schema_name, REASON_SCHEMA_BOUND),
                **stamp,
            }
            removed = ()
            new_state = STATE_QUARANTINED
        else:
            updates = {
                "schemaName": schema_name,
                "schemaSource": SCHEMA_SOURCE_DATABASE,
                "complianceState": STATE_PENDING_EVALUATION,
                "updatedAt": written_at,
                **listing.asset_name_attributes(asset_id, asset_name),
                **stamp,
            }
            removed = _replacing_fields(existing, updates)
            new_state = STATE_PENDING_EVALUATION
        try:
            store.update_asset_state(database_id, asset_id, updates, remove_fields=removed,
                                     computed_from=existing or {})
        except Exception as e:
            if not store.is_conditional_check_failure(e):
                raise
            continue
        return WRITTEN, change(asset_id, existing, new_state, asset_name)
    logger.warning(f"The binding of {schema_name} did not reach asset {database_id}:{asset_id}: "
                   f"its record changed under each of {store.STATE_WRITE_ATTEMPTS} writes")
    return None, None


def unbind_record(database_id: str, item: Dict[str, Any], apply: Optional[Dict[str, Any]] = None
                  ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Remove the database binding from one record that inherited it, computed from `item` (the
    record as read) and conditional on its revision (D159): a quarantined record stays
    quarantined, marked awaiting re-evaluation with no schema (stamped with the `apply`); any other
    is deleted; an asset-level override (or a record gone since) is left alone. A record this apply
    already kept is left alone. When the record changed since the read it is read again
    consistently and decided as it is now, up to `store.STATE_WRITE_ATTEMPTS` tries; after the last
    it is left as it is (logged). Returns `(WRITTEN | STAMPED, change)` or `(None, None)`."""
    asset_id = item["assetId"]
    for attempt in range(store.STATE_WRITE_ATTEMPTS):
        if attempt:
            item = store.get_compliance_record(database_id, asset_id, consistent_read=True)
        if not item or item.get("schemaSource") == SCHEMA_SOURCE_ASSET:
            return None, None
        if apply and item.get(APPLY_STAMP_FIELD) == apply.get("applyId"):
            return STAMPED, change(asset_id, None, item.get("complianceState") or STATE_QUARANTINED)
        try:
            if item.get("complianceState") == STATE_QUARANTINED:
                # No schema applies any more: the record keeps the quarantine and names none.
                written_at = store.now_iso()
                updates = {"updatedAt": written_at,
                           **awaiting(written_at, None, REASON_SCHEMA_UNBOUND)}
                if apply:
                    updates[APPLY_STAMP_FIELD] = apply["applyId"]
                store.update_asset_state(
                    database_id, asset_id, updates,
                    remove_fields=(store.AWAITING_REEVALUATION_SCHEMA, *UNBOUND_RECORD_FIELDS),
                    computed_from=item)
                return WRITTEN, change(asset_id, item, STATE_QUARANTINED)
            store.delete_asset_state(database_id, asset_id, item)
            return WRITTEN, change(asset_id, item, STATE_UNKNOWN)
        except Exception as e:
            if not store.is_conditional_check_failure(e):
                raise
    logger.warning(f"The binding removal did not reach asset {database_id}:{asset_id}: its record "
                   f"changed under each of {store.STATE_WRITE_ATTEMPTS} writes")
    return None, None


def audit_asset_changes(database_id: str, changes: List[Dict[str, Any]], actor: str,
                        schema_name: Optional[str], previous_schema: Optional[str]) -> None:
    """One `schema_binding_applied_to_asset` entry per asset a database binding change reached:
    its state before and after, the schema now bound (None when the binding was removed) and the
    one before. `actor` is the person who made the change."""
    for entry in changes:
        store.write_audit(
            database_id, entry["assetId"],
            event_type=AUDIT_BINDING_APPLIED_TO_ASSET,
            actor=actor,
            schema_name=schema_name or previous_schema,
            previous_state=entry["previousState"],
            new_state=entry["newState"],
            asset_name=entry.get("assetName"),
            details={"binding": SCHEMA_SOURCE_DATABASE, "schemaName": schema_name,
                     "previousSchema": entry.get("previousSchema") or previous_schema,
                     "awaitingReevaluation": entry["newState"] == STATE_QUARANTINED},
        )


# --- A run ---


class _Page:
    """What one page did: the next cursor, the changes written (to audit), the asset ids to
    evaluate, and whether the database's move began during it (the page then stops before its
    next record write)."""

    def __init__(self):
        self.cursor: Optional[Dict[str, Any]] = None
        self.changes: List[Dict[str, Any]] = []
        self.to_evaluate: List[str] = []
        self.stopped = False


def _tally(counts: Dict[str, int], outcome: Optional[str], entry: Optional[Dict[str, Any]]) -> None:
    if outcome not in (WRITTEN, STAMPED) or entry is None:
        return
    if entry["newState"] == STATE_QUARANTINED:
        counts["assetsKeptQuarantined"] += 1
    elif entry["newState"] == STATE_UNKNOWN:
        counts["removedComplianceRecords"] += 1
    else:
        counts["assetsPendingEvaluation"] += 1


def _page_kwargs(database_id: str, cursor, page_size: int) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"KeyConditionExpression": Key("databaseId").eq(database_id),
                              "ConsistentRead": True, "Limit": page_size}
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    return kwargs


def _bind_page(database_id, record, cursor, page_size, counts) -> _Page:
    """One page of the database's asset rows under a bind or a change."""
    page = _Page()
    response = store.asset_table.query(ProjectionExpression="assetId, assetName",
                                       **_page_kwargs(database_id, cursor, page_size))
    page.cursor = response.get("LastEvaluatedKey")
    for row in response.get("Items", []):
        asset_id = row.get("assetId")
        if not asset_id:
            continue
        # Nothing is written under the live id once the database's move began.
        if store.database_moving(database_id):
            page.stopped = True
            return page
        counts["assetsProcessed"] += 1
        existing = store.get_compliance_record(database_id, asset_id, consistent_read=True)
        outcome, entry = bind_record(database_id, asset_id, existing, record["schemaName"],
                                     row.get("assetName"), record)
        _tally(counts, outcome, entry)
        if outcome == WRITTEN:
            page.changes.append(entry)
            page.to_evaluate.append(asset_id)
    return page


def _unbind_page(database_id, record, cursor, page_size, counts) -> _Page:
    """One page of the database's asset-state records under a removal."""
    page = _Page()
    response = store.asset_state_table.query(**_page_kwargs(database_id, cursor, page_size))
    page.cursor = response.get("LastEvaluatedKey")
    for item in response.get("Items", []):
        if item.get("schemaSource") == SCHEMA_SOURCE_ASSET:
            continue
        if store.database_moving(database_id):
            page.stopped = True
            return page
        counts["assetsProcessed"] += 1
        outcome, entry = unbind_record(database_id, item, record)
        _tally(counts, outcome, entry)
        if outcome == WRITTEN:
            page.changes.append(entry)
    return page


def _pending_page(database_id, record, cursor, page_size, counts) -> _Page:
    """One page of the database's asset-state records when automatic evaluation was turned on for
    the schema in force: the records still pending or awaiting re-evaluation are queued."""
    page = _Page()
    response = store.asset_state_table.query(**_page_kwargs(database_id, cursor, page_size))
    page.cursor = response.get("LastEvaluatedKey")
    page.to_evaluate = [item["assetId"] for item in response.get("Items", [])
                        if item.get("schemaSource") != SCHEMA_SOURCE_ASSET
                        and (item.get("complianceState") == STATE_PENDING_EVALUATION
                             or item.get(store.AWAITING_REEVALUATION_SINCE))]
    counts["assetsProcessed"] += len(page.to_evaluate)
    return page


_PAGES: Dict[str, Callable[..., _Page]] = {
    OPERATION_BIND: _bind_page,
    OPERATION_CHANGE: _bind_page,
    OPERATION_UNBIND: _unbind_page,
    OPERATION_EVALUATE_PENDING: _pending_page,
}


def _binding_matches(record: Dict[str, Any], db_item: Dict[str, Any]) -> bool:
    """Whether the database row still holds the binding the apply applies."""
    bound = db_item.get("complianceSchemaName")
    if record.get("operation") == OPERATION_UNBIND:
        return not bound
    return bound == record.get("schemaName")


def run(database_id: str, apply_id: str, run_id: str, deadline: float,
        clock: Callable[[], float] = time.monotonic, sqs=None, queue_url: str = "",
        page_size: int = PAGE_SIZE) -> bool:
    """Apply the database's binding change from its cursor until done or `deadline` (a `clock`
    value), page by page; `sqs` and `queue_url` queue the evaluations automatic evaluation calls
    for. Returns False when the apply is not done and the next message must be queued; True when
    it is done, ended, or not this run's to make (nothing is written then)."""
    record = get_record(database_id)
    if (not record or record.get("applyStatus") != STATUS_APPLYING
            or record.get("applyId") != apply_id or record.get("runId") != run_id):
        logger.info(f"Binding apply message for {database_id} ignored: it names no running apply")
        return True
    deleted_scope = databaseRetirement.deleted_id(database_id)
    if not _of_live_generation(record, database_id) or store.database_moving(database_id):
        finish(record, STATUS_FAILED, reason=FAILED_DATABASE_DELETED,
               audit_database_id=deleted_scope)
        return True
    db_item = store.database_table.get_item(Key={"databaseId": database_id},
                                            ConsistentRead=True).get("Item")
    if not db_item:
        finish(record, STATUS_FAILED, reason=FAILED_DATABASE_DELETED,
               audit_database_id=deleted_scope)
        return True
    if not _binding_matches(record, db_item):
        finish(record, STATUS_FAILED, reason=FAILED_BINDING_CHANGED)
        return True
    operation = record.get("operation")
    auto_eval = db_item.get("complianceAutoEval") is True
    evaluates = operation == OPERATION_EVALUATE_PENDING or (
        operation in (OPERATION_BIND, OPERATION_CHANGE) and auto_eval)
    read_page = _PAGES[operation]
    cursor = json.loads(record["cursor"]) if record.get("cursor") else None
    counts = counts_of(record)
    actor = record.get("actor") or store.SYSTEM_ACTOR
    pages = 0
    while True:
        # Every run reads at least one page, so a continuation always makes progress.
        if pages and clock() >= deadline:
            return False
        pages += 1
        page = read_page(database_id, record, cursor, page_size, counts)
        if page.stopped:
            # The records written before the move began are the deleted database's now (its move
            # takes them), and so are their audit entries.
            audit_asset_changes(deleted_scope, page.changes, actor, record.get("schemaName"),
                                record.get("previousSchema"))
            finish(record, STATUS_FAILED, counts=counts, reason=FAILED_DATABASE_DELETED,
                   audit_database_id=deleted_scope)
            return True
        audit_asset_changes(database_id, page.changes, actor, record.get("schemaName"),
                            record.get("previousSchema"))
        if evaluates and page.to_evaluate:
            counts["evaluationsQueued"] += queue_evaluations(database_id, page.to_evaluate, sqs,
                                                             queue_url)
        cursor = page.cursor
        if cursor is None:
            if finish(record, STATUS_APPLIED, counts=counts):
                logger.info(f"Binding apply {apply_id} of {database_id} finished: {counts}")
            return True
        if not _save_progress(record, cursor, counts):
            logger.info(f"Binding apply {apply_id} of {database_id} continues in another run")
            return True
