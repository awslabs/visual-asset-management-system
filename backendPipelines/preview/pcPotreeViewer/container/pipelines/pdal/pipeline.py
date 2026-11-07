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

# Keep the intermediate LAZ at the LAS 1.2 / point data record format 3 shape PDAL 2.7.0 wrote by
# default, so the PotreeConverter input does not change with the PDAL version.
PDAL_LAS_WRITER_OPTIONS = ['--writers.las.minor_version=2', '--writers.las.dataformat_id=3']

# PDAL's message is carried in the task failure `error` (256 characters, after the
# "Pipeline Failure: " prefixes the container and pipelineEnd add) behind a ~45-character headline.
PDAL_MESSAGE_MAX_CHARS = 160

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
        # The run's launch version when the stage reads the asset input; no argument reads the
        # current object (an intermediate the previous stage wrote, or a definition without one).
        version = {"version_id": input.versionId} if input.versionId else {}
        try:
            local_filepath = s3.download(
                input.bucketName,
                input.objectKey,
                os.path.join(local_input_dir, os.path.basename(input.objectKey)),
                **version)
        except s3.InputVersionMissingError as e:
            return ext.error_response(stage, str(e))

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
        # anything is uploaded, with PDAL's own message so the run's failure names the cause.
        if pipeline_response["returncode"] != 0:
            return ext.error_response(stage, converter_failure_message(
                local_filename_lowered, pipeline_response["returncode"], pipeline_response["converter_output"]))

        # get las file for further pipeline steps
        for file in pipeline_response["output_files"]:
            if file.lower().endswith(ext.Extensions.LAZ) or file.lower().endswith(ext.Extensions.LAS):
                laz_filepath = file
                break
    # A LAZ/LAS input needs no conversion, so it is never routed here: constructPipeline sends only
    # PDAL_INPUT_EXTENSIONS (.e57, .ply) to this stage and gives LAZ/LAS straight to POTREE. Arriving
    # here means the definition was built wrong, and it is reported rather than carried on with: this
    # branch produced no converter output, so the upload loop below would dereference an unbound
    # pipeline_response and end the container with an UnboundLocalError. Nothing above run() catches
    # that, so the step would report nothing against its task token and wait out its full taskTimeout.
    elif local_filename_lowered.endswith(ext.Extensions.LAZ) or local_filename_lowered.endswith(ext.Extensions.LAS):
        return ext.error_response(stage,
            "A LAZ/LAS input requires no PDAL conversion and must be sent to the POTREE stage "
            "directly. Reaching the PDAL stage with one means the pipeline definition names the "
            "wrong stage for this file type."
        )

    # If we were given another file or we could not convert, error
    if laz_filepath is None:
        return ext.error_response(stage,
            "Failed to convert to LAS/LAZ format. Check filename, file paths, and data formats."
        )

    # Upload only: this stage removes nothing. Its destination is the run's own working folder, and in
    # a definition built before those folders existed it is the Potree viewer folder, whose previous
    # octree must stay until the POTREE stage has published a replacement.

    #stage.outputFiles.fileNames = []

    # gather outputs and upload to s3
    for file in pipeline_response["output_files"]:
        object_key = os.path.join(output.objectDir, file)
        file_path = os.path.join(local_output_dir, file)

        logger.info(f"Uploading PDAL File: {file_path}")
        if s3.uploadV2(output.bucketName, object_key, file_path) is None:
            return ext.error_response(stage,
                f"Failed to upload converted point cloud file to S3: {output.bucketName}/{object_key}"
            )

        #Final output filenames to append on stage
        #stage.outputFiles.fileNames.append(object_key)

    return ext.success_response(stage)


def allconvert_pdalconversion_pipeline(input_file_path: str, output_dir: str) -> dict:
    """
    Conversion Pipeline
    Converts Point Cloud Format (E57, PLY and others) to LAZ
    """
    logger.info("Constructing PDAL Conversion Pipeline...")
    filename, extension = os.path.splitext(os.path.basename(input_file_path))

    laz_filepath = os.path.join(output_dir, filename + ext.Extensions.LAZ)

    # Formulate local subprocess to run for PDAL Converter Build. The LAS writer options keep the
    # LAZ this stage hands to PotreeConverter at LAS 1.2 / point data record format 3 (XYZ, intensity,
    # GPS time, RGB), the shape PDAL 2.7.0 wrote by default; PDAL 2.10 defaults to LAS 1.4 / format 7.
    PDAL_CONVERTER_CMD = ['pdal', 'translate',
                          '-i', input_file_path,
                          '-o', laz_filepath] + PDAL_LAS_WRITER_OPTIONS

    logger.info("Executing PDAL Conversion to Laz")

    # Run PDAL local subprocess. Its output is captured so a failure can be reported with PDAL's own
    # message, and logged so the container log still carries it.
    process = subprocess.Popen(PDAL_CONVERTER_CMD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) # nosemgrep: dangerous-subprocess-use-audit
    converter_output, _ = process.communicate()
    returncode = process.returncode
    converter_output = (converter_output or "").strip()
    if converter_output:
        logger.info(f"PDAL output: {converter_output}")

    # Report the converted file only when the converter exited cleanly AND wrote a non-empty file.
    # PDAL opens the writer before it reads the input, so a failed read leaves a 0-byte .laz behind;
    # that file is removed so nothing downstream can take it for an output.
    wrote_output = os.path.isfile(laz_filepath) and os.path.getsize(laz_filepath) > 0
    if returncode != 0 or not wrote_output:
        if os.path.isfile(laz_filepath):
            os.remove(laz_filepath)
        output_files = []
    else:
        output_files = [filename + ext.Extensions.LAZ]

    return {
        "output_dir": output_dir,
        "output_files": output_files,
        "returncode": returncode,
        "converter_output": converter_output,
    }


def converter_failure_message(input_filename: str, returncode: int, converter_output: str) -> str:
    """The stage's error message for a failed `pdal translate`.

    The message is the first part of the Step Functions task failure `error`, which is capped at 256
    characters, so the fixed headline comes first and PDAL's text follows, cut to its last line and
    to PDAL_MESSAGE_MAX_CHARS."""
    detail = converter_output.strip().splitlines()[-1].strip() if converter_output.strip() else ""
    if len(detail) > PDAL_MESSAGE_MAX_CHARS:
        detail = detail[:PDAL_MESSAGE_MAX_CHARS - 3] + "..."
    if input_filename.lower().endswith(ext.Extensions.E57):
        headline = "E57 file could not be read by the converter"
    else:
        headline = f"PDAL translate exited with code {returncode}"
    return f"{headline}: {detail}" if detail else f"{headline}. Check the container logs for the converter output."
