#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import shlex
import boto3
from customLogging.logger import safeLogger
import manifestHelper

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
        # Forward the metadata + input-configuration S3 locations, not their content.
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
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

    # Read the input configuration (rp_config) from its S3 location.
    config = manifestHelper.fetch_input_configuration(s3, event.get('inputConfigurationS3Location', '')) or {}

    # outputType is a VAMS-reserved key in the input configuration: it selects the output file
    # extension and is removed before the remainder is written as the rpdx rp_config.json. Fall
    # back to the threaded outputFileType, then to the input file's own extension so the written
    # object always carries one — rpdx then optimizes the model without changing its format.
    output_s3_asset_extension = (config.pop('outputType', None)
                                 or event.get('outputFileType', '')
                                 or input_s3_asset_extension)

    # Every value interpolated into the shell command below originates from asset
    # filenames / S3 keys / caller-supplied parameters, so each is shell-quoted with
    # shlex.quote(). The command still runs under a shell (it chains steps with &&),
    # so untrusted values must be inert single-quoted literals to prevent command
    # injection (e.g. a filename containing $(...), backticks, or ';').
    input_file = f"{input_s3_asset_file_filename}{input_s3_asset_extension}"
    output_s3_asset_file_filename = input_s3_asset_file_filename + output_s3_asset_extension
    input_object = f"s3://{input_s3_asset_file_bucket}/{input_s3_asset_file_key}"
    output_object = f"s3://{output_s3_asset_files_bucket}/{output_s3_asset_files_key}"

    q_input_object = shlex.quote(input_object)
    q_input_file = shlex.quote(input_file)
    q_output_file = shlex.quote(output_s3_asset_file_filename)
    q_output_object = shlex.quote(output_object)

    # format standard RapidPipeline command string
    standard_command_with_config = f"aws s3 cp {q_input_object} . && /rpdx/rpdx --read_config rp_config.json -i {q_input_file} -c -e {q_output_file} && aws s3 cp {q_output_file} {q_output_object}"
    standard_command_no_config = f"aws s3 cp {q_input_object} . && /rpdx/rpdx -i {q_input_file} -c -e {q_output_file} && aws s3 cp {q_output_file} {q_output_object}"

    # Handle custom configurations using the input configuration read from S3 above.
    if config:
        # Namespace the config object per execution so concurrent runs cannot read each other's
        # config (L13). jobName carries a millisecond stamp and a random suffix, so it stays distinct
        # for runs launched in the same second — one upload can fan out to several simultaneous runs
        # of this pipeline.
        config_key = f"rp_config_{event.get('jobName', 'default')}.json"
        # write config json file to S3
        s3.put_object(
            Body=json.dumps(config),
            Bucket=inputOutput_s3_assetAuxiliary_files_bucket,
            Key=config_key
        )
        q_config_object = shlex.quote(f"s3://{inputOutput_s3_assetAuxiliary_files_bucket}/{config_key}")
        # download config file from S3, read config file, then execute standard command
        command = f"aws s3 cp {q_config_object} rp_config.json && " + standard_command_with_config
    else:
        # if no input configuration is found, execute standard command
        command = standard_command_no_config


    commands = [
        "/bin/sh",
        "-c",
        command
    ]

    return commands

