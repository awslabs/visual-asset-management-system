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

s3 = boto3.client(
    's3',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

# constructPipeline.STAGED_INPUT_PREFIX: where a non-current input version is copied for the
# handler. Only an object under it is ever deleted here.
STAGED_INPUT_PREFIX = "pipelines/modelops.staged-inputs/"


def delete_staged_input(staged_input_uri):
    """Delete the run's staged input copy, every version of it, so no copy of the customer's file
    outlives the run. The auxiliary bucket is versioned, so a plain delete would only add a delete
    marker. Anything outside the staged-inputs prefix is left alone, and a failure is logged rather
    than raised: the run's outcome is already decided, and the prefix's lifecycle rule expires what
    is left."""
    if not staged_input_uri:
        return
    bucket, _, key = (staged_input_uri[len("s3://"):].partition("/")
                      if staged_input_uri.startswith("s3://") else ("", "", ""))
    if not bucket or not key.startswith(STAGED_INPUT_PREFIX) or key == STAGED_INPUT_PREFIX:
        logger.warning(f"Not deleting {staged_input_uri}: it is not a staged input copy")
        return
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket, Prefix=key):
            versions = [entry for entry in page.get("Versions", []) + page.get("DeleteMarkers", [])
                        if entry.get("Key") == key]
            if versions:
                response = s3.delete_objects(Bucket=bucket, Delete={
                    "Objects": [{"Key": key, "VersionId": entry["VersionId"]} for entry in versions],
                    "Quiet": True})
                if response.get("Errors"):
                    raise RuntimeError(f"S3 refused {len(response['Errors'])} version deletes: "
                                       f"{response['Errors'][0].get('Code', '')}")
        logger.info(f"Deleted the staged input copy {staged_input_uri}")
    except Exception as e:
        logger.error(f"Failed to delete the staged input copy {staged_input_uri}: {e}")

def task_token_from_event(event):
    """The VAMS workflow's callback token, read from either shape this state reaches.

    Both routes into this state keep the state input, so the token is normally the field
    constructPipeline emitted: the caught-error route adds the error to it, and a completed ECS task
    puts its result at ``$.ModelOpsTaskResult``. A state machine defined before that ResultPath
    REPLACES the state input with the completed task's description, so the token then survives only
    as the container environment override the task was started with. Reading either shape alone
    leaves the other unable to report the token, and the workflow's task then waits for its full
    taskTimeout.
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

    logger.info("Event Input", event=event)
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = task_token_from_event(event)

    if("error" not in event):
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])

    try:
        if (externalSfnTaskToken != None and externalSfnTaskToken != ""):
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
    finally:
        # After the report, so a slow cleanup never delays the workflow; on both routes, since the
        # state input carries constructPipeline's output either way.
        delete_staged_input(event.get("stagedInputS3Uri", ""))

    return event
