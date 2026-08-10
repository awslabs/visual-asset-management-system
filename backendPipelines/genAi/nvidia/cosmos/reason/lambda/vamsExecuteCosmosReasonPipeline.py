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
import manifestHelper


logger = safeLogger(service="VamsExecuteCosmosReasonPipeline")
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path,
                      output_s3_asset_metadata_path, inputOutput_s3_assetAuxiliary_files_path,
                      input_metadata_s3_location, input_configuration_s3_location, external_task_token,
                      executing_userName, executing_requestContext, asset_id, database_id, cosmos_prompt,
                      orchestration_event_prefix=""):
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
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "assetId": asset_id,
        "databaseId": database_id,
        "cosmosPrompt": cosmos_prompt,
        "orchestrationEventPrefix": orchestration_event_prefix
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

        # Resolve input/output locations from the workflow manifest (fallback to payload fields)
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        # Read metadata + input-configuration content from S3 (inline fallback for transition). The
        # metadata file is the grouped-by-asset envelope, projected onto the legacy {"VAMS": {...}}
        # view for this run's subject that the scopes below read (manifestHelper.run_vams_view).
        input_metadata = manifestHelper.run_vams_view(
            manifestHelper.fetch_metadata(s3_client, resolved['inputMetadataS3Location']), resolved) \
            or data.get('inputMetadata', '')
        input_parameters = manifestHelper.fetch_input_configuration(s3_client, resolved['inputConfigurationS3Location']) \
            or data.get('inputParameters', '')

        # Extract COSMOS_REASON_PROMPT from file metadata
        # VAMS metadata format: {"VAMS": {"assetMetadata": {...}, "fileMetadata": {"key": "value", ...}}}
        cosmos_prompt = ""
        # CONFIG-FIRST with a metadata fallback (manifestHelper.resolve_input_setting): the prompt
        # supplied on the execute screen as a template dynamic tag wins; a blank field falls back to a
        # standing value saved on the asset. This pipeline reads one file per run, so per-FILE metadata
        # is honoured first, then the asset's.
        cosmos_prompt = manifestHelper.resolve_input_setting(
            input_parameters, input_metadata, ("PROMPT", "prompt"), "COSMOS_REASON_PROMPT",
            metadata_scopes=("fileMetadata", "assetMetadata"))
        if cosmos_prompt:
            logger.info(f"Resolved COSMOS_REASON_PROMPT: {cosmos_prompt}")

        # Prompt is OPTIONAL for Reason - use a sensible default if not provided
        if not cosmos_prompt:
            cosmos_prompt = "Caption the video in detail."
            logger.info(f"No COSMOS_REASON_PROMPT found - using default prompt: {cosmos_prompt}")

        # Get Executing username
        executing_userName = data.get('executingUserName', '')

        # Get Executing requestContext
        executing_requestContext = data.get('executingRequestContext', '')

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
            resolved['assetId'],
            resolved['databaseId'],
            cosmos_prompt,
            resolved['orchestrationEventPrefix']
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
