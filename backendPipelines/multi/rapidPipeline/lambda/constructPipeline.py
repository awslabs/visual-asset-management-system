#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructPipelineRapidPipeline")
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
    definition = construct_rapidPipeline_definition(event)

    logger.info(f"Definition: {definition}")
    
    return {
        "jobName": event.get("jobName"),
        "commands": definition,
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING"
    }


def construct_rapidPipeline_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath'] 
    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)
    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)
    
    output_s3_asset_extension = event['outputFileType']
    
    # rename file based on conversion or compression
    if input_s3_asset_extension == output_s3_asset_extension:
        output_s3_asset_file_filename = input_s3_asset_file_filename + output_s3_asset_extension
    else:
        output_s3_asset_file_filename = input_s3_asset_file_filename + '-' + output_s3_asset_extension.replace(".", "") + output_s3_asset_extension
    
    # format standard RapidPipeline command string
    standard_command_with_config = "aws s3 cp s3://" + input_s3_asset_file_bucket + "/" + input_s3_asset_file_key + " . && /rpdx/rpdx --read_config rp_config.json -i " + input_s3_asset_file_filename + input_s3_asset_extension + " -c -e " + output_s3_asset_file_filename + " && aws s3 cp " + output_s3_asset_file_filename + " s3://" + output_s3_asset_files_bucket + "/" + output_s3_asset_files_key
    standard_command_no_config = "aws s3 cp s3://" + input_s3_asset_file_bucket + "/" + input_s3_asset_file_key + " . && /rpdx/rpdx -i " + input_s3_asset_file_filename + input_s3_asset_extension + " -c -e " + output_s3_asset_file_filename + " && aws s3 cp " + output_s3_asset_file_filename + " s3://" + output_s3_asset_files_bucket + "/" + output_s3_asset_files_key
    
    # Handle custom configurations using config.json file 
    if event['inputParameters'] != "":
        config = event['inputParameters']
        # write config json file to S3 
        s3.put_object(
            Body=config,
            Bucket=inputOutput_s3_assetAuxiliary_files_bucket,
            Key="rp_config.json"
        )
        # download config file from S3, read config file, then execute standard command
        command = "aws s3 cp s3://" + inputOutput_s3_assetAuxiliary_files_bucket + "/rp_config.json rp_config.json && " + standard_command_with_config
    else:
        # if no input parameters are found, execute standard command
        command = standard_command_no_config      


    commands = [
        "/bin/sh",
        "-c",
        command
    ]
    
    return commands

