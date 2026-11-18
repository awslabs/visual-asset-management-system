#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.config import Config
from customLogging.logger import safeLogger

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="EndImageTo3dPipeline")

# The Batch state's own Step Functions bound counts the time a job waits in the AWS Batch queue as
# well as its run, and Step Functions gives that States.Timeout no cause of its own.
STATE_TIMEOUT_ERROR = "States.Timeout"
STATE_TIMEOUT_CAUSE = (
    "The Image to 3D job did not finish within the pipeline's time limit. The limit counts the time "
    "the job waited in the AWS Batch queue for GPU capacity as well as its run time."
)

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

# Step Functions cancels a .sync Batch job when the execution is stopped, not when the Batch state's
# own bound fires, so after that timeout the run's job, still queued or running, is ended here; else
# it would start or go on on a GPU after the run had failed. The queues are every queue a model's
# state submits to (BATCH_JOB_QUEUES, comma-separated); the job name is unique to the run.
BATCH_JOB_QUEUES = [queue for queue in os.environ.get("BATCH_JOB_QUEUES", "").split(",") if queue]
LIVE_BATCH_JOB_STATUSES = frozenset({"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"})


def end_batch_job_after_state_timeout(error, job_name):
    """Terminate the run's Batch job when ``error`` is the Batch state's own timeout.

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
    ClosePipeline
    Do any final closeouts of the image-to-3D pipeline and report the outcome to the VAMS workflow.
    This lambda is the single owner of the outer workflow callback: both the Batch success branch and
    the caught-failure branch route here, so the container itself sends nothing and a container crash
    still closes the waiting task.
    """

    logger.info("Event Input", event=event)
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = event["externalSfnTaskToken"]
    error = event.get("error")

    end_batch_job_after_state_timeout(error, event.get("jobName"))

    if not externalSfnTaskToken:
        logger.warning("No external task token present; no workflow callback to send")
        return event

    if error:
        error_info = error if isinstance(error, dict) else {"Error": "PipelineFailure", "Cause": str(error)}
        logger.error("Pipeline Failure")
        logger.error(error)
        cause = error_info.get("Cause", "See AWS CloudWatch logs for error cause.")
        if error_info.get("Error") == STATE_TIMEOUT_ERROR:
            cause = STATE_TIMEOUT_CAUSE
        sfn.send_task_failure(
            taskToken=externalSfnTaskToken,
            error=str(error_info.get("Error", "PipelineFailure"))[:256],
            cause=str(cause)[:32000],
        )
    else:
        logger.info("Pipeline Success")
        sfn.send_task_success(
            taskToken=externalSfnTaskToken,
            output=json.dumps({'status': 'Pipeline Success'})
        )

    return event
