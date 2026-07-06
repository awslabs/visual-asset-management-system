#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams. 
"""
import os
import boto3
import json
from customLogging.logger import safeLogger
import manifestHelper


logger = safeLogger(service="VamsExecuteSplatToolboxPipeline")
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path, output_s3_asset_metadata_path
                                        , inputOutput_s3_assetAuxiliary_files_path, input_metadata_s3_location, input_configuration_s3_location, external_task_token
                                        , executing_userName, executing_requestContext, orchestration_event_prefix=""):

    # Create the object message to be sent
    messagePayload = {
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "orchestrationEventPrefix": orchestration_event_prefix
    }

    # Invoke the pipeline construct pipeline lambda
    logger.info("Invoking Asset Lambda .........")
    lambda_response = lambda_client.invoke(FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
                                        InvocationType='RequestResponse',
                                        Payload=json.dumps(messagePayload).encode('utf-8'))
    logger.info("lambda response")
    logger.info(lambda_response)
    logger.info("Invoke Open Pipeline Lambda Successfully.")

    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)


def lambda_handler(event, context):
    logger.info(event)

    try:
        # Parse request body (same pattern as rapidPipeline)
        if not event.get('body'):
            message = 'Request body is required'
            logger.error(message)
            return {
                'statusCode': 400,
                'body': json.dumps({"message": message})
            }

        if isinstance(event['body'], str):
            data = json.loads(event['body'])
        else:
            data = event['body']

        # Get external task token if passed
        if 'TaskToken' in data:
            external_task_token = data['TaskToken']
        else:
            raise Exception("VAMS Workflow TaskToken not found in pipeline input. Make sure to register this pipeline in VAMS as needing a task token callback.")

        # Get Executing username
        executing_userName = data.get('executingUserName', '')

        # Get Executing requestContext
        executing_requestContext = data.get('executingRequestContext', '')

        # Resolve input/output locations from the workflow manifest (fallback to payload fields)
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        # Starts execution of pipeline
        execute_pipeline(
            resolved['inputS3AssetFilePath'],
            resolved['outputS3AssetFilesPath'],
            resolved['outputS3AssetPreviewPath'],
            resolved['outputS3AssetMetadataPath'],
            resolved['inputOutputS3AssetAuxiliaryFilesPath'],
            resolved['inputMetadataS3Location'],
            resolved['inputConfigurationS3Location'],
            external_task_token,
            executing_userName,
            executing_requestContext,
            resolved['orchestrationEventPrefix']
        )

        return {
            'statusCode': 200,
            'body': 'Success'
        }
    except Exception as e:
        logger.exception(e)
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "Internal Server Error"})
        }
