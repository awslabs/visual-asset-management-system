#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution.
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
This handler executes the Gr00t Fine-Tuning pipeline by extracting training configuration
from asset metadata and invoking the openPipeline Lambda.

Operates at the asset level (not file level). Downloads the entire asset for training.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger


logger = safeLogger(service="VamsExecuteGr00tFinetunePipeline")
lambda_client = boto3.client('lambda')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path, output_s3_asset_metadata_path,
                      inputOutput_s3_assetAuxiliary_files_path, input_metadata, input_parameters, external_task_token,
                      executing_userName, executing_requestContext, asset_id, database_id, groot_config):
    """
    Execute the Gr00t Fine-Tuning pipeline by invoking the openPipeline Lambda.
    Asset-level pipeline: downloads entire asset, not a single file.
    """

    messagePayload = {
        "inputS3AssetPath": input_s3_asset_file_path.rstrip("/") + "/",
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "inputMetadata": "",
        "inputParameters": input_parameters,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "assetId": asset_id,
        "databaseId": database_id,
        "gr00tConfig": json.dumps(groot_config) if isinstance(groot_config, dict) else groot_config
    }

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
    external_task_token = ""

    try:
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

        # Get external task token
        if 'TaskToken' in data:
            external_task_token = data['TaskToken']
        else:
            raise Exception("VAMS Workflow TaskToken not found in pipeline input. Make sure to register this pipeline in VAMS as needing a task token callback.")

        # Get input parameters (3rd priority)
        input_parameters = data.get('inputParameters', '')
        logger.info(f"Input parameters received: {input_parameters}")

        # Get input metadata (2nd priority source for config)
        input_metadata = data.get('inputMetadata', '')
        logger.info(f"Input metadata received: {input_metadata}")

        # Build merged config from asset metadata (2nd priority) and inputParameters (3rd priority)
        # gr00t_config.json in the asset (1st priority) is handled by the container after download
        groot_config = {}

        # Extract from inputParameters (3rd priority -- lowest, applied first so metadata can override)
        if input_parameters:
            try:
                params_obj = json.loads(input_parameters) if isinstance(input_parameters, str) else input_parameters
                param_mappings = {
                    "datasetPath": "datasetPath",
                    "dataConfig": "dataConfig",
                    "baseModelPath": "baseModelPath",
                    "maxSteps": "maxSteps",
                    "batchSize": "batchSize",
                    "learningRate": "learningRate",
                    "weightDecay": "weightDecay",
                    "warmupRatio": "warmupRatio",
                    "saveSteps": "saveSteps",
                    "numGpus": "numGpus",
                    "loraRank": "loraRank",
                    "loraAlpha": "loraAlpha",
                    "loraDropout": "loraDropout",
                    "tuneLlm": "tuneLlm",
                    "tuneVisual": "tuneVisual",
                    "tuneProjector": "tuneProjector",
                    "tuneDiffusionModel": "tuneDiffusionModel",
                    "embodimentTag": "embodimentTag",
                    "videoBackend": "videoBackend",
                }
                for param_key, config_key in param_mappings.items():
                    if param_key in params_obj:
                        groot_config[config_key] = params_obj[param_key]
            except Exception as e:
                logger.warning(f"Failed to parse input parameters: {e}")

        # Extract from asset metadata (2nd priority -- overrides inputParameters)
        if input_metadata:
            try:
                metadata_obj = json.loads(input_metadata) if isinstance(input_metadata, str) else input_metadata
                vams_metadata = metadata_obj.get("VAMS", {})
                asset_metadata = vams_metadata.get("assetMetadata", {})

                metadata_mappings = {
                    "GROOT_DATASET_PATH": "datasetPath",
                    "GROOT_DATA_CONFIG": "dataConfig",
                    "GROOT_BASE_MODEL_PATH": "baseModelPath",
                    "GROOT_MAX_STEPS": "maxSteps",
                    "GROOT_BATCH_SIZE": "batchSize",
                    "GROOT_LEARNING_RATE": "learningRate",
                    "GROOT_WEIGHT_DECAY": "weightDecay",
                    "GROOT_WARMUP_RATIO": "warmupRatio",
                    "GROOT_SAVE_STEPS": "saveSteps",
                    "GROOT_NUM_GPUS": "numGpus",
                    "GROOT_LORA_RANK": "loraRank",
                    "GROOT_LORA_ALPHA": "loraAlpha",
                    "GROOT_LORA_DROPOUT": "loraDropout",
                    "GROOT_TUNE_LLM": "tuneLlm",
                    "GROOT_TUNE_VISUAL": "tuneVisual",
                    "GROOT_TUNE_PROJECTOR": "tuneProjector",
                    "GROOT_TUNE_DIFFUSION_MODEL": "tuneDiffusionModel",
                    "GROOT_EMBODIMENT_TAG": "embodimentTag",
                    "GROOT_VIDEO_BACKEND": "videoBackend",
                }
                for metadata_key, config_key in metadata_mappings.items():
                    val = asset_metadata.get(metadata_key, "")
                    if val:
                        groot_config[config_key] = val
                        logger.info(f"Extracted {metadata_key} from asset metadata: {val}")
            except Exception as e:
                logger.warning(f"Failed to extract config from asset metadata: {e}")

        asset_id = data.get('assetId', '')
        database_id = data.get('databaseId', '')
        executing_userName = data.get('executingUserName', '')
        executing_requestContext = data.get('executingRequestContext', '')

        execute_pipeline(
            data.get('inputS3AssetFilePath', ''),
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
            groot_config
        )

        return {
            'statusCode': 200,
            'body': 'Success'
        }
    except Exception as e:
        logger.exception(e)
        if external_task_token:
            try:
                sfn_client.send_task_failure(
                    taskToken=external_task_token,
                    error="Gr00tFinetunePipelineError",
                    cause=str(e)[:256]
                )
                logger.info("Sent task failure callback to Step Functions")
            except Exception as sfn_err:
                logger.error(f"Failed to send task failure callback: {sfn_err}")
        return {
            'statusCode': 500,
            'body': json.dumps({"message": str(e)})
        }
