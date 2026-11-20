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

logger = safeLogger(service="EndPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

MAX_CAUSE_CHARS = 256


def failure_cause(error):
    """The text a failed run reports to the workflow: the error's message when the state machine's
    catch carried one (the Lambda attempt's error, the selector's refusal, the Batch job's status
    reason), cut to what Step Functions accepts."""
    cause = (error or {}).get("Cause") if isinstance(error, dict) else None
    if isinstance(cause, str) and cause.strip():
        try:
            parsed = json.loads(cause)
            message = parsed.get("errorMessage") if isinstance(parsed, dict) else None
            cause = message if isinstance(message, str) and message else cause
        except ValueError:
            pass
        return cause[:MAX_CAUSE_CHARS]
    return 'See AWS cloudwatch logs for error cause.'


def success_output(event):
    """The task output of a successful run. A Lambda attempt that converted but left referenced files
    out answers a message in place of ``Success``; it travels as ``message``, as a preview run that
    produced nothing reports its reason."""
    output = {'status': 'Pipeline Success'}
    attempt = event.get('attempt') if isinstance(event.get('attempt'), dict) else {}
    payload = attempt.get('Payload') if isinstance(attempt.get('Payload'), dict) else {}
    body = payload.get('body')
    if isinstance(body, str) and body and body != 'Success' and payload.get('escalate') is not True:
        output['message'] = body[:2000]
    return output


def lambda_handler(event, context):
    """
    ClosePipeline
    Do any final closeouts of the pipeline
    """

    logger.info("Event Input", event=event)
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = event.get('externalSfnTaskToken', "")

    if("error" not in event):
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])

    if (externalSfnTaskToken != None and externalSfnTaskToken != ""):
        if("error" not in event):
            sfn.send_task_success(
                taskToken=event['externalSfnTaskToken'],
                output=json.dumps(success_output(event))
            )
            logger.info("Sent external task token: success")
        else:
            error = event["error"]
            sfn.send_task_failure(
                taskToken=event['externalSfnTaskToken'],
                error='Pipeline Failure: ' + str(error.get("Error", "Error"))[:200],
                cause=failure_cause(error)
            )
            logger.info("Sent external task token: error")

    return event
