# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import time
import boto3
import json
from customLogging.logger import safeLogger
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="EndPipeline-ZipArchive")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)
batch = boto3.client(
    'batch',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

# The pipeline's AWS Batch job queues (comma-separated), where the run's job is looked up by its
# job name after the ZipArchiveBatchJob token wait times out. The name is the run's execution name,
# unique to the run, and the state submits one job, so one ListJobs page holds it.
BATCH_JOB_QUEUES = [queue for queue in os.environ.get("BATCH_JOB_QUEUES", "").split(",") if queue]

# The token wait ends with States.Timeout both when its 30-minute heartbeat runs out and when its
# 11-hour bound is reached: Step Functions records a heartbeat expiry as States.Timeout, with no
# cause. Nothing else ends the job executeBatchJob submitted under WAIT_FOR_TASK_TOKEN, so the run's
# job is looked up and, while it is still queued or running, ended here; else it would start or go
# on after its run had failed. Where the job got to names the end, and the backend's zip finalizer
# (common/zipDownloads.py) maps each name to a user message:
# - still waiting to start (the heartbeat clock starts at submit): JOB_QUEUED_ERROR;
# - failed before its container ran (an image that cannot be pulled): JOB_NOT_STARTED_ERROR;
# - ran up to the state's bound: STATE_TIMEOUT_ERROR, unchanged;
# - ran and stopped sending heartbeats inside the bound (killed without a SIGTERM): HEARTBEAT_LOST_ERROR.
STATE_TIMEOUT_ERROR = "States.Timeout"
# The Catch matcher name of a heartbeat expiry; looked up the same way should a run ever carry it.
HEARTBEAT_TIMEOUT_ERROR = "States.HeartbeatTimeout"
TOKEN_WAIT_TIMEOUT_ERRORS = frozenset({STATE_TIMEOUT_ERROR, HEARTBEAT_TIMEOUT_ERROR})
JOB_QUEUED_ERROR = "ZipArchiveJobQueued"
JOB_NOT_STARTED_ERROR = "ZipArchiveJobNotStarted"
HEARTBEAT_LOST_ERROR = "ZipArchiveHeartbeatLost"
# The ZipArchiveBatchJob state's own bound (zipArchive-construct.ts ZIP_ARCHIVE_BATCH_TIMEOUT). The
# job is submitted a few seconds after the state's clock starts, so a job this close to the bound when
# its run ends reached it.
STATE_TIMEOUT_SECONDS = int(os.environ.get("STATE_TIMEOUT_SECONDS", "39600"))
STATE_TIMEOUT_MARGIN_SECONDS = 120
# STARTING is still before the container runs (the image is being pulled), so it ends the same way.
PRE_START_STATUSES = frozenset({"SUBMITTED", "PENDING", "RUNNABLE", "STARTING"})
LIVE_STATUSES = PRE_START_STATUSES | {"RUNNING"}


def run_batch_job(job_name):
    """The newest AWS Batch job of this run's name on the pipeline's queues, or None."""
    if not job_name:
        return None
    jobs = []
    for queue in BATCH_JOB_QUEUES:
        response = batch.list_jobs(
            jobQueue=queue,
            filters=[{"name": "JOB_NAME", "values": [job_name]}],
        )
        jobs.extend(response.get("jobSummaryList") or [])
    return max(jobs, key=lambda job: job.get("createdAt", 0)) if jobs else None


def end_job(job, reason):
    try:
        batch.terminate_job(jobId=job["jobId"], reason=reason)
    except Exception as e:
        logger.warning(f"Could not end the archive job {job.get('jobId')}: {e}")


def token_wait_timeout_error(job_name, now_ms=None):
    """The error to report for a timed-out token wait, from where the run's Batch job got to.

    A job still queued or running is ended, so it does not start or go on after its run has ended.
    Without the job (a failed lookup, or none found) the timeout is reported as it is."""
    try:
        job = run_batch_job(job_name)
    except Exception as e:
        logger.warning(f"Could not look up the archive job after its token wait timed out: {e}")
        return STATE_TIMEOUT_ERROR
    if not job:
        return STATE_TIMEOUT_ERROR
    status = job.get("status")
    if status in PRE_START_STATUSES:
        end_job(job, "The archive job did not start before its run ended")
        return JOB_QUEUED_ERROR
    if status == "FAILED" and not job.get("startedAt"):
        logger.error(f"The archive job failed before it started: {job.get('statusReason', '')}")
        return JOB_NOT_STARTED_ERROR
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    age_seconds = (now_ms - (job.get("createdAt") or 0)) / 1000
    if age_seconds >= STATE_TIMEOUT_SECONDS - STATE_TIMEOUT_MARGIN_SECONDS:
        if status in LIVE_STATUSES:
            end_job(job, "The archive job's time limit was reached")
        return STATE_TIMEOUT_ERROR
    if status in LIVE_STATUSES:
        end_job(job, "The archive job stopped responding")
    return HEARTBEAT_LOST_ERROR


def lambda_handler(event, context):
    """
    PipelineEnd - zipArchive
    Handles final pipeline closeout and external task token callbacks.
    """

    logger.info("Event", event=event)

    externalSfnTaskToken = event.get('externalSfnTaskToken', "")

    if "error" not in event:
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])

    if externalSfnTaskToken:
        if "error" not in event:
            sfn.send_task_success(
                taskToken=externalSfnTaskToken,
                output=json.dumps({'status': 'Pipeline Success'})
            )
            logger.info("Sent external task token: success")
        else:
            error_name = event["error"].get("Error", "Unknown")
            if error_name in TOKEN_WAIT_TIMEOUT_ERRORS:
                error_name = token_wait_timeout_error(event.get("jobName"))
            try:
                sfn.send_task_failure(
                    taskToken=externalSfnTaskToken,
                    error='Pipeline Failure: ' + error_name,
                    cause='See AWS cloudwatch logs for error cause.'
                )
                logger.info("Sent external task token: error")
            except Exception as e:
                # A token already reported on, or one that timed out while the job ran, raises here.
                # The failure is recorded either way, so reporting it twice is not worth failing this
                # state for: a construct-stage failure is reported by the handler's own abort and then
                # again from here, and a job outliving the parent's task timeout invalidates the token.
                logger.error(f"Failed to send external task token failure: {e}")

    return event
