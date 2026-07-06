#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="ConstructPipelineModelOps")
s3 = boto3.client('s3')

def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the ECS task
    """

    ##################
    #Valid Test Input Parameters Definition to this Pipeline
    # {"includeAllAssetFileHierarchyFiles": "True", "seedMetadataGenerationWithInputMetadata": "True" }
    #################

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    # construct different pipeline definition
    definition = construct_modelops_definition(event)

    logger.info(f"Definition: {definition}")

    return {
        "jobName": event.get("jobName"),
        "commands": definition,
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING"
    }


def construct_modelops_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath']
    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)
    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    # Read the input configuration from S3 and update parameters based on event data
    config = manifestHelper.fetch_input_configuration(s3, event.get('inputConfigurationS3Location', ''))

    if config:
        config["state"]["name"] = input_s3_asset_file_filename
        config["state"]["bucket"] = input_s3_asset_file_bucket
        config["state"]["prefix"] = input_s3_asset_file_key.split("/")[0]
        config["state"]["extension"] = input_s3_asset_extension.replace(".", "")

        command_string = json.dumps(config)
        command = "printf '" + command_string + "' | /home/app/apps/handler/dist/index.js -i yaml --debug"

    else:
        # if no input configuration is found, execute standard command
        return "Error: No configuration file detected."

    commands = [
        "/bin/bash",
         "-c",
         command
    ]

    return commands

