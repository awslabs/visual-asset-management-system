#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
This handler executes the Cosmos Transfer pipeline by extracting metadata keys
COSMOS_TRANSFER_PROMPT, COSMOS_TRANSFER_CONTROL_TYPE, and COSMOS_TRANSFER_CONTROL_PATH
from file metadata and invoking the openPipeline Lambda.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger
import manifestHelper


logger = safeLogger(service="VamsExecuteCosmosTransferPipeline")
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path,
                      output_s3_asset_metadata_path, inputOutput_s3_assetAuxiliary_files_path,
                      input_metadata_s3_location, input_configuration_s3_location, orchestration_event_prefix,
                      external_task_token, executing_userName, executing_requestContext,
                      asset_id, database_id, cosmos_prompt, control_type, control_path):
    """
    Execute the Cosmos Transfer pipeline by invoking the openPipeline Lambda.
    Transfer requires an input file path (source video) and optionally a control signal.
    """

    # Create the object message to be sent
    messagePayload = {
        "modelType": "transfer",
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "orchestrationEventPrefix": orchestration_event_prefix,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "assetId": asset_id,
        "databaseId": database_id,
        "cosmosPrompt": cosmos_prompt,
        "controlType": control_type,
        "controlPath": control_path
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

        # Resolve inputs from the manifest (preferred) or legacy payload fields (fallback)
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)

        # Read metadata + input configuration content from S3 (locations travel, content does not).
        # The metadata file is the grouped-by-asset envelope, projected onto the legacy
        # {"VAMS": {...}} view for this run's subject that the scopes below read
        # (manifestHelper.run_vams_view).
        input_metadata = manifestHelper.run_vams_view(
            manifestHelper.fetch_metadata(s3_client, resolved['inputMetadataS3Location']), resolved)
        if not input_metadata:
            input_metadata = data.get('inputMetadata', '')
        input_parameters = manifestHelper.fetch_input_configuration(s3_client, resolved['inputConfigurationS3Location'])
        if not input_parameters:
            input_parameters = data.get('inputParameters', '')

        # Transfer settings resolve CONFIG-FIRST with a metadata fallback
        # (manifestHelper.resolve_input_setting): what the operator supplied on the execute screen as a
        # template dynamic tag wins, and a blank field falls back to a standing value saved on the
        # asset/file. This pipeline converts ONE file per run, so per-FILE metadata is honoured before
        # the asset's.
        _scopes = ("fileMetadata", "assetMetadata")

        def _setting(config_keys, metadata_key, default=""):
            value = manifestHelper.resolve_input_setting(
                input_parameters, input_metadata, config_keys, metadata_key,
                metadata_scopes=_scopes)
            return value if value != "" else default

        cosmos_prompt = _setting(("PROMPT", "prompt"), "COSMOS_TRANSFER_PROMPT")
        control_type = _setting(
            ("CONTROL_TYPE", "controlType"), "COSMOS_TRANSFER_CONTROL_TYPE", default="edge")
        control_path = _setting(("CONTROL_PATH", "controlPath"), "COSMOS_TRANSFER_CONTROL_PATH")
        logger.info(
            f"Resolved transfer settings: controlType={control_type} "
            f"promptSupplied={bool(cosmos_prompt)} controlPathSupplied={bool(control_path)}")

        # Prompt is optional for Transfer - default to generic prompt
        if not cosmos_prompt:
            cosmos_prompt = "Transform the video"
            logger.info(f"No COSMOS_TRANSFER_PROMPT found - using default: {cosmos_prompt}")

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
            resolved['orchestrationEventPrefix'],
            external_task_token,
            executing_userName,
            executing_requestContext,
            resolved['assetId'],
            resolved['databaseId'],
            cosmos_prompt,
            control_type,
            control_path
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
                    error="CosmosTransferPipelineError",
                    cause=str(e)[:256]
                )
                logger.info("Sent task failure callback to Step Functions")
            except Exception as sfn_err:
                logger.error(f"Failed to send task failure callback: {sfn_err}")
        return {
            'statusCode': 500,
            'body': json.dumps({"message": str(e)})
        }
