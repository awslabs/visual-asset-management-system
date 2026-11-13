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
queues `move_message` on the compliance job queue; the compliance job Lambda (`complianceJobs`)
runs the move until its own deadline and queues the next message when the move is not done. A run
that fails leaves the message to the queue's redelivery and, after its receive limit, to the
queue's dead-letter queue, so a failing move never loops; the record then stays `moving` and the
move resumes from its last saved page when the message is sent again.

The job Lambda runs with Lambda's recursive-loop detection off (a move of large tables continues
itself for more runs than the detection allows), so the move counts its own runs: every run counts
itself on the record (`runs`, `begin_run`) against a limit derived from the size of the tables the
move reads (`runLimit`, `run_limit`). A run past the limit moves nothing: the job Lambda puts the
move's message in the job queue's dead-letter queue and the record notes why (`stopReason`,
`stoppedAt`, `stop_at_run_limit`) with its count started again, so the move is in the same state
as one that kept failing, and resumes from its last saved page when the message is sent again.

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
from typing import Any, Callable, Dict, Iterable, List, Optional

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

# The message that runs the move: the database service queues it on the compliance job queue
# once the database row has moved, and the job Lambda queues the next one when a run ends before the
# move does. Read from a raw queue body only (never an SNS envelope), and acted on only while the
# record is `moving`, so a message can continue a move a delete started and never start one.
MOVE_ACTION = "moveDeletedDatabaseCompliance"

# Time a job run leaves itself before the Lambda timeout to save its cursor and queue the rest.
RUN_SAFETY_MARGIN_SECONDS = 45
PAGE_SIZE = 100

# The run limit (module docstring): runs counted against the pages the move's reads take over the
# tables it reads, twice over, plus an allowance for redeliveries. The tables' item counts are
# DynamoDB's (DescribeTable, refreshed about every six hours), which the factor and the allowance
# also cover.
RUN_LIMIT_FACTOR = 2
RUN_LIMIT_ALLOWANCE = 20
STOPPED_RUN_LIMIT = ("The move ran more steps than the size of the compliance tables needs, so it "
                     "was stopped; send its message again to resume it")
# What `begin_run` decided.
RUN_GO = "run"
RUN_NOTHING = "nothing"
RUN_STOP = "stop"

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

# A database delete while DynamoDB still builds PipelineRuleWaitIndex (an update that adds it
# completes in AWS CloudFormation before the index is active) answers this, retryable.
WAIT_INDEX_NOT_READY_STATUS = 503
WAIT_INDEX_NOT_READY_MESSAGE = ("The database cannot be deleted while compliance finishes building "
                                "an index after an update. Try again in a few minutes.")


class WaitIndexNotReady(Exception):
    """PipelineRuleWaitIndex cannot be read yet: DynamoDB is still building it."""


def wait_index_not_ready(error: Exception) -> bool:
    """Whether a DynamoDB error is the refusal of a read of a global secondary index that is still
    being built ("Cannot read from backfilling global secondary index"), or of one the table does
    not have yet. Any other error is a real failure."""
    if not isinstance(error, ClientError):
        return False
    details = error.response.get("Error", {})
    message = str(details.get("Message", "")).lower()
    return details.get("Code") == "ValidationException" and (
        "backfilling" in message or "does not have the specified index" in message)


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


def last_deleted_at(evaluation_table, database_id: Optional[str]) -> Optional[str]:
    """When a database with this id was last deleted (its retirement record's `deletedAt`), or
    None when it never was. Read strongly consistent, projected on `deletedAt`. An asset archived
    before that time is the deleted database's, not the live id's (`assetAccess`)."""
    if not database_id or database_id in ("GLOBAL", "*") or is_deleted_scope(database_id):
        return None
    item = evaluation_table.get_item(Key=record_key(database_id), ConsistentRead=True,
                                     ProjectionExpression="deletedAt").get("Item")
    value = (item or {}).get("deletedAt") if isinstance(item, dict) else None
    return value if isinstance(value, str) and value else None


def retirement_states(evaluation_table, database_ids: List[str], batch: int = 100,
                      attempts: int = 4) -> Dict[str, Dict[str, Any]]:
    """The retirement state of several live database ids at once, `{id: {"moving": bool,
    "deletedAt": str | None}}`: their records read with one consistent BatchGetItem per `batch` ids
    (keys `databaseRetirement#<id>` only, projected on the key, `state` and `deletedAt`). An id
    without a record is not moving and was never deleted. Keys DynamoDB leaves unprocessed are
    asked again up to `attempts` times; an id whose record could not be read is left out of the
    answer, so its caller reads it on its own."""
    client = getattr(getattr(evaluation_table, "meta", None), "client", None)
    table_name = getattr(evaluation_table, "name", None)
    if client is None or not isinstance(table_name, str):
        return {}
    answer: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(database_ids), batch):
        chunk = database_ids[start:start + batch]
        # A table resource's client converts plain values to and from DynamoDB's typed form.
        request = {table_name: {
            "Keys": [record_key(database_id) for database_id in chunk],
            "ConsistentRead": True,
            "ProjectionExpression": "#e, #s, #d",
            "ExpressionAttributeNames": {"#e": "evaluationId", "#s": "state", "#d": "deletedAt"},
        }}
        records: Dict[str, Dict[str, Any]] = {}
        for _attempt in range(attempts):
            response = client.batch_get_item(RequestItems=request)
            if not isinstance(response, dict):
                return answer
            for item in (response.get("Responses") or {}).get(table_name, []):
                records[str(item.get("evaluationId", ""))] = item
            unprocessed = response.get("UnprocessedKeys") or {}
            if not (unprocessed.get(table_name) or {}).get("Keys"):
                request = None
                break
            request = unprocessed
        left = ({str(key.get("evaluationId")) for key in request[table_name]["Keys"]}
                if request else set())
        for database_id in chunk:
            key = f"{RECORD_PREFIX}{database_id}"
            if key not in left:
                record = records.get(key) or {}
                deleted_at = record.get("deletedAt")
                answer[database_id] = {
                    "moving": record.get("state") == STATE_MOVING,
                    "deletedAt": deleted_at if isinstance(deleted_at, str) and deleted_at else None,
                }
    return answer


def moves_in_progress(evaluation_table, database_ids: List[str], batch: int = 100,
                      attempts: int = 4) -> Dict[str, bool]:
    """`move_in_progress` of several live database ids at once (`retirement_states`)."""
    return {database_id: state["moving"] for database_id, state in retirement_states(
        evaluation_table, database_ids, batch, attempts).items()}


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
    id but belongs to `<databaseId>#deleted`. A listing reads the records of a page's database ids
    together (`prime`, one consistent BatchGetItem per PRIME_BATCH ids); an id not primed is read
    on its own when first presented. Each id is read at most once per listing."""

    PRIME_BATCH = 100
    PRIME_ATTEMPTS = 4

    def __init__(self, evaluation_table):
        self._table = evaluation_table
        self._moving: Dict[str, bool] = {}
        self._deleted_at: Dict[str, Optional[str]] = {}

    def moving(self, database_id: Optional[str]) -> bool:
        if not database_id:
            return False
        if database_id not in self._moving:
            self._moving[database_id] = move_in_progress(self._table, database_id)
        return self._moving[database_id]

    def deleted_at(self, database_id: Optional[str]) -> Optional[str]:
        """When a database with this id was last deleted (`last_deleted_at`), from the primed
        records or, for an id not primed, its own read; None when it never was. The asset
        decisions use it to tell an asset archived in an earlier, deleted database with the same id
        from one archived in the live one (`assetAccess.AssetAccess`)."""
        if not database_id:
            return None
        if database_id not in self._deleted_at:
            self._deleted_at[database_id] = last_deleted_at(self._table, database_id)
        return self._deleted_at[database_id]

    def prime(self, database_ids: Iterable[Optional[str]]) -> None:
        """Read the retirement records of the given database ids not read yet, with one consistent
        BatchGetItem per PRIME_BATCH ids (keys `databaseRetirement#<id>` only, `state` and
        `deletedAt`). An id that names no live database (`GLOBAL`, `*`, `<id>#deleted`) has no
        record and is not read. Keys DynamoDB leaves unprocessed are asked again a few times, and
        any still left are read on their own when presented."""
        wanted = []
        for database_id in database_ids:
            if not database_id or database_id in self._moving or database_id in wanted:
                continue
            if database_id in ("GLOBAL", "*") or is_deleted_scope(database_id):
                self._moving[database_id] = False
                self._deleted_at[database_id] = None
                continue
            wanted.append(database_id)
        if wanted:
            for database_id, state in retirement_states(self._table, wanted, self.PRIME_BATCH,
                                                         self.PRIME_ATTEMPTS).items():
                self._moving[database_id] = state["moving"]
                self._deleted_at.setdefault(database_id, state["deletedAt"])

    def prepare(self, field: str = "databaseId",
                then: Optional[Callable[[List[Dict[str, Any]]], None]] = None
                ) -> Callable[[List[Dict[str, Any]]], None]:
        """A `listing.query_page` `prepare` hook: prime the records of a read's database ids
        (`field` of each item), then run `then` with the read's items."""
        def prepare_read(items: List[Dict[str, Any]]) -> None:
            self.prime(item.get(field) for item in items)
            if then is not None:
                then(items)
        return prepare_read

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
        # The waiting rules first: their index read is what fails while the index is still being
        # built (WaitIndexNotReady), and it fails before anything is closed.
        closed_rules = close_waiting_rules(tables, database_id, now)
        closed_cascades = close_pending_cascades(tables, database_id, now)
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
    (only waiting rows carry its key), so it is read whole. Raises `WaitIndexNotReady` while
    DynamoDB still builds the index."""
    closed = 0
    prefix = f"{database_id}:"
    kwargs = {"IndexName": PIPELINE_WAIT_INDEX,
              "FilterExpression": Attr("waitingAssetKey").begins_with(prefix)}
    while True:
        try:
            response = tables.evaluation.scan(**kwargs)
        except Exception as e:
            if wait_index_not_ready(e):
                raise WaitIndexNotReady(str(e)) from e
            raise
        for row in response.get("Items", []):
            # An errored rule's enforcement does not apply, so `inform` stands for the rule's own.
            result = {"ruleName": row.get("pipelineRuleName", ""), "ruleType": "pipeline",
                      "enforcement": "inform", "passed": False, "status": "error",
                      "message": WAITING_RULE_CLOSED_MESSAGE}
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
                close_parent_evaluation(tables, parent_id, result, now)
        if "LastEvaluatedKey" not in response:
            return closed
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def close_parent_evaluation(tables: Tables, parent_id: str, closed_result: Dict[str, Any],
                            now: Optional[datetime] = None) -> bool:
    """Close the evaluation of a waiting rule the delete ended, with the end state a superseded
    evaluation gets (`evaluationEngine.closed_evaluation_fields`, reason `databaseDeleted`): no
    verdict, every pipeline entry `completed`, the closed rule's result among the rule results (any
    other rule still outstanding with EVALUATION_CLOSED_MESSAGE), under a `status =
    pending_pipeline` condition. Returns whether it was closed here."""
    from common.compliance import evaluationEngine as engine
    parent = tables.evaluation.get_item(
        Key={"evaluationId": parent_id}, ConsistentRead=True).get("Item")
    if not parent or parent.get("status") != "pending_pipeline":
        return False
    results = engine.rule_results_from_json(parent.get("ruleResults", "[]"))
    reported = {result.ruleName for result in results}
    added = [closed_result] + [
        {"ruleName": entry.get("ruleName", ""), "ruleType": "pipeline", "enforcement": "inform",
         "passed": False, "status": "error", "message": EVALUATION_CLOSED_MESSAGE}
        for entry in parent.get("pipelineExecutions") or []
        if entry.get("status") not in ("completed", "not_started")
        and entry.get("ruleName") not in reported | {closed_result["ruleName"]}]
    results = ([result for result in results if result.ruleName != closed_result["ruleName"]]
               + engine.rule_results_from_json(added))
    fields = engine.closed_evaluation_fields(
        parent.get("pipelineExecutions"), results, engine.CLOSED_DATABASE_DELETED, now_iso(now))
    names = {f"#c{index}": name for index, name in enumerate(fields)}
    values = {f":c{index}": value for index, value in enumerate(fields.values())}
    removed = {f"#r{index}": name
               for index, name in enumerate(engine.CLOSED_EVALUATION_REMOVED_FIELDS)}
    try:
        tables.evaluation.update_item(
            Key={"evaluationId": parent_id},
            UpdateExpression=("SET " + ", ".join(f"{name} = :c{index}"
                                                 for index, name in enumerate(names))
                              + " REMOVE " + ", ".join(removed)),
            ConditionExpression="#cs = :pending",
            ExpressionAttributeNames={**names, **removed, "#cs": "status"},
            ExpressionAttributeValues={**values, ":pending": "pending_pipeline"})
    except Exception as e:
        if not _conditional_failure(e):
            raise
        return False
    return True


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


def _item_count(table) -> int:
    """The table's item count as DynamoDB keeps it (DescribeTable, refreshed about every six
    hours); 0 when it cannot be read."""
    try:
        return int(table.meta.client.describe_table(TableName=table.name)["Table"]["ItemCount"])
    except Exception:
        return 0


def run_limit(tables: Tables) -> int:
    """The most runs a move may take (module docstring). Every run reads at least one page, and
    each phase reads at most its table's items a page at a time (the evaluation and cascade phases
    scan their whole table, the audit phase reads the whole audit listing), so the pages of the five
    tables bound the runs. Twice that, plus a fixed allowance for redeliveries."""
    pages = sum(max(1, -(-_item_count(table) // PAGE_SIZE))
                for table in (tables.evaluation, tables.cascade, tables.asset_state, tables.schema,
                              tables.audit))
    return RUN_LIMIT_FACTOR * pages + RUN_LIMIT_ALLOWANCE


def begin_run(tables: Tables, database_id: str) -> tuple:
    """Count one run of the move against its run limit, before it moves anything: `(RUN_GO,
    record)` to run, `(RUN_NOTHING, record)` when no move of the id is under way (or a later
    delete restarted it), `(RUN_STOP, record)` when this run is past the limit. The limit is
    derived once per move, on its first run, and kept on the record."""
    record = get_record(tables.evaluation, database_id)
    if not record or record.get("state") != STATE_MOVING:
        return RUN_NOTHING, record
    limit = int(record.get("runLimit") or 0) or run_limit(tables)
    try:
        response = tables.evaluation.update_item(
            Key=record_key(database_id),
            UpdateExpression="SET runLimit = if_not_exists(runLimit, :limit) ADD runs :one",
            ConditionExpression="generation = :gen AND #s = :moving",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":limit": limit, ":one": 1, ":moving": STATE_MOVING,
                                       ":gen": record.get("generation")},
            ReturnValues="ALL_NEW")
    except Exception as e:
        if _conditional_failure(e):
            return RUN_NOTHING, record
        raise
    counted = (response or {}).get("Attributes") or dict(
        record, runs=int(record.get("runs") or 0) + 1, runLimit=limit)
    if int(counted.get("runs") or 0) > int(counted.get("runLimit") or limit):
        return RUN_STOP, counted
    return RUN_GO, counted


def stop_at_run_limit(tables: Tables, database_id: str, generation, restart_count: bool,
                      now: Optional[datetime] = None) -> bool:
    """Note on the record that the move stopped at its run limit (`stopReason`, `stoppedAt`); with
    `restart_count` (its message is kept in the dead-letter queue) its run count starts again, so
    the message sent again resumes it. The record stays `moving`. Conditional on the move still
    being this generation's. Returns whether it landed."""
    expression = "SET stopReason = :reason, stoppedAt = :now"
    values = {":reason": STOPPED_RUN_LIMIT, ":now": now_iso(now), ":gen": generation,
              ":moving": STATE_MOVING}
    if restart_count:
        expression += ", runs = :zero"
        values[":zero"] = 0
    try:
        tables.evaluation.update_item(
            Key=record_key(database_id), UpdateExpression=expression,
            ConditionExpression="generation = :gen AND #s = :moving",
            ExpressionAttributeNames={"#s": "state"}, ExpressionAttributeValues=values)
    except Exception as e:
        if _conditional_failure(e):
            return False
        raise
    return True


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
