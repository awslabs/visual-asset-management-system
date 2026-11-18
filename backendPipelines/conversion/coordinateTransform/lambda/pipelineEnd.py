# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

logger = safeLogger(service="EndPipeline-CoordinateTransform")

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

# The token wait on CoordTransformBatchJob ends with States.Timeout at its own bound, and nothing else
# ends the job executeBatchJob submitted under WAIT_FOR_TASK_TOKEN, so the run's job, still queued or
# running, is ended here; else it would start or go on after the run had failed. The queue is the
# pipeline's (BATCH_JOB_QUEUES); the job name is unique to the run.
STATE_TIMEOUT_ERROR = "States.Timeout"
BATCH_JOB_QUEUES = [queue for queue in os.environ.get("BATCH_JOB_QUEUES", "").split(",") if queue]
LIVE_BATCH_JOB_STATUSES = frozenset({"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"})


def end_batch_job_after_state_timeout(error, job_name):
    """Terminate the run's Batch job when ``error`` is the token wait's own timeout.

    Returns how many jobs were ended. Best effort: a failed lookup or terminate is logged, and the
    failure is still reported to the workflow."""
    if not isinstance(error, dict) or error.get("Error") != STATE_TIMEOUT_ERROR or not job_name:
        return 0
    ended = 0
    for queue in BATCH_JOB_QUEUES:
        try:
            response = batch.list_jobs(
                jobQueue=queue, filters=[{"name": "JOB_NAME", "values": [job_name]}])
            for job in response.get("jobSummaryList") or []:
                if job.get("status") in LIVE_BATCH_JOB_STATUSES:
                    batch.terminate_job(jobId=job["jobId"], reason="The pipeline's time limit was reached")
                    ended += 1
        except Exception as e:
            logger.warning(f"Could not end the Batch job {job_name}: {e}")
    return ended


def lambda_handler(event, context):
    """
    PipelineEnd - Coordinate Transform
    Handles final pipeline closeout and external task token callbacks.
    """

    logger.info("Event", event=event)

    externalSfnTaskToken = event.get('externalSfnTaskToken', "")

    if "error" not in event:
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])
        end_batch_job_after_state_timeout(event["error"], event.get("jobName"))

    if externalSfnTaskToken:
        if "error" not in event:
            sfn.send_task_success(
                taskToken=externalSfnTaskToken,
                output=json.dumps({'status': 'Pipeline Success'})
            )
            logger.info("Sent external task token: success")
        else:
            try:
                sfn.send_task_failure(
                    taskToken=externalSfnTaskToken,
                    error='Pipeline Failure: ' + event["error"].get("Error", "Unknown"),
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
