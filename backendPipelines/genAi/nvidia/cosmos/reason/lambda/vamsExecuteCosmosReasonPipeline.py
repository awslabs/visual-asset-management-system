#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
This handler executes the Cosmos Reason pipeline by extracting the COSMOS_REASON_PROMPT
from file metadata and invoking the openPipeline Lambda.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger


logger = safeLogger(service="VamsExecuteCosmosReasonPipeline")
lambda_client = boto3.client('lambda')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path,
                      output_s3_asset_metadata_path, inputOutput_s3_assetAuxiliary_files_path, input_metadata,
                      input_parameters, external_task_token, executing_userName, executing_requestContext,
                      asset_id, database_id, cosmos_prompt):
    """
    Execute the Cosmos Reason pipeline by invoking the openPipeline Lambda.
    Reason requires an input file path (video or image file) to analyze.
    """

    # Create the object message to be sent
    messagePayload = {
        "modelType": "reason",
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "inputMetadata": "",  # Don't forward full metadata - prompt already extracted above
        "inputParameters": input_parameters,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "assetId": asset_id,
        "databaseId": database_id,
        "cosmosPrompt": cosmos_prompt
    }

    # Invoke the pipeline open pipeline lambda
    logger.info("Invoking openPipeline Lambda .........")
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

    external_task_token = None

    try:
        # Parse request body
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

        # Get input parameters if defined
        input_parameters = data.get('inputParameters', '')
        logger.info(f"Input parameters received: {input_parameters}")

        # Get input metadata if defined
        input_metadata = data.get('inputMetadata', '')
        logger.info(f"Input metadata received: {input_metadata}")

        # Extract COSMOS_REASON_PROMPT from file metadata
        # VAMS metadata format: {"VAMS": {"assetMetadata": {...}, "fileMetadata": {"key": "value", ...}}}
        cosmos_prompt = ""
        if input_metadata:
            try:
                metadata_obj = json.loads(input_metadata) if isinstance(input_metadata, str) else input_metadata
                vams_metadata = metadata_obj.get("VAMS", {})
                file_metadata = vams_metadata.get("fileMetadata", {})

                # fileMetadata is a flat dict of {key: value} pairs
                cosmos_prompt = file_metadata.get("COSMOS_REASON_PROMPT", "")
                if cosmos_prompt:
                    logger.info(f"Extracted COSMOS_REASON_PROMPT from file metadata: {cosmos_prompt}")
            except Exception as e:
                logger.warning(f"Failed to extract COSMOS_REASON_PROMPT from file metadata: {e}")

        # If not found in metadata, try inputParameters as fallback
        if not cosmos_prompt and input_parameters:
            try:
                params_obj = json.loads(input_parameters) if isinstance(input_parameters, str) else input_parameters
                cosmos_prompt = params_obj.get("PROMPT") or params_obj.get("prompt") or ""
                if cosmos_prompt:
                    logger.info(f"Using COSMOS_REASON_PROMPT from input parameters: {cosmos_prompt}")
            except Exception as e:
                logger.warning(f"Failed to extract prompt from input parameters: {e}")

        # Prompt is OPTIONAL for Reason - use a sensible default if not provided
        if not cosmos_prompt:
            cosmos_prompt = "Caption the video in detail."
            logger.info(f"No COSMOS_REASON_PROMPT found - using default prompt: {cosmos_prompt}")

        # Get asset and database IDs
        asset_id = data.get('assetId', '')
        database_id = data.get('databaseId', '')

        # Get Executing username
        executing_userName = data.get('executingUserName', '')

        # Get Executing requestContext
        executing_requestContext = data.get('executingRequestContext', '')

        # Starts execution of pipeline
        execute_pipeline(
            data['inputS3AssetFilePath'],
            data.get('outputS3AssetFilesPath', ''),
            data.get('outputS3AssetPreviewPath', ''),
            data.get('outputS3AssetMetadataPath', ''),
            data['inputOutputS3AssetAuxiliaryFilesPath'],
            input_metadata,
            input_parameters,
            external_task_token,
            executing_userName,
            executing_requestContext,
            asset_id,
            database_id,
            cosmos_prompt
        )

        return {
            'statusCode': 200,
            'body': 'Success'
        }
    except Exception as e:
        logger.exception(e)
        # Send task failure to Step Functions so the workflow fails instead of hanging
        if external_task_token:
            try:
                sfn_client.send_task_failure(
                    taskToken=external_task_token,
                    error="CosmosReasonPipelineError",
                    cause=str(e)[:256]
                )
                logger.info("Sent task failure callback to Step Functions")
            except Exception as sfn_err:
                logger.error(f"Failed to send task failure callback: {sfn_err}")
        return {
            'statusCode': 500,
            'body': json.dumps({"message": str(e)})
        }
