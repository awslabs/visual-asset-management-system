#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructSplatToolboxPipeline")

def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")
    
    job_name = event.get("jobName")

    definition = construct_splattoolbox_definition(event)
    logger.info(f"Definition: {definition}")

    return {
        "jobName": job_name,
        "currentStageType": definition["stages"][0]["type"],
        "definition": ["python", "__main__.py", json.dumps(definition)],
        # Forward the metadata + input-configuration S3 locations, not their content
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING"
    }

def determine_resource_requirements(definition):
    """
    Determine appropriate vCPUs and memory based on job requirements
    """
    # Default resources for basic jobs
    vcpus = 4
    memory = 16384  # 16 GB
    
    # Check if this is a complex job requiring more resources
    stage = definition["stages"][0]
    input_file = stage.get("inputFile", {})
    filename = input_file.get("objectKey", "")
    
    # Estimate complexity based on file size indicators or job parameters
    if "large" in filename.lower() or "4k" in filename.lower():
        vcpus = 16
        memory = 65536  # 64 GB
    elif "medium" in filename.lower() or "hd" in filename.lower():
        vcpus = 8
        memory = 32768  # 32 GB
    
    return vcpus, memory


def construct_splattoolbox_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri.replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)

    # MUST use outputS3AssetFilesPath from workflow for proper file registration
    if output_s3_asset_files_uri:
        output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)
        output_bucket = output_s3_asset_files_bucket
        output_dir = output_s3_asset_files_key if output_s3_asset_files_key.endswith('/') else output_s3_asset_files_key + '/'
    else:
        # Fallback for non-workflow execution (direct pipeline invocation)
        output_bucket = input_s3_asset_file_bucket
        output_dir = f"{input_s3_asset_file_root}/3dRecon/splatToolbox/"

    splat_stage = {
        "type": "SPLAT",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            "fileExtension": input_s3_asset_extension
        },
        "outputFiles": {
            "bucketName": output_bucket,
            "objectDir": output_dir,
        },
        "outputMetadata": {
            "bucketName": "",
            "objectDir": "",
        },
        "temporaryFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": f"{inputOutput_s3_assetAuxiliary_files_key}/",
        }
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [splat_stage],
        # Metadata + input-configuration S3 locations travel with the definition, not their content
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    return definition
