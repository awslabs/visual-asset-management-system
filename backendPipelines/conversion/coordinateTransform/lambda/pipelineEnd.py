# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from customLogging.logger import safeLogger

logger = safeLogger(service="EndPipeline-CoordinateTransform")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
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
            sfn.send_task_failure(
                taskToken=externalSfnTaskToken,
                error='Pipeline Failure: ' + event["error"].get("Error", "Unknown"),
                cause='See AWS cloudwatch logs for error cause.'
            )
            logger.info("Sent external task token: error")

    return event
