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
    input_metadata_s3_location = event.get("inputMetadataS3Location", "")
    input_configuration_s3_location = event.get("inputConfigurationS3Location", "")
    groot_config = event.get("gr00tConfig", "{}")
    external_sfn_task_token = event.get("externalSfnTaskToken", "")
    # finetune (default) trains; evaluate scores an existing checkpoint. Threaded through the
    # definition so both modes share one Batch job definition, queue, and state machine.
    mode = str(event.get("mode", "") or "finetune").strip().lower()

    job_name = f"gr00t-{'eval' if mode == 'evaluate' else 'finetune'}-{str(uuid.uuid4())[:8]}"

    definition = {
        "jobName": job_name,
        "inputS3AssetPath": input_s3_asset_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "gr00tConfig": groot_config,
        "externalSfnTaskToken": external_sfn_task_token,
        "mode": mode
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
