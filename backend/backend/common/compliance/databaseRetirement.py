#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A deleted database's compliance data moves under `<databaseId>#deleted`.

Deleting a database moves its row to `<databaseId>#deleted` (`databaseService.delete_database`), as
archiving moves an asset's row. Its compliance data follows: the asset-state records, evaluations,
cascades, database-scoped schemas and audit entries stored under the database id are re-keyed to
`<databaseId>#deleted`, so a database created later with the same id starts with no compliance data,
and the deleted one's history stays readable under `<databaseId>#deleted`.

The move is tracked by one **retirement record** in the evaluation table (`evaluationId`
`databaseRetirement#<databaseId>`; no attribute any index of the table reads, so no listing holds
it, and its key is not a UUID, so no route addresses it):

    databaseId, deletedId, state (`moving` | `done`), deletedAt, generation (how many times a
    database with this id was deleted), phase, cursor (the phase's paging key, JSON), moved (rows
    moved per phase), closedCascades, closedWaitingRules, updatedAt, doneAt.

`begin` runs inside the delete request before the database row moves: it writes the record
(`moving`), closes the database's cascades awaiting approval (`aborted`, `abortReason` "The database
was deleted", a `cascade_database_deleted` audit entry each) and ends its pipeline rules that wait
for a running execution of their workflow (`completed` with an error result, the evaluation
`error`), so nothing of the deleted database can be approved or launched later. `run` moves rows,
phase by phase (`PHASES`), one page at a time, saving the phase and cursor on the record after each
page, until the deadline it is given; it is resumable and idempotent (a row is moved conditionally
on still carrying the database id, so a page read twice moves nothing twice). The delete request
queues `move_message` on the compliance trigger's queue; the trigger runs the move until its own
deadline and queues the next message when the move is not done. A run that fails leaves the
message to the queue's redelivery and, after its receive limit, to the queue's dead-letter queue,
so a failing move never loops; the record then stays `moving` and the move resumes from its last
saved page when the message is sent again.

While the record is `moving`, the deleted generation's rows may still carry the live id. Every
compliance read of the live id then answers as for a database with no compliance data, every
compliance action on it answers `409` (`MOVING_MESSAGE`) and no evaluation of its assets runs
(`move_in_progress`), so a database re-created with the id during the move never shows or changes
the deleted one's data. Cross-database listings present such a row under `<databaseId>#deleted`
(`Presenter`), where the move is taking it.

A second delete of the same id merges into `<databaseId>#deleted`, as assets do: the record's
generation goes up and the move runs again over the rows the later database wrote. Evaluations,
cascades and audit entries have their own keys and never collide; an asset-state record of an asset
id both databases evaluated is the later one's.

Reads of `<databaseId>#deleted` are decided on that id (Tier 2 as for any database id). The id is
known while the database table holds its row and, once a re-create removed that row, while the
retirement record exists (`knownDatabase.database_known`).
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from common.compliance.auditRecord import AUDIT_LIST_PARTITION, build_audit_item

DELETED_SUFFIX = "#deleted"
RECORD_PREFIX = "databaseRetirement#"
RECORD_TYPE = "databaseRetirement"
STATE_MOVING = "moving"
STATE_DONE = "done"

# The order rows are moved in. Evaluations first: a pipeline result that arrives during the move
# finalizes its evaluation under the id the evaluation row carries, so once the evaluations are
# moved, late results land under `<databaseId>#deleted`, and the asset-state and audit rows a result
# wrote under the live id before that are still ahead of the move.
PHASES = ("evaluations", "cascades", "assetState", "schemas", "audit")

# The message that runs the move: the database service queues it on the compliance trigger's queue
# once the database row has moved, and the trigger queues the next one when a run ends before the
# move does. Read from a raw queue body only (never an SNS envelope), and acted on only while the
# record is `moving`, so a message can continue a move a delete started and never start one.
MOVE_ACTION = "moveDeletedDatabaseCompliance"

# Time a trigger run leaves itself before the Lambda timeout to save its cursor and queue the rest.
TRIGGER_SAFETY_MARGIN_SECONDS = 45
PAGE_SIZE = 100

CASCADE_STATE_PENDING_APPROVAL = "pending_approval"
CASCADE_STATE_ABORTED = "aborted"
CASCADE_DATABASE_DELETED_REASON = "The database was deleted"
CASCADE_PENDING_MARKER_PREFIX = "pendingCascadeMarker:"
AUDIT_CASCADE_DATABASE_DELETED = "cascade_database_deleted"
AUDIT_DATABASE_DELETED = "database_deleted"
AUDIT_DATABASE_COMPLIANCE_MOVED = "database_compliance_moved"
SYSTEM_ACTOR = "SYSTEM_USER"

PIPELINE_WAIT_INDEX = "PipelineRuleWaitIndex"
WAITING_RULE_CLOSED_MESSAGE = "Not started: the database was deleted"
EVALUATION_CLOSED_MESSAGE = "Not completed: the database was deleted"

MOVING_STATUS = 409
MOVING_MESSAGE = ("The compliance data of a deleted database with this id is still being moved. "
                  "Try again in a few minutes.")


def deleted_id(database_id: str) -> str:
    """The id a deleted database's row and compliance data are stored under."""
    return f"{database_id}{DELETED_SUFFIX}"


def is_deleted_scope(database_id: Optional[str]) -> bool:
    """Whether a database id names a deleted database's data (`<databaseId>#deleted`)."""
    return bool(database_id) and database_id.endswith(DELETED_SUFFIX)


def base_id(database_id: str) -> str:
    """The database id of `<databaseId>#deleted` (an id without the suffix is returned as is)."""
    return database_id[:-len(DELETED_SUFFIX)] if is_deleted_scope(database_id) else database_id


def record_key(database_id: str) -> Dict[str, str]:
    return {"evaluationId": f"{RECORD_PREFIX}{database_id}"}


def now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def get_record(evaluation_table, database_id: str) -> Optional[Dict[str, Any]]:
    """The retirement record of a database id, read strongly consistent; None when it has none."""
    if not database_id or is_deleted_scope(database_id):
        return None
    return evaluation_table.get_item(Key=record_key(database_id), ConsistentRead=True).get("Item")


def move_in_progress(evaluation_table, database_id: Optional[str]) -> bool:
    """Whether the compliance data of a deleted database with this id is still being moved, so
    that reads of the live id must show none of it and actions on it must wait."""
    if not database_id or database_id in ("GLOBAL", "*") or is_deleted_scope(database_id):
        return False
    record = get_record(evaluation_table, database_id)
    return bool(record) and record.get("state") == STATE_MOVING


def has_history(evaluation_table, database_id: str) -> bool:
    """Whether a deleted database's compliance data was moved (or is moving) under
    `<databaseId>#deleted`, for an id with the suffix."""
    return bool(get_record(evaluation_table, base_id(database_id)))


def moving_response(event=None):
    """The `409` of a compliance action on a database id whose deleted generation is moving."""
    from models.common import general_error
    return general_error(status_code=MOVING_STATUS, body={"message": MOVING_MESSAGE}, event=event)


class Presenter:
    """Presents rows of a cross-database listing under the id their data is stored under at the
    end of the move: a row of a database id whose deleted generation is moving carries the live
    id but belongs to `<databaseId>#deleted`. One record read per database id, per listing."""

    def __init__(self, evaluation_table):
        self._table = evaluation_table
        self._moving: Dict[str, bool] = {}

    def moving(self, database_id: Optional[str]) -> bool:
        if not database_id:
            return False
        if database_id not in self._moving:
            self._moving[database_id] = move_in_progress(self._table, database_id)
        return self._moving[database_id]

    def present(self, item: Dict[str, Any], field: str = "databaseId") -> Dict[str, Any]:
        database_id = item.get(field)
        if not self.moving(database_id):
            return item
        moved = dict(item)
        moved[field] = deleted_id(database_id)
        asset_id = item.get("assetId")
        if "databaseId:assetId" in item and asset_id is not None:
            moved["databaseId:assetId"] = f"{moved[field]}:{asset_id}"
        return moved


class Tables:
    """The tables the move touches. Resolved from SSM on first use (the database service and the
    compliance Lambdas name them through `ResourceKeys`); tests pass their own."""

    def __init__(self, dynamodb=None, schema=None, asset_state=None, evaluation=None,
                 cascade=None, audit=None):
        self._dynamodb = dynamodb
        self._tables = {"schema": schema, "asset_state": asset_state, "evaluation": evaluation,
                        "cascade": cascade, "audit": audit}

    def _table(self, name):
        if self._tables[name] is None:
            from common.resourceNames import ResourceKeys, get_table_name
            keys = {
                "schema": ResourceKeys.COMPLIANCE_SCHEMA_STORAGE_TABLE,
                "asset_state": ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE,
                "evaluation": ResourceKeys.COMPLIANCE_EVALUATION_STORAGE_TABLE,
                "cascade": ResourceKeys.COMPLIANCE_CASCADE_STORAGE_TABLE,
                "audit": ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE,
            }
            if self._dynamodb is None:
                import boto3
                from botocore.config import Config
                self._dynamodb = boto3.resource(
                    "dynamodb", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))
            self._tables[name] = self._dynamodb.Table(get_table_name(keys[name]))
        return self._tables[name]

    schema = property(lambda self: self._table("schema"))
    asset_state = property(lambda self: self._table("asset_state"))
    evaluation = property(lambda self: self._table("evaluation"))
    cascade = property(lambda self: self._table("cascade"))
    audit = property(lambda self: self._table("audit"))


def _conditional_failure(error: Exception) -> bool:
    return (isinstance(error, ClientError)
            and error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException")


def write_audit(tables: Tables, database_id: str, asset_id: str, event_type: str,
                details: Dict[str, Any], cascade_id: Optional[str] = None) -> None:
    """One audit entry of the move, built by `build_audit_item`."""
    tables.audit.put_item(Item=build_audit_item(
        database_id, asset_id, event_type, SYSTEM_ACTOR, details, cascade_id=cascade_id))


# --- Begin: inside the delete request ---


def begin(tables: Tables, database_id: str, actor: str,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """Start the move of a database's compliance data, before its row moves: write the record
    (`moving`), close its cascades awaiting approval and end its waiting pipeline rules. Raises on
    a failure, so the delete fails rather than leave the deleted database's data under the live id.
    Returns the record."""
    previous = get_record(tables.evaluation, database_id) or {}
    record = {
        **record_key(database_id),
        "recordType": RECORD_TYPE,
        "databaseId": database_id,
        "deletedId": deleted_id(database_id),
        "state": STATE_MOVING,
        "deletedAt": now_iso(now),
        "deletedBy": actor,
        "generation": int(previous.get("generation", 0)) + 1,
        "phase": PHASES[0],
        "moved": {phase: 0 for phase in PHASES},
        "updatedAt": now_iso(now),
    }
    tables.evaluation.put_item(Item=record)
    try:
        closed_cascades = close_pending_cascades(tables, database_id, now)
        closed_rules = close_waiting_rules(tables, database_id, now)
    except Exception:
        # The delete fails; the database stays live, so its record must not hide its data.
        if previous:
            tables.evaluation.put_item(Item=previous)
        else:
            tables.evaluation.delete_item(Key=record_key(database_id))
        raise
    tables.evaluation.update_item(
        Key=record_key(database_id),
        UpdateExpression="SET closedCascades = :c, closedWaitingRules = :w",
        ExpressionAttributeValues={":c": closed_cascades, ":w": closed_rules})
    write_audit(tables, deleted_id(database_id), "*", event_type=AUDIT_DATABASE_DELETED,
                details={"actor": actor, "generation": record["generation"],
                         "pendingCascadesClosed": closed_cascades,
                         "waitingPipelineRulesClosed": closed_rules})
    record.update(closedCascades=closed_cascades, closedWaitingRules=closed_rules)
    return record


def close_pending_cascades(tables: Tables, database_id: str,
                           now: Optional[datetime] = None) -> int:
    """Abort every cascade of the database still awaiting approval, with an audit entry each, and
    release its parent's pending-cascade marker. Each abort is conditional on the cascade still
    awaiting approval, so an approval that won the race keeps its run."""
    closed = 0
    kwargs = {
        "IndexName": "StateIndex",
        "KeyConditionExpression": Key("state").eq(CASCADE_STATE_PENDING_APPROVAL),
        "FilterExpression": Attr("triggeredByDatabaseId").eq(database_id),
    }
    while True:
        response = tables.cascade.query(**kwargs)
        for cascade in response.get("Items", []):
            try:
                tables.cascade.update_item(
                    Key={"cascadeId": cascade["cascadeId"]},
                    UpdateExpression="SET #s = :aborted, abortReason = :reason, completedAt = :now",
                    ConditionExpression="#s = :pending",
                    ExpressionAttributeNames={"#s": "state"},
                    ExpressionAttributeValues={
                        ":aborted": CASCADE_STATE_ABORTED, ":pending": CASCADE_STATE_PENDING_APPROVAL,
                        ":reason": CASCADE_DATABASE_DELETED_REASON, ":now": now_iso(now)})
            except Exception as e:
                if not _conditional_failure(e):
                    raise
                continue
            closed += 1
            write_audit(tables, database_id, cascade.get("triggeredByAssetId", ""),
                        event_type=AUDIT_CASCADE_DATABASE_DELETED,
                        details={"reason": CASCADE_DATABASE_DELETED_REASON},
                        cascade_id=cascade["cascadeId"])
            asset_id = cascade.get("triggeredByAssetId")
            if asset_id:
                try:
                    tables.cascade.delete_item(
                        Key={"cascadeId": f"{CASCADE_PENDING_MARKER_PREFIX}{database_id}:{asset_id}"},
                        ConditionExpression="markerFor = :c",
                        ExpressionAttributeValues={":c": cascade["cascadeId"]})
                except Exception as e:
                    if not _conditional_failure(e):
                        raise
        if "LastEvaluatedKey" not in response:
            return closed
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def close_waiting_rules(tables: Tables, database_id: str, now: Optional[datetime] = None) -> int:
    """End every pipeline rule of the database's assets that waits for a running execution of its
    workflow: its tracking row becomes `completed` with an error result and leaves the wait index,
    and its evaluation, still pending its pipeline rules, becomes `error`. The wait index is sparse
    (only waiting rows carry its key), so it is read whole."""
    closed = 0
    prefix = f"{database_id}:"
    kwargs = {"IndexName": PIPELINE_WAIT_INDEX,
              "FilterExpression": Attr("waitingAssetKey").begins_with(prefix)}
    while True:
        response = tables.evaluation.scan(**kwargs)
        for row in response.get("Items", []):
            result = {"ruleName": row.get("pipelineRuleName", ""), "ruleType": "pipeline",
                      "passed": False, "status": "error", "message": WAITING_RULE_CLOSED_MESSAGE}
            try:
                tables.evaluation.update_item(
                    Key={"evaluationId": row["evaluationId"]},
                    UpdateExpression=("SET #s = :completed, ruleResults = :results, "
                                      "completedAt = :now REMOVE waitingWorkflowKey, waitingSince"),
                    ConditionExpression="#s IN (:waiting, :launching)",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":completed": "completed", ":waiting": "waiting", ":launching": "launching",
                        ":results": json.dumps([result]), ":now": now_iso(now)})
            except Exception as e:
                if not _conditional_failure(e):
                    raise
                continue
            closed += 1
            parent_id = row.get("parentEvaluationId")
            if parent_id:
                try:
                    tables.evaluation.update_item(
                        Key={"evaluationId": parent_id},
                        UpdateExpression="SET #s = :error, errorMessage = :message, completedAt = :now",
                        ConditionExpression="#s = :pending",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={
                            ":error": "error", ":pending": "pending_pipeline",
                            ":message": EVALUATION_CLOSED_MESSAGE, ":now": now_iso(now)})
                except Exception as e:
                    if not _conditional_failure(e):
                        raise
        if "LastEvaluatedKey" not in response:
            return closed
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


# --- Run: the move itself, resumable ---


def _move_evaluation(tables: Tables, database_id: str, item: Dict[str, Any]) -> bool:
    return _relabel(tables.evaluation, {"evaluationId": item["evaluationId"]}, "databaseId",
                    database_id, item.get("assetId"))


def _move_audit(tables: Tables, database_id: str, item: Dict[str, Any]) -> bool:
    return _relabel(tables.audit, {"entryId": item["entryId"]}, "databaseId", database_id,
                    item.get("assetId"))


def _move_schema(tables: Tables, database_id: str, item: Dict[str, Any]) -> bool:
    return _relabel(tables.schema, {"schemaName": item["schemaName"],
                                    "internalVersion": item["internalVersion"]},
                    "databaseId", database_id, None)


def _move_cascade(tables: Tables, database_id: str, item: Dict[str, Any]) -> bool:
    cascade_id = item["cascadeId"]
    if str(cascade_id).startswith(CASCADE_PENDING_MARKER_PREFIX):
        # A claim, not history: the cascade it named was closed by `begin`.
        tables.cascade.delete_item(Key={"cascadeId": cascade_id})
        return True
    return _relabel(tables.cascade, {"cascadeId": cascade_id}, "triggeredByDatabaseId",
                    database_id, None)


def _relabel(table, key: Dict[str, Any], field: str, database_id: str,
             asset_id: Optional[str]) -> bool:
    """Re-key one row's database id to `<databaseId>#deleted` (and its `databaseId:assetId` index
    key with it), conditional on the row still carrying the database id."""
    names = {"#f": field}
    values = {":d": deleted_id(database_id), ":old": database_id}
    expression = "SET #f = :d"
    if asset_id is not None:
        names["#k"] = "databaseId:assetId"
        values[":dk"] = f"{deleted_id(database_id)}:{asset_id}"
        expression += ", #k = :dk"
    try:
        table.update_item(Key=key, UpdateExpression=expression, ConditionExpression="#f = :old",
                          ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        return True
    except Exception as e:
        if _conditional_failure(e):
            return False
        raise


def _move_asset_state(tables: Tables, database_id: str, item: Dict[str, Any]) -> bool:
    """The asset-state record's key is the database id, so it is copied under
    `<databaseId>#deleted` and the original deleted (each step repeatable)."""
    moved = dict(item)
    moved["databaseId"] = deleted_id(database_id)
    tables.asset_state.put_item(Item=moved)
    tables.asset_state.delete_item(Key={"databaseId": database_id, "assetId": item["assetId"]})
    return True


def _phase_read(tables: Tables, phase: str, database_id: str) -> Dict[str, Any]:
    """(table, read kwargs, read method name) of one phase. Each read finds exactly the rows that
    still carry the database id, so a row moved is never read again."""
    if phase == "evaluations":
        return {"table": tables.evaluation, "method": "scan",
                "kwargs": {"FilterExpression": Attr("databaseId").eq(database_id)},
                "move": _move_evaluation}
    if phase == "cascades":
        return {"table": tables.cascade, "method": "scan",
                "kwargs": {"FilterExpression": Attr("triggeredByDatabaseId").eq(database_id)},
                "move": _move_cascade}
    if phase == "assetState":
        return {"table": tables.asset_state, "method": "query",
                "kwargs": {"KeyConditionExpression": Key("databaseId").eq(database_id),
                           "ConsistentRead": True},
                "move": _move_asset_state}
    if phase == "schemas":
        return {"table": tables.schema, "method": "query",
                "kwargs": {"IndexName": "DatabaseIdIndex",
                           "KeyConditionExpression": Key("databaseId").eq(database_id)},
                "move": _move_schema}
    return {"table": tables.audit, "method": "query",
            "kwargs": {"IndexName": "AuditByDateGSI",
                       "KeyConditionExpression": Key("allListPartition").eq(AUDIT_LIST_PARTITION),
                       "FilterExpression": Attr("databaseId").eq(database_id)},
            "move": _move_audit}


def run(tables: Tables, database_id: str, deadline: float,
        clock: Callable[[], float] = time.monotonic, page_size: int = PAGE_SIZE) -> bool:
    """Move the database's compliance data until done or `deadline` (a `clock` value). Saves the
    phase, the cursor and the counts on the record after every page, so a run that stops anywhere
    (the deadline, a timeout, a crash) is continued by the next from the last saved page. Returns
    whether the move is done."""
    record = get_record(tables.evaluation, database_id)
    if not record or record.get("state") != STATE_MOVING:
        return True
    generation = record.get("generation")
    phase = record.get("phase") or PHASES[0]
    cursor = json.loads(record["cursor"]) if record.get("cursor") else None
    moved = {name: int(count) for name, count in (record.get("moved") or {}).items()}
    pages = 0
    while phase is not None:
        # Every run reads at least one page, so a continuation always makes progress.
        if pages and clock() >= deadline:
            return False
        pages += 1
        spec = _phase_read(tables, phase, database_id)
        kwargs = dict(spec["kwargs"], Limit=page_size)
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        response = getattr(spec["table"], spec["method"])(**kwargs)
        for item in response.get("Items", []):
            if item.get("recordType") == RECORD_TYPE:
                continue
            if spec["move"](tables, database_id, item):
                moved[phase] = moved.get(phase, 0) + 1
        if "LastEvaluatedKey" in response:
            cursor = response["LastEvaluatedKey"]
        else:
            cursor = None
            index = PHASES.index(phase) + 1
            phase = PHASES[index] if index < len(PHASES) else None
        if not _save_progress(tables, database_id, generation, phase, cursor, moved):
            # A later delete of the id restarted the move; its own run continues it.
            return True
    write_audit(tables, deleted_id(database_id), "*", event_type=AUDIT_DATABASE_COMPLIANCE_MOVED,
                details={"generation": int(generation or 0), "moved": moved})
    return True


def _save_progress(tables: Tables, database_id: str, generation, phase: Optional[str],
                   cursor: Optional[Dict[str, Any]], moved: Dict[str, int]) -> bool:
    """Save the move's progress, conditional on the record still being this generation's move (a
    second delete restarts it). The last save marks it `done`. Returns False when the record is
    another generation's."""
    values = {":gen": generation, ":moving": STATE_MOVING, ":moved": moved, ":now": now_iso()}
    if phase is None:
        expression = "SET #s = :done, moved = :moved, updatedAt = :now, doneAt = :now REMOVE phase, #c"
        values[":done"] = STATE_DONE
    else:
        expression = "SET phase = :phase, moved = :moved, updatedAt = :now"
        values[":phase"] = phase
        if cursor:
            expression += ", #c = :cursor"
            values[":cursor"] = json.dumps(cursor, default=_json_default)
        else:
            expression += " REMOVE #c"
    try:
        tables.evaluation.update_item(
            Key=record_key(database_id), UpdateExpression=expression,
            ConditionExpression="generation = :gen AND #s = :moving",
            ExpressionAttributeNames={"#s": "state", "#c": "cursor"},
            ExpressionAttributeValues=values)
    except Exception as e:
        if _conditional_failure(e):
            return False
        raise
    return True


def _json_default(value):
    """DynamoDB numbers in a paging key (a schema version) as JSON."""
    from decimal import Decimal
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    raise TypeError(f"Unserializable paging key value {value!r}")


def move_message(database_id: str) -> Dict[str, Any]:
    """The queue message that runs (or continues) the move (`MOVE_ACTION`)."""
    return {"vamsInternalAction": MOVE_ACTION, "databaseId": database_id}


def move_message_database(record: Any) -> Optional[str]:
    """The database id of a queue record carrying `move_message` as its raw body, else None. An SNS
    envelope is never one (the indexer topics cannot carry the action)."""
    body = record.get("body") if isinstance(record, dict) else None
    try:
        payload = json.loads(body) if isinstance(body, str) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("vamsInternalAction") != MOVE_ACTION:
        return None
    database_id = payload.get("databaseId")
    return database_id if isinstance(database_id, str) else None


def iter_moving(presenter: Presenter, items: Iterable[Dict[str, Any]],
                field: str = "databaseId") -> Iterable[Dict[str, Any]]:
    """`items` as a cross-database listing presents them (`Presenter.present`)."""
    for item in items:
        yield presenter.present(item, field)
