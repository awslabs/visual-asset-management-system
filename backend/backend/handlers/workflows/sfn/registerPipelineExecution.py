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
            "subExecution": { "resourceType": "stepFunctionsExecution",    # optional; defaults to
                              "stateMachineArn": "...", "executionArn": "..." },  #   stepFunctionsExecution
            "logs": [ { "logGroupArn": "...", "logGroupName": "...",
                        "logStreamName": "...", "logStreamPrefix": "..." } ]  # optional
        })

A reported subExecution may be any sub-process resource type (Step Functions execution today;
AWS Batch job, ECS/Fargate task, etc. later) — it is typed by ``resourceType`` and carries
whichever locator keys apply (executionArn/stateMachineArn, jobArn, taskArn, ...). All reported
types are stored now; the abort path acts only on Step Functions executions today and surfaces a
non-fatal warning for types it cannot yet stop.

A single standing EventBridge rule (source prefix = deployment eventSourcePrefix, detail-type
= "pipeline.execution.register") routes every such event to this lambda. The lambda appends
the reported resources onto the targeted PipelineExecutions row's typed lists
(registeredSubExecutions / registeredLogs), so abort and full-mode log retrieval can later
act on them. Registration is optional and additive: it does not replace the task-token
success/failure callback a pipeline already uses.
"""

import json
import boto3
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate

logger = safeLogger(service="RegisterPipelineExecution")

dynamodb = boto3.resource('dynamodb')

try:
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

REGISTER_DETAIL_TYPE = "pipeline.execution.register"


# Sub-process resource types a pipeline may register. Step Functions executions are the only
# type the abort path can stop today; the others are accepted and stored now (so the data model
# is complete) and become abortable in a later stage.
RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION = "stepFunctionsExecution"

# Locator keys carried through verbatim for any sub-process resource, so a new resource type
# (Batch job, ECS task, ...) can be registered without changing this lambda. Each maps to the
# validator its value must satisfy; whichever are present on the reported entry are validated and
# preserved, absent (or invalid) ones are omitted.
_SUB_EXECUTION_LOCATOR_VALIDATORS = {
    "stateMachineArn": "ARN",   # Step Functions state machine (definition)
    "executionArn": "ARN",      # Step Functions execution (running instance) — abortable today
    "jobArn": "ARN",            # AWS Batch job
    "jobId": "ID",              # AWS Batch / Deadline Cloud job id (not an ARN)
    "taskArn": "ARN",           # ECS/Fargate task
    "clusterArn": "ARN",        # ECS cluster (needed to stop a task)
    "farmId": "ID",             # Deadline Cloud farm (with queueId+jobId locates a job)
    "queueId": "ID",            # Deadline Cloud queue
    "arn": "ARN",               # generic fallback ARN for any other resource type
}

# resourceType is a short identifier (camelCase resource-type name); bound it like an ID.
_RESOURCE_TYPE_VALIDATOR = "ID"


def _field_valid(field_name, value, validator):
    """Return True if value passes the named validator. Best-effort: never raises (a validator
    error is treated as invalid so the field is dropped rather than crashing registration)."""
    try:
        ok, _msg = validate({field_name: {"value": value, "validator": validator}})
        return bool(ok)
    except Exception as e:  # nosec B110 - defensive; an invalid field is simply dropped
        logger.warning(f"Validation error for {field_name} (dropping field): {e}")
        return False


def _normalize_sub_execution(sub):
    """Normalize + validate a reported sub-process resource to a typed entry. Always carries a
    ``resourceType`` (defaulting to a Step Functions execution for back-compat with pipelines
    that report a bare {stateMachineArn, executionArn}) plus whichever locator keys were reported
    AND pass field validation. Invalid resourceType or locator values are dropped (logged), not
    stored. Returns None if no valid locator/identifier remains."""
    if not isinstance(sub, dict):
        return None
    resource_type = sub.get("resourceType", "") or RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION
    if not _field_valid("resourceType", resource_type, _RESOURCE_TYPE_VALIDATOR):
        logger.warning(f"Dropping sub-process with invalid resourceType: {resource_type!r}")
        return None
    entry = {"resourceType": resource_type}
    for key, validator in _SUB_EXECUTION_LOCATOR_VALIDATORS.items():
        val = sub.get(key, "") or ""
        if not val:
            continue
        if _field_valid(key, val, validator):
            entry[key] = val
        else:
            logger.warning(f"Dropping invalid {key} on registered sub-process ({resource_type})")
    # Drop entries that carried only a resourceType with no valid resource locator at all.
    if len(entry) == 1:
        return None
    return entry


def _normalize_log(log):
    """Normalize + validate a reported log location to {logGroupArn, logGroupName, logStreamName,
    logStreamPrefix}. logStreamPrefix scopes full-mode log retrieval for sources that emit to a
    known stream prefix (e.g. AWS Batch/ECS). Each field is format-validated; an invalid field is
    dropped (logged). Returns None if no valid log group identifier (ARN or name) remains."""
    if not isinstance(log, dict):
        return None

    def _checked(field, validator):
        val = (log.get(field, "") or "")
        if not val:
            return ""
        if _field_valid(field, val, validator):
            return val
        logger.warning(f"Dropping invalid {field} on registered log location")
        return ""

    entry = {
        "logGroupArn": _checked("logGroupArn", "CLOUDWATCH_LOG_GROUP_ARN"),
        "logGroupName": _checked("logGroupName", "CLOUDWATCH_LOG_GROUP_NAME"),
        "logStreamName": _checked("logStreamName", "LOG_STREAM_NAME"),
        "logStreamPrefix": _checked("logStreamPrefix", "LOG_STREAM_NAME"),
    }
    if not entry["logGroupArn"] and not entry["logGroupName"]:
        return None
    return entry


def _not_already_registered(new_entries, existing_entries):
    """Drop entries the row already carries. EventBridge delivery is at-least-once, so a
    redelivered registration event reports locators that are already stored; appending them again
    would duplicate CloudWatch reads and abort calls for the same resource."""
    existing = [e for e in (existing_entries or []) if isinstance(e, dict)]
    return [e for e in new_entries if e not in existing]


def register(detail):
    """Append the reported sub-execution / log ARNs onto the PipelineExecutions row for
    detail.pipelineExecutionId. No-op when the row is unknown or no valid ARNs were reported."""
    pipeline_execution_id = (detail or {}).get("pipelineExecutionId", "")
    if not pipeline_execution_id:
        logger.warning("Registration event missing pipelineExecutionId; ignoring")
        return
    # pipelineExecutionId is used as a DynamoDB key; reject a malformed value rather than query
    # with it (ASSET_ID covers the GUID/filename-safe id format).
    if not _field_valid("pipelineExecutionId", pipeline_execution_id, "ASSET_ID"):
        logger.warning("Registration event has invalid pipelineExecutionId; ignoring")
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

    # Skip locators the row already carries so an at-least-once redelivery of the same event does
    # not store the same sub-process/log twice.
    new_subs = _not_already_registered(new_subs, row.get("registeredSubExecutions"))
    new_logs = _not_already_registered(new_logs, row.get("registeredLogs"))
    if not new_subs and not new_logs:
        logger.info(f"Registration event for {pipeline_execution_id} reported only already-registered "
                    f"locators; nothing to record")
        return

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
