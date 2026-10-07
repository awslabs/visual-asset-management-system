#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

logger = safeLogger(service="SystemGenAiMetadataPipelineEnd")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)


def lambda_handler(event, context):
    """
    PipelineEnd
    Reports the parent workflow's task token: success unless the machine recorded an error.
    A caught Bedrock failure is recorded through execution.status.json and is not an error here.
    """

    # Identifiers only: the state carries externalSfnTaskToken, so it is never rendered whole.
    logger.info("Event Input", jobName=event.get("jobName", ""), assetId=event.get("assetId", ""),
                workflowExecutionId=event.get("workflowExecutionId", ""),
                analysisStatus=event.get("analysisStatus", ""), hasError="error" in event,
                eventKeys=sorted(event))
    logger.info(f"Context Input: {context}")

    external_sfn_task_token = event.get('externalSfnTaskToken', "")

    if "error" not in event:
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])

    if external_sfn_task_token is not None and external_sfn_task_token != "":
        if "error" not in event:
            sfn.send_task_success(
                taskToken=external_sfn_task_token,
                output=json.dumps({'status': 'Pipeline Success'})
            )
        else:
            sfn.send_task_failure(
                taskToken=external_sfn_task_token,
                error='Pipeline Failure: ' + event["error"]["Error"],
                cause='See AWS cloudwatch logs for error cause.'
            )

    return event
