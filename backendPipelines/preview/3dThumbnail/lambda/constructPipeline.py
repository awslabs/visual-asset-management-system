#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructPipeline3dThumbnail")

def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    definition = construct_preview_3d_thumbnail_definition(event)

    logger.info(f"Definition: {definition}")

    return {
        "jobName": event.get("jobName"),
        "currentStageType": definition["stages"][0]["type"],
        "definition": [json.dumps(definition)],
        "inputMetadata": "", #Don't pass metadata as not needed here and causes less room for errors due to long metadata for ECS payloads
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING"
    }


def construct_preview_3d_thumbnail_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event.get('inputOutputS3AssetAuxiliaryFilesPath', '')

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)

    # Determine output bucket and path for file preview outputs (.previewFile.gif/.jpg).
    # Primary: use outputS3AssetFilesPath (provided by workflow ASL) — this is the
    # asset files output location where the process-output step expects to find
    # file-level outputs. outputS3AssetPreviewPath is for asset-level previews only.
    # Fallback: use inputOutputS3AssetAuxiliaryFilesPath for direct/local invocations.
    if output_s3_asset_files_uri:
        output_bucket, output_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)
    elif inputOutput_s3_assetAuxiliary_files_uri:
        output_bucket, output_key = inputOutput_s3_assetAuxiliary_files_uri.replace("s3://", "").split("/", 1)
    else:
        output_bucket = ""
        output_key = ""

    # Ensure trailing slash so the container can append filenames.
    if output_key and not output_key.endswith('/'):
        output_key += '/'

    preview_3d_thumbnail_stage = {
        "type": "PREVIEW_3D_THUMBNAIL",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            "fileExtension": input_s3_asset_extension
        },
        "outputFiles": {
            "bucketName": output_bucket,
            "objectDir": output_key,
        },
        "outputMetadata": {
            "bucketName": "",
            "objectDir": "",
        },
        "temporaryFiles": {
            "bucketName": "",
            "objectDir": "",
        }
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [preview_3d_thumbnail_stage],
        "inputMetadata": "", #Don't pass metadata as not needed here and causes less room for errors due to long metadata for ECS payloads
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "assetId": event.get("assetId", ""),
    }

    return definition
