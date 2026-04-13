#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from customLogging.logger import safeLogger

logger = safeLogger(service="OpenGr00tFinetunePipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]


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
    input_metadata = event.get('inputMetadata', '')
    input_parameters = event.get('inputParameters', '')
    external_sfn_task_token = event.get('sfnExternalTaskToken', '')

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
    job_name = f"gr00t-finetune-{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    sfn_input = {
        "jobName": job_name,
        "inputS3AssetPath": input_s3_asset_path,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadata": input_metadata,
        "inputParameters": input_parameters,
        "gr00tConfig": groot_config,
        "externalSfnTaskToken": external_sfn_task_token
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
