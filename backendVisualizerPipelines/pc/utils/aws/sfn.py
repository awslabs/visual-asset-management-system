# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
        logger.error(e)
        raise


def send_task_failure(output: PipelineExecutionParams):
    logger.error(f"Sending Task Failure. Token: {task_token}")
    try:
        return client.send_task_failure(
            taskToken=task_token,
            output=output.to_json(),
        )
    except ClientError as e:
        logger.error(e)
        raise
