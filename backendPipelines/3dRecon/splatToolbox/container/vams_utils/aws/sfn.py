# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os

import boto3
from botocore.exceptions import ClientError
from vams_utils.logging import log
from vams_utils.pipeline.objects import PipelineExecutionParams
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = log.get_logger()

task_token = os.getenv("TASK_TOKEN")

client = boto3.client(
    "stepfunctions", region_name=os.getenv("AWS_REGION", "us-east-1"), config=retry_config)


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
