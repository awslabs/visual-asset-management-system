#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="EndPipelineVisualizer")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)


def lambda_handler(event, context):
    """
    ClosePipeline
    Does any final closeouts of the pipeline
    """

    logger.info(f"Event Input: {event}")
    logger.info(f"Context Input: {context}")

    # TODO: Get if any StateMachine Step errors occured and update to not always return success
    if ('externalSfnTaskToken' in event):
        logger.info(f"External Sfn Task Token: {event['externalSfnTaskToken']}")
        sfn.send_task_success(
            taskToken=event['externalSfnTaskToken'],
            output='Visualizer Pipeline Success'
        )

    return event
