# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import json
from .utils.pipeline.objects import PipelineStage, StageInput, StageOutput
from .utils.pipeline import extensions as ext
from .utils.logging import log
from .utils.aws import s3


logger = log.get_logger()

#Local Testing Commands - Powershell / unix:
#docker build -f Dockerfile_Potree -t potree:v1 .
#docker run -it -v ${PWD}/inputTest:/data/input:ro -v ${PWD}/outputTest:/data/output:rw potree:v1 "localTest" "POTREE"

def run(stage: PipelineStage, inputMetadata: str = '', inputParameters: str = '', localTest: bool = False) -> PipelineStage:
    """
    Run the Splat Toolbox Pipeline.
    """

    #Get and parse input parameters
    inputParametersObject = {}
    if(isinstance(inputParameters,str) and inputParameters != ''):
        try:
            inputParametersObject = json.loads(inputParameters)
        except:
            logger.error("Input parameters is not valid JSON.")

    #Get and parse input metadata
    inputMetadataObject = {}
    if(isinstance(inputMetadata,str) and inputMetadata != ''):
        try:
            inputMetadataObject = json.loads(inputMetadata)
        except:
            logger.error("Input metadata is not valid JSON.")

    # Debugging: Set to true and update path to point cloud file
    # Production: Set to false
    useLocalBuildFilePath = localTest
    localBuildFilePath = "/data/input/sample.zip"

    # create local input and output dirs in container
    local_input_dir = ext.create_dir(["tmp", "input"])
    local_output_dir = ext.create_dir(["tmp", "output"])

    logger.info("Running Pipeline...")
    logger.info(f"Stage: {stage}")

    # get pipeline stage input and output
    input = StageInput(**stage.inputFile)
    output = StageOutput(**stage.outputFiles)

    # get point cloud object from s3. Check first if we have a local build path for debugging
    if useLocalBuildFilePath == True:
        local_filepath = localBuildFilePath
    else:
        logger.info(
            f"Downloading file from S3: {input.bucketName}/{input.objectKey}")
        local_filepath = s3.download(
            input.bucketName,
            input.objectKey,
            os.path.join(local_input_dir, os.path.basename(input.objectKey)))

    # verify file has been downloaded from s3
    if not os.path.isfile(local_filepath):
        return ext.error_response(stage, 
            "Unable to download file from S3 and/or no input file provided. Check bucket name, object key, and local input parameters."
        )

    pipeline_response = splat_pipeline(local_filepath, local_output_dir)
    logger.info(f"Pipeline Response: {pipeline_response}")
    
    #Delete any temp files
    logger.info("Deleting Any Existing Auxiliary Assets Files for Pipeline: " + output.bucketName + ":"+ output.objectDir)
    s3.delete_all_path_contents(output.bucketName, output.objectDir)

    #stage.outputFiles.fileNames = []

    # gather outputs and upload to s3
    for file in pipeline_response["output_files"]:
        object_key = os.path.join(output.objectDir, file)
        file_path = os.path.join(local_output_dir, file)

        logger.info(f"Uploading Splat File: {file_path}")
        s3.uploadV2(output.bucketName, object_key, file_path)

    # send success response back to core | keep source bucket ,key, file extension the same as we are not making intermediate conversion files
    return ext.success_response(stage)

def splat_pipeline(input_file_path: str, output_dir: str) -> dict:
    """
    Splat Toolbox Pipeline
    """
    logger.info("Constructing Splat Toolbox Pipeline...")

    # Formulate local subprocess to run for PDAL Converter Build
    SPLAT_TOOLBOX_CMD = ["python", "main.py"]

    logger.info("Executing Splat Toolbox Pipeline...")

    # Run Potree Converter local subprocess
    subprocess.Popen(SPLAT_TOOLBOX_CMD).wait()

    # Get an array of all file names in output directory
    output_files = os.listdir(output_dir)

    return {
        "output_dir": output_dir,
        "output_files": output_files
    }
