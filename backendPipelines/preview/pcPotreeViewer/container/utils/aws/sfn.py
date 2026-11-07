# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os

import boto3
from botocore.exceptions import ClientError
from ..logging import log
from ...utils.pipeline.objects import PipelineExecutionParams
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
    logger.info("Sending Task Success")
    try:
        return client.send_task_success(
            taskToken=task_token,
            output=output.to_json(),
        )
    except ClientError as e:
        logger.exception(e)
        raise


# SendTaskFailure caps `error` at 256 characters and `cause` at 32,768; a longer value is rejected
# with a ValidationException, the failure is never reported and the step waits out its timeout.
TASK_FAILURE_ERROR_MAX_CHARS = 256
TASK_FAILURE_CAUSE_MAX_CHARS = 32768
TASK_FAILURE_CAUSE_SUFFIX = 'See AWS cloudwatch logs for full error log and cause.'


def _bounded(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 3] + '...'


def send_task_failure(errorMessage: str = ''):
    logger.error("Sending Task Failure")
    errorMessage = errorMessage or ''
    # The full message also goes in `cause`, so the part cut from `error` is still recorded.
    cause = f"{errorMessage} {TASK_FAILURE_CAUSE_SUFFIX}" if errorMessage else TASK_FAILURE_CAUSE_SUFFIX
    try:
        return client.send_task_failure(
            taskToken=task_token,
            error=_bounded('Pipeline Failure: ' + errorMessage, TASK_FAILURE_ERROR_MAX_CHARS),
            cause=_bounded(cause, TASK_FAILURE_CAUSE_MAX_CHARS)
        )
    except ClientError as e:
        logger.exception(e)
        raise

def send_external_task_heartbeat(externalSfnTaskToken: str):
    if externalSfnTaskToken:
        try:
            logger.info("Sending External Task Heartbeat")
            return client.send_task_heartbeat(
                taskToken=externalSfnTaskToken,
            )
        except Exception as e:
            logger.exception(e)
            #Don't raise error further, just fail silently if these fail.
