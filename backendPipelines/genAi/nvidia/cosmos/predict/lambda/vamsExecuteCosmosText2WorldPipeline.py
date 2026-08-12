#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
This handler executes the Cosmos Text2World pipeline by extracting the COSMOS_PREDICT_PROMPT
from asset metadata and invoking the openPipeline Lambda.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger
import manifestHelper


logger = safeLogger(service="VamsExecuteCosmosText2WorldPipeline")
lambda_client = boto3.client('lambda')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
s3_client = boto3.client('s3')
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path,
                      output_s3_asset_metadata_path, inputOutput_s3_assetAuxiliary_files_path,
                      input_metadata_s3_location, input_configuration_s3_location, orchestration_event_prefix,
                      external_task_token, executing_userName, executing_requestContext, asset_id, database_id,
                      cosmos_prompt):
    """
    Execute the Cosmos Text2World pipeline by invoking the openPipeline Lambda.
    Text2World does not require an input file, only a prompt.
    """

    # Create the object message to be sent
    messagePayload = {
        "modelType": "text2world",
        "inputS3AssetFilePath": input_s3_asset_file_path,  # Empty for text2world
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

        # Resolve manifest-preferred inputs (locations + paths), legacy-fallback
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)

        # Read metadata + input configuration content from S3 (inline fallback for transition). The
        # metadata file is the grouped-by-asset envelope, projected onto the legacy {"VAMS": {...}}
        # view for this run's subject that the scope below reads. This pipeline takes no input file,
        # so that subject is the envelope's first metadata-source asset (manifestHelper.run_vams_view).
        metadata = manifestHelper.run_vams_view(
            manifestHelper.fetch_metadata(s3_client, resolved['inputMetadataS3Location']), resolved)
        if not metadata and data.get('inputMetadata'):
            inline = data.get('inputMetadata')
            metadata = json.loads(inline) if isinstance(inline, str) else inline
        input_configuration = manifestHelper.fetch_input_configuration(s3_client, resolved['inputConfigurationS3Location'])
        if not input_configuration and data.get('inputParameters'):
            inline = data.get('inputParameters')
            input_configuration = json.loads(inline) if isinstance(inline, str) else inline

        # The prompt resolves CONFIG-FIRST with an ASSET-METADATA fallback
        # (manifestHelper.resolve_input_setting). Text2World takes NO input file, so the prompt IS the
        # input: it is supplied on the execute screen as a template dynamic tag and must win over a
        # value saved on the asset earlier. A blank field falls back to the asset's standing value.
        # Only assetMetadata is consulted — with no input file there is no file metadata to read.
        cosmos_prompt = manifestHelper.resolve_input_setting(
            input_configuration, metadata, ("PROMPT", "prompt"), "COSMOS_PREDICT_PROMPT",
            metadata_scopes=("assetMetadata",))
        if not cosmos_prompt:
            raise Exception(
                "No prompt supplied. Provide it as the pipeline's PROMPT input configuration value "
                "(the execute screen's dynamic tag), or set COSMOS_PREDICT_PROMPT on the asset's "
                "metadata.")
        logger.info(f"Resolved COSMOS_PREDICT_PROMPT: {cosmos_prompt}")

        # Get Executing username
        executing_userName = data.get('executingUserName', '')

        # Get Executing requestContext
        executing_requestContext = data.get('executingRequestContext', '')

        # Starts execution of pipeline
        execute_pipeline(
            "",  # Empty for text2world
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
                    error="CosmosText2WorldPipelineError",
                    cause=str(e)[:256]
                )
                logger.info("Sent task failure callback to Step Functions")
            except Exception as sfn_err:
                logger.error(f"Failed to send task failure callback: {sfn_err}")
        return {
            'statusCode': 500,
            'body': json.dumps({"message": str(e)})
        }
