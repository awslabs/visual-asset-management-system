#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Deadline Cloud job task-token callback lambda.

EventBridge-triggered (not API). AWS Deadline Cloud publishes job status events to the
account's DEFAULT event bus (source ``aws.deadline``). Two standing rules route events
here: "Job Run Status Change" filtered to terminal combined ``taskRunStatus`` values, and
"Job Lifecycle Status Change" filtered to lifecycle failure states (a job that fails at
CREATE/UPLOAD never reaches a terminal task-run status, so without the lifecycle rule its
task token would only resolve by timing out). For each event this lambda:

1. Calls ``deadline:GetJob`` for the farm/queue/job in the event detail.
2. Reads the reserved ``VamsTaskToken`` job parameter the workflow ASL injected at
   ``createJob`` time. A job without that parameter is not a VAMS workflow job (the rules
   see every Deadline job in the account) and is ignored.
3. Best-effort registers the Deadline job as the pipeline execution's sub-process by
   putting a ``pipeline.execution.register`` event on the orchestration bus (using the
   reserved ``VamsPipelineExecutionId`` job parameter), so log retrieval can later locate
   the job. Registration runs before token resolution and never raises, so a failing
   ``SendTask*`` call cannot lose the registration (and vice versa).
4. Resolves the Step Functions task token: task-run ``SUCCEEDED`` -> ``SendTaskSuccess``;
   task-run ``FAILED``/``CANCELED``/``NOT_COMPATIBLE`` or lifecycle
   ``CREATE_FAILED``/``UPLOAD_FAILED`` -> ``SendTaskFailure`` with the status and job
   identity as the cause.

Duplicate or late events are expected (EventBridge is at-least-once; a token may already
be resolved or timed out): ``TaskDoesNotExist``/``TaskTimedOut``/``InvalidToken`` from
``SendTask*`` are logged and swallowed.
"""

import os
import json
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger
from common.workflows.executionRecords import orchestration_event_prefix
from common.workflows.stepfunctions_builder import (
    DEADLINE_TASK_TOKEN_PARAMETER,
    DEADLINE_PIPELINE_EXECUTION_ID_PARAMETER,
    DEADLINE_WORKFLOW_EXECUTION_ID_PARAMETER,
)

logger = safeLogger(service="DeadlineCloudJobCallback")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
# The events client publishes a best-effort next-status event; bound its connect/read timeouts so an
# unreachable events endpoint fails fast rather than blocking the callback (matches sqsBucketSync).
events_retry_config = Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 2})

deadline_client = boto3.client('deadline', config=retry_config)
sfn_client = boto3.client('stepfunctions', config=retry_config)
events_client = boto3.client('events', config=events_retry_config)

try:
    # Orchestration bus + event source prefix for sub-process registration (optional:
    # registration is skipped when unset).
    orchestration_bus_arn = os.environ.get("ORCHESTRATION_BUS_ARN", "")
    orchestration_event_source_prefix = os.environ.get("ORCHESTRATION_EVENT_SOURCE_PREFIX", "")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Reserved OpenJD job parameters the workflow ASL injects on createJob (shared constants
# with the task-state builder so both sides of the contract stay in lockstep).
TASK_TOKEN_PARAMETER = DEADLINE_TASK_TOKEN_PARAMETER
PIPELINE_EXECUTION_ID_PARAMETER = DEADLINE_PIPELINE_EXECUTION_ID_PARAMETER
WORKFLOW_EXECUTION_ID_PARAMETER = DEADLINE_WORKFLOW_EXECUTION_ID_PARAMETER

# Terminal combined task-run statuses for a Deadline job. SUCCEEDED resolves the token
# successfully; the rest fail it. Non-terminal statuses (RUNNING, SUSPENDED, ...) are
# filtered out by the EventBridge rule and additionally ignored here.
SUCCESS_STATUSES = {"SUCCEEDED"}
FAILURE_STATUSES = {"FAILED", "CANCELED", "NOT_COMPATIBLE"}

# Lifecycle failure states (Job Lifecycle Status Change events): the job failed before
# any task ran, so no terminal task-run event will ever arrive for it.
LIFECYCLE_FAILURE_STATUSES = {"CREATE_FAILED", "UPLOAD_FAILED"}

REGISTER_DETAIL_TYPE = "pipeline.execution.register"
RESOURCE_TYPE_DEADLINE_JOB = "deadlineCloudJob"

# SendTask* error codes that indicate the token is already resolved/expired — expected
# with at-least-once event delivery, never an error for this lambda.
_TOKEN_GONE_ERROR_CODES = {"TaskDoesNotExist", "TaskTimedOut", "InvalidToken"}


def _get_string_parameter(job, name):
    """Read a string-typed job parameter from a GetJob response ('' when absent)."""
    param = (job.get("parameters") or {}).get(name) or {}
    return param.get("string", "") or ""


def _resolve_task_token(task_token, task_run_status, farm_id, queue_id, job_id):
    """Resolve the Step Functions task token for a terminal Deadline job status.
    Token-gone errors are swallowed (duplicate/late events); anything else raises."""
    try:
        if task_run_status in SUCCESS_STATUSES:
            sfn_client.send_task_success(
                taskToken=task_token,
                output=json.dumps({
                    "status": task_run_status,
                    "farmId": farm_id,
                    "queueId": queue_id,
                    "jobId": job_id,
                }))
            logger.info(f"SendTaskSuccess for Deadline job {job_id} ({task_run_status})")
        else:
            sfn_client.send_task_failure(
                taskToken=task_token,
                error="DeadlineCloudJobFailed",
                cause=json.dumps({
                    "status": task_run_status,
                    "farmId": farm_id,
                    "queueId": queue_id,
                    "jobId": job_id,
                })[:32768])
            logger.info(f"SendTaskFailure for Deadline job {job_id} ({task_run_status})")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in _TOKEN_GONE_ERROR_CODES:
            logger.info(f"Task token already resolved/expired for Deadline job {job_id} "
                        f"({code}); ignoring duplicate event")
            return
        raise


def _register_job(pipeline_execution_id, workflow_execution_id, farm_id, queue_id, job_id):
    """Best-effort: register the Deadline job as the pipeline execution's sub-process on
    the orchestration bus (same contract every pipeline uses). Never raises."""
    if not orchestration_bus_arn or not pipeline_execution_id:
        return
    try:
        source = orchestration_event_prefix(
            orchestration_event_source_prefix, workflow_execution_id or "unknown",
            pipeline_execution_id)
        events_client.put_events(Entries=[{
            "EventBusName": orchestration_bus_arn,
            "Source": source,
            "DetailType": REGISTER_DETAIL_TYPE,
            "Detail": json.dumps({
                "pipelineExecutionId": pipeline_execution_id,
                "subExecution": {
                    "resourceType": RESOURCE_TYPE_DEADLINE_JOB,
                    "farmId": farm_id,
                    "queueId": queue_id,
                    "jobId": job_id,
                },
            }),
        }])
        logger.info(f"Registered Deadline job {job_id} for pipeline execution "
                    f"{pipeline_execution_id}")
    except Exception as e:
        logger.exception(f"Error registering Deadline job (non-critical): {e}")


def _terminal_status(detail):
    """Map an event detail to the terminal status to resolve the token with, or None.
    Job Run Status Change events carry ``taskRunStatus``; Job Lifecycle Status Change
    events carry ``lifecycleStatus`` (only its failure states are terminal here — a
    successfully created job terminates through a task-run event later)."""
    task_run_status = detail.get("taskRunStatus", "")
    if task_run_status in SUCCESS_STATUSES | FAILURE_STATUSES:
        return task_run_status
    lifecycle_status = detail.get("lifecycleStatus", "")
    if lifecycle_status in LIFECYCLE_FAILURE_STATUSES:
        return lifecycle_status
    return None


def handle_job_event(detail):
    """Process one Deadline job status detail: no-op for non-VAMS jobs and non-terminal
    statuses; otherwise register the job and resolve the task token."""
    detail = detail or {}
    farm_id = detail.get("farmId", "")
    queue_id = detail.get("queueId", "")
    job_id = detail.get("jobId", "")

    if not farm_id or not queue_id or not job_id:
        logger.warning("Deadline event missing farmId/queueId/jobId; ignoring")
        return

    status = _terminal_status(detail)
    if not status:
        logger.info(f"Non-terminal Deadline job status for {job_id}; ignoring")
        return

    job = deadline_client.get_job(farmId=farm_id, queueId=queue_id, jobId=job_id)

    task_token = _get_string_parameter(job, TASK_TOKEN_PARAMETER)
    if not task_token:
        logger.info(f"Deadline job {job_id} carries no {TASK_TOKEN_PARAMETER} parameter "
                    f"(not a VAMS workflow job); ignoring")
        return

    # Register first (never raises) so a SendTask* failure cannot lose the registration.
    _register_job(
        pipeline_execution_id=_get_string_parameter(job, PIPELINE_EXECUTION_ID_PARAMETER),
        workflow_execution_id=_get_string_parameter(job, WORKFLOW_EXECUTION_ID_PARAMETER),
        farm_id=farm_id, queue_id=queue_id, job_id=job_id)

    _resolve_task_token(task_token, status, farm_id, queue_id, job_id)


def lambda_handler(event, context):
    """EventBridge-invoked. The event is an EventBridge envelope; the Deadline job status
    payload is in event['detail']. GetJob/SendTask* errors (other than token-gone) raise so
    EventBridge retries the delivery."""
    logger.info(event)
    detail = event.get("detail", {})
    if isinstance(detail, str):
        detail = json.loads(detail)
    handle_job_event(detail or {})
    return {"handled": True}
