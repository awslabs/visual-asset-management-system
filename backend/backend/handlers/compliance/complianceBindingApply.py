#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A database binding change, applied to the database's asset records in the background (D161).

Binding a schema to a database, changing it, removing it, and turning automatic evaluation on for
the schema in force reach every asset record of the database, which takes longer than an API
request may run on a large database. The request (`PUT` / `DELETE /compliance/bind/{databaseId}`,
`complianceSchemaBindingService`) makes every check it made before, saves the database binding,
writes the database-level audit entry naming the caller, records an **apply** (`begin`) and queues
its first message on the compliance job queue (`message`), then answers `202` with the number of
assets the apply goes through (`count_assets`, a count query). The compliance job Lambda
(`complianceJobs`) runs the apply (`run`), as it moves a deleted database's data
(`databaseRetirement.run`), in two phases, and queues each continuation on its own queue; the
evaluations the apply queues go to the compliance trigger's queue, where the trigger evaluates
them as it evaluates any asset change:

- **records** (`PHASE_RECORDS`; the apply is `applying`): the records are written page by page
  until the run's deadline (the asset rows for a bind or a change, each record decided by
  `bind_record`; the asset-state records for a removal, `unbind_record`), each record changed with
  one `schema_binding_applied_to_asset` audit entry naming the apply's caller. No evaluation is
  queued in this phase, so no message of the apply ever waits behind its own evaluations while a
  record still carries the binding before the change. When the pages end the apply is `applied`
  and one `schema_binding_apply_finished` entry records the totals (a second database-level entry:
  an audit entry is never rewritten).
- **evaluations** (`PHASE_EVALUATIONS`; the apply is `applied`, `evaluationStatus` `queueing`),
  only with automatic evaluation on: the records the apply wrote (its stamp) are read again and one
  evaluation is queued for each on the trigger's queue, in shares of EVALUATIONS_PER_SHARE. The
  trigger's queue is shared by every database's automatic evaluations, so the apply never has more
  than EVALUATION_OUTSTANDING_LIMIT of its own evaluations on it (D164): the job counts every
  evaluation it sends on the apply record before sending it (`evaluationsSent`,
  `reserve_evaluations`), the trigger counts every one it is done with (`evaluationsTaken`,
  `count_taken`, once per batch), and a share is queued only when it fits under the limit beside
  the ones sent and not yet taken (`outstanding_evaluations`). Another database's change then
  waits behind at most that many of the apply's, whatever its size. The bound rests on the two
  counts alone, never on the queue's approximate message count, which lags the sends. While no
  share fits the phase gives way: it hands over to a continuation delayed by
  EVALUATION_FIRST_WAIT_SECONDS, doubled at each wait up to EVALUATION_MAX_WAIT_SECONDS, for as
  long as the trigger takes, so a busy or held trigger slows the phase down but never stops it
  and never lets it past the limit. An evaluation that left the queue uncounted (its last
  delivery's batch failed as a whole) is no longer counted once the trigger queue's retention
  period has passed since the apply's last share, when no message of the apply can remain on the
  queue. Turning automatic evaluation on for the
  schema in force is this phase alone (`evaluatePending`, over the records pending or awaiting
  re-evaluation). An evaluation message names the apply (`bindingApplyId`) and the trigger
  evaluates the asset only while that apply is the database's, only for a record it reached, and
  judged as a change made
  when the binding reached the record (`bindingAppliedAt`), so a record evaluated since, or a
  message delivered twice, is not evaluated again (`complianceTrigger._process_binding_evaluation`).
  The phase does not hold the database: every record already carries the binding, so another
  binding change or Evaluate All may run; a later change replaces the apply, and its messages are
  then ignored.

**The apply record** lives in the evaluation table, beside the retirement record (`evaluationId`
`bindingApply#<databaseId>`). It carries no attribute any index of the table reads and no
`databaseId`, so no listing and no deleted database's move reaches it. One per database (the last
apply, kept after it ends, so `GET /compliance/bind/{databaseId}` shows its outcome):

    recordType, bindingDatabaseId, applyId, runId, operation (`bind` | `change` | `unbind` |
    `evaluatePending`), phase (`records` | `evaluations`; absent once both ended), schemaName,
    previousSchema, actor (the caller's user id, for the audit entries), complianceAutoEval,
    assetCount, startedAt, lastProgressAt, finishedAt, cursor (the paging key, JSON), counts
    (`COUNT_FIELDS`), applyStatus (`applying` | `applied` | `failed`), failureReason,
    evaluationStatus (`queueing` | `queued` | `cancelled`), evaluationsToQueue,
    evaluationEndReason, nextMessageAt, nextMessageDelay (seconds the next message waits before
    delivery while the apply's evaluations fill its share of the trigger's queue),
    evaluationWaits, evaluationsSent, evaluationsSentAt, evaluationsTaken, runStartedAt,
    runReceiveCount, runs, runLimit, stopReason, resumedAt, resumedBy, resumes.

**The message** (`APPLY_ACTION`) carries the database id, the apply id and the run id only, and
is read from a raw queue body only (`message_target`, never an SNS envelope). A run acts only while
the stored apply's phase is running with that apply id and run id, so a message can continue an
apply a request started and never start one. Everything the run applies comes from the stored apply
and the database row, read when it starts: the schema, the operation and the actor are never taken
from the message. A run that ends before its phase does saves its cursor with `nextMessageAt` and
returns False, and its caller queues the next message; a run that fails leaves its message to the
queue's redelivery and, past the receive limit, to its dead-letter queue.

**Stopped or not** (`stop_reason`) is derived from the apply's own messages, never from how long
ago it last made progress, so a healthy apply of any size never reads `stalled`, however long its
next message waits (behind other databases' jobs on the job queue, or delayed while the trigger
works through the apply's evaluations): a message recorded as queued after the last run started
(`nextMessageAt`) will be delivered; a run started (`runStartedAt`) within the job Lambda's timeout is in flight; a run that
ended without handing over is retried by the queue unless that was its last delivery
(`runReceiveCount` at the queue's receive limit). Only when none holds (the last delivery failed,
the next message could not be queued, or a message outlived the queue's retention), or the apply
was stopped at its run limit (below), does the apply read `stalled`; repeating the same change then
resumes it from its cursor with a new run id (`resume`), and a message of the run before is
ignored. The timings are the job Lambda's and its queue's (complianceFunctions.ts; a test reads
them from there).

**The run limit.** The job Lambda runs with Lambda's recursive-loop detection off, since an apply
continues itself for as many runs as the database's size needs; the apply's own count replaces it.
Every run counts itself on the record (`runs`, each delivery of the apply's message included) and
the apply carries a limit derived from its size (`runLimit`, `run_limit`: every run reads at least
one page, so the pages its phases read bound its runs, with a factor and an allowance for
redeliveries and for the evaluations phase's waits). A run whose message waited the longest delay
(EVALUATION_MAX_WAIT_SECONDS, while the trigger works through the apply's evaluations) is not
counted: such runs come at most four an hour, so they cannot form the fast chain the limit stops,
and a trigger held for hours does not stop the apply. A run past the limit runs nothing, queues no
continuation and records why (`stopReason`, STALLED_RUN_LIMIT), so the apply reads `stalled` and the
same change resumes it with a fresh count.

**Idempotency.** Every record a bind or a kept-quarantined removal writes carries the apply's id
(`APPLY_STAMP_FIELD`) and the time of the write (`APPLIED_AT_FIELD`); a page applied twice (a
redelivered message, a run that stopped mid-page) finds the records it wrote stamped and writes
nothing and audits nothing for them again (they are counted from the stamp). A record deleted by a
removal is not read again. Each record's write is conditional on its revision as read (D159), so a
writer that changed it meanwhile wins and the record is decided again as it is now. A bind also
leaves alone a record that already names the apply's schema from the database binding and was
written after the apply began (a new asset registered under the new binding, or an evaluation
under it): it already has the binding.

**While applying** (the records phase) another binding change of the database answers `409`
(`APPLYING_MESSAGE`, `applying_response`), and so does Evaluate All of it (it would evaluate the
records not reached yet under the binding before the change). A deleted database's move wins: a run
checks the retirement record before each record it writes and ends the apply `failed` (or its
evaluations `cancelled`) once the move began, and an apply that began before the database's last
delete is not the live database's (`current_record`).
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

# The apply id a bind or a kept-quarantined removal stamps on each record it writes, and the time of
# that write (no API answer returns either: `listing.PRIVATE_STATE_ATTRIBUTES`).
APPLY_STAMP_FIELD = listing.BINDING_APPLY_STAMP_ATTRIBUTE
APPLIED_AT_FIELD = listing.BINDING_APPLIED_AT_ATTRIBUTE
COUNT_FIELDS = ("assetsProcessed", "assetsPendingEvaluation", "assetsKeptQuarantined",
                "removedComplianceRecords", "evaluationsQueued")

PAGE_SIZE = 100
# The evaluations phase queues the apply's evaluations in shares of this many, a share only when it
# fits beside the apply's evaluations sent and not yet taken by the trigger under
# EVALUATION_OUTSTANDING_LIMIT (D164); while none fits the phase hands over to a continuation
# delayed by EVALUATION_FIRST_WAIT_SECONDS, doubled at each wait up to SQS's largest delay, for as
# long as the trigger takes (module docstring). EVALUATION_WAITS_PER_SHARE is how many of a share's
# waits are shorter than the largest delay: those count against the run limit, and the ones at the
# largest delay do not (`mark_run_started`).
EVALUATIONS_PER_SHARE = 100
EVALUATION_OUTSTANDING_LIMIT = 300
EVALUATION_FIRST_WAIT_SECONDS = 5
EVALUATION_MAX_WAIT_SECONDS = 900
EVALUATION_WAITS_PER_SHARE = 8
# The compliance trigger's queue (infra/lib/lambdaBuilder/complianceFunctions.ts:
# COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT; the queue sets no retention period, so SQS keeps a
# message 4 days; a test reads them from there). The trigger counts an evaluation it failed on its
# last delivery as taken (SQS moves it to the dead-letter queue), and once the retention period has
# passed since the apply's last share no evaluation of it can remain on the queue.
TRIGGER_QUEUE_MAX_RECEIVE_COUNT = 3
TRIGGER_QUEUE_RETENTION_SECONDS = 4 * 24 * 3600

PHASE_RECORDS = "records"
PHASE_EVALUATIONS = "evaluations"
EVALUATIONS_QUEUEING = "queueing"
EVALUATIONS_QUEUED = "queued"
EVALUATIONS_CANCELLED = "cancelled"
# Not stored: how a `queueing` evaluations phase that stopped reads (`evaluation_status`).
EVALUATIONS_STALLED = "stalled"
CANCELLED_AUTO_EVAL_OFF = "Automatic evaluation was turned off"
CANCELLED_DATABASE_DELETED = "The database was deleted"
CANCELLED_BINDING_CHANGED = "The database binding changed"

# The settings of the compliance job Lambda and its queue the stop decision relies on
# (infra/lib/lambdaBuilder/complianceFunctions.ts: COMPLIANCE_JOB_TIMEOUT,
# COMPLIANCE_JOB_QUEUE_VISIBILITY_TIMEOUT, COMPLIANCE_JOB_QUEUE_MAX_RECEIVE_COUNT; the queue sets
# no retention period, so SQS keeps a message 4 days). A test reads them from there.
JOB_TIMEOUT_SECONDS = 300
JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS = 1800
JOB_QUEUE_MAX_RECEIVE_COUNT = 3
JOB_QUEUE_RETENTION_SECONDS = 4 * 24 * 3600
# Time after a run's start by which it has certainly ended (its Lambda timeout, plus clock skew).
RUN_END_MARGIN_SECONDS = 60
# The run limit (module docstring, "The run limit"): runs counted against the pages the apply's
# phases read, twice over, plus an allowance for redeliveries and resumes.
RUN_LIMIT_FACTOR = 2
RUN_LIMIT_ALLOWANCE = 20

APPLYING_STATUS = 409
APPLYING_MESSAGE = ("The compliance schema binding of this database is still being applied; "
                    "try again when it finishes")
STALLED_MESSAGE = ("The compliance schema binding of this database stopped while it was being "
                   "applied; repeat the same change to resume it")
STALLED_FAILED = "Its last step failed on every delivery; repeat the same change to resume it"
STALLED_NOT_QUEUED = "Its next step could not be queued; repeat the same change to resume it"
STALLED_NOT_DELIVERED = ("Its next step was not delivered within the queue's retention period; "
                         "repeat the same change to resume it")
STALLED_RUN_LIMIT = ("It ran more steps than a change of its size needs, so it was stopped; "
                     "repeat the same change to resume it")
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


def active_phase(record: Optional[Dict[str, Any]]) -> Optional[str]:
    """The phase an apply is running (`PHASE_RECORDS`, `PHASE_EVALUATIONS`), or None once both
    ended."""
    if not record:
        return None
    phase = record.get("phase")
    if phase in (PHASE_RECORDS, PHASE_EVALUATIONS):
        return phase
    return PHASE_RECORDS if record.get("applyStatus") == STATUS_APPLYING else None


def applying_record(database_id: str) -> Optional[Dict[str, Any]]:
    """The database's apply while its records are being written (stalled or not), else None."""
    record = current_record(database_id)
    return record if record and record.get("applyStatus") == STATUS_APPLYING else None


def monotonic() -> float:
    """The clock a run's deadline is measured on (time.monotonic; tests replace it)."""
    return time.monotonic()


def stop_reason(record: Optional[Dict[str, Any]], now: Optional[datetime] = None
                ) -> Optional[str]:
    """None while the apply's running phase has a run in flight or a message that will run; else
    why it stopped (module docstring, "Stopped or not"). How long ago it last made progress does
    not count: a message waiting on a busy queue is delivered when its turn comes."""
    if active_phase(record) is None:
        return None
    if record.get("stopReason"):
        # Stopped at its run limit (`run`): nothing runs until the same change resumes it.
        return record["stopReason"]
    now = now or datetime.now(timezone.utc)
    queued = _parse(record.get("nextMessageAt"))
    started = _parse(record.get("runStartedAt"))
    if queued is not None and (started is None or queued >= started):
        waited = (now - queued).total_seconds() - _number(record.get("nextMessageDelay"))
        return STALLED_NOT_DELIVERED if waited > JOB_QUEUE_RETENTION_SECONDS else None
    if started is None:
        return STALLED_NOT_QUEUED
    age = (now - started).total_seconds()
    if age <= JOB_TIMEOUT_SECONDS + RUN_END_MARGIN_SECONDS:
        return None
    if max(1, _number(record.get("runReceiveCount"))) < JOB_QUEUE_MAX_RECEIVE_COUNT:
        # The run ended without handing over; the queue delivers its message again once its
        # visibility timeout passes.
        limit = JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS + JOB_QUEUE_RETENTION_SECONDS
        return STALLED_NOT_DELIVERED if age > limit else None
    return STALLED_FAILED


def run_limit(operation: Optional[str], asset_count: Any, auto_eval: Any) -> int:
    """The most runs an apply of `asset_count` records may take (module docstring, "The run
    limit"). Every run reads at least one page, so a phase takes at most as many runs as it reads
    pages: the records phase one per PAGE_SIZE records, and the evaluations phase (a bind or a
    change with automatic evaluation on, or `evaluatePending`) as many again, each of whose shares
    may also wait up to EVALUATION_WAITS_PER_SHARE counted times for the trigger (a wait at the
    largest delay is not counted, `mark_run_started`). Twice that, plus a fixed allowance for
    redeliveries and resumes."""
    pages = max(1, -(-_number(asset_count) // PAGE_SIZE))
    records = 0 if operation == OPERATION_EVALUATE_PENDING else pages
    evaluates = operation == OPERATION_EVALUATE_PENDING or (
        operation in (OPERATION_BIND, OPERATION_CHANGE) and bool(auto_eval))
    evaluations = pages * (1 + EVALUATION_WAITS_PER_SHARE) if evaluates else 0
    return RUN_LIMIT_FACTOR * (records + evaluations) + RUN_LIMIT_ALLOWANCE


def limit_of(record: Dict[str, Any]) -> int:
    """The apply's run limit as stored, or derived from it (an apply recorded before the limit
    was)."""
    return _number(record.get("runLimit")) or run_limit(
        record.get("operation"), record.get("assetCount"), record.get("complianceAutoEval"))


def is_stalled(record: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """Whether an apply's running phase has stopped (`stop_reason`)."""
    return active_phase(record) is not None and stop_reason(record, now) is not None


def status(record: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[str]:
    """`applying`, `stalled`, `applied` or `failed`; None without an apply. An apply whose records
    are all written is `applied` while its evaluations are still being queued
    (`evaluation_status`)."""
    if not record:
        return None
    if record.get("applyStatus") == STATUS_APPLYING and stop_reason(record, now):
        return STATUS_STALLED
    return record.get("applyStatus")


def evaluation_status(record: Optional[Dict[str, Any]], now: Optional[datetime] = None
                      ) -> Optional[str]:
    """`queueing`, `stalled`, `queued` or `cancelled` for an apply that queues evaluations; None
    for one that queues none (automatic evaluation off, a removal)."""
    if not record:
        return None
    stored = record.get("evaluationStatus")
    if (stored == EVALUATIONS_QUEUEING and active_phase(record) == PHASE_EVALUATIONS
            and stop_reason(record, now)):
        return EVALUATIONS_STALLED
    return stored


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
    the run id, the cursor and the message bookkeeping stay internal."""
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
        view["reason"] = stop_reason(record, now)
    evaluations = evaluation_status(record, now)
    if evaluations:
        view["evaluationStatus"] = evaluations
        view["evaluationsToQueue"] = _number(record.get("evaluationsToQueue"))
        if evaluations == EVALUATIONS_STALLED:
            view["evaluationReason"] = stop_reason(record, now)
        elif evaluations == EVALUATIONS_CANCELLED and record.get("evaluationEndReason"):
            view["evaluationReason"] = record["evaluationEndReason"]
    return view


def applying_response(event=None, record: Optional[Dict[str, Any]] = None):
    """The `409` of a binding change (or Evaluate All) of a database whose records are being
    written; a stalled apply says how to resume it."""
    from models.common import general_error
    message = STALLED_MESSAGE if status(record) == STATUS_STALLED else APPLYING_MESSAGE
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
    """Record a new apply, conditional on the database having none whose records are being written
    (an apply of a deleted generation does not count; one still queueing its evaluations is
    replaced). A bind, change or removal starts `applying` in its records phase; automatic
    evaluation turned on (`evaluatePending`) was saved by the request, so it starts `applied` in its
    evaluations phase. Returns the record, or None when another request's apply is running."""
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
        # The request queues the first message right after this write (`message_not_queued` when
        # it could not).
        "nextMessageAt": started,
        "counts": {field: 0 for field in COUNT_FIELDS},
        "resumes": 0,
        "runs": 0,
        "runLimit": run_limit(operation, asset_count, auto_eval),
    }
    if operation == OPERATION_EVALUATE_PENDING:
        record.update(applyStatus=STATUS_APPLIED, finishedAt=started, phase=PHASE_EVALUATIONS,
                      evaluationStatus=EVALUATIONS_QUEUEING, evaluationsToQueue=int(asset_count))
    else:
        record.update(applyStatus=STATUS_APPLYING, phase=PHASE_RECORDS)
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
    """Resume a stalled apply from its cursor, in the phase it stopped in: a new run id (a message
    of the run before is then ignored), its message recorded as queued and its run count started
    again (a stop at the run limit is cleared), conditional on the apply and run being the ones
    read. Returns the record as resumed, or None when another request resumed it or a run moved it
    meanwhile."""
    resumed_at = now_iso(now)
    run_id = str(uuid.uuid4())
    limit = limit_of(record)
    cleared = ("runStartedAt", "runReceiveCount", "stopReason", "nextMessageDelay")
    try:
        store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]),
            UpdateExpression=("SET runId = :run, lastProgressAt = :now, nextMessageAt = :now, "
                              "resumedAt = :now, resumedBy = :actor, runs = :zero, "
                              "runLimit = :limit ADD resumes :one REMOVE " + ", ".join(cleared)),
            ConditionExpression="applyId = :apply AND runId = :readRun",
            ExpressionAttributeValues={
                ":run": run_id, ":now": resumed_at, ":actor": actor, ":one": 1, ":zero": 0,
                ":limit": limit, ":apply": record["applyId"], ":readRun": record.get("runId")})
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return None
        raise
    resumed = {key: value for key, value in record.items() if key not in cleared}
    resumed.update(runId=run_id, lastProgressAt=resumed_at, nextMessageAt=resumed_at,
                   resumedAt=resumed_at, resumedBy=actor, runs=0, runLimit=limit,
                   resumes=_number(record.get("resumes")) + 1)
    return resumed


def _owner_condition(record: Dict[str, Any]) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """The record still names this apply and run, in the phase read."""
    phase = active_phase(record)
    names = {"#phase": "phase"}
    values = {":apply": record["applyId"], ":run": record["runId"], ":phase": phase}
    if record.get("phase") is None:
        return ("applyId = :apply AND runId = :run AND attribute_not_exists(#phase)", names,
                {":apply": record["applyId"], ":run": record["runId"]})
    return "applyId = :apply AND runId = :run AND #phase = :phase", names, values


def _owned_update(record: Dict[str, Any], expression: str, values: Dict[str, Any],
                  names: Optional[Dict[str, str]] = None) -> bool:
    """One UpdateItem of the apply record under `_owner_condition`; False when it does not hold."""
    condition, owner_names, owner_values = _owner_condition(record)
    try:
        store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]), UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames={**owner_names, **(names or {})},
            ExpressionAttributeValues={**owner_values, **values})
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return False
        raise
    return True


def mark_run_started(record: Dict[str, Any], receive_count: int = 1,
                     now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Record that a run of the apply started, with its message's delivery count (the queue's
    `ApproximateReceiveCount`), so `stop_reason` knows a run is in flight and whether the queue
    delivers its message again if it fails. Returns the record as updated, or None when it no
    longer names this apply and run. A run whose message waited the largest delay (the
    evaluations phase waiting for the trigger, `long_wait`) is not counted against the run
    limit (module docstring, "The run limit")."""
    started = now_iso(now)
    count = max(1, int(receive_count or 1))
    counted = 0 if long_wait(record) else 1
    condition, names, values = _owner_condition(record)
    try:
        response = store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]),
            UpdateExpression=("SET runStartedAt = :started, runReceiveCount = :count, "
                              "lastProgressAt = :started ADD runs :one"),
            ConditionExpression=condition, ExpressionAttributeNames=names,
            ExpressionAttributeValues={**values, ":started": started, ":count": count,
                                       ":one": counted},
            ReturnValues="UPDATED_NEW")
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return None
        raise
    runs = _number((response or {}).get("Attributes", {}).get("runs")) or (
        _number(record.get("runs")) + counted)
    return dict(record, runStartedAt=started, runReceiveCount=count, lastProgressAt=started,
                runs=runs)


def long_wait(record: Optional[Dict[str, Any]]) -> bool:
    """Whether the apply's last message was delayed by the largest wait: its evaluations phase was
    waiting for the trigger to take the apply's evaluations (`_run_evaluations`)."""
    return (active_phase(record) == PHASE_EVALUATIONS
            and _number((record or {}).get("nextMessageDelay")) >= EVALUATION_MAX_WAIT_SECONDS)


# --- The apply's evaluations on the trigger's queue (D164) ---


def _retention_passed(record: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Whether the trigger queue's retention period has passed since the apply's last share, so
    SQS holds no evaluation of it any more."""
    sent_at = _parse(record.get("evaluationsSentAt"))
    if sent_at is None:
        return False
    age = ((now or datetime.now(timezone.utc)) - sent_at).total_seconds()
    return age > TRIGGER_QUEUE_RETENTION_SECONDS + RUN_END_MARGIN_SECONDS


def outstanding_evaluations(record: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> int:
    """How many of the apply's evaluations the job counted as sent and the trigger has not counted
    as taken (never below 0). None once the trigger queue's retention period has passed since the
    apply's last share: no message of it can remain on the queue, so an evaluation that left it
    uncounted no longer holds the phase back."""
    record = record or {}
    if _retention_passed(record, now):
        return 0
    return max(0, _number(record.get("evaluationsSent")) - _number(record.get("evaluationsTaken")))


def reserve_evaluations(record: Dict[str, Any], current: Dict[str, Any], count: int,
                        now: Optional[datetime] = None) -> bool:
    """Count `count` evaluations as sent on the apply record before they are sent (`ADD
    evaluationsSent`, and the time of the share), conditional on the record still naming this
    apply and run and on no other run having counted any since `current` (the record as this run
    last read it) was read, so two runs can never both fill the same room. When the trigger
    queue's retention period has passed since the last share, the evaluations counted before are
    counted taken in the same write (`outstanding_evaluations`). Returns whether it landed."""
    read_sent = _number(current.get("evaluationsSent"))
    owner, names, values = _owner_condition(record)
    values = {**values, ":now": now_iso(now), ":count": int(count), ":readSent": read_sent}
    expression = "SET evaluationsSentAt = :now"
    if read_sent and _retention_passed(current, now):
        expression += ", evaluationsTaken = :readSent"
    expression += " ADD evaluationsSent :count"
    counted = ("(attribute_not_exists(evaluationsSent) OR evaluationsSent = :readSent)"
               if read_sent == 0 else "evaluationsSent = :readSent")
    try:
        store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]), UpdateExpression=expression,
            ConditionExpression=f"({owner}) AND {counted}", ExpressionAttributeNames=names,
            ExpressionAttributeValues=values)
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return False
        raise
    return True


def release_evaluations(record: Dict[str, Any], count: int) -> None:
    """Take back the count of `count` evaluations `reserve_evaluations` counted that were not
    queued, while the record names the apply. Best-effort: a count left too high only makes the
    phase queue less (logged)."""
    if count <= 0:
        return
    try:
        store.evaluation_table.update_item(
            Key=record_key(record["bindingDatabaseId"]),
            UpdateExpression="ADD evaluationsSent :minus", ConditionExpression="applyId = :apply",
            ExpressionAttributeValues={":minus": -int(count), ":apply": record["applyId"]})
    except Exception as e:
        logger.warning(f"Could not take back the count of {count} unqueued evaluations of the "
                       f"binding apply of {record['bindingDatabaseId']}: {e}")


def count_taken(database_id: str, apply_id: str, count: int) -> bool:
    """Count `count` of an apply's evaluations as taken by the trigger (`ADD evaluationsTaken`),
    conditional on the record still naming that apply: the trigger calls it once per batch with
    the apply's evaluations it is done with
    (`complianceTrigger.TriggerBatch.count_binding_evaluations`).
    Returns False when another apply is the database's now; raises a failed write."""
    if not database_id or not apply_id or count <= 0:
        return False
    try:
        store.evaluation_table.update_item(
            Key=record_key(database_id), UpdateExpression="ADD evaluationsTaken :count",
            ConditionExpression="applyId = :apply",
            ExpressionAttributeValues={":count": int(count), ":apply": apply_id})
    except Exception as e:
        if store.is_conditional_check_failure(e):
            return False
        raise
    return True


def stop_at_run_limit(record: Dict[str, Any]) -> bool:
    """Stop an apply whose run went past its run limit: record why (`stopReason`), so it reads
    `stalled` and the same change resumes it, and that no message is queued. Conditional on the
    record still naming this apply and run. Returns whether it landed."""
    logger.error(f"Binding apply {record['applyId']} of {record['bindingDatabaseId']} stopped: run "
                 f"{_number(record.get('runs'))} is past its limit of {limit_of(record)}")
    return _owned_update(record, "SET stopReason = :reason REMOVE nextMessageAt, nextMessageDelay",
                         {":reason": STALLED_RUN_LIMIT})


def message_not_queued(record: Dict[str, Any]) -> Dict[str, Any]:
    """Record that the apply's next message could not be queued, so the apply reads `stalled` (and
    the same change resumes it) rather than waiting for a message that will never come. Returns
    the record as updated."""
    try:
        _owned_update(record, "REMOVE nextMessageAt", {})
    except Exception as e:
        logger.exception(f"Could not record the unqueued message of the binding apply of "
                         f"{record['bindingDatabaseId']}: {e}")
    return {key: value for key, value in record.items() if key != "nextMessageAt"}


def _save_progress(record: Dict[str, Any], cursor: Optional[Dict[str, Any]],
                   counts: Dict[str, int], continuing: bool = False,
                   now: Optional[datetime] = None, waits: Optional[int] = None,
                   delay: int = 0) -> bool:
    """Save a page's cursor and the counts, conditional on the record still naming this apply and
    run; `continuing` (the run hands over to its next message) also records that message as
    queued, delivered after `delay` seconds (the evaluations phase waiting for the trigger to take
    the apply's evaluations). `waits` saves how many times the evaluations phase has waited for
    its next share.
    Returns False when the record no longer names them (a resume handed the apply to a new run)."""
    stamp = now_iso(now)
    values: Dict[str, Any] = {":counts": counts, ":now": stamp}
    expression = "SET counts = :counts, lastProgressAt = :now"
    removed = []
    if continuing:
        expression += ", nextMessageAt = :now"
        if delay > 0:
            expression += ", nextMessageDelay = :delay"
            values[":delay"] = int(delay)
        else:
            removed.append("nextMessageDelay")
    if waits is not None:
        expression += ", evaluationWaits = :waits"
        values[":waits"] = int(waits)
    if cursor:
        expression += ", #c = :cursor"
        values[":cursor"] = json.dumps(cursor, default=_json_default)
    else:
        removed.append("#c")
    if removed:
        expression += " REMOVE " + ", ".join(removed)
    return _owned_update(record, expression, values, {"#c": "cursor"})


def finish(record: Dict[str, Any], outcome: str, counts: Optional[Dict[str, int]] = None,
           reason: Optional[str] = None, audit: bool = True,
           audit_database_id: Optional[str] = None, evaluations_to_queue: int = 0,
           now: Optional[datetime] = None) -> bool:
    """End an apply's records phase `applied` or `failed`, conditional on the record still naming
    this apply and run; when it lands (once), write the `schema_binding_apply_finished` entry
    naming the apply's caller with the totals, under `audit_database_id` (the database id by
    default; a deleted database's apply is recorded under `<databaseId>#deleted`, where its move
    takes its data). With `evaluations_to_queue`, an applied apply goes on to its evaluations phase
    (`queueing`, its first message recorded as queued: the caller sends it). Returns whether it
    landed."""
    finished_at = now_iso(now)
    counts = counts if counts is not None else counts_of(record)
    values: Dict[str, Any] = {":outcome": outcome, ":now": finished_at, ":counts": counts}
    expression = ("SET applyStatus = :outcome, finishedAt = :now, lastProgressAt = :now, "
                  "counts = :counts")
    if reason:
        expression += ", failureReason = :reason"
        values[":reason"] = reason
    evaluating = outcome == STATUS_APPLIED and evaluations_to_queue > 0
    if evaluating:
        expression += (", #phase = :evaluations, evaluationStatus = :queueing, "
                       "evaluationsToQueue = :toQueue, nextMessageAt = :now REMOVE #c")
        values.update({":evaluations": PHASE_EVALUATIONS, ":queueing": EVALUATIONS_QUEUEING,
                       ":toQueue": int(evaluations_to_queue)})
    else:
        expression += " REMOVE #c, #phase"
    if not _owned_update(record, expression, values, {"#c": "cursor"}):
        return False
    if audit:
        details = {"applyId": record["applyId"], "operation": record.get("operation"),
                   "outcome": outcome, "assetCount": _number(record.get("assetCount")),
                   "previousSchema": record.get("previousSchema"), **counts}
        if evaluating:
            details["evaluationsToQueue"] = int(evaluations_to_queue)
        if reason:
            details["reason"] = reason
        store.write_audit(audit_database_id or record["bindingDatabaseId"], "*",
                          event_type=AUDIT_APPLY_FINISHED,
                          actor=record.get("actor") or store.SYSTEM_ACTOR,
                          schema_name=record.get("schemaName") or record.get("previousSchema"),
                          details=details)
    return True


def end_evaluations(record: Dict[str, Any], outcome: str, counts: Optional[Dict[str, int]] = None,
                    reason: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """End an apply's evaluations phase `queued` or `cancelled` (with `reason`), conditional on the
    record still naming this apply and run. Returns whether it landed."""
    values: Dict[str, Any] = {":outcome": outcome, ":now": now_iso(now),
                              ":counts": counts if counts is not None else counts_of(record)}
    expression = ("SET evaluationStatus = :outcome, evaluationsFinishedAt = :now, "
                  "lastProgressAt = :now, counts = :counts")
    if reason:
        expression += ", evaluationEndReason = :reason"
        values[":reason"] = reason
    expression += " REMOVE #c, #phase"
    landed = _owned_update(record, expression, values, {"#c": "cursor"})
    if landed:
        logger.info(f"Binding apply {record['applyId']} of {record['bindingDatabaseId']}: "
                    f"evaluations {outcome}{f' ({reason})' if reason else ''}")
    return landed


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
    """Queue the message of an apply's run on the compliance job queue (`queue_url`), delayed by
    the record's `nextMessageDelay` when the evaluations phase waits for the trigger to take the
    apply's evaluations; False (logged) when it could not be queued: the caller
    then records it (`message_not_queued`, so the apply reads stalled and the same change resumes
    it) or leaves its own message to the queue's redelivery."""
    if not queue_url or sqs is None:
        logger.error(f"No compliance job queue to apply the binding of "
                     f"{record['bindingDatabaseId']} on")
        return False
    kwargs: Dict[str, Any] = {}
    delay = min(EVALUATION_MAX_WAIT_SECONDS, _number(record.get("nextMessageDelay")))
    if delay > 0:
        kwargs["DelaySeconds"] = delay
    try:
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(
            message(record["bindingDatabaseId"], record["applyId"], record["runId"])), **kwargs)
    except Exception as e:
        logger.exception(f"Could not queue the binding apply of {record['bindingDatabaseId']}: {e}")
        return False
    return True


def queue_evaluations(database_id: str, asset_ids: List[str], sqs, queue_url: str,
                      apply_id: Optional[str] = None) -> int:
    """Queue one evaluation message (`{databaseId, assetId}`, and `bindingApplyId` for an apply's
    evaluations phase) per asset on the compliance trigger's queue, ten to a SendMessageBatch; the
    trigger evaluates each as automatic evaluation does. Best-effort: an entry that is not queued is
    logged (the asset stays pending, and its next change or a sweep evaluates it). Returns the
    count queued."""
    if not queue_url or sqs is None or not asset_ids:
        return 0
    queued = 0
    ids = list(dict.fromkeys(asset_ids))
    for start in range(0, len(ids), QUEUE_BATCH):
        entries = [{"Id": str(index), "MessageBody": json.dumps(dict(
                        {"databaseId": database_id, "assetId": asset_id},
                        **({APPLY_STAMP_FIELD: apply_id} if apply_id else {})))}
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
        stamp = {APPLY_STAMP_FIELD: apply["applyId"], APPLIED_AT_FIELD: written_at} if apply else {}
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
    """What one page did: the next cursor, the changes written (to audit), and whether the
    database's move began during it (the page then stops before its next record write)."""

    def __init__(self):
        self.cursor: Optional[Dict[str, Any]] = None
        self.changes: List[Dict[str, Any]] = []
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


_RECORD_PAGES: Dict[str, Callable[..., _Page]] = {
    OPERATION_BIND: _bind_page,
    OPERATION_CHANGE: _bind_page,
    OPERATION_UNBIND: _unbind_page,
}


def _binding_matches(record: Dict[str, Any], db_item: Dict[str, Any]) -> bool:
    """Whether the database row still holds the binding the apply applies."""
    bound = db_item.get("complianceSchemaName")
    if record.get("operation") == OPERATION_UNBIND:
        return not bound
    return bound == record.get("schemaName")


def _to_evaluate(record: Dict[str, Any], item: Dict[str, Any]) -> bool:
    """Whether the evaluations phase queues an evaluation of this asset-state record: one the
    apply's records phase reached (its stamp), or, for automatic evaluation turned on, one still
    pending or awaiting re-evaluation. An asset-level override is its own binding's."""
    if item.get("schemaSource") == SCHEMA_SOURCE_ASSET:
        return False
    if record.get("operation") == OPERATION_EVALUATE_PENDING:
        return (item.get("complianceState") == STATE_PENDING_EVALUATION
                or bool(item.get(store.AWAITING_REEVALUATION_SINCE)))
    return item.get(APPLY_STAMP_FIELD) == record.get("applyId")


def _run_records(database_id: str, record: Dict[str, Any], db_item: Dict[str, Any],
                 deadline: float, clock: Callable[[], float], page_size: int) -> bool:
    """The records phase from the cursor, page by page, until it ends or the deadline passes."""
    read_page = _RECORD_PAGES[record["operation"]]
    cursor = json.loads(record["cursor"]) if record.get("cursor") else None
    counts = counts_of(record)
    actor = record.get("actor") or store.SYSTEM_ACTOR
    deleted_scope = databaseRetirement.deleted_id(database_id)
    while True:
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
        cursor = page.cursor
        if cursor is None:
            to_queue = 0
            if (record["operation"] in (OPERATION_BIND, OPERATION_CHANGE)
                    and db_item.get("complianceAutoEval") is True):
                to_queue = counts["assetsPendingEvaluation"] + counts["assetsKeptQuarantined"]
            if not finish(record, STATUS_APPLIED, counts=counts, evaluations_to_queue=to_queue):
                return True
            logger.info(f"Binding apply {record['applyId']} of {database_id}: records done, "
                        f"{to_queue} evaluations to queue: {counts}")
            # With evaluations to queue, the caller sends the evaluations phase's first message.
            return to_queue == 0
        if clock() >= deadline:
            return not _save_progress(record, cursor, counts, continuing=True)
        if not _save_progress(record, cursor, counts):
            logger.info(f"Binding apply {record['applyId']} of {database_id} continues in "
                        "another run")
            return True


def wait_delay(waits: int) -> int:
    """The delay of the evaluations phase's continuation on its `waits`-th wait for a share (0
    first): EVALUATION_FIRST_WAIT_SECONDS doubled each time, at most SQS's largest delay."""
    return min(EVALUATION_MAX_WAIT_SECONDS, EVALUATION_FIRST_WAIT_SECONDS * (2 ** max(0, waits)))


def _gather(database_id: str, record: Dict[str, Any], cursor, page_size: int, room: int,
            deadline: float, clock: Callable[[], float]) -> Tuple[List[str], Any, bool]:
    """The next share: the asset ids to evaluate from `cursor` on, at most `room` of them, paged
    until that many are gathered, the phase ends or the deadline passes. Returns the ids, the
    cursor after the last record read (the last id gathered when the share filled mid-page) and
    whether records remain."""
    asset_ids: List[str] = []
    while True:
        response = store.asset_state_table.query(**_page_kwargs(database_id, cursor, page_size))
        items = response.get("Items", [])
        # The phase goes on while the read says more pages remain (the key's presence).
        more = "LastEvaluatedKey" in response
        for index, item in enumerate(items):
            if not item.get("assetId") or not _to_evaluate(record, item):
                continue
            asset_ids.append(item["assetId"])
            if len(asset_ids) >= room:
                if index < len(items) - 1 or more:
                    return asset_ids, {"databaseId": database_id, "assetId": item["assetId"]}, True
                return asset_ids, None, False
        if not more:
            return asset_ids, None, False
        cursor = response["LastEvaluatedKey"]
        if clock() >= deadline:
            return asset_ids, cursor, True


def _run_evaluations(database_id: str, record: Dict[str, Any], deadline: float,
                     clock: Callable[[], float], page_size: int, per_share: int, sqs,
                     queue_url: str) -> bool:
    """The evaluations phase from the cursor, share by share until it ends or the deadline
    passes (D164). Before each share it reads the apply record again (strongly consistent) for the
    trigger's count of the evaluations it took: a share of up to `per_share` is queued only when it
    fits under EVALUATION_OUTSTANDING_LIMIT beside the apply's evaluations sent and not yet taken
    (`outstanding_evaluations`; the last share may be smaller). Its evaluations are counted as
    sent before they are sent (`reserve_evaluations`, so the count never trails the queue), and
    the ones that could not be queued are taken back. While no share fits the phase hands over to
    a continuation delayed by `wait_delay`, for as long as the trigger takes. A share is sent
    before the cursor is saved, so a run that fails in between sends it again on redelivery
    (counted again, and taken twice), and the trigger does not evaluate an asset twice for it."""
    cursor = json.loads(record["cursor"]) if record.get("cursor") else None
    counts = counts_of(record)
    waits = _number(record.get("evaluationWaits"))
    while True:
        current = get_record(database_id)
        if not current or (current.get("applyId"), current.get("runId")) != (
                record["applyId"], record["runId"]):
            logger.info(f"Binding apply {record['applyId']} of {database_id} continues in "
                        "another run")
            return True
        room = EVALUATION_OUTSTANDING_LIMIT - outstanding_evaluations(current)
        # A share waits for room for all of it (the last one for what is left to queue).
        remaining = max(1, _number(record.get("evaluationsToQueue")) - counts["evaluationsQueued"])
        if room < min(per_share, remaining, EVALUATION_OUTSTANDING_LIMIT):
            logger.info(f"Binding apply {record['applyId']} of {database_id}: "
                        f"{EVALUATION_OUTSTANDING_LIMIT - room} of its evaluations wait on the "
                        f"trigger; the next share waits {wait_delay(waits)} s")
            return not _save_progress(record, cursor, counts, continuing=True, waits=waits + 1,
                                      delay=wait_delay(waits))
        asset_ids, cursor, more = _gather(database_id, record, cursor, page_size,
                                          min(per_share, room), deadline, clock)
        if asset_ids:
            if not reserve_evaluations(record, current, len(asset_ids)):
                logger.info(f"Binding apply {record['applyId']} of {database_id}: another run "
                            "counted evaluations first; it continues there")
                return True
            queued = queue_evaluations(database_id, asset_ids, sqs, queue_url,
                                       apply_id=record["applyId"])
            release_evaluations(record, len(asset_ids) - queued)
            counts["evaluationsQueued"] += queued
        waits = 0
        if not more:
            end_evaluations(record, EVALUATIONS_QUEUED, counts)
            return True
        if clock() >= deadline:
            return not _save_progress(record, cursor, counts, continuing=True, waits=0)
        if not _save_progress(record, cursor, counts, waits=0):
            logger.info(f"Binding apply {record['applyId']} of {database_id} continues in "
                        "another run")
            return True


def run(database_id: str, apply_id: str, run_id: str, deadline: float,
        clock: Optional[Callable[[], float]] = None, sqs=None, queue_url: str = "",
        page_size: Optional[int] = None, receive_count: int = 1,
        evaluations_per_share: Optional[int] = None) -> bool:
    """Run the apply's current phase from its cursor until it ends, the deadline (a `clock` value)
    passes, or (evaluations) no share fits beside the apply's evaluations the trigger has not taken
    yet; `sqs` and `queue_url` (the compliance trigger's queue) queue the evaluations.
    `receive_count` is the delivery count of the message
    that started the run. A run past the apply's run limit runs nothing and stops the apply
    (`stop_at_run_limit`). Returns False when the apply goes on and the next message must be queued
    (recorded as queued already); True when it is done, ended, stopped, or not this run's to make
    (nothing is written then)."""
    clock = clock or monotonic
    page_size = page_size or PAGE_SIZE
    per_share = evaluations_per_share or EVALUATIONS_PER_SHARE
    record = get_record(database_id)
    phase = active_phase(record)
    if (phase is None or record.get("applyId") != apply_id or record.get("runId") != run_id):
        logger.info(f"Binding apply message for {database_id} ignored: it names no running apply")
        return True
    deleted_scope = databaseRetirement.deleted_id(database_id)
    gone = not _of_live_generation(record, database_id) or store.database_moving(database_id)
    db_item = None if gone else store.database_table.get_item(
        Key={"databaseId": database_id}, ConsistentRead=True).get("Item")
    if phase == PHASE_RECORDS:
        if not db_item:
            finish(record, STATUS_FAILED, reason=FAILED_DATABASE_DELETED,
                   audit_database_id=deleted_scope)
            return True
        if not _binding_matches(record, db_item):
            finish(record, STATUS_FAILED, reason=FAILED_BINDING_CHANGED)
            return True
    else:
        if not db_item:
            end_evaluations(record, EVALUATIONS_CANCELLED, reason=CANCELLED_DATABASE_DELETED)
            return True
        if not _binding_matches(record, db_item):
            end_evaluations(record, EVALUATIONS_CANCELLED, reason=CANCELLED_BINDING_CHANGED)
            return True
        if db_item.get("complianceAutoEval") is not True:
            end_evaluations(record, EVALUATIONS_CANCELLED, reason=CANCELLED_AUTO_EVAL_OFF)
            return True
    if record.get("stopReason"):
        logger.info(f"Binding apply message for {database_id} ignored: the apply was stopped")
        return True
    record = mark_run_started(record, receive_count)
    if record is None:
        return True
    if _number(record.get("runs")) > limit_of(record):
        stop_at_run_limit(record)
        return True
    if phase == PHASE_RECORDS:
        return _run_records(database_id, record, db_item, deadline, clock, page_size)
    return _run_evaluations(database_id, record, deadline, clock, page_size, per_share, sqs,
                            queue_url)
