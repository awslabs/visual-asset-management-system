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
#docker build -f Dockerfile_PDAL -t pdal:v1 .
#docker run -it -v ${PWD}/inputTest:/data/input:ro -v ${PWD}/outputTest:/data/output:rw pdal:v1 "localTest" "PDAL"

def run(stage: PipelineStage, inputMetadataS3Location: str = '', inputConfigurationS3Location: str = '', localTest: bool = False) -> PipelineStage:
    """
    Run the PDAL Pipeline.
    """

    # Debugging: Set to true and update path to point cloud file
    # Production: Set to false
    useLocalBuildFilePath = localTest
    localBuildFilePath = "/data/input/inputE57.e57"

    # create local input and output dirs in container
    local_input_dir = ext.create_dir(["tmp", "input"])
    local_output_dir = ext.create_dir(["tmp", "output"])

    logger.info("Running Pipeline...")
    logger.info(f"Stage: {stage}")

    # get pipeline stage input and output
    input = StageInput(**stage.inputFile)
    output = StageOutput(**stage.outputFiles)

    # get point cloud object from s3
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

    # check file extension to determine if we can continue processing
    # currently only supports E57, PLY, LAZ, and LAS
    # extensions are matched case-insensitively, as the upstream allowed-extension gates are
    local_filename_lowered = os.path.basename(local_filepath).lower()
    if not local_filename_lowered.endswith(ext.Extensions.E57) and not local_filename_lowered.endswith(ext.Extensions.PLY) and not local_filename_lowered.endswith(ext.Extensions.LAZ) and not local_filename_lowered.endswith(ext.Extensions.LAS):
        return ext.error_response(stage,
            "Unsupported file type for point cloud visualization pipeline conversion. Currently only supports E57, PLY, LAZ, and LAS."
        )

    # If input file is E57 or PLY, convert to LAZ
    laz_filepath = None
    if local_filename_lowered.endswith(ext.Extensions.E57) or local_filename_lowered.endswith(ext.Extensions.PLY):
        pipeline_response = allconvert_pdalconversion_pipeline(
            local_filepath, local_output_dir)
        logger.info(f"Pipeline Response: {pipeline_response}")

        # A non-zero converter exit status means no usable output was written; report it before
        # the destination is cleared so a failed re-run cannot destroy an existing octree.
        if pipeline_response["returncode"] != 0:
            return ext.error_response(stage,
                f"PDAL translate exited with code {pipeline_response['returncode']}. Check the container logs for the converter output."
            )

        # get las file for further pipeline steps
        for file in pipeline_response["output_files"]:
            if file.lower().endswith(ext.Extensions.LAZ) or file.lower().endswith(ext.Extensions.LAS):
                laz_filepath = file
                break
    # If input file is already LAZ/LAS, do nothing and just pass through
    elif local_filename_lowered.endswith(ext.Extensions.LAZ) or local_filename_lowered.endswith(ext.Extensions.LAS):
        laz_filepath = local_filepath

    # If we were given another file or we could not convert, error
    if laz_filepath is None:
        return ext.error_response(stage,
            "Failed to convert to LAS/LAZ format. Check filename, file paths, and data formats."
        )

    # The destination is the Potree viewer directory, so what it already holds is the previous run's
    # working viewer data. Those objects are listed now and removed only once every replacement is in
    # place, which leaves them intact when an upload fails rather than clearing the destination and
    # then failing with nothing to put back.
    superseded_object_keys = existing_object_keys(output.bucketName, output.objectDir)

    #stage.outputFiles.fileNames = []

    # gather outputs and upload to s3
    uploaded_object_keys = []
    for file in pipeline_response["output_files"]:
        object_key = os.path.join(output.objectDir, file)
        file_path = os.path.join(local_output_dir, file)

        logger.info(f"Uploading PDAL File: {file_path}")
        if s3.uploadV2(output.bucketName, object_key, file_path) is None:
            return ext.error_response(stage,
                f"Failed to upload converted point cloud file to S3: {output.bucketName}/{object_key}"
            )
        uploaded_object_keys.append(object_key)

        #Final output filenames to append on stage
        #stage.outputFiles.fileNames.append(object_key)

    delete_superseded_objects(output.bucketName, superseded_object_keys, uploaded_object_keys)

    return ext.success_response(stage)


def existing_object_keys(bucket_name: str, object_dir: str) -> list:
    """The object keys already present under the stage's output directory.

    A listing failure yields no keys, which leaves those objects in place: the stage's own output is
    uploaded either way, and the POTREE stage that follows clears this same directory itself. Raising
    instead would end the container before it reports against the workflow's task token, since
    nothing above ``run`` catches an exception.
    """
    try:
        return [item["key"] for item in s3.get_all_files_in_path(bucket_name, object_dir)]
    except Exception as e:
        logger.exception(e)
        return []


def delete_superseded_objects(bucket_name: str, existing_keys: list, uploaded_keys: list) -> None:
    """Remove the objects a previous run left in the destination, keeping the ones this run wrote.

    A re-run of the same input writes the same object key, so the destination listing includes keys the
    new output has already replaced -- deleting those would remove the run's own result.
    """
    uploaded = set(uploaded_keys)
    for object_key in existing_keys:
        if object_key in uploaded:
            continue
        logger.info(
            f"Deleting Superseded Auxiliary Assets File for Potree Viewer: {bucket_name}:{object_key}")
        if s3.delete(bucket_name, object_key) is None:
            logger.warning(f"Could not delete superseded file: {bucket_name}:{object_key}")


def allconvert_pdalconversion_pipeline(input_file_path: str, output_dir: str) -> dict:
    """
    Conversion Pipeline
    Converts Point Cloud Format (E57, PLY and others) to LAZ
    """
    logger.info("Constructing PDAL Conversion Pipeline...")
    filename, extension = os.path.splitext(os.path.basename(input_file_path))

    laz_filepath = os.path.join(output_dir, filename + ext.Extensions.LAZ)

    # Formulate local subprocess to run for PDAL Converter Build
    PDAL_CONVERTER_CMD = ['pdal', 'translate',
                          '-i', input_file_path,
                          '-o', laz_filepath]

    logger.info("Executing PDAL Conversion to Laz")

    # Run PDAL local subprocess
    returncode = subprocess.Popen(PDAL_CONVERTER_CMD).wait() # nosemgrep: dangerous-subprocess-use-audit

    # Report the converted file only when the converter actually wrote it
    output_files = [filename + ext.Extensions.LAZ] if os.path.isfile(laz_filepath) else []

    return {
        "output_dir": output_dir,
        "output_files": output_files,
        "returncode": returncode
    }
