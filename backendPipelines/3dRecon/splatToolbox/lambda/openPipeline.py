#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import uuid
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="OpenPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)
events_client = boto3.client(
    'events',
    region_name=os.environ["AWS_REGION"]
)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]
# Orchestration bus + state-machine log group for optional sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

def abort_external_workflow(error, task_token):
    if (task_token != None and task_token != ""):
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
    Starts StepFunctions State Machine for processing 
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    responses = []

    # Get the input metadata + input-configuration S3 locations
    input_metadata_s3_location = event.get('inputMetadataS3Location', '')
    input_configuration_s3_location = event.get('inputConfigurationS3Location', '')

    # Orchestration event prefix for optional sub-process registration
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')

    # Get any given additional outer/external task token to report back to (when using this pipeline as part of another state machine)
    if ('sfnExternalTaskToken' in event):
        external_sfn_task_token = event['sfnExternalTaskToken']
    else:
        external_sfn_task_token = ''

    input_s3_asset_files_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    #Folder check
    if (input_s3_asset_files_uri.endswith("/")):
        abort_external_workflow("Input S3 URI cannot be a folder for this pipeline", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Input S3 URI cannot be a folder"
            }
        }

    # Extract the root name and extension from the input key
    file_root, extension = os.path.splitext(input_s3_asset_files_uri)

    logger.info(f"Checking for valid file")
    # Check to make sure we are working with the right file types (if not, exit)
    if (not extension or extension == '' or extension.lower() not in ALLOWED_INPUT_FILEEXTENSIONS):
        abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Pipeline cannot process file type provided"
            }
        }

    # Generate new job name
    job_name = f"PipelineJob_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{uuid.uuid4().hex[:8]}"

    # StateMachine Execution Input
    sfn_input = {
        "jobName": job_name,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token
    }

    try:
        # Start the Step Functions state machine with the bucket key and name
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
                "message": "Starting Asset Processing State Machine",
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

    # Loop through responses and see if any have errors; If so return 500 error response
    for response in responses:
        if "error" in response['body']:
            return response

    # Return success 200 response
    return {
        'statusCode': 200,
        'body': {
            "message": "Starting Asset Processing State Machine",
            "execution": sfn_response
        }
    }
