#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from customLogging.logger import safeLogger

logger = safeLogger(service="OpenPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]

def abort_external_workflow(error, task_token):
    if (task_token != None and task_token != ""):
        logger.error(f"Aborting external task: {task_token}")
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )

def lambda_handler(event, context):
    """
    OpenPipeline
    Starts StepFunctions State Machine for processing 
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    responses = []

    # Get any given additional inputMetadata
    if ('inputMetadata' in event):
        input_Metadata = event['inputMetadata']
    else:
        input_Metadata = ''

    # Get any given additional inputParameters
    if ('inputParameters' in event):
        input_Parameters = event['inputParameters']
    else:
        input_Parameters = ''

    # Get any given additional outer/external task token to report back to (when using this pipeline as part of another state machine)
    if ('sfnExternalTaskToken' in event):
        external_sfn_task_token = event['sfnExternalTaskToken']
    else:
        external_sfn_task_token = ''

    input_s3_asset_files_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath']
    output_s3_asset_preview_uri = event['outputS3AssetPreviewPath']
    output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath']
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
    job_name = f"PipelineJob_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # StateMachine Execution Input
    sfn_input = {
        "jobName": job_name,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "inputMetadata": input_Metadata,
        "inputParameters": input_Parameters,
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
