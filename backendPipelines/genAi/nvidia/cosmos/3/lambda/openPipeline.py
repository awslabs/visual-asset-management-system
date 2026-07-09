#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from customLogging.logger import safeLogger

logger = safeLogger(service="OpenCosmos3Pipeline")

sfn = boto3.client('stepfunctions', region_name=os.environ["AWS_REGION"])

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.jpg,.jpeg,.png,.webp")

# Task modes / variants that require an input file
INPUT_FILE_MODES = ("image2video", "video2video")


def abort_external_workflow(error, task_token):
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
    Starts the StepFunctions State Machine for a Cosmos 3 pipeline.
    Validates input based on task mode (input-file modes require a valid file).
    """
    logger.info(f"Event: {event}")

    model_variant = event.get('modelVariant', 'nano')
    task_mode = event.get('taskMode', '')
    input_Metadata = event.get('inputMetadata', '')
    input_Parameters = event.get('inputParameters', '')
    external_sfn_task_token = event.get('sfnExternalTaskToken', '')
    input_s3_asset_files_uri = event.get('inputS3AssetFilePath', '')
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    cosmos_prompt = event.get('cosmosPrompt', '')
    cosmos_negative_prompt = event.get('cosmosNegativePrompt', '')
    cosmos_seed = event.get('cosmosSeed', '')
    cosmos_guidance = event.get('cosmosGuidance', '')
    cosmos_num_frames = event.get('cosmosNumFrames', '')
    cosmos_control_type = event.get('cosmosControlType', '')
    cosmos_control_path = event.get('cosmosControlPath', '')
    cosmos_control_weight = event.get('cosmosControlWeight', '')
    cosmos_control_guidance = event.get('cosmosControlGuidance', '')
    asset_id = event.get('assetId', '')
    database_id = event.get('databaseId', '')

    needs_input = task_mode in INPUT_FILE_MODES or model_variant == "super-image2video"

    if needs_input:
        if not input_s3_asset_files_uri:
            abort_external_workflow("Input S3 URI is required for this mode", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": "Input S3 URI is required for this mode"}}
        if input_s3_asset_files_uri.endswith("/"):
            abort_external_workflow("Input S3 URI cannot be a folder", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": "Input S3 URI cannot be a folder"}}
        file_parts = input_s3_asset_files_uri.split('.')
        extension = ('.' + file_parts[-1].lower()) if len(file_parts) > 1 else ''
        allowed_extensions = [ext.strip() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',')]
        if not extension or extension not in allowed_extensions:
            abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": f"Pipeline cannot process file type provided. Allowed: {ALLOWED_INPUT_FILEEXTENSIONS}"}}
    else:
        # Text-input modes require a prompt
        if not cosmos_prompt:
            abort_external_workflow("Cosmos prompt is required for this mode", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": "Cosmos prompt is required for this mode"}}

    job_name = f"cosmos3-{model_variant}-{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    sfn_input = {
        "jobName": job_name,
        "modelVariant": model_variant,
        "taskMode": task_mode,
        "cosmosPrompt": cosmos_prompt,
        "cosmosNegativePrompt": cosmos_negative_prompt,
        "cosmosSeed": cosmos_seed,
        "cosmosGuidance": cosmos_guidance,
        "cosmosNumFrames": cosmos_num_frames,
        "cosmosControlType": cosmos_control_type,
        "cosmosControlPath": cosmos_control_path,
        "cosmosControlWeight": cosmos_control_weight,
        "cosmosControlGuidance": cosmos_control_guidance,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadata": input_Metadata,
        "inputParameters": input_Parameters,
        "externalSfnTaskToken": external_sfn_task_token,
    }

    try:
        logger.info(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN, name=job_name, input=json.dumps(sfn_input)
        )
        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')
    except Exception as e:
        logger.exception(e)
        abort_external_workflow("Internal Server Error", external_sfn_task_token)
        return {'statusCode': 500, 'body': {"message": "Internal Server Error"}}

    return {
        'statusCode': 200,
        'body': {"message": "Starting Cosmos 3 Pipeline State Machine", "execution": sfn_response},
    }
