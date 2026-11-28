# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""System-workflow launcher: one execution of the system GenAI metadata workflow per launch message.

The vector reindexer enqueues one message per latest live file (and the vector indexer one per
unarchived file that has no vector). Each message becomes one synchronous executeWorkflowV2 cross-call:
a system-pipeline launch (common/workflows/systemPipelines.py, #1135), marked with `SYSTEM_USER` and the
`vamsSystemPipeline` source, whose top-level `systemExecution` names the system trigger value
`System-Reindex`, a small context (the reindex run id and chunk) and the concurrency slot request. Its
body is an ordinary manual request with `executionGroupId: vec-{reindexRunId}-{chunk}`, so the
executions of a run are listable and abortable as groups. A reindex run is a system run: it is never
re-run (a reindex is launched again by running the reindex again).

At most `app.vectorSearch.indexingConcurrency` (REINDEX_SLOT_COUNT) reindex runs are running at once:
each holds one of the workflow's concurrency slot rows in the execution locks table from its launch to
its end (`systemExecution.concurrencySlots`; `common/workflows/executionLocks`, "Concurrency slots").
Before it calls executeWorkflow the launcher reads the slot rows (one BatchGetItem per 100 keys), the
rows earlier releases wrote under the `reindexSlot` scope included: a run launched before the upgrade
holds one of those until it ends (its terminal step releases it through the holder index), so an index
is free only when neither row is held. With none free it does not call it: the message
is sent again with a delay that grows with each wait (30 s doubling to 900 s) and the original is
deleted, so a wait never counts toward the queue's dead-letter threshold. With some free it passes them
as candidates; executeWorkflow claims one with a conditional write, and answers 429 when another launch
took them first, which is a wait too. A held slot whose holder wrote no execution record, or whose
record has ended, is passed as a candidate once its row is old enough for executeWorkflow to take it
over (at most RECLAIM_CHECKS per wait, oldest first).

A 400 (the workflow's per-input-file-version lock refusing a version already running, or a validation
refusal) is final for this message and is dropped. A throttle, a 5xx, or an invocation fault is reported
as a batch item failure: SQS redelivers it and dead-letters it after three attempts. A connection that
fails before the call reaches executeWorkflow is tried again at once, at most twice, before that
(`common/workflows/executeWorkflowInvoke.py`), so a pooled connection the network closed while the
container was idle does not leave the message waiting out the queue's visibility timeout.
"""

import json
import os
import random
import time
from typing import Any, Dict, List

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.batchItemFailures import (
    all_batch_item_failures,
    batch_item_identifier,
    with_batch_item_failures,
)
from common.dynamodb import query_all_items
from common.resourceNames import ResourceKeys, get_table_name
from common.workflows import executeWorkflowInvoke as ewi
from common.workflows import executionLocks as el
from common.workflows import systemPipelines as sp
from common.workflows import triggerMatching as tm
from common.workflows.executionRecords import workflow_composite_key
from customLogging.auditLogging import audited_handler
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
# The executeWorkflowV2 Invoke is synchronous and NOT idempotent: botocore's retries would also repeat a
# read timeout, which follows a delivered call, and launch a duplicate execution. So this client delivers
# one Invoke per attempt and waits out the callee's full 15-minute runtime;
# `executeWorkflowInvoke.invoke_execute_workflow` repeats only a failed connection. The retrying config
# stays on the read-only clients.
invoke_config = Config(retries={"total_max_attempts": 1}, read_timeout=900, connect_timeout=60)

dynamodb = boto3.resource("dynamodb", config=retry_config)
lambda_client = boto3.client("lambda", config=invoke_config)
sqs_client = boto3.client("sqs", config=retry_config)
logger = safeLogger(service_name="SystemWorkflowLauncher")

# The system trigger value a reindex run is recorded under (common/workflows/systemPipelines.py format).
SYSTEM_TRIGGER_REINDEX = "System-Reindex"
# The request form of an ordinary run: the stored trigger is the system value above.
TRIGGER_TYPE_MANUAL = "manual"
TRIGGER_TYPE_FILE_UPLOAD = "fileUpload"
# The execution group a reindex run's launches share: vec-{reindexRunId}-{chunk}.
EXECUTION_GROUP_PREFIX = "vec-"
# The slot rows earlier releases wrote for reindex runs ({workflow}|reindexSlot|{n}), read so the cap holds
# while a run launched before the upgrade still holds one.
LEGACY_SLOT_SCOPE = "reindexSlot"
REQUIRED_MESSAGE_KEYS = ("databaseId", "assetId", "relativeFileKey", "reindexRunId", "chunk")
LAUNCHED = "launched"
DROPPED = "dropped"
WAITED = "waited"
# How many times the message has waited for a slot; carried on the message it is sent again as.
SLOT_WAITS_KEY = "slotWaits"
# The delay before a waiting message is looked at again: 30 s, doubled per wait, at most SQS's 900 s.
WAIT_FIRST_DELAY_SECONDS = 30
WAIT_MAX_DELAY_SECONDS = 900
# The most times one message waits. Past it the message is reported as a failure, so after the queue's
# three receives it is in the dead-letter queue, whose alarm fires (about three weeks of waiting at the
# longest delay: every slot held that long means the runs are not ending).
MAX_SLOT_WAITS = 2000
# A held slot is nominated for takeover only once it is older than executeWorkflow can run (Lambda's
# maximum) plus the lock takeover margin, the age below which executeWorkflow never takes one over.
RECLAIM_MIN_AGE_SECONDS = 900 + el.LOCK_TAKEOVER_MARGIN_SECONDS
# Held slots whose holder's record is read per wait, oldest first.
RECLAIM_CHECKS = 3
_ENDED_STATUSES = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT", "SKIPPED"})
_BATCH_GET_KEYS = 100
# The expiry an unread slot row is given: held.
_HELD_FOREVER = 2 ** 62

try:
    workflow_triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
    workflow_execution_locks_table_name = get_table_name(ResourceKeys.WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE)
    workflow_executions_table_name = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    execute_workflow_v2_function = os.environ["EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME"]
    system_workflow_id = os.environ["GENAI_METADATA_WORKFLOW_ID"]
    system_workflow_database_id = os.environ["GENAI_METADATA_WORKFLOW_DATABASE_ID"]
    launch_queue_url = os.environ["WORKFLOW_LAUNCH_QUEUE_URL"]
    reindex_slot_count = int(os.environ["REINDEX_SLOT_COUNT"])
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

workflow_triggers_table = dynamodb.Table(workflow_triggers_table_name)
workflow_executions_table = dynamodb.Table(workflow_executions_table_name)

# The system workflow's upload trigger row, read once per container: every message launches the same
# workflow, and the row only changes on a redeploy.
_trigger_row_cache: Dict[str, Dict[str, Any]] = {}


class MalformedLaunchMessage(Exception):
    pass


def _system_upload_trigger_row() -> Dict[str, Any]:
    """The workflow's fileUpload trigger row ({} when none): its defaultTemplateIds select the template an
    upload-triggered run uses, so a reindex run is configured identically."""
    if "row" in _trigger_row_cache:
        return _trigger_row_cache["row"]
    composite = workflow_composite_key(system_workflow_database_id, system_workflow_id)
    rows = query_all_items(
        workflow_triggers_table,
        KeyConditionExpression=Key("workflowDatabaseId:workflowId").eq(composite)
        & Key("triggerType").begins_with(TRIGGER_TYPE_FILE_UPLOAD))
    row = rows[0] if len(rows) > 0 else {}
    if not row:
        logger.warning(f"{composite} has no fileUpload trigger row; reindex launches run template-less")
    _trigger_row_cache["row"] = row
    return row


def _parse_message(record: Dict[str, Any]) -> Dict[str, Any]:
    try:
        message = json.loads(record.get("body") or "")
    except (TypeError, ValueError) as e:
        raise MalformedLaunchMessage(f"launch message is not JSON: {e}")
    if not isinstance(message, dict):
        raise MalformedLaunchMessage("launch message is not a JSON object")
    missing = [k for k in REQUIRED_MESSAGE_KEYS if message.get(k) in (None, "")]
    if missing:
        raise MalformedLaunchMessage(f"launch message is missing {missing}")
    return message


def build_launch_body(message: Dict[str, Any]) -> Dict[str, Any]:
    """The execute request body: the upload trigger's selection and templates, as an ordinary manual
    request (the stored trigger value comes from the marked call's systemExecution, never the body)."""
    body = tm.build_trigger_execute_body(
        _system_upload_trigger_row(), message["databaseId"], message["assetId"],
        message["relativeFileKey"], version_id=message.get("versionId") or "")
    body["triggerType"] = TRIGGER_TYPE_MANUAL
    body["executionGroupId"] = f"{EXECUTION_GROUP_PREFIX}{message['reindexRunId']}-{message['chunk']}"
    return body


def build_system_execution(message: Dict[str, Any], candidates: List[int]) -> Dict[str, Any]:
    """The marked call's systemExecution: the System-Reindex trigger value, the run's context (its reindex
    run id and chunk; nothing secret, far below the 4,096-byte bound) and the slot request."""
    return {
        "triggerType": SYSTEM_TRIGGER_REINDEX,
        "context": {"reindexRunId": str(message["reindexRunId"]), "chunk": message["chunk"]},
        sp.SYSTEM_EXECUTION_SLOTS_KEY: {"count": reindex_slot_count, "candidates": list(candidates)},
    }


def build_launch_event(message: Dict[str, Any], candidates: List[int]) -> Dict[str, Any]:
    """The executeWorkflowV2 cross-call: a system-pipeline launch (SYSTEM_USER plus the vamsSystemPipeline
    source marker, and the top-level systemExecution object), which no API request can carry."""
    return {
        "requestContext": {
            "http": {"method": "POST",
                     "path": f"/workflows/{system_workflow_database_id}/{system_workflow_id}/execute"},
        },
        "pathParameters": {"workflowDatabaseId": system_workflow_database_id,
                           "workflowId": system_workflow_id},
        "queryStringParameters": {},
        "body": json.dumps(build_launch_body(message)),
        "lambdaCrossCall": {"userName": sp.SYSTEM_PIPELINE_USER_NAME,
                            "source": sp.SYSTEM_PIPELINE_SOURCE_MARKER},
        sp.SYSTEM_EXECUTION_EVENT_KEY: build_system_execution(message, candidates),
    }


def legacy_slot_key(index: int) -> str:
    """The key an earlier release gave slot `index` (LEGACY_SLOT_SCOPE)."""
    return f"{system_workflow_database_id}:{system_workflow_id}|{LEGACY_SLOT_SCOPE}|{int(index)}"


def _slot_rows() -> Dict[int, List[Dict[str, Any]]]:
    """The workflow's slot rows by index (absent rows left out), the current key and the earlier release's
    key of every slot, read with BatchGetItem. A key the service returns unprocessed is read as held, so a
    partial read never overstates what is free."""
    keys = el.system_slot_keys(system_workflow_database_id, system_workflow_id, reindex_slot_count)
    index_of = {key: index for index, key in enumerate(keys)}
    index_of.update({legacy_slot_key(index): index for index in range(len(keys))})
    all_keys = list(index_of)
    rows: Dict[int, List[Dict[str, Any]]] = {}
    for start in range(0, len(all_keys), _BATCH_GET_KEYS):
        request = {workflow_execution_locks_table_name: {
            "Keys": [{el.LOCK_KEY_ATTRIBUTE: key} for key in all_keys[start:start + _BATCH_GET_KEYS]],
            "ConsistentRead": True}}
        response = dynamodb.batch_get_item(RequestItems=request)
        for item in (response.get("Responses") or {}).get(workflow_execution_locks_table_name, []):
            rows.setdefault(index_of[item[el.LOCK_KEY_ATTRIBUTE]], []).append(item)
        for key in ((response.get("UnprocessedKeys") or {}).get(workflow_execution_locks_table_name) or {}).get(
                "Keys", []):
            rows.setdefault(index_of[key[el.LOCK_KEY_ATTRIBUTE]], []).append(
                {el.EXPIRES_AT_ATTRIBUTE: _HELD_FOREVER})
    return rows


def _holder_has_ended(holder: str) -> bool:
    """Whether a slot holder wrote no execution record or its record is in an ended status. A failed
    read answers False, so the slot is not nominated."""
    try:
        item = workflow_executions_table.get_item(
            Key={"workflowExecutionId": holder,
                 "workflowDatabaseId:workflowId": workflow_composite_key(system_workflow_database_id,
                                                                         system_workflow_id)},
            ConsistentRead=True, ProjectionExpression="executionStatus").get("Item")
    except Exception as error:
        logger.warning(f"Could not read the execution record of slot holder {holder}: {error}")
        return False
    return not item or (item.get("executionStatus") or "") in _ENDED_STATUSES


def slot_candidates(now: float = None) -> List[int]:
    """The slots a launch may try: every free one (in random order, so concurrent launches spread out),
    or with none free the held ones old enough to take over whose holders have ended. An index is free
    when neither its row nor the earlier release's row is held. Empty means wait."""
    now_epoch = int(time.time() if now is None else now)
    rows = _slot_rows()
    held = {index: [row for row in rows.get(index, []) if not el.slot_is_free(row, now_epoch)]
            for index in range(reindex_slot_count)}
    free = [index for index, blocking in held.items() if not blocking]
    if free:
        random.shuffle(free)
        return free[:el.MAX_SYSTEM_SLOT_CANDIDATES]
    old = []
    for index, blocking in held.items():
        acquired = [el.slot_acquired_epoch(row) for row in blocking]
        holders = [row.get(el.HOLDER_ATTRIBUTE) for row in blocking]
        if (all(a is not None and now_epoch - a > RECLAIM_MIN_AGE_SECONDS for a in acquired)
                and all(holders)):
            old.append((min(acquired), index, holders))
    return [index for _acquired, index, holders in sorted(old)[:RECLAIM_CHECKS]
            if all(_holder_has_ended(holder) for holder in holders)]


def wait_delay_seconds(waits: int) -> int:
    return min(WAIT_MAX_DELAY_SECONDS, WAIT_FIRST_DELAY_SECONDS * 2 ** max(0, min(waits - 1, 5)))


def requeue(message: Dict[str, Any]) -> int:
    """Send the message again, delayed, with its wait count raised; the caller then lets the original be
    deleted. A new message starts with a receive count of zero, so waiting never reaches the
    dead-letter queue. Returns the delay."""
    waits = int(message.get(SLOT_WAITS_KEY) or 0) + 1
    if waits > MAX_SLOT_WAITS:
        raise RuntimeError(f"reindex {message['reindexRunId']}: the launch waited {MAX_SLOT_WAITS} times for a "
                           f"reindex slot; leaving it to the dead-letter queue")
    delay = wait_delay_seconds(waits)
    sqs_client.send_message(QueueUrl=launch_queue_url, MessageBody=json.dumps(dict(message, **{SLOT_WAITS_KEY: waits})),
                            DelaySeconds=delay)
    logger.info(f"reindex {message['reindexRunId']}: every slot is in use; waiting (wait {waits}, next look "
                f"in {delay} s)")
    return delay


def launch(message: Dict[str, Any]) -> str:
    """LAUNCHED on a 200, DROPPED on a 400, WAITED when no reindex slot is free (the message is sent
    again with a delay); raises on any other status or an invocation fault."""
    candidates = slot_candidates()
    if not candidates:
        requeue(message)
        return WAITED
    invoke_event = build_launch_event(message, candidates)
    # A connection that failed before delivery is tried again at once (common/workflows/
    # executeWorkflowInvoke.py). If a closed connection did deliver the call, the system workflow's
    # per-input-file-version lock, taken before a reindex slot is claimed, refuses the repeat with a 400,
    # which is dropped below.
    response = ewi.invoke_execute_workflow(
        lambda_client, execute_workflow_v2_function, json.dumps(invoke_event).encode("utf-8"), logger)
    if response.get("FunctionError"):
        raise RuntimeError(f"executeWorkflow invocation faulted: {response['FunctionError']}")
    payload = json.loads(response["Payload"].read().decode("utf-8"))
    status_code = int(payload.get("statusCode", 500))
    target = f"{message['databaseId']}:{message['assetId']}{message['relativeFileKey']}"
    if status_code == 200:
        logger.info(f"reindex {message['reindexRunId']} launched the system workflow for {target}")
        return LAUNCHED
    if status_code == 400:
        logger.info(f"reindex {message['reindexRunId']} launch declined (400) for {target}; dropped")
        return DROPPED
    if status_code == 429:
        # Another launch claimed the candidate slots first.
        requeue(message)
        return WAITED
    raise RuntimeError(f"executeWorkflow returned status {status_code}")


@audited_handler
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    try:
        records = event.get("Records") if isinstance(event, dict) else None
        if records is None:
            return success(body={"message": "No records", "launched": 0, "dropped": 0, "waited": 0})
        launched = dropped = waited = 0
        failures: List[Dict[str, str]] = []
        for record in records:
            try:
                result = launch(_parse_message(record))
                if result == LAUNCHED:
                    launched += 1
                elif result == WAITED:
                    waited += 1
                else:
                    dropped += 1
            except Exception as e:
                logger.exception(f"Launch message failed: {e}")
                identifier = batch_item_identifier(record)
                if identifier:
                    failures.append({"itemIdentifier": identifier})
        body = {"message": "System workflow launches processed", "launched": launched, "dropped": dropped,
                "waited": waited}
        return with_batch_item_failures(success(body=body), event, failures)
    except Exception as e:
        logger.exception(f"Internal error in system workflow launcher: {e}")
        return with_batch_item_failures(internal_error(), event, all_batch_item_failures(event))
