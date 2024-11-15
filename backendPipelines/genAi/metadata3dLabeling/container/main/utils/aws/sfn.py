# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from botocore.exceptions import ClientError
from ..logging import log
from ...utils.pipeline.objects import PipelineExecutionParams

logger = log.get_logger()

task_token = os.getenv("TASK_TOKEN")
client = boto3.client(
    "stepfunctions", region_name=os.getenv("AWS_REGION", "us-east-1"))


def send_task_success(output: PipelineExecutionParams):
    logger.info(f"Sending Task Success. Token: {task_token}")
    try:
        return client.send_task_success(
            taskToken=task_token,
            output=output.to_json(),
        )
    except ClientError as e:
        logger.exception(e)
        raise


def send_task_failure(errorMessage: str = ''):
    logger.error(f"Sending Task Failure. Token: {task_token}")
    try:
        return client.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: '+errorMessage,
            cause='See AWS cloudwatch logs for full error log and cause.'
        )
    except ClientError as e:
        logger.exception(e)
        raise

def send_external_task_heartbeat(externalSfnTaskToken: str):
    if externalSfnTaskToken:
        try:
            logger.info(f"Sending External Task Heartbeat. Token: {externalSfnTaskToken}")
            return client.send_task_heartbeat(
                taskToken=externalSfnTaskToken,
            )
        except Exception as e:
            logger.exception(e)
            #Don't raise error further, just fail silently if these fail.
