#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from customLogging.logger import safeLogger
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="EndPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

def task_token_from_event(event):
    """The VAMS workflow's callback token, read from either shape this state reaches.

    The two routes into this state carry the token in different places. A completed ECS task's
    result REPLACES the state input, so the token survives only as the container environment
    override the task was started with. The caught-error route keeps the state input and adds the
    error to it, so the token is the field constructPipeline emitted and no container description is
    present. Reading either shape alone leaves the other route unable to report the token, and the
    workflow's task then waits for its full taskTimeout.
    """
    token = event.get('externalSfnTaskToken', "")
    if token:
        return token
    overrides = (event.get('Overrides') or {}).get('ContainerOverrides') or []
    for container in overrides:
        variables = container.get('Environment') or []
        for variable in variables:
            if variable.get('Name') == 'externalSfnTaskToken':
                return variable.get('Value', "")
        # The task is started with exactly one environment override, so its value is the token even
        # when the name is not carried in the task description.
        if variables:
            return variables[0].get('Value', "")
    return ""


def lambda_handler(event, context):
    """
    ClosePipeline
    Do any final closeouts of the pipeline
    """

    logger.info(f"Event Input: {event}")
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = task_token_from_event(event)

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
            try:
                sfn.send_task_failure(
                    taskToken=externalSfnTaskToken,
                    error='Pipeline Failure: ' + event["error"]["Error"],
                    cause='See AWS cloudwatch logs for error cause.'
                )
            except Exception as e:
                # A token already reported on by the handler's own abort, or one that timed out
                # while the job ran, raises here. The failure is recorded either way, so reporting
                # it twice is not worth failing this state for.
                logger.error(f"Failed to send external task token failure: {e}")

    return event
