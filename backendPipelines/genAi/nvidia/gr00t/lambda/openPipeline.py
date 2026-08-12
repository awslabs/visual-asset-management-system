#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="OpenGr00tFinetunePipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)
events_client = boto3.client(
    'events',
    region_name=os.environ["AWS_REGION"]
)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
# Orchestration bus + state-machine log group for optional sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"


def abort_external_workflow(error, task_token):
    """Abort external workflow by sending task failure"""
    if task_token and task_token != "":
        logger.error(f"Aborting external task: {task_token}")
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )


def register_sub_execution(orchestration_bus_name, orchestration_event_prefix,
                           sub_execution_arn, state_machine_arn):
    # Best-effort: report this sub-SFN execution to the orchestration bus; failures are swallowed
    if not orchestration_bus_name or not orchestration_event_prefix:
        logger.info("Orchestration bus/prefix not configured; skipping sub-process registration")
        return
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(
        orchestration_event_prefix)
    if not pipeline_execution_id:
        logger.warning("Could not derive pipelineExecutionId from event prefix; skipping registration")
        return
    detail = {
        "pipelineExecutionId": pipeline_execution_id,
        "subExecution": {
            "stateMachineArn": state_machine_arn or "",
            "executionArn": sub_execution_arn or "",
        },
    }
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        detail["logs"] = [{
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
        }]
    try:
        events_client.put_events(Entries=[{
            "EventBusName": orchestration_bus_name,
            "Source": orchestration_event_prefix,
            "DetailType": REGISTER_DETAIL_TYPE,
            "Detail": json.dumps(detail),
        }])
        logger.info(f"Registered sub-execution for pipeline execution {pipeline_execution_id}")
    except Exception as e:  # nosec B110 - registration is best-effort; never fail the pipeline
        logger.warning(f"Sub-process registration failed (non-critical): {e}")


def lambda_handler(event, context):
    """
    OpenPipeline
    Starts StepFunctions State Machine for Gr00t Fine-Tuning pipeline.
    Asset-level pipeline -- no file extension validation needed.
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    responses = []

    input_s3_asset_path = event.get('inputS3AssetPath', '')
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    groot_config = event.get('gr00tConfig', '{}')
    asset_id = event.get('assetId', '')
    database_id = event.get('databaseId', '')
    input_metadata_s3_location = event.get('inputMetadataS3Location', '')
    input_configuration_s3_location = event.get('inputConfigurationS3Location', '')
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')
    external_sfn_task_token = event.get('sfnExternalTaskToken', '')
    # finetune (default) trains; evaluate scores an existing checkpoint. Forwarded to the state machine
    # so constructPipeline can name the Batch job and the container can branch. This lambda enumerates
    # the fields it forwards, so a new one is silently dropped unless it is added here.
    mode = str(event.get('mode', '') or 'finetune').strip().lower()

    # Validate asset path exists
    if not input_s3_asset_path:
        abort_external_workflow("Input S3 asset path is required", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Input S3 asset path is required for Gr00t fine-tuning"
            }
        }

    # Generate unique execution name
    job_name = (f"gr00t-{'eval' if mode == 'evaluate' else 'finetune'}-"
                f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")

    sfn_input = {
        "jobName": job_name,
        "inputS3AssetPath": input_s3_asset_path,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "gr00tConfig": groot_config,
        "externalSfnTaskToken": external_sfn_task_token,
        "mode": mode
    }

    try:
        logger.info(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
        logger.info(f"SFN Input: {json.dumps(sfn_input)}")

        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_name,
            input=json.dumps(sfn_input)
        )

        logger.info(f"SFN Response: {sfn_response}")

        # Best-effort: register this sub-SFN execution with the VAMS execution
        register_sub_execution(
            ORCHESTRATION_BUS_NAME, orchestration_event_prefix,
            sfn_response.get("executionArn", ""), STATE_MACHINE_ARN)

        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')

        responses.append({
            'statusCode': 200,
            'body': {
                "message": "Starting Gr00t Fine-Tuning Pipeline State Machine",
                "execution": sfn_response
            }
        })
    except Exception as e:
        logger.exception(e)
        abort_external_workflow("Internal Server Error", external_sfn_task_token)
        responses.append({
            'statusCode': 500,
            'body': {
                "message": "Internal Server Error",
            }
        })

    logger.info(f"Responses: {responses}")

    for response in responses:
        if "error" in response['body']:
            return response

    return {
        'statusCode': 200,
        'body': {
            "message": "Starting Gr00t Fine-Tuning Pipeline State Machine",
            "execution": sfn_response
        }
    }
