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
the restriction hold under concurrency: two launches racing for one scope cannot both put the row.
"""

import time
from datetime import datetime, timezone
from typing import List, Optional

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from customLogging.logger import safeLogger

logger = safeLogger(service="ExecutionLocks")

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
LOCK_SCOPE_ATTRIBUTE = "lockScope"
ACQUIRED_AT_ATTRIBUTE = "acquiredAt"
EXPIRES_AT_ATTRIBUTE = "expiresAt"

_PUT_CONDITION = f"attribute_not_exists({LOCK_KEY_ATTRIBUTE}) OR {EXPIRES_AT_ATTRIBUTE} < :now"
_DELETE_CONDITION = f"{HOLDER_ATTRIBUTE} = :e"


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


def _holder_from_conflict(error) -> str:
    """The holder's workflowExecutionId carried on a ConditionalCheckFailedException when the put asked
    for ReturnValuesOnConditionCheckFailure=ALL_OLD. The item arrives in wire format through the resource
    client, so both the typed and the plain shape are read."""
    item = (getattr(error, "response", None) or {}).get("Item") or {}
    holder = item.get(HOLDER_ATTRIBUTE, "")
    if isinstance(holder, dict):
        holder = holder.get("S", "")
    return str(holder or "")


def _scope_of_key(lock_key: str) -> str:
    """The scope segment of a key built by build_lock_key ("" for any other shape)."""
    parts = (lock_key or "").split("|")
    return parts[1] if len(parts) == 3 and parts[1] in LOCK_SCOPE_BY_RESTRICTION.values() else ""


def acquire_locks(table, lock_keys, execution_id, ttl_seconds, now=None) -> List[str]:
    """Take every lock in `lock_keys` for `execution_id`, or none of them.

    Each key is one conditional PutItem that succeeds when no row exists or the row has expired. On the
    first conflict every lock this call already took is released and ExecutionLockConflict names the
    contested key and its holder; any other DynamoDB error releases the same way and propagates. Returns
    the acquired keys (input order, deduplicated) so the caller can release them if its launch fails."""
    now_epoch = _now_epoch(now)
    acquired: List[str] = []
    for lock_key in lock_keys or []:
        if lock_key in acquired:
            continue
        try:
            table.put_item(
                Item={
                    LOCK_KEY_ATTRIBUTE: lock_key,
                    HOLDER_ATTRIBUTE: execution_id,
                    LOCK_SCOPE_ATTRIBUTE: _scope_of_key(lock_key),
                    ACQUIRED_AT_ATTRIBUTE: _iso(now_epoch),
                    EXPIRES_AT_ATTRIBUTE: now_epoch + int(ttl_seconds),
                },
                ConditionExpression=_PUT_CONDITION,
                ExpressionAttributeValues={":now": now_epoch},
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as error:
            release_locks(table, acquired, execution_id)
            if not _is_condition_failure(error):
                raise
            raise ExecutionLockConflict(lock_key, _holder_from_conflict(error)) from None
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


def release_locks_for_execution(dynamo, *, locks_table_name, workflow_table_name, inputs_table_name,
                                workflow_execution_id, workflow_database_id, workflow_id) -> int:
    """Terminal-side release. Reads the workflow's stored concurrencyRestriction; when it is one that
    locks (perAsset, perInputFile, perInputFileVersion), rebuilds the execution's lock keys from its
    WorkflowExecutionInputs rows and releases them. Best-effort: any failure is logged and 0 returned, so
    a terminal status write never fails on the lock table — an unreleased row expires through the
    table's TTL."""
    if not workflow_execution_id:
        return 0
    try:
        workflow_record = dynamo.Table(workflow_table_name).get_item(
            Key={"databaseId": workflow_database_id, "workflowId": workflow_id}).get("Item") or {}
        restriction = (workflow_record.get("systemConfig") or {}).get("concurrencyRestriction")
        if lock_scope_for_restriction(restriction) is None:
            return 0
        rows = _execution_input_rows(dynamo.Table(inputs_table_name), workflow_execution_id)
        keys = lock_keys_for_execution(workflow_record, rows)
        if not keys:
            return 0
        return release_locks(dynamo.Table(locks_table_name), keys, workflow_execution_id)
    except Exception as error:
        logger.exception(f"Lock release for execution {workflow_execution_id} failed: {error}")
        return 0
