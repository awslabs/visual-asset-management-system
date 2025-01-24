#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from customLogging.logger import safeLogger

logger = safeLogger(service="EndPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)

def lambda_handler(event, context):
    """
    ClosePipeline
    Do any final closeouts of the pipeline
    """

    logger.info(f"Event Input: {event}")
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = event['Overrides']['ContainerOverrides'][0]['Environment'][0]['Value']

    if("error" not in event):
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])

    if (externalSfnTaskToken != None and externalSfnTaskToken != ""):
        logger.info(f"External Sfn Task Token: {externalSfnTaskToken}")

        if("error" not in event):
            sfn.send_task_success(
                taskToken=externalSfnTaskToken,
                output=json.dumps({'status': 'Pipeline Success'})
            )
        else:
            sfn.send_task_failure(
                taskToken=externalSfnTaskToken,
                error='Pipeline Failure: ' + event["error"]["Error"],
                cause='See AWS cloudwatch logs for error cause.'
            )

    return event
