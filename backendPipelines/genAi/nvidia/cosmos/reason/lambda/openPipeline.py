#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from customLogging.logger import safeLogger

logger = safeLogger(service="OpenCosmosReasonPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.jpg,.jpeg,.png,.webp")


def abort_external_workflow(error, task_token):
    """Abort external workflow by sending task failure"""
    if task_token and task_token != "":
        logger.error(f"Aborting external task: {task_token}")
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )


def lambda_handler(event, context):
    """
    OpenPipeline
    Starts StepFunctions State Machine for processing Cosmos Reason pipeline.
    Validates input file extension (video/image types required).
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    responses = []

    # Get any given additional inputMetadata
    input_Metadata = event.get('inputMetadata', '')

    # Get any given additional inputParameters
    input_Parameters = event.get('inputParameters', '')

    # Get any given additional outer/external task token to report back to
    external_sfn_task_token = event.get('sfnExternalTaskToken', '')

    input_s3_asset_files_uri = event.get('inputS3AssetFilePath', '')
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    cosmos_prompt = event.get('cosmosPrompt', '')
    asset_id = event.get('assetId', '')
    database_id = event.get('databaseId', '')

    # Reason always requires an input file (video or image)
    if not input_s3_asset_files_uri:
        abort_external_workflow("Input S3 URI is required for Cosmos Reason", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Input S3 URI is required for Cosmos Reason"
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
    job_name = f"cosmos-reason-{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # StateMachine Execution Input
    sfn_input = {
        "jobName": job_name,
        "modelType": "reason",
        "cosmosPrompt": cosmos_prompt,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadata": input_Metadata,
        "inputParameters": input_Parameters,
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

        # response datetime not JSON serializable
        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')

        responses.append({
            'statusCode': 200,
            'body': {
                "message": "Starting Cosmos Reason Pipeline State Machine",
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
            "message": "Starting Cosmos Reason Pipeline State Machine",
            "execution": sfn_response
        }
    }
