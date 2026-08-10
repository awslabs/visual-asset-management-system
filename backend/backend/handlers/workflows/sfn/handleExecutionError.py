#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Workflow-execution error-handler lambda.

Every pipeline / interim / process-output state's Catch routes here (with the caught error
captured at $.errorInfo) before the terminal Fail state. It reconciles the execution's
tables to a failed terminal state so a failure never leaves stale RUNNING rows:

  - the sub-processes each in-flight pipeline registered (Step Functions sub-executions, AWS Batch
    jobs) -> stopped, before their rows are stamped terminal (a terminal row is no longer a
    candidate for the abort API, so anything left running here has no in-product remedy);
  - the V2 main row -> FAILED with a stop date, the specific executionError (from the caught
    Step Functions Error/Cause), and the full CloudWatch executionLog;
  - every non-terminal PipelineExecutions row -> FAILED with a stop date;
  - a per-pipeline logs row for the failing pipeline when identifiable.

It is best-effort and idempotent: any error inside it is logged and the state machine still
transitions to the Fail state (the ASL Catch on this state also routes to Fail), so a
bookkeeping problem never masks the original failure. It records FAILED; the abort API
records ABORTED.
"""

import os
import json
import boto3
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger
from common.resourceNames import get_table_name, ResourceKeys
from common.workflows import executionRecords as er
from common.workflows import executionOutputs as eo

logger = safeLogger(service="HandleExecutionError")

logs_client = boto3.client('logs')
dynamodb = boto3.resource('dynamodb')
sfn_client = boto3.client('stepfunctions')
batch_client = boto3.client('batch')

try:
    workflow_execution_database_v2 = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
    pipeline_execution_logs_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_LOGS_STORAGE_TABLE)
    workflow_execution_log_group_arn = os.environ.get("WORKFLOW_EXECUTION_LOG_GROUP_ARN", "")
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

FAILED_STATUS = "FAILED"

# executionError and executionLog land on the same main-row item, so they share the item's
# free-form text budget: the error message keeps a small reserved slice and the log takes the
# rest, keeping the finalized item under the DynamoDB 400 KB limit.
MAX_ERROR_FIELD_BYTES = 16 * 1024
MAX_ERROR_LOG_FIELD_BYTES = er.MAX_LOG_FIELD_BYTES - MAX_ERROR_FIELD_BYTES


def _extract_error_message(error_info):
    """Build a human-readable failure message from the captured SFN error object
    ($.errorInfo = {Error, Cause}). Cause is often a JSON string with errorMessage. The result is
    trimmed to MAX_ERROR_FIELD_BYTES so a multi-KB Cause cannot push the main row item over the
    DynamoDB item limit."""
    if not isinstance(error_info, dict):
        text, _ = er.truncate_text(str(error_info) if error_info else "",
                                   limit=MAX_ERROR_FIELD_BYTES)
        return text
    err = error_info.get('Error', '')
    cause = error_info.get('Cause', '')
    # Cause may itself be a JSON string (Lambda error) with errorMessage/errorType.
    if cause:
        try:
            parsed = json.loads(cause)
            if isinstance(parsed, dict) and parsed.get('errorMessage'):
                cause = parsed.get('errorMessage')
        except (json.JSONDecodeError, TypeError):
            pass
    message, _ = er.truncate_text(": ".join(p for p in (err, cause) if p),
                                  limit=MAX_ERROR_FIELD_BYTES)
    return message


def _fetch_execution_log(execution_id):
    """Best-effort full CloudWatch execution log for this execution within the shared
    workflow log group, scoped by the execution id filter pattern. '' on any failure."""
    if not workflow_execution_log_group_arn or not execution_id:
        return ""
    parts = workflow_execution_log_group_arn.split(":log-group:")
    if len(parts) < 2:
        return ""
    log_group_name = parts[1]
    if log_group_name.endswith(":*"):
        log_group_name = log_group_name[:-2]
    try:
        resp = logs_client.filter_log_events(
            logGroupName=log_group_name, filterPattern=f'"{execution_id}"', limit=50)
        text = "\n".join(e.get('message', '') for e in resp.get('events', []))
        text, _ = er.truncate_text(text, limit=MAX_ERROR_LOG_FIELD_BYTES)
        return text
    except Exception as e:
        logger.info(f"Could not fetch CloudWatch logs (non-critical): {e}")
        return ""


def _get_pipeline_rows(execution_id):
    table = dynamodb.Table(pipeline_executions_table)
    items = []
    kwargs = {
        'IndexName': 'PipelineExecByWorkflowExecGSI',
        'KeyConditionExpression': Key('workflowExecutionId').eq(execution_id),
    }
    resp = table.query(**kwargs)
    while True:
        items.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = table.query(**kwargs)
    return items


def reconcile_failed_execution(body, error_info):
    """Stop the in-flight pipelines' registered sub-processes, mark the execution + in-flight pipeline
    rows FAILED with stop times, and capture the error message + full execution log. Best-effort per
    step; logs and continues on error."""
    execution_id = body.get('workflowExecutionId', '')
    workflow_database_id = body.get('workflowDatabaseId', '')
    workflow_id = body.get('workflowId', '')

    if not execution_id:
        logger.warning("No workflowExecutionId in error handler body; nothing to reconcile")
        return

    now = er.iso_now()
    error_message = _extract_error_message(error_info)
    execution_log = _fetch_execution_log(execution_id)

    # 1) Fetch pipeline rows, stop the in-flight ones' registered sub-processes, then mark those rows
    #    FAILED. A sub-process that could not be stopped is named in the per-pipeline log row below,
    #    which is the only record of what is still running once the rows are terminal.
    stop_failures = []
    try:
        pipeline_rows = _get_pipeline_rows(execution_id)
        stop_failures = eo.mark_inflight_pipelines_terminal(
            dynamodb, pipeline_executions_table, pipeline_rows, FAILED_STATUS, now,
            sfn_client=sfn_client, batch_client=batch_client)
        if stop_failures:
            logger.warning(f"Sub-processes left running for execution {execution_id}: "
                           f"{'; '.join(stop_failures)}")
    except Exception as e:
        logger.exception(f"Error marking pipeline rows failed (continuing): {e}")
        pipeline_rows = []

    # 2) Per-pipeline log row for each in-flight pipeline that was just failed. Step Functions
    #    cannot inject the failing state's id into this handler's static payload, so attribute
    #    the failure log to the non-terminal pipeline rows (the ones that did not complete) --
    #    these are the pipelines affected by the failure.
    error_log = execution_log or error_message
    if stop_failures:
        # Recorded on the row rather than only logged: once the rows are terminal this is the only
        # in-product record of a sub-process still running. Placed FIRST so a log already at its
        # budget cannot trim it away.
        error_log, _ = er.truncate_text(
            "\n".join(stop_failures + [error_log]) if error_log else "\n".join(stop_failures),
            limit=MAX_ERROR_LOG_FIELD_BYTES)
    try:
        logs_table = dynamodb.Table(pipeline_execution_logs_table)
        for prow in pipeline_rows:
            if prow.get('executionStatus', '') in eo.TERMINAL_STATUSES:
                continue
            pexec_id = prow.get('pipelineExecutionId', '')
            if not pexec_id:
                continue
            logs_table.put_item(Item=er.build_log_record(
                pipeline_execution_id=pexec_id, log_type="summary",
                result_log="", error_log=error_log,
                log_group_arn=workflow_execution_log_group_arn, log_stream_name="",
            ))
    except Exception as e:
        logger.exception(f"Error writing failing-pipeline log rows (continuing): {e}")

    # 3) Finalize the main row FAILED (unless already terminal) with error + log.
    try:
        main_table = dynamodb.Table(workflow_execution_database_v2)
        existing = main_table.query(
            KeyConditionExpression=Key('workflowExecutionId').eq(execution_id), ScanIndexForward=False)
        rows = existing.get('Items', [])
        current_status = rows[0].get('executionStatus', '') if rows else ''
        if current_status not in eo.TERMINAL_STATUSES:
            eo.finalize_main_row(
                dynamodb, workflow_execution_database_v2, execution_id,
                workflow_database_id, workflow_id, FAILED_STATUS, now,
                execution_log=execution_log, execution_error=error_message)
    except Exception as e:
        logger.exception(f"Error finalizing main execution row (continuing): {e}")


def lambda_handler(event, context):
    """SFN-invoked on any caught error. Reconciles tables to FAILED, then returns so the
    state machine transitions to the Fail state. Never raises (so it never masks the
    original failure)."""
    logger.info(event)
    try:
        body = event.get('body', event)
        if isinstance(body, str):
            body = json.loads(body)
        error_info = event.get('errorInfo', {})
        reconcile_failed_execution(body or {}, error_info)
    except Exception as e:
        # Best-effort: never let a bookkeeping error mask the original failure.
        logger.exception(f"Error handler encountered an error (continuing to Fail state): {e}")
    return {"handled": True}
