#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import uuid
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructCosmosPredictPipeline")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application for Cosmos Predict.
    Supports both text2world and video2world model types.
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    model_type = event.get("modelType", "text2world")
    cosmos_prompt = event.get("cosmosPrompt", "")
    input_s3_asset_file_path = event.get("inputS3AssetFilePath", "")
    output_s3_asset_files_path = event.get("outputS3AssetFilesPath", "")
    output_s3_asset_preview_path = event.get("outputS3AssetPreviewPath", "")
    output_s3_asset_metadata_path = event.get("outputS3AssetMetadataPath", "")
    inputOutput_s3_assetAuxiliary_files_path = event.get("inputOutputS3AssetAuxiliaryFilesPath", "")
    asset_id = event.get("assetId", "")
    database_id = event.get("databaseId", "")
    input_metadata = event.get("inputMetadata", "")
    input_parameters = event.get("inputParameters", "")
    external_sfn_task_token = event.get("externalSfnTaskToken", "")

    # Extract prompt from inputParameters if present and not already set
    if not cosmos_prompt and input_parameters:
        try:
            params_obj = json.loads(input_parameters) if isinstance(input_parameters, str) else input_parameters
            cosmos_prompt = params_obj.get("PROMPT") or params_obj.get("prompt") or ""
        except Exception as e:
            logger.warning(f"Failed to parse input parameters for prompt: {e}")

    # Generate unique job name
    job_name = f"cosmos-{model_type}-{str(uuid.uuid4())[:8]}"

    # Build pipeline definition
    definition = {
        "jobName": job_name,
        "modelType": model_type,
        "cosmosPrompt": cosmos_prompt,
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadata": input_metadata,
        "inputParameters": input_parameters,
        "externalSfnTaskToken": external_sfn_task_token
    }

    logger.info(f"Definition: {definition}")

    return {
        "jobName": job_name,
        "definition": ["python", "__main__.py", json.dumps(definition)],
        "inputMetadata": input_metadata,
        "inputParameters": input_parameters,
        "externalSfnTaskToken": external_sfn_task_token,
        "status": "STARTING"
    }
