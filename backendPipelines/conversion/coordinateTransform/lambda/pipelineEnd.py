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


def lambda_handler(event, context):
    """
    PipelineEnd - Coordinate Transform
    Handles final pipeline closeout and external task token callbacks.
    """

    logger.info(f"Event: {event}")

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
