# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""System-workflow launcher: one execution of the system GenAI metadata workflow per launch message.

The vector reindexer enqueues one message per latest live file; this consumer runs at the event source
mapping's MaximumConcurrency (config `vectorSearch.indexingConcurrency`), so the queue depth is what
paces a reindex. Each message becomes one synchronous executeWorkflowV2 cross-call as SYSTEM_USER with
`triggerType: "systemReindex"` and `executionGroupId: vec-{reindexRunId}-{chunk}`, so the executions of
a run are listable and abortable as groups.

A 400 (the workflow's per-input-file-version lock refusing a version already running, or a validation
refusal) is final for this message and is dropped. A throttle, a 5xx, or an invocation fault is reported
as a batch item failure: SQS redelivers it and dead-letters it after three attempts.
"""

import json
import os
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
from common.workflows import triggerMatching as tm
from common.workflows.executionRecords import workflow_composite_key
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
# The executeWorkflowV2 Invoke is synchronous and NOT idempotent: a retry of a slow-but-successful call
# would launch a duplicate execution, so this client delivers exactly one Invoke and waits out the
# callee's full 15-minute runtime. The retrying config stays on the read-only clients.
invoke_config = Config(retries={"total_max_attempts": 1}, read_timeout=900, connect_timeout=60)

dynamodb = boto3.resource("dynamodb", config=retry_config)
lambda_client = boto3.client("lambda", config=invoke_config)
logger = safeLogger(service_name="SystemWorkflowLauncher")

TRIGGER_TYPE_SYSTEM_REINDEX = "systemReindex"
TRIGGER_TYPE_FILE_UPLOAD = "fileUpload"
REQUIRED_MESSAGE_KEYS = ("databaseId", "assetId", "relativeFileKey", "reindexRunId", "chunk")
LAUNCHED = "launched"
DROPPED = "dropped"

try:
    workflow_triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
    execute_workflow_v2_function = os.environ["EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME"]
    system_workflow_id = os.environ["GENAI_METADATA_WORKFLOW_ID"]
    system_workflow_database_id = os.environ["GENAI_METADATA_WORKFLOW_DATABASE_ID"]
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

workflow_triggers_table = dynamodb.Table(workflow_triggers_table_name)

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
    body = tm.build_trigger_execute_body(
        _system_upload_trigger_row(), message["databaseId"], message["assetId"],
        message["relativeFileKey"], version_id=message.get("versionId") or "")
    body["triggerType"] = TRIGGER_TYPE_SYSTEM_REINDEX
    body["executionGroupId"] = f"vec-{message['reindexRunId']}-{message['chunk']}"
    return body


def launch(message: Dict[str, Any]) -> str:
    """LAUNCHED on a 200, DROPPED on a 400; raises on any other status or an invocation fault."""
    body = build_launch_body(message)
    invoke_event = {
        "requestContext": {
            "http": {"method": "POST",
                     "path": f"/workflows/{system_workflow_database_id}/{system_workflow_id}/execute"},
        },
        "pathParameters": {"workflowDatabaseId": system_workflow_database_id,
                           "workflowId": system_workflow_id},
        "queryStringParameters": {},
        "body": json.dumps(body),
        "lambdaCrossCall": {"userName": "SYSTEM_USER"},
    }
    response = lambda_client.invoke(
        FunctionName=execute_workflow_v2_function,
        InvocationType="RequestResponse",
        Payload=json.dumps(invoke_event).encode("utf-8"))
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
    raise RuntimeError(f"executeWorkflow returned status {status_code}")


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    try:
        records = event.get("Records") if isinstance(event, dict) else None
        if records is None:
            return success(body={"message": "No records", "launched": 0, "dropped": 0})
        launched = dropped = 0
        failures: List[Dict[str, str]] = []
        for record in records:
            try:
                result = launch(_parse_message(record))
                if result == LAUNCHED:
                    launched += 1
                else:
                    dropped += 1
            except Exception as e:
                logger.exception(f"Launch message failed: {e}")
                identifier = batch_item_identifier(record)
                if identifier:
                    failures.append({"itemIdentifier": identifier})
        body = {"message": "System workflow launches processed", "launched": launched, "dropped": dropped}
        return with_batch_item_failures(success(body=body), event, failures)
    except Exception as e:
        logger.exception(f"Internal error in system workflow launcher: {e}")
        return with_batch_item_failures(internal_error(), event, all_batch_item_failures(event))
