# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Lambda to execute the Coordinate Transform pipeline from VAMS workflows.
Note: Lambda function name must start with "vams" to allow invoke permissioning.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger

logger = safeLogger(service="VamsExecuteCoordinateTransformPipeline")
lambda_client = boto3.client('lambda')
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path,
                     output_s3_asset_preview_path, output_s3_asset_metadata_path,
                     inputOutput_s3_assetAuxiliary_files_path,
                     asset_id, database_id,
                     input_metadata, input_parameters, external_task_token,
                     executing_userName, executing_requestContext):

    messagePayload = {
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadata": input_metadata,
        "inputParameters": input_parameters,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
    }

    logger.info("Invoking Open Pipeline Lambda")
    lambda_response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(messagePayload).encode('utf-8')
    )
    logger.info(f"Lambda response: {lambda_response}")

    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)


def lambda_handler(event, context):
    logger.info(event)

    try:
        if not event.get('body'):
            raise ValueError('Request body is required')

        if isinstance(event['body'], str):
            data = json.loads(event['body'])
        else:
            data = event['body']

        if 'TaskToken' in data:
            external_task_token = data['TaskToken']
        else:
            raise Exception(
                "VAMS Workflow TaskToken not found in pipeline input. "
                "Register this pipeline as needing a task token callback."
            )

        input_parameters = data.get('inputParameters', '')
        input_metadata = data.get('inputMetadata', '')
        executing_userName = data.get('executingUserName', '')
        executing_requestContext = data.get('executingRequestContext', '')

        execute_pipeline(
            data['inputS3AssetFilePath'],
            data['outputS3AssetFilesPath'],
            data.get('outputS3AssetPreviewPath', ''),
            data.get('outputS3AssetMetadataPath', ''),
            data.get('inputOutputS3AssetAuxiliaryFilesPath', ''),
            data.get('assetId', ''),
            data.get('databaseId', ''),
            input_metadata,
            input_parameters,
            external_task_token,
            executing_userName,
            executing_requestContext,
        )

        return {'statusCode': 200, 'body': 'Success'}

    except Exception as e:
        logger.exception(e)
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "Internal Server Error"})
        }
