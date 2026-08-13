#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="OpenCosmosTransferPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)
events_client = boto3.client(
    'events',
    region_name=os.environ["AWS_REGION"]
)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.avi")
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
    Starts StepFunctions State Machine for processing Cosmos Transfer pipeline.
    Validates input: Transfer always requires a valid source video file.
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    responses = []

    # Get the input metadata + input-configuration S3 locations
    input_metadata_s3_location = event.get('inputMetadataS3Location', '')
    input_configuration_s3_location = event.get('inputConfigurationS3Location', '')

    # Orchestration event prefix for optional sub-process registration
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')

    # Get any given additional outer/external task token to report back to
    external_sfn_task_token = event.get('sfnExternalTaskToken', '')

    input_s3_asset_files_uri = event.get('inputS3AssetFilePath', '')
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    cosmos_prompt = event.get('cosmosPrompt', '')
    control_type = event.get('controlType', 'edge')
    control_path = event.get('controlPath', '')
    asset_id = event.get('assetId', '')
    database_id = event.get('databaseId', '')

    # Transfer always requires a valid input file (source video)
    if not input_s3_asset_files_uri:
        abort_external_workflow("Input S3 URI is required for transfer", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Input S3 URI is required for transfer"
            }
        }

    # Folder check
    if input_s3_asset_files_uri.endswith("/"):
        abort_external_workflow("Input S3 URI cannot be a folder for this pipeline", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Input S3 URI cannot be a folder"
            }
        }

    # Extract extension from the input key
    file_parts = input_s3_asset_files_uri.split('.')
    if len(file_parts) > 1:
        extension = '.' + file_parts[-1].lower()
    else:
        extension = ''

    logger.info(f"Checking for valid file extension: {extension}")
    # Check to make sure we are working with the right file types
    allowed_extensions = [ext.strip() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',')]
    if not extension or extension not in allowed_extensions:
        abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": f"Pipeline cannot process file type provided. Allowed: {ALLOWED_INPUT_FILEEXTENSIONS}"
            }
        }

    # Generate unique execution name
    job_name = f"cosmos-transfer-{control_type}-{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # StateMachine Execution Input
    sfn_input = {
        "jobName": job_name,
        "modelType": "transfer",
        "cosmosPrompt": cosmos_prompt,
        "controlType": control_type,
        "controlPath": control_path,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token
    }

    try:
        # Start the Step Functions state machine
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

        # response datetime not JSON serializable
        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')

        responses.append({
            'statusCode': 200,
            'body': {
                "message": "Starting Cosmos Transfer Pipeline State Machine",
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

    # Loop through responses and see if any have errors; If so return error response
    for response in responses:
        if "error" in response['body']:
            return response

    # Return success 200 response
    return {
        'statusCode': 200,
        'body': {
            "message": "Starting Cosmos Transfer Pipeline State Machine",
            "execution": sfn_response
        }
    }
