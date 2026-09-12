#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="EndPipeline-VideoSopBom")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

# The container's SendTaskSuccess payload names itself; a job that exited 0 without calling back
# returns Batch's job description instead and must not read as success.
REPORTER_MARKER = "video_sop_bom_pipeline"
ERROR_PIPELINE = "VideoSopBomPipelineError"
KNOWN_ERROR_CODES = frozenset({
    "VideoSopBomInputRejected", "VideoSopBomLimitExceeded", "VideoSopBomTranscribeFailed",
    "VideoSopBomModelOutputInvalid", "VideoSopBomConnectivityError", ERROR_PIPELINE,
})
# A token already reported on (constructPipeline's own abort, or the container's callback) or
# one whose parent task has already timed out: the outcome is recorded either way.
ALREADY_REPORTED_CODES = frozenset({"TaskDoesNotExist", "TaskTimedOut", "InvalidToken"})
# Batch's statusReason when the job definition's attempt duration elapsed.
ATTEMPT_TIMEOUT_MARKER = "attempt duration exceeded timeout"
ATTEMPT_TIMEOUT_CAUSE = ("the container did not finish within the 6 h bound; last stage per the "
                         "container log")
# The stored executionError budget; the SendTaskFailure API itself accepts 32768.
MAX_CAUSE_CHARS = 16384


def _parse_json_object(text):
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _lookup(obj, *names):
    """The first present, non-empty value among PascalCase / camelCase spellings of a key."""
    if not isinstance(obj, dict):
        return None
    for name in names:
        value = obj.get(name)
        if value is not None and value != "":
            return value
    return None


def is_batch_job_description(obj):
    return isinstance(obj, dict) and any(
        key in obj for key in ("JobId", "jobId", "JobName", "jobName", "Status", "status",
                               "Container", "container"))


def _status_reason(job):
    reason = _lookup(job, "StatusReason", "statusReason")
    if reason:
        return str(reason)
    attempts = _lookup(job, "Attempts", "attempts") or []
    if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict):
        return str(_lookup(attempts[-1], "StatusReason", "statusReason") or "")
    return ""


def batch_job_failure_cause(job):
    """One readable sentence from a Batch job description — what the .sync task returns when the
    container never called back (OOM, SIGKILL, attempt timeout, exit without reporting)."""
    status_reason = _status_reason(job)
    if ATTEMPT_TIMEOUT_MARKER in status_reason.lower():
        return ATTEMPT_TIMEOUT_CAUSE
    container = _lookup(job, "Container", "container") or {}
    details = [f"Batch status {_lookup(job, 'Status', 'status') or 'unknown'}"]
    exit_code = _lookup(container, "ExitCode", "exitCode")
    if exit_code is not None:
        details.append(f"exit {exit_code}")
    for reason in (_lookup(container, "Reason", "reason"), status_reason):
        if reason:
            details.append(str(reason))
    return f"container exited without reporting ({', '.join(details)})."


def failure_for(error_block):
    """(error code, cause) for the external token from the caught $.error block."""
    if not isinstance(error_block, dict):
        text = str(error_block) if error_block else "the pipeline failed without a cause"
        return ERROR_PIPELINE, text[:MAX_CAUSE_CHARS]
    error = str(error_block.get("Error") or "")
    cause = str(error_block.get("Cause") or "")
    if error in KNOWN_ERROR_CODES:
        return error, (cause or "the container reported a failure without a cause")[:MAX_CAUSE_CHARS]
    parsed = _parse_json_object(cause)
    if parsed is not None:
        if parsed.get("errorMessage"):
            return ERROR_PIPELINE, str(parsed["errorMessage"])[:MAX_CAUSE_CHARS]
        if is_batch_job_description(parsed):
            return ERROR_PIPELINE, batch_job_failure_cause(parsed)[:MAX_CAUSE_CHARS]
    text = cause or error or "the pipeline failed without a cause"
    return ERROR_PIPELINE, text[:MAX_CAUSE_CHARS]


def _send(callback, **kwargs):
    """Run a task-token callback; an already-reported token is logged and treated as done, any
    other client error propagates so the state machine records it."""
    try:
        callback(**kwargs)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ALREADY_REPORTED_CODES:
            logger.info(f"External task token already reported ({code}); nothing to send")
            return False
        raise


def lambda_handler(event, context):
    """
    PipelineEnd - Video SOP/BOM Extraction
    Releases the external task token with the run's outcome and returns the event unchanged.
    """

    logger.info(event)

    external_sfn_task_token = event.get('externalSfnTaskToken', "")

    if "error" in event:
        code, cause = failure_for(event["error"])
        logger.error(f"Pipeline failure: {code}: {cause}")
        if external_sfn_task_token:
            _send(sfn.send_task_failure, taskToken=external_sfn_task_token, error=code, cause=cause)
        return event

    batch_result = event.get("batchResult")
    reporter = batch_result.get("reporter") if isinstance(batch_result, dict) else None
    if reporter == REPORTER_MARKER:
        logger.info("Pipeline success")
        if external_sfn_task_token:
            _send(sfn.send_task_success, taskToken=external_sfn_task_token,
                  output=json.dumps({'status': 'Pipeline Success'}))
        return event

    if is_batch_job_description(batch_result):
        cause = batch_job_failure_cause(batch_result)
    else:
        cause = "container exited without reporting its outcome on the task token."
    logger.error(f"Pipeline failure: {ERROR_PIPELINE}: {cause}")
    if external_sfn_task_token:
        _send(sfn.send_task_failure, taskToken=external_sfn_task_token, error=ERROR_PIPELINE,
              cause=cause[:MAX_CAUSE_CHARS])
    return event
