#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import uuid
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructGr00tFinetunePipeline")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application for Gr00t Fine-Tuning.
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    input_s3_asset_path = event.get("inputS3AssetPath", "")
    output_s3_asset_files_path = event.get("outputS3AssetFilesPath", "")
    output_s3_asset_preview_path = event.get("outputS3AssetPreviewPath", "")
    output_s3_asset_metadata_path = event.get("outputS3AssetMetadataPath", "")
    inputOutput_s3_assetAuxiliary_files_path = event.get("inputOutputS3AssetAuxiliaryFilesPath", "")
    asset_id = event.get("assetId", "")
    database_id = event.get("databaseId", "")
    input_metadata = event.get("inputMetadata", "")
    input_parameters = event.get("inputParameters", "")
    groot_config = event.get("gr00tConfig", "{}")
    external_sfn_task_token = event.get("externalSfnTaskToken", "")

    job_name = f"gr00t-finetune-{str(uuid.uuid4())[:8]}"

    definition = {
        "jobName": job_name,
        "inputS3AssetPath": input_s3_asset_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadata": input_metadata,
        "inputParameters": input_parameters,
        "gr00tConfig": groot_config,
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
