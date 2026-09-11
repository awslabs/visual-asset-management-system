# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import json
from ...utils.pipeline.objects import PipelineStage, StageInput, StageOutput
from ...utils.pipeline import extensions as ext
from ...utils.logging import log
from ...utils.aws import s3

logger = log.get_logger()

#Local Testing Commands - Powershell / unix:
#docker build -f Dockerfile_BlenderRenderer -t blender:v1 .
#docker run -it -v ${PWD}/inputTest:/data/input:ro -v ${PWD}/outputTest:/data/output:rw blender:v1 "localTest" "BLENDERRENDERER"

def run(stage: PipelineStage, inputMetadata: str = '', inputParameters: str = '', localTest: bool = False) -> PipelineStage:
    """
    Run the Blender Renderer Pipeline.
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

    if localTest == True:
        logger.info("Running Local...")
        # Debugging
        localTestInputFilePath = "/data/input/inputGlb.glb"
        localTestOutputFilePath = "/data/output/"

        # verify file has been downloaded from s3
        if not os.path.isfile(localTestInputFilePath):
            return ext.error_response(stage, 
                "Unable to download file from S3 and/or no input file provided. Check bucket name, object key, and local input parameters."
            )
        
        #Delete all files from output folder ahead of run for local test
        logger.info("Deleting All Files from Output Folder: " + localTestOutputFilePath)
        for root, dirs, files in os.walk(localTestOutputFilePath):
            for file in files:
                os.remove(os.path.join(root, file))

        pipeline_response = allconvert_blenderrenderer_pipeline(
            localTestInputFilePath, localTestOutputFilePath)
        logger.info(f"Pipeline Response: {pipeline_response}")

        if pipeline_response["returncode"] != 0:
            return ext.error_response(stage,
                f"Blender render failed with exit code {pipeline_response['returncode']}. Check the logs for the Blender output."
            )

        return ext.success_response(stage)
    else:
        logger.info("Running Non-Local...")
        # create local input and output dirs in container
        local_input_dir = ext.create_dir(["tmp", "input"])
        local_output_dir = ext.create_dir(["tmp", "output"])

        logger.info("Running Pipeline...")
        logger.info(f"Stage: {stage}")

        # get pipeline stage input and output files (don't need the others for this pipeline)
        input = StageInput(**stage.inputFile)
        output = StageOutput(**stage.outputFiles)

        #Get input parameters
        includeAllAssetFileHierarchyFiles = inputParametersObject.get("includeAllAssetFileHierarchyFiles", "False")

        #Download files from S3
        filePathsToDownload = []

        if includeAllAssetFileHierarchyFiles == "True":
            #Get all S3 file paths at input bucket directory location
            logger.info("Getting All Files in Bucket Directory Path: " + input.bucketName + ":" + input.objectKey.removesuffix(os.path.basename(input.objectKey)))
            filePathsToDownload = s3.get_all_files_in_path(bucket=input.bucketName, path=input.objectKey.removesuffix(os.path.basename(input.objectKey)))
        else:
            #Get only the S3 file path at input bucket directory location
            logger.info("Getting Single File in Bucket: " + input.bucketName + ":" + input.objectKey)
            filePathsToDownload.append({"key": input.objectKey, "relativePath":os.path.basename(input.objectKey)})
            
        logger.info(filePathsToDownload)

        #S3 keys are opaque, so a relative path may carry leading separators or '..' segments
        input_dir_root = os.path.realpath(local_input_dir)

        for filePath in filePathsToDownload:
            relativePath = filePath['relativePath'].lstrip('/')

            # Skip directory markers (empty relativePath or keys ending with '/')
            if not relativePath or filePath['key'].endswith('/'):
                logger.info(f"Skipping directory marker: {filePath['key']}")
                continue

            localPath = os.path.realpath(os.path.join(input_dir_root, relativePath))

            # Confine the write to the input directory
            if not localPath.startswith(input_dir_root + os.sep):
                logger.error(f"Skipping file resolving outside the input directory: {filePath['key']}")
                continue

            # Ensure parent directory exists for nested file structures
            os.makedirs(os.path.dirname(localPath), exist_ok=True)

            s3.download(
                input.bucketName,
                filePath['key'],
                localPath
            )

        #get the local file path of the original provided key (main model file)
        local_primaryfile_filepath = os.path.join(local_input_dir, os.path.basename(input.objectKey))

        # verify file has been downloaded from s3
        if not os.path.isfile(local_primaryfile_filepath):
            return ext.error_response(stage,
                "Unable to download primary file from S3 and/or no input file provided. Check bucket name, object key, and local input parameters."
            )
        
        #Delete any preview temp files and GenAI Metadata Generator files
        logger.info("Deleting Any Existing Auxiliary Assets Files for Pipeline Folder: " + output.bucketName + ":"+ output.objectDir)
        s3.delete_all_path_contents(output.bucketName, output.objectDir)

        pipeline_response = allconvert_blenderrenderer_pipeline(
            local_primaryfile_filepath, local_output_dir)
        logger.info(f"Pipeline Response: {pipeline_response}")

        #Error if the render subprocess failed (a failed model import must not report success)
        if pipeline_response["returncode"] != 0:
            return ext.error_response(stage,
                f"Blender render failed with exit code {pipeline_response['returncode']}. Check the logs for the Blender output."
            )

        #Error if nothing generated
        if len(pipeline_response["output_files"]) == 0:
            return ext.error_response(stage, "No output files generated.")

        #stage.outputFiles.fileNames = []
        # gather outputs and upload to s3
        for file in pipeline_response["output_files"]:
            object_key = os.path.join(output.objectDir, file)
            file_path = os.path.join(local_output_dir, file)

            logger.info(f"Uploading Image Files: {file_path}")
            s3.uploadV2(output.bucketName, object_key, file_path)

            #Final output filenames to append on stage
            #stage.outputFiles.fileNames.append(object_key)

        return ext.success_response(stage)


def allconvert_blenderrenderer_pipeline(input_file_path: str, output_dir: str) -> dict:
    """
    Blender Renderer
    Renders multiple 2D image views from the provided 3D model
    """

    logger.info("Executing Pipeline")

    # Run Blender processes
    # Formulate local subprocess to run Blender render Pipeline
    BLENDER_JOIN_CMD = ['blender',
                           '--background',
                           '-noaudio',
                           '-E', 'CYCLES', #Run with CYCLES engine for non-GPU
                           '-P', 'main/blenderAppScripts/renderScene.py',
                           '--',
                           input_file_path,
                           output_dir]

    completed = subprocess.run(BLENDER_JOIN_CMD) # nosemgrep: dangerous-subprocess-use-audit
    logger.info(f"Blender exit code: {completed.returncode}")

    # Get an array of all file names in output directory
    output_files = os.listdir(output_dir)

    return {
        "output_dir": output_dir,
        "output_files": output_files,
        "returncode": completed.returncode
    }
