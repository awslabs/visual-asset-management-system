#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from customLogging.logger import safeLogger

logger = safeLogger(service="EndCosmosReasonPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)


def lambda_handler(event, context):
    """
    ClosePipeline
    Do any final closeouts of the Cosmos Reason pipeline.
    Sends task success or failure to external workflow if task token is present.
    """

    logger.info(f"Event Input: {event}")
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = event.get('externalSfnTaskToken', "")
    status = event.get('status', '')

    # Check for errors
    has_error = False
    if "error" in event:
        has_error = True
        logger.error("Pipeline Failure")
        logger.error(event["error"])
    elif status == "FAILED":
        has_error = True
        logger.error("Pipeline Failure (status=FAILED)")
    else:
        logger.info("Pipeline Success")

    # Report back to external workflow if task token present
    if externalSfnTaskToken and externalSfnTaskToken != "":
        logger.info(f"External Sfn Task Token: {externalSfnTaskToken}")

        if not has_error:
            sfn.send_task_success(
                taskToken=externalSfnTaskToken,
                output=json.dumps({'status': 'Pipeline Success'})
            )
        else:
            error_info = event.get("error", {})
            error_msg = error_info.get("Error", "Unknown error")
            error_cause = error_info.get("Cause", "See AWS CloudWatch logs for error cause.")
            # Truncate cause to SFN limit (256 chars)
            sfn.send_task_failure(
                taskToken=externalSfnTaskToken,
                error='Pipeline Failure: ' + str(error_msg),
                cause=str(error_cause)[:256]
            )

    return event
