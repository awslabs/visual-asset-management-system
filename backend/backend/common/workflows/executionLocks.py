# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execution locks for the perAsset, perInputFile and perInputFileVersion concurrency restrictions.

A lock row is one item in WorkflowExecutionLocksStorageTable keyed by what the workflow's restriction
serialises on:

    {workflowDatabaseId}:{workflowId}|asset|{databaseId}:{assetId}
    {workflowDatabaseId}:{workflowId}|assetFile|{databaseId}:{assetId}:{inputAssetFileKey}
    {workflowDatabaseId}:{workflowId}|assetFileVersion|{databaseId}:{assetId}:{inputAssetFileKey}#{versionId}

inputAssetFileKey is the normalized full asset-bucket key and versionId the resolved S3 VersionId ("" on
an unversioned bucket and for whole-asset/folder selections) — the same values the WorkflowExecutionInputs
rows store, so a terminal handler rebuilds the identical keys from those rows. A launch takes one key per
distinct scope value across its selected inputs, so a perAsset run over three files of one asset holds one
lock and a perInputFile run over them holds three.

Acquire is a conditional PutItem (`attribute_not_exists(lockKey) OR expiresAt < :now`); release is a
conditional DeleteItem on the holder's workflowExecutionId. expiresAt is the table's TTL attribute and a
safety net only: TTL deletion lags, so the acquire condition tests it directly. The lock is what makes
the restriction hold under concurrency: two launches racing for one scope cannot both put the row. A
launch also takes over a row whose holder wrote no execution record and took the row longer ago than
the launch handler can run: that holder died before its records existed, so nothing would release it.

A terminal release finds the rows to delete two ways and deletes the union: the keys rebuilt from the
input rows under the workflow's current restriction (strongly consistent, so it holds even right after
the launch), and the keys the holder index (WorkflowExecutionLocksByHolderGSI, partition key
workflowExecutionId) lists for the execution. The index covers the rows the input rows cannot rebuild:
the workflow's restriction edited or the workflow deleted while the run was going, and a run launched
before the input rows carried the version, whose two versions of one file share a single input row.
"""

import time
from datetime import datetime, timezone
from typing import List, Optional

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from customLogging.logger import safeLogger

logger = safeLogger()

CONCURRENCY_PER_ASSET = "perAsset"
CONCURRENCY_PER_INPUT_FILE = "perInputFile"
CONCURRENCY_PER_INPUT_FILE_VERSION = "perInputFileVersion"

# Lock scope written on the row and into the key: the restriction decides which of the input's identity
# attributes the key carries, and the scope name keeps the three key shapes from colliding.
LOCK_SCOPE_ASSET = "asset"
LOCK_SCOPE_ASSET_FILE = "assetFile"
LOCK_SCOPE_ASSET_FILE_VERSION = "assetFileVersion"

LOCK_SCOPE_BY_RESTRICTION = {
    CONCURRENCY_PER_ASSET: LOCK_SCOPE_ASSET,
    CONCURRENCY_PER_INPUT_FILE: LOCK_SCOPE_ASSET_FILE,
    CONCURRENCY_PER_INPUT_FILE_VERSION: LOCK_SCOPE_ASSET_FILE_VERSION,
}
LOCKING_RESTRICTIONS = frozenset(LOCK_SCOPE_BY_RESTRICTION)

# The 400 body a conflicting launch answers with. Generic on purpose: the contested key and the holder
# are logged server-side, never echoed. The CLI maps the response by the substrings "already running" /
# "conflicting execution".
LOCK_CONFLICT_MESSAGE = "A conflicting execution of this workflow is already running."
LOCK_CONFLICT_MESSAGE_BY_SCOPE = {
    LOCK_SCOPE_ASSET: "A conflicting execution of this workflow is already running for this asset.",
    LOCK_SCOPE_ASSET_FILE: "A conflicting execution of this workflow is already running for this file.",
    LOCK_SCOPE_ASSET_FILE_VERSION:
        "A conflicting execution of this workflow is already running for this file version.",
}

# Bound on one pipeline's run when it declares no taskTimeout (or a malformed one): the Step Functions
# task-token default of one day. The workflow's pipelines run one after another, so a lock lives for the
# sum of its pipelines' bounds; the floor keeps a workflow of short declared timeouts at one day, and the
# margin covers the launch and terminal handlers' own windows on either side of the run.
PIPELINE_DEFAULT_TIMEOUT_SECONDS = 86400
LOCK_TTL_FLOOR_SECONDS = 86400
LOCK_TTL_MARGIN_SECONDS = 1800

LOCK_KEY_ATTRIBUTE = "lockKey"
HOLDER_ATTRIBUTE = "workflowExecutionId"
# Global secondary index on the holder (KEYS_ONLY): the lock keys one execution holds.
HOLDER_INDEX_NAME = "WorkflowExecutionLocksByHolderGSI"
LOCK_SCOPE_ATTRIBUTE = "lockScope"
ACQUIRED_AT_ATTRIBUTE = "acquiredAt"
EXPIRES_AT_ATTRIBUTE = "expiresAt"

_PUT_CONDITION = f"attribute_not_exists({LOCK_KEY_ATTRIBUTE}) OR {EXPIRES_AT_ATTRIBUTE} < :now"
_DELETE_CONDITION = f"{HOLDER_ATTRIBUTE} = :e"
# The takeover of a stranded row: free as above, or still the exact row the conflict returned.
_TAKEOVER_CONDITION = (f"{_PUT_CONDITION} OR "
                       f"({HOLDER_ATTRIBUTE} = :holder AND {ACQUIRED_AT_ATTRIBUTE} = :acquiredAt)")

# A row whose holder wrote no execution record, taken longer ago than the launch handler can run, belongs
# to a launch that died between taking its locks and writing its records. The margin covers clock
# differences between the hosts that wrote and read acquiredAt.
LOCK_TAKEOVER_MARGIN_SECONDS = 300

# Step Functions statuses of an execution that runs no further. PENDING_REDRIVE is not one: a redriven
# execution resumes.
SFN_ENDED_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"})


class ExecutionLockConflict(Exception):
    """A lock in the requested set is held by another, unexpired execution."""

    def __init__(self, lock_key: str, holder_execution_id: str):
        super().__init__(f"execution lock held: {lock_key}")
        self.lock_key = lock_key
        self.holder_execution_id = holder_execution_id

    @property
    def scope(self) -> str:
        """The lock scope named in the contested key ("" for a key of another shape)."""
        parts = self.lock_key.split("|")
        return parts[1] if len(parts) == 3 and parts[1] in LOCK_SCOPE_BY_RESTRICTION.values() else ""

    @property
    def message(self) -> str:
        return LOCK_CONFLICT_MESSAGE_BY_SCOPE.get(self.scope, LOCK_CONFLICT_MESSAGE)


def sfn_execution_has_ended(sfn_client, execution_arn) -> bool:
    """Whether DescribeExecution shows the execution in a status it runs no further from. A caller whose
    StopExecution failed uses it to decide whether the execution's locks may be released; a failed
    describe answers False, so the locks are kept until a terminal step releases them or they expire."""
    try:
        status = (sfn_client.describe_execution(executionArn=execution_arn) or {}).get("status", "")
    except Exception as error:
        logger.warning(f"Could not describe execution {execution_arn}: {error}")
        return False
    return status in SFN_ENDED_STATUSES


def lock_scope_for_restriction(restriction) -> Optional[str]:
    """The lock scope a stored concurrencyRestriction serialises on; None for `none` and anything unknown,
    which is what makes an unrecognised value fail open to "no lock" rather than to a malformed key."""
    return LOCK_SCOPE_BY_RESTRICTION.get(restriction or "")


def build_lock_key(workflow_database_id, workflow_id, database_id, asset_id,
                   input_asset_file_key, version_id, scope=LOCK_SCOPE_ASSET_FILE_VERSION) -> str:
    """One lock key. The scope decides how much of the input's identity the key carries; the identity
    attributes past that point are ignored, so callers may pass every attribute they hold."""
    prefix = f"{workflow_database_id}:{workflow_id}|{scope}|{database_id}:{asset_id}"
    if scope == LOCK_SCOPE_ASSET:
        return prefix
    if scope == LOCK_SCOPE_ASSET_FILE:
        return f"{prefix}:{input_asset_file_key}"
    if scope == LOCK_SCOPE_ASSET_FILE_VERSION:
        return f"{prefix}:{input_asset_file_key}#{version_id or ''}"
    raise ValueError(f"unknown lock scope: {scope!r}")


def pipeline_timeout_seconds(pipeline_record) -> int:
    """One pipeline's bound: its executionConfig.taskTimeout in seconds, or PIPELINE_DEFAULT_TIMEOUT_SECONDS
    when it declares none, an empty one, a malformed one, or a non-positive one."""
    raw = ((pipeline_record or {}).get("executionConfig") or {}).get("taskTimeout")
    try:
        seconds = int(raw) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        seconds = 0
    return seconds if seconds > 0 else PIPELINE_DEFAULT_TIMEOUT_SECONDS


def lock_ttl_seconds(pipeline_records) -> int:
    """Lifetime of a lock row: the sum of the workflow's per-pipeline bounds (the pipelines run in
    sequence, so the run can last as long as all of them together), lifted to LOCK_TTL_FLOOR_SECONDS when
    shorter, plus the margin."""
    total = sum(pipeline_timeout_seconds(record) for record in pipeline_records or [])
    return max(total, LOCK_TTL_FLOOR_SECONDS) + LOCK_TTL_MARGIN_SECONDS


def lock_keys_for_execution(workflow_record, input_rows) -> List[str]:
    """The lock keys an execution holds, rebuilt from its WorkflowExecutionInputs rows under the
    workflow's stored concurrencyRestriction. Empty for `none` (and any unknown value). Rows that resolve
    to one scope value collapse to one key, in row order."""
    record = workflow_record or {}
    scope = lock_scope_for_restriction((record.get("systemConfig") or {}).get("concurrencyRestriction"))
    if scope is None:
        return []
    keys: List[str] = []
    for row in input_rows or []:
        key = build_lock_key(
            record.get("databaseId", ""), record.get("workflowId", ""),
            row.get("databaseId", ""), row.get("assetId", ""),
            row.get("inputAssetFileKey", ""), row.get("versionId", ""), scope=scope)
        if key not in keys:
            keys.append(key)
    return keys


def _now_epoch(now: Optional[float]) -> int:
    return int(time.time() if now is None else now)


def _iso(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_condition_failure(error) -> bool:
    return ((getattr(error, "response", None) or {}).get("Error") or {}).get(
        "Code") == "ConditionalCheckFailedException"


def _attribute_from_conflict(error, name) -> str:
    """A string attribute of the row carried on a ConditionalCheckFailedException when the put asked for
    ReturnValuesOnConditionCheckFailure=ALL_OLD. The item arrives in wire format through the resource
    client, so both the typed and the plain shape are read."""
    item = (getattr(error, "response", None) or {}).get("Item") or {}
    value = item.get(name, "")
    if isinstance(value, dict):
        value = value.get("S", "")
    return str(value or "")


def _holder_from_conflict(error) -> str:
    """The holder's workflowExecutionId carried on a ConditionalCheckFailedException."""
    return _attribute_from_conflict(error, HOLDER_ATTRIBUTE)


def _epoch_of_iso(value) -> Optional[int]:
    """Epoch seconds of an acquiredAt stamp written by _iso, or None for anything else."""
    try:
        return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _is_stranded(holder, acquired_at, now_epoch, stranded_after_seconds, holder_has_record) -> bool:
    """Whether a conflicting row was left by a launch that died before writing its execution record: the
    caller enabled the takeover, the row is older than `stranded_after_seconds`, and its holder has no
    record. The record is read last, and only for a row old enough."""
    if stranded_after_seconds is None or holder_has_record is None or not holder:
        return False
    acquired_epoch = _epoch_of_iso(acquired_at)
    if acquired_epoch is None or now_epoch - acquired_epoch <= int(stranded_after_seconds):
        return False
    return not holder_has_record(holder)


def _take_over_or_raise(table, item, error, now_epoch, stranded_after_seconds, holder_has_record):
    """Answer an acquire put that failed. A row left by a launch that died before writing its records is
    taken over with a put conditional on that same holder and acquiredAt, so of two launches taking over
    one row only one wins. Otherwise raises: the put's own error when it was not a condition failure, or
    ExecutionLockConflict naming the holder that keeps the row."""
    if not _is_condition_failure(error):
        raise error
    lock_key = item[LOCK_KEY_ATTRIBUTE]
    holder = _holder_from_conflict(error)
    acquired_at = _attribute_from_conflict(error, ACQUIRED_AT_ATTRIBUTE)
    if not _is_stranded(holder, acquired_at, now_epoch, stranded_after_seconds, holder_has_record):
        raise ExecutionLockConflict(lock_key, holder) from None
    try:
        table.put_item(
            Item=item,
            ConditionExpression=_TAKEOVER_CONDITION,
            ExpressionAttributeValues={":now": now_epoch, ":holder": holder, ":acquiredAt": acquired_at},
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
        )
    except ClientError as takeover_error:
        if not _is_condition_failure(takeover_error):
            raise
        raise ExecutionLockConflict(lock_key, _holder_from_conflict(takeover_error)) from None
    logger.warning(f"Took over lock {lock_key} from execution {holder}, which took it at {acquired_at} "
                   f"and wrote no execution record")


def _scope_of_key(lock_key: str) -> str:
    """The scope segment of a key built by build_lock_key ("" for any other shape)."""
    parts = (lock_key or "").split("|")
    return parts[1] if len(parts) == 3 and parts[1] in LOCK_SCOPE_BY_RESTRICTION.values() else ""


def acquire_locks(table, lock_keys, execution_id, ttl_seconds, now=None, *,
                  stranded_after_seconds=None, holder_has_record=None) -> List[str]:
    """Take every lock in `lock_keys` for `execution_id`, or none of them.

    Each key is one conditional PutItem that succeeds when no row exists or the row has expired. On the
    first conflict every lock this call already took is released and ExecutionLockConflict names the
    contested key and its holder; any other DynamoDB error releases the same way and propagates. Returns
    the acquired keys (sorted, deduplicated) so the caller can release them if its launch fails.

    The keys are taken in sorted order, not in the order the caller selected its inputs. Two launches
    whose selections overlap then contend for their shared keys in the same order, so the first to take
    the lowest shared key goes on to take the rest and the other is refused. Taken in selection order,
    two overlapping launches sent in opposite order (a,b,c and c,b,a) can each take one end, collide in
    the middle, and both be refused.

    With `stranded_after_seconds` and `holder_has_record` (holder execution id -> whether its execution
    record exists), a conflicting row older than `stranded_after_seconds` whose holder has no record is
    taken over instead of refusing the launch: its holder died between taking the lock and writing its
    records, so no release will ever run for it."""
    now_epoch = _now_epoch(now)
    acquired: List[str] = []
    for lock_key in sorted(set(lock_keys or [])):
        item = {
            LOCK_KEY_ATTRIBUTE: lock_key,
            HOLDER_ATTRIBUTE: execution_id,
            LOCK_SCOPE_ATTRIBUTE: _scope_of_key(lock_key),
            ACQUIRED_AT_ATTRIBUTE: _iso(now_epoch),
            EXPIRES_AT_ATTRIBUTE: now_epoch + int(ttl_seconds),
        }
        try:
            table.put_item(
                Item=item,
                ConditionExpression=_PUT_CONDITION,
                ExpressionAttributeValues={":now": now_epoch},
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            try:
                _take_over_or_raise(table, item, error, now_epoch, stranded_after_seconds,
                                    holder_has_record)
            except Exception:
                release_locks(table, acquired, execution_id)
                raise
        acquired.append(lock_key)
    return acquired


def release_locks(table, lock_keys, execution_id) -> int:
    """Delete every lock in `lock_keys` that `execution_id` still holds. A row another execution has since
    taken (the first one expired) or that is already gone is left alone; any other DynamoDB error is
    logged and the remaining keys are still attempted. Returns the number of rows deleted."""
    released = 0
    for lock_key in lock_keys or []:
        try:
            table.delete_item(
                Key={LOCK_KEY_ATTRIBUTE: lock_key},
                ConditionExpression=_DELETE_CONDITION,
                ExpressionAttributeValues={":e": execution_id},
            )
            released += 1
        except ClientError as error:
            if _is_condition_failure(error):
                logger.info(f"Lock {lock_key} is not held by execution {execution_id}; nothing to release")
                continue
            logger.exception(f"Failed releasing lock {lock_key} for execution {execution_id}: {error}")
    return released


def _execution_input_rows(inputs_table, workflow_execution_id) -> list:
    """Every WorkflowExecutionInputs row of one execution (primary-key query, paged on key presence)."""
    rows: list = []
    kwargs = {"KeyConditionExpression": Key("workflowExecutionId").eq(workflow_execution_id)}
    response = inputs_table.query(**kwargs)
    while True:
        rows.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = inputs_table.query(**kwargs)
    return rows


def held_lock_keys(locks_table, workflow_execution_id) -> List[str]:
    """Every lock key the holder index lists for one execution (paged on key presence). The index is
    eventually consistent, so a row taken moments ago may not be listed yet; release_locks_for_execution
    pairs this with the keys rebuilt from the input rows for that reason."""
    keys: List[str] = []
    kwargs = {"IndexName": HOLDER_INDEX_NAME,
              "KeyConditionExpression": Key(HOLDER_ATTRIBUTE).eq(workflow_execution_id)}
    response = locks_table.query(**kwargs)
    while True:
        for item in response.get("Items", []):
            key = item.get(LOCK_KEY_ATTRIBUTE)
            if key and key not in keys:
                keys.append(key)
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = locks_table.query(**kwargs)
    return keys


def _rebuilt_lock_keys(dynamo, workflow_table_name, inputs_table_name, workflow_execution_id,
                       workflow_database_id, workflow_id) -> List[str]:
    """The keys rebuilt from the workflow's stored concurrencyRestriction and the execution's input rows;
    empty (and no input-row read) when the restriction does not lock or the workflow row is gone."""
    workflow_record = dynamo.Table(workflow_table_name).get_item(
        Key={"databaseId": workflow_database_id, "workflowId": workflow_id}).get("Item") or {}
    restriction = (workflow_record.get("systemConfig") or {}).get("concurrencyRestriction")
    if lock_scope_for_restriction(restriction) is None:
        return []
    rows = _execution_input_rows(dynamo.Table(inputs_table_name), workflow_execution_id)
    return lock_keys_for_execution(workflow_record, rows)


def release_locks_for_execution(dynamo, *, locks_table_name, workflow_table_name, inputs_table_name,
                                workflow_execution_id, workflow_database_id, workflow_id) -> int:
    """Terminal-side release of every lock row an execution still holds.

    The keys are the union of those rebuilt from the input rows under the workflow's stored
    concurrencyRestriction and those the holder index lists for the execution (see the module
    docstring for what each covers); every delete is conditional on the holder, so a row another
    execution has since taken is left alone. Best-effort: each source's failure is logged and the other
    still used, and nothing raises, so a terminal status write never fails on the lock table — an
    unreleased row expires through the table's TTL. Returns the number of rows deleted."""
    if not workflow_execution_id:
        return 0
    keys: List[str] = []
    try:
        keys = _rebuilt_lock_keys(dynamo, workflow_table_name, inputs_table_name,
                                  workflow_execution_id, workflow_database_id, workflow_id)
    except Exception as error:
        logger.exception(f"Rebuilding the lock keys of execution {workflow_execution_id} failed: {error}")
    try:
        locks_table = dynamo.Table(locks_table_name)
    except Exception as error:
        logger.exception(f"Lock release for execution {workflow_execution_id} failed: {error}")
        return 0
    try:
        for key in held_lock_keys(locks_table, workflow_execution_id):
            if key not in keys:
                keys.append(key)
    except Exception as error:
        logger.exception(f"Listing the locks execution {workflow_execution_id} holds failed: {error}")
    if not keys:
        return 0
    try:
        return release_locks(locks_table, keys, workflow_execution_id)
    except Exception as error:
        logger.exception(f"Lock release for execution {workflow_execution_id} failed: {error}")
        return 0
