#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructPipelineGenAiMetadataLabeling")

def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application
    """

    ##################
    #Valid Test Input Parameters Definition to this Pipeline
    # {"includeAllAssetFileHierarchyFiles": "True", "seedMetadataGenerationWithInputMetadata": "True" }
    #################

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    # construct different pipeline definition
    definition = construct_metadataLabeling_definition(event)

    logger.info(f"Definition: {definition}")

    return {
        "jobName": event.get("jobName"),
        "currentStageType": definition["stages"][0]["type"],
        "definition": [json.dumps(definition)],
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING"
    }


def construct_metadataLabeling_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    #output_s3_asset_files_uri = event['outputS3AssetFilesPath'] #unused in this pipeline
    #output_s3_asset_preview_uri = event['outputS3AssetPreviewPath'] #unused in this pipeline
    output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath']
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    #output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_preview_bucket, output_s3_asset_preview_key = output_s3_asset_preview_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    output_s3_asset_metadata_bucket, output_s3_asset_metadata_key = output_s3_asset_metadata_uri .replace("s3://", "").split("/", 1)
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]

    blender_stage = {
        "type": "BLENDERRENDERER",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            "fileExtension": input_s3_asset_extension
        },
        "outputFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": os.path.join(inputOutput_s3_assetAuxiliary_files_key),
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

    metadataGeneration_stage = {
        "type": "METADATAGENERATION",
        "inputFile": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": os.path.join(inputOutput_s3_assetAuxiliary_files_key),
        },
        "outputFiles": {
            "bucketName": "",
            "objectDir": "",
        },
        "outputMetadata": {
            "bucketName": output_s3_asset_metadata_bucket,
            "objectDir": output_s3_asset_metadata_key,
        },
        "temporaryFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": os.path.join(inputOutput_s3_assetAuxiliary_files_key),
        }
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [blender_stage, metadataGeneration_stage],
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    return definition

