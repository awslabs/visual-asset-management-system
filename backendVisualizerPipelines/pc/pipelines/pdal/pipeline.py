# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
from ...utils.pipeline.objects import PipelineStage, StageInput, StageOutput
from ...utils.pipeline import extensions as ext
from ...utils.logging import log
from ...utils.aws import s3


logger = log.get_logger()


def run(stage: PipelineStage) -> PipelineStage:
    """
    Run the PDAL Pipeline.
    """

    # Debugging: Set to true and update path to point cloud file
    # Production: Set to false
    useLocalBuildFilePath = False
    localBuildFilePath = "/data/input/inputE57.e57"

    # create local input and output dirs in container
    local_input_dir = ext.create_dir(["tmp", "input"])
    local_output_dir = ext.create_dir(["tmp", "output"])

    logger.info("Running Pipeline...")
    logger.info(f"Stage: {stage}")

    # get pipeline stage input and output
    input = StageInput(**stage.input)
    output = StageOutput(**stage.output)

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
        return ext.error_response(
            "Unable to download file from S3 and/or no input file provided. Check bucket name, object key, and local input parameters."
        )

    # check file extension to determine if we can continue processing
    # currently only supports E57, LAZ, and LAS
    if not local_filepath.endswith(ext.Extensions.E57) and not local_filepath.endswith(ext.Extensions.LAZ) and not local_filepath.endswith(ext.Extensions.LAS):
        return ext.error_response(
            "Unsupported file type for point cloud visualization pipeline conversion. Currently only supports E57, LAZ, and LAS."
        )

    # If input file is E57, convert to LAZ
    if local_filepath.endswith(ext.Extensions.E57):
        pipeline_response = allconvert_pdalconversion_pipeline(
            local_filepath, local_output_dir)
        logger.info(f"Pipeline Response: {pipeline_response}")

        # get las file for further pipeline steps
        for file in pipeline_response["output_files"]:
            if file.endswith(ext.Extensions.LAZ) or file.endswith(ext.Extensions.LAS):
                laz_filepath = file
                break
    # If input file is already LAZ/LAS, do nothing and just pass through
    elif local_filepath.endswith(ext.Extensions.LAZ) or local_filepath.endswith(ext.Extensions.LAS):
        laz_filepath = local_filepath

    # If we were given another file or we could not convert, error
    if laz_filepath is None:
        return ext.error_response(
            "Failed to convert to LAS/LAZ format. Check filename, file paths, and data formats."
        )

    # gather outputs and upload to s3
    for file in pipeline_response["output_files"]:
        object_key = os.path.join(output.objectDir, file)
        file_path = os.path.join(local_output_dir, file)

        # TODO: delete temporary files in S3
        logger.info(f"Uploading PDAL File: {file_path}")
        s3.uploadV2(output.bucketName, object_key, file_path)

    return ext.success_response(stage)


def allconvert_pdalconversion_pipeline(input_file_path: str, output_dir: str) -> dict:
    """
    Conversion Pipeline
    Converts Point Cloud Format (E57 and others) to LAZ
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
    subprocess.Popen(PDAL_CONVERTER_CMD).wait()

    return {
        "output_dir": output_dir,
        "output_files": [filename + ext.Extensions.LAZ]
    }
