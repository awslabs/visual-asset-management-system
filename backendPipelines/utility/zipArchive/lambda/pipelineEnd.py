# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
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

# The pipeline's AWS Batch job queue, where the run's job is looked up after a heartbeat timeout.
BATCH_JOB_QUEUE = os.environ.get("BATCH_JOB_QUEUE", "")

# The ZipArchiveBatchJob heartbeat clock starts at submit, so it also expires for a job that is still
# waiting in the queue or that failed before its container ran. These names, reported in place of
# the heartbeat timeout, are the ones the backend's zip finalizer (common/zipDownloads.py) maps to a
# user message; a job that did run keeps the heartbeat timeout.
HEARTBEAT_TIMEOUT_ERROR = "States.HeartbeatTimeout"
JOB_QUEUED_ERROR = "ZipArchiveJobQueued"
JOB_NOT_STARTED_ERROR = "ZipArchiveJobNotStarted"
# STARTING is still before the container runs (the image is being pulled), so it ends the same way.
PRE_START_STATUSES = frozenset({"SUBMITTED", "PENDING", "RUNNABLE", "STARTING"})


def run_batch_job(job_name):
    """The newest AWS Batch job of this run's name on the pipeline's queue, or None."""
    if not BATCH_JOB_QUEUE or not job_name:
        return None
    response = batch.list_jobs(
        jobQueue=BATCH_JOB_QUEUE,
        filters=[{"name": "JOB_NAME", "values": [job_name]}],
    )
    jobs = response.get("jobSummaryList") or []
    return max(jobs, key=lambda job: job.get("createdAt", 0)) if jobs else None


def heartbeat_timeout_error(job_name):
    """The error to report for a heartbeat timeout, from where the run's Batch job got to.

    A job still waiting to start is cancelled, so it does not start after its run has ended."""
    try:
        job = run_batch_job(job_name)
    except Exception as e:
        logger.warning(f"Could not look up the archive job after its heartbeat timeout: {e}")
        return HEARTBEAT_TIMEOUT_ERROR
    if not job:
        return HEARTBEAT_TIMEOUT_ERROR
    status = job.get("status")
    if status in PRE_START_STATUSES:
        try:
            batch.terminate_job(
                jobId=job["jobId"],
                reason="The archive job did not start before its run ended",
            )
        except Exception as e:
            logger.warning(f"Could not cancel the queued archive job {job.get('jobId')}: {e}")
        return JOB_QUEUED_ERROR
    if status == "FAILED" and not job.get("startedAt"):
        logger.error(f"The archive job failed before it started: {job.get('statusReason', '')}")
        return JOB_NOT_STARTED_ERROR
    return HEARTBEAT_TIMEOUT_ERROR


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
            if error_name == HEARTBEAT_TIMEOUT_ERROR:
                error_name = heartbeat_timeout_error(event.get("jobName"))
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
