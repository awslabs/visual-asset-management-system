# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os

import boto3
from botocore.exceptions import ClientError

from ..logging import log
from ..pipeline.objects import PipelineExecutionParams

logger = log.get_logger()

task_token = os.getenv("TASK_TOKEN")

client = boto3.client(
    "stepfunctions", region_name=os.getenv("AWS_REGION", "us-east-1")
)


def send_task_success(output: PipelineExecutionParams) -> None:
    """Send task success callback to the internal pipeline state machine."""
    if not task_token:
        logger.warning("No TASK_TOKEN set, skipping send_task_success")
        return
    logger.info("Sending Task Success")
    try:
        client.send_task_success(
            taskToken=task_token,
            output=output.to_json(),
        )
    except ClientError as e:
        logger.exception(e)
        raise


def send_task_failure(error_message: str = "") -> None:
    """Send task failure callback to the internal pipeline state machine."""
    if not task_token:
        logger.warning("No TASK_TOKEN set, skipping send_task_failure")
        return
    logger.error(f"Sending Task Failure: {error_message}")
    try:
        client.send_task_failure(
            taskToken=task_token,
            error="Pipeline Failure: " + error_message,
            cause="See AWS CloudWatch logs for full error log.",
        )
    except ClientError as e:
        logger.exception(e)
        raise


def send_task_heartbeat(external_token: str) -> None:
    """Send heartbeat to the external VAMS workflow task token."""
    if external_token:
        try:
            logger.info("Sending Task Heartbeat")
            client.send_task_heartbeat(taskToken=external_token)
        except Exception as e:
            logger.exception(e)
