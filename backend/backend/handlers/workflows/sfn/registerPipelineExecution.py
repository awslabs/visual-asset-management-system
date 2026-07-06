#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pipeline sub-process registration lambda.

EventBridge-triggered (not API). A pipeline step may optionally report the lower-level
resources it created -- its Step Functions sub-execution and/or CloudWatch log locations --
by putting an event on the orchestration bus:

    PutEvents(
        EventBusName = <orchestration bus>,
        Source       = "<eventSourcePrefix>.execution.<executionId>.pipeline.<pipelineExecutionId>",
        DetailType   = "pipeline.execution.register",
        Detail       = {
            "pipelineExecutionId": "...",                                  # required
            "subExecution": { "stateMachineArn": "...", "executionArn": "..." },  # optional
            "logs": [ { "logGroupArn": "...", "logGroupName": "...", "logStreamName": "..." } ]  # optional
        })

A single standing EventBridge rule (source prefix = deployment eventSourcePrefix, detail-type
= "pipeline.execution.register") routes every such event to this lambda. The lambda appends
the reported ARNs onto the targeted PipelineExecutions row's typed lists
(registeredSubExecutions / registeredLogs), so abort and full-mode log retrieval can later
act on them. Registration is optional and additive: it does not replace the task-token
success/failure callback a pipeline already uses.
"""

import os
import json
import boto3
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger

logger = safeLogger(service="RegisterPipelineExecution")

dynamodb = boto3.resource('dynamodb')

try:
    pipeline_executions_table = os.environ["PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME"]
    if not pipeline_executions_table:
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

REGISTER_DETAIL_TYPE = "pipeline.execution.register"


def _normalize_sub_execution(sub):
    """Normalize a reported sub-execution to the typed shape {stateMachineArn, executionArn}.
    Returns None if neither ARN is present."""
    if not isinstance(sub, dict):
        return None
    entry = {
        "stateMachineArn": sub.get("stateMachineArn", "") or "",
        "executionArn": sub.get("executionArn", "") or "",
    }
    if not entry["stateMachineArn"] and not entry["executionArn"]:
        return None
    return entry


def _normalize_log(log):
    """Normalize a reported log location to {logGroupArn, logGroupName, logStreamName}.
    Returns None if no log group identifier is present."""
    if not isinstance(log, dict):
        return None
    entry = {
        "logGroupArn": log.get("logGroupArn", "") or "",
        "logGroupName": log.get("logGroupName", "") or "",
        "logStreamName": log.get("logStreamName", "") or "",
    }
    if not entry["logGroupArn"] and not entry["logGroupName"]:
        return None
    return entry


def register(detail):
    """Append the reported sub-execution / log ARNs onto the PipelineExecutions row for
    detail.pipelineExecutionId. No-op when the row is unknown or no valid ARNs were reported."""
    pipeline_execution_id = (detail or {}).get("pipelineExecutionId", "")
    if not pipeline_execution_id:
        logger.warning("Registration event missing pipelineExecutionId; ignoring")
        return

    new_subs = []
    sub = _normalize_sub_execution(detail.get("subExecution"))
    if sub:
        new_subs.append(sub)
    new_logs = [n for n in (_normalize_log(log) for log in (detail.get("logs") or [])) if n]

    if not new_subs and not new_logs:
        logger.info(f"Registration event for {pipeline_execution_id} carried no ARNs; nothing to record")
        return

    table = dynamodb.Table(pipeline_executions_table)
    # Look up the SK (workflowExecutionId) by querying the PK so we update the exact row.
    resp = table.query(KeyConditionExpression=Key('pipelineExecutionId').eq(pipeline_execution_id))
    rows = resp.get('Items', [])
    if not rows:
        logger.warning(f"No PipelineExecutions row for {pipeline_execution_id}; ignoring registration")
        return
    row = rows[0]

    # Atomic list_append (if_not_exists seeds an empty list) so concurrent registration events
    # accumulate without clobbering each other.
    table.update_item(
        Key={"pipelineExecutionId": pipeline_execution_id,
             "workflowExecutionId": row.get("workflowExecutionId", "")},
        UpdateExpression=(
            "SET registeredSubExecutions = list_append(if_not_exists(registeredSubExecutions, :empty), :s), "
            "registeredLogs = list_append(if_not_exists(registeredLogs, :empty), :l)"),
        ExpressionAttributeValues={
            ":s": new_subs,
            ":l": new_logs,
            ":empty": [],
        },
    )
    logger.info(f"Registered {len(new_subs)} sub-execution(s) + {len(new_logs)} log(s) "
                f"for pipeline execution {pipeline_execution_id}")


def lambda_handler(event, context):
    """EventBridge-invoked. The event is an EventBridge envelope; the registration payload is
    in event['detail']. Best-effort: logs and returns on any error (a registration failure must
    not disrupt the pipeline, which reports success/failure via its task token)."""
    logger.info(event)
    try:
        detail = event.get("detail", {})
        if isinstance(detail, str):
            detail = json.loads(detail)
        register(detail or {})
    except Exception as e:
        logger.exception(f"Error registering pipeline sub-process (non-critical): {e}")
    return {"handled": True}
