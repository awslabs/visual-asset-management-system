#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import uuid
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructCosmosTransferPipeline")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application for Cosmos Transfer.
    Includes controlType and controlPath in the definition for the container.
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    model_type = event.get("modelType", "transfer")
    cosmos_prompt = event.get("cosmosPrompt", "")
    control_type = event.get("controlType", "edge")
    control_path = event.get("controlPath", "")
    input_s3_asset_file_path = event.get("inputS3AssetFilePath", "")
    output_s3_asset_files_path = event.get("outputS3AssetFilesPath", "")
    output_s3_asset_preview_path = event.get("outputS3AssetPreviewPath", "")
    output_s3_asset_metadata_path = event.get("outputS3AssetMetadataPath", "")
    inputOutput_s3_assetAuxiliary_files_path = event.get("inputOutputS3AssetAuxiliaryFilesPath", "")
    asset_id = event.get("assetId", "")
    database_id = event.get("databaseId", "")
    input_metadata_s3_location = event.get("inputMetadataS3Location", "")
    input_configuration_s3_location = event.get("inputConfigurationS3Location", "")
    external_sfn_task_token = event.get("externalSfnTaskToken", "")

    # Generate unique job name
    job_name = f"cosmos-transfer-{control_type}-{str(uuid.uuid4())[:8]}"

    # Build pipeline definition
    definition = {
        "jobName": job_name,
        "modelType": model_type,
        "cosmosPrompt": cosmos_prompt,
        "controlType": control_type,
        "controlPath": control_path,
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token
    }

    logger.info(f"Definition: {definition}")

    return {
        "jobName": job_name,
        "definition": ["python", "__main__.py", json.dumps(definition)],
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token,
        "status": "STARTING"
    }
