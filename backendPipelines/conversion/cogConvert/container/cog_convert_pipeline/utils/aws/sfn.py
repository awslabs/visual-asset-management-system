# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os

import boto3
from botocore.exceptions import ClientError, ParamValidationError

from ..logging import log
from ..pipeline.objects import PipelineExecutionParams
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = log.get_logger()

task_token = os.getenv("TASK_TOKEN")

client = boto3.client(
    "stepfunctions", region_name=os.getenv("AWS_REGION", "us-east-1"),
    config=retry_config
)


# AWS Step Functions bounds a task failure: `error` at 256 characters and `cause` at 32,768. A call over either bound
# is rejected with a ValidationException and the failure it was reporting is lost, which leaves the workflow's
# task waiting for its full timeout. The machine-readable name stays short and the human text goes in `cause`.
ERROR_NAME_LIMIT = 256
CAUSE_LIMIT = 32768
DEFAULT_ERROR_NAME = "CogConvertFailed"
FALLBACK_CAUSE = "The conversion failed; the failure message could not be sent. See the container's CloudWatch logs."


def bounded_error(name: str) -> str:
    """A failure name of at most ERROR_NAME_LIMIT characters; never empty."""
    return (str(name or DEFAULT_ERROR_NAME))[:ERROR_NAME_LIMIT]


def bounded_cause(message: str) -> str:
    """The failure text cut to CAUSE_LIMIT characters, with a marker when it was cut; never empty."""
    text = str(message or "Unknown error")
    if len(text) <= CAUSE_LIMIT:
        return text
    marker = " ... [truncated]"
    return text[: CAUSE_LIMIT - len(marker)] + marker


def send_task_success(output: PipelineExecutionParams) -> None:
    """Send task success callback to the internal pipeline state machine.

    A rejected call is logged and retried once with a minimal valid output, so a run that finished is not left
    waiting. When the minimal call is also rejected the error is raised to the caller."""
    if not task_token:
        logger.warning("No TASK_TOKEN set, skipping send_task_success")
        return
    logger.info("Sending Task Success")
    try:
        client.send_task_success(taskToken=task_token, output=output.to_json())
    except (ClientError, ParamValidationError) as e:
        logger.error(f"SendTaskSuccess was rejected ({type(e).__name__}: {e}); retrying with a minimal output")
        client.send_task_success(
            taskToken=task_token,
            output=json.dumps({"jobName": output.jobName, "status": "COMPLETE", "definition": []}))


def send_task_failure(error_message: str = "", error_name: str = DEFAULT_ERROR_NAME) -> None:
    """Send task failure callback to the internal pipeline state machine.

    `error_name` is a short machine-readable name; `error_message` is the text a person reads and travels in `cause`.
    A rejected call is logged with its reason and retried once with a minimal valid payload, so the failure is
    never dropped silently. When the minimal call is also rejected the error is raised to the caller."""
    if not task_token:
        logger.warning("No TASK_TOKEN set, skipping send_task_failure")
        return
    logger.error(f"Sending Task Failure: {error_name}: {error_message}")
    try:
        client.send_task_failure(
            taskToken=task_token, error=bounded_error(error_name), cause=bounded_cause(error_message))
    except (ClientError, ParamValidationError) as e:
        logger.error(f"SendTaskFailure was rejected ({type(e).__name__}: {e}); retrying with a minimal payload")
        client.send_task_failure(taskToken=task_token, error=DEFAULT_ERROR_NAME, cause=FALLBACK_CAUSE)


def send_task_heartbeat(external_token: str) -> None:
    """Send heartbeat to the external VAMS workflow task token."""
    if external_token:
        try:
            logger.info("Sending Task Heartbeat")
            client.send_task_heartbeat(taskToken=external_token)
        except Exception as e:
            logger.exception(e)
