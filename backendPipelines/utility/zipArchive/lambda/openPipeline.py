# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenPipeline - zipArchive. Starts the zipArchive pipeline's internal state machine."""

import datetime
import json
import os
import uuid

import boto3
from botocore.config import Config

import manifestHelper
from customLogging.logger import safeLogger

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenPipeline-ZipArchive")
sfn = boto3.client('stepfunctions', region_name=os.environ["AWS_REGION"], config=retry_config)
events_client = boto3.client('events', config=retry_config)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"
PASSED_THROUGH = ("mode", "runManifestS3Location", "inputConfigurationS3Location",
                  "outputS3AssetFilesPath", "outputBucketRegion", "inputBucketRegion", "assetId",
                  "databaseId", "workflowExecutionId", "orchestrationEventPrefix", "zipDownload")


def abort_external_workflow(error, task_token):
    if task_token:
        logger.error("Aborting external task")
        try:
            sfn.send_task_failure(taskToken=task_token, error='Pipeline Failure: ' + error,
                                  cause='See AWS cloudwatch logs for error cause.')
        except Exception as e:
            logger.error(f"Failed to send task failure callback: {e}")


def register_sub_execution(orchestration_event_prefix, sub_execution_arn):
    """Best-effort report of this sub-SFN execution to the VAMS orchestration bus."""
    if not ORCHESTRATION_BUS_NAME or not orchestration_event_prefix:
        return
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(orchestration_event_prefix)
    if not pipeline_execution_id:
        return
    detail = {"pipelineExecutionId": pipeline_execution_id,
              "subExecution": {"stateMachineArn": STATE_MACHINE_ARN, "executionArn": sub_execution_arn or "",
                               "label": "Zip Archive processing"}}
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        detail["logs"] = [{"logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
                           "logGroupName": STATE_MACHINE_LOG_GROUP_NAME, "logStreamName": "",
                           "sourceType": "stateMachine", "label": "Zip Archive state machine"}]
    try:
        events_client.put_events(Entries=[{"EventBusName": ORCHESTRATION_BUS_NAME,
                                           "Source": orchestration_event_prefix,
                                           "DetailType": REGISTER_DETAIL_TYPE,
                                           "Detail": json.dumps(detail)}])
    except Exception as e:  # nosec B110 - registration is best-effort; never fail the pipeline
        logger.warning(f"Sub-process registration failed (non-critical): {e}")


def lambda_handler(event, context):
    logger.info("Event", event=event)
    external_sfn_task_token = event.get('sfnExternalTaskToken', '')
    if event.get("mode") not in ("zipDownload", "archiveRun"):
        abort_external_workflow("Unknown zipArchive mode", external_sfn_task_token)
        return {'statusCode': 400, 'body': {"message": "Unknown zipArchive mode"}}
    job_name = f"ZipArchive_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{uuid.uuid4().hex[:8]}"
    sfn_input = {k: event[k] for k in PASSED_THROUGH if k in event}
    sfn_input.update(jobName=job_name, externalSfnTaskToken=external_sfn_task_token)
    try:
        response = sfn.start_execution(stateMachineArn=STATE_MACHINE_ARN, name=job_name,
                                       input=json.dumps(sfn_input))
        register_sub_execution(event.get('orchestrationEventPrefix', ''), response.get("executionArn", ""))
        response["startDate"] = response["startDate"].strftime('%m-%d-%Y %H:%M:%S')
        return {'statusCode': 200, 'body': {"message": "Starting Zip Archive Pipeline", "execution": response}}
    except Exception as e:
        logger.exception(e)
        abort_external_workflow("Internal Server Error", external_sfn_task_token)
        return {'statusCode': 500, 'body': {"message": "Internal Server Error"}}
