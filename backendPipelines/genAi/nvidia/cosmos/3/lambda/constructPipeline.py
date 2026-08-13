#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import uuid
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructCosmos3Pipeline")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds the pipeline input definition to run the Batch application for Cosmos 3.
    """
    logger.info(f"Event: {event}")

    model_variant = event.get("modelVariant", "nano")
    task_mode = event.get("taskMode", "")
    cosmos_prompt = event.get("cosmosPrompt", "")
    cosmos_negative_prompt = event.get("cosmosNegativePrompt", "")
    cosmos_seed = event.get("cosmosSeed", "")
    cosmos_guidance = event.get("cosmosGuidance", "")
    cosmos_num_frames = event.get("cosmosNumFrames", "")
    cosmos_control_type = event.get("cosmosControlType", "")
    cosmos_control_path = event.get("cosmosControlPath", "")
    cosmos_control_weight = event.get("cosmosControlWeight", "")
    cosmos_control_guidance = event.get("cosmosControlGuidance", "")
    input_s3_asset_file_path = event.get("inputS3AssetFilePath", "")
    output_s3_asset_files_path = event.get("outputS3AssetFilesPath", "")
    output_s3_asset_preview_path = event.get("outputS3AssetPreviewPath", "")
    output_s3_asset_metadata_path = event.get("outputS3AssetMetadataPath", "")
    inputOutput_s3_assetAuxiliary_files_path = event.get("inputOutputS3AssetAuxiliaryFilesPath", "")
    asset_id = event.get("assetId", "")
    database_id = event.get("databaseId", "")
    # Metadata + input-configuration S3 LOCATIONS travel onward (never the inline content); the
    # container reads them from S3 as needed.
    input_metadata_s3_location = event.get("inputMetadataS3Location", "")
    input_configuration_s3_location = event.get("inputConfigurationS3Location", "")
    external_sfn_task_token = event.get("externalSfnTaskToken", "")

    job_name = f"cosmos3-{model_variant}-{str(uuid.uuid4())[:8]}"

    definition = {
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
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token,
    }

    logger.info(f"Definition: {definition}")

    return {
        "jobName": job_name,
        "definition": ["python", "__main__.py", json.dumps(definition)],
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token,
        "status": "STARTING",
    }
