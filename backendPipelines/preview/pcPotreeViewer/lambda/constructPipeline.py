#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from customLogging.logger import safeLogger
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="ConstructPipelineVisualizer")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

PDAL_INPUT_EXTENSIONS = (".e57", ".ply")
POTREE_INPUT_EXTENSIONS = (".las", ".laz")


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token. The state machine carries no catch on
    this task and the following choice reads a field an error return does not have, so reporting
    here is what ends the waiting task instead of leaving it until the full taskTimeout."""
    if not task_token:
        return
    try:
        sfn.send_task_failure(
            taskToken=task_token,
            error="PcPotreeViewerPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    try:
        input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(event['inputS3AssetFilePath'])

        # extensions are matched case-insensitively, as the allowed-extension gate in openPipeline is
        input_s3_asset_extension = input_s3_asset_extension.lower()

        # construct different pipeline definitions based on file type
        if input_s3_asset_extension in PDAL_INPUT_EXTENSIONS:
            definition = construct_pdal_definition(event)
        elif input_s3_asset_extension in POTREE_INPUT_EXTENSIONS:
            definition = construct_potree_definition(event)
        else:
            raise ValueError(
                "Unsupported file type for point cloud potree viewer pipeline conversion. "
                "Currently only supports E57, PLY, LAZ, and LAS.")

        logger.info(f"Definition: {definition}")

        return {
            "jobName": event.get("jobName"),
            "currentStageType": definition["stages"][0]["type"],
            "definition": [json.dumps(definition)],
            "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
            "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
            "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
            "status": "STARTING"
        }
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get("externalSfnTaskToken", ""))
        raise


def construct_pdal_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    #output_s3_asset_files_uri = event['outputS3AssetFilesPath'] #unused in this pipeline
    #output_s3_asset_preview_uri = event['outputS3AssetPreviewPath'] #unused in this pipeline
    #output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath'] #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    #output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_preview_bucket, output_s3_asset_preview_key = output_s3_asset_preview_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_metadata_bucket, output_s3_asset_metadata_key = output_s3_asset_metadata_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]

    #Override output path as preview pipeline path is very specific for the potree viewer
    #Go up in the directory structure two and then add the hardcoded path 
    baseAssetAuxiliaryKeyPath = os.path.dirname(os.path.dirname(inputOutput_s3_assetAuxiliary_files_key))
    inputOutput_s3_assetAuxiliary_files_key = baseAssetAuxiliaryKeyPath + '/preview/PotreeViewer/'

    pdal_stage = {
        "type": "PDAL",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            "fileExtension": input_s3_asset_extension
        },
        "outputFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": inputOutput_s3_assetAuxiliary_files_key,
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

    potree_stage = {
        "type": "POTREE",
        "inputFile": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectKey": os.path.join(inputOutput_s3_assetAuxiliary_files_key, input_s3_asset_file_filename + ".laz"),
            "fileExtension": ".laz"
        },
        "outputFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": inputOutput_s3_assetAuxiliary_files_key,
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
        "stages": [pdal_stage, potree_stage],
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    return definition


def construct_potree_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    #output_s3_asset_files_uri = event['outputS3AssetFilesPath'] #unused in this pipeline
    #output_s3_asset_preview_uri = event['outputS3AssetPreviewPath'] #unused in this pipeline
    #output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath'] #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    #output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_preview_bucket, output_s3_asset_preview_key = output_s3_asset_preview_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_metadata_bucket, output_s3_asset_metadata_key = output_s3_asset_metadata_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]

    #Override output path as preview pipeline path is very specific for the potree viewer
    #Split provided AssetAuxiliary Path and keep everything but the last two path elements
    baseAssetAuxiliaryKeyPath = os.path.dirname(os.path.dirname(inputOutput_s3_assetAuxiliary_files_key))
    inputOutput_s3_assetAuxiliary_files_key = baseAssetAuxiliaryKeyPath + '/preview/PotreeViewer/'

    potree_stage = {
        "type": "POTREE",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            "fileExtension": input_s3_asset_extension
        },
        "outputFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": inputOutput_s3_assetAuxiliary_files_key,
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
        "stages": [potree_stage],
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    return definition
