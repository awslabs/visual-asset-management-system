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

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)


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

    if not externalSfnTaskToken:
        logger.warning("No external task token present; no workflow callback to send")
        return event

    if error:
        error_info = error if isinstance(error, dict) else {"Error": "PipelineFailure", "Cause": str(error)}
        logger.error("Pipeline Failure")
        logger.error(error)
        sfn.send_task_failure(
            taskToken=externalSfnTaskToken,
            error=str(error_info.get("Error", "PipelineFailure"))[:256],
            cause=str(error_info.get("Cause", "See AWS CloudWatch logs for error cause."))[:32000],
        )
    else:
        logger.info("Pipeline Success")
        sfn.send_task_success(
            taskToken=externalSfnTaskToken,
            output=json.dumps({'status': 'Pipeline Success'})
        )

    return event
