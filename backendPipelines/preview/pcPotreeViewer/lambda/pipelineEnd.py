#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

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

logger = safeLogger(service="EndPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

# SendTaskFailure caps `error` at 256 characters and `cause` at 32,768. A longer value is rejected, the
# calling workflow's task is never told and waits out its timeout, so both are cut to fit.
TASK_FAILURE_ERROR_MAX_CHARS = 256
TASK_FAILURE_CAUSE_MAX_CHARS = 32768
TASK_FAILURE_CAUSE_DEFAULT = 'See AWS cloudwatch logs for error cause.'


def _bounded(text, limit):
    return text if len(text) <= limit else text[:limit - 3] + '...'


def external_task_failure(error):
    """The `error` and `cause` reported to the calling workflow for this run's caught error.

    Only a failure the pipeline container reported itself (its `error` starts "Pipeline Failure:") has
    its cause passed on: that cause is the container's own message, e.g. PDAL's text for an unreadable
    E57. Any other caught error (a Lambda or Batch fault, a timeout) keeps the generic cause, as its
    Step Functions cause can carry stack traces and resource names."""
    error_text = str(error.get("Error") or "")
    cause_text = str(error.get("Cause") or "") if error_text.startswith("Pipeline Failure:") else ""
    cause_text = cause_text or TASK_FAILURE_CAUSE_DEFAULT
    return {
        "error": _bounded('Pipeline Failure: ' + error_text, TASK_FAILURE_ERROR_MAX_CHARS),
        "cause": _bounded(cause_text, TASK_FAILURE_CAUSE_MAX_CHARS),
    }


def lambda_handler(event, context):
    """
    ClosePipeline
    Do any final closeouts of the pipeline
    """

    logger.info("Event Input", event=event)
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = event.get('externalSfnTaskToken', "")

    if("error" not in event):
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])

    if (externalSfnTaskToken != None and externalSfnTaskToken != ""):
        if("error" not in event):
            sfn.send_task_success(
                taskToken=event['externalSfnTaskToken'],
                output=json.dumps({'status': 'Pipeline Success'})
            )
            logger.info("Sent external task token: success")
        else:
            try:
                sfn.send_task_failure(
                    taskToken=event['externalSfnTaskToken'],
                    **external_task_failure(event["error"])
                )
                logger.info("Sent external task token: error")
            except Exception as e:
                # A token already reported on by the handler's own abort, or one that timed out
                # while the job ran, raises here. The failure is recorded either way, so reporting
                # it twice is not worth failing this state for.
                logger.error(f"Failed to send external task token failure: {e}")

    return event
