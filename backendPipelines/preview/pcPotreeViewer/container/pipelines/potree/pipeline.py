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
#docker build -f Dockerfile_Potree -t potree:v1 .
#docker run -it -v ${PWD}/inputTest:/data/input:ro -v ${PWD}/outputTest:/data/output:rw potree:v1 "localTest" "POTREE"

def run(stage: PipelineStage, inputMetadataS3Location: str = '', inputConfigurationS3Location: str = '', localTest: bool = False) -> PipelineStage:
    """
    Run the Potree 2.0 Pipeline, then remove the run's working folder (the PDAL intermediate) whether
    the conversion succeeded or not: it belongs to this run alone and no later stage reads it.
    """
    try:
        return convert_and_publish(stage, inputMetadataS3Location, inputConfigurationS3Location, localTest)
    finally:
        if not localTest:
            remove_run_working_files(stage)


def convert_and_publish(stage: PipelineStage, inputMetadataS3Location: str = '', inputConfigurationS3Location: str = '', localTest: bool = False) -> PipelineStage:
    """
    Convert the stage input to a Potree 2.0 octree and publish it to the viewer folder.
    """

    # Debugging: Set to true and update path to point cloud file
    # Production: Set to false
    useLocalBuildFilePath = localTest
    localBuildFilePath = "/data/input/inputLaz.laz"

    # create local input and output dirs in container
    local_input_dir = ext.create_dir(["tmp", "input"])
    local_output_dir = ext.create_dir(["tmp", "output"])

    logger.info("Running Pipeline...")
    logger.info(f"Stage: {stage}")

    # get pipeline stage input and output
    input = StageInput(**stage.inputFile)
    output = StageOutput(**stage.outputFiles)

    # # Check if we are using a temporary intermediate conversion file (source bucket = destination bucket already)
    # usingDestinationTemporaryIntermediateConversionFile = False
    # if (input.bucketName == output.bucketName):
    #     usingDestinationTemporaryIntermediateConversionFile = True

    # get point cloud object from s3. Check first if we have a local build path for debugging
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
    # currently only supports LAZ and LAS
    # extensions are matched case-insensitively, as the upstream allowed-extension gates are
    local_filename_lowered = os.path.basename(local_filepath).lower()
    if not local_filename_lowered.endswith(ext.Extensions.LAZ) and not local_filename_lowered.endswith(ext.Extensions.LAS):
        return ext.error_response(stage,
            "Unsupported file type for point cloud visualization pipeline conversion. Currently only supports LAZ and LAS."
        )

    # If input file is LAZ/LAS, run through Potree Converter Pipeline
    if local_filename_lowered.endswith(ext.Extensions.LAZ) or local_filename_lowered.endswith(ext.Extensions.LAS):
        pipeline_response = potree_conversion_pipeline(
            local_filepath, local_output_dir)
        logger.info(f"Pipeline Response: {pipeline_response}")
    else:
        return ext.error_response(stage,
            "Failed to convert from LAS/LAZ format. Check filename, file paths, and data formats."
        )

    # The converter's exit status and its output are both checked before the destination is
    # cleared, so a failed conversion leaves any existing viewer files in place.
    if pipeline_response["returncode"] != 0:
        return ext.error_response(stage,
            f"PotreeConverter exited with code {pipeline_response['returncode']}. Check the container logs for the converter output."
        )

    if not pipeline_response["output_files"]:
        return ext.error_response(stage,
            "PotreeConverter wrote no output files. Check the container logs for the converter output."
        )

    # The destination holds the previous run's working viewer data. Those objects are listed now and
    # removed only once every replacement is in place, which leaves them intact when an upload fails
    # rather than clearing the destination and then failing with nothing to put back.
    superseded_object_keys = existing_object_keys(output.bucketName, output.objectDir)

    #stage.outputFiles.fileNames = []

    # gather outputs and upload to s3
    uploaded_object_keys = []
    for file in pipeline_response["output_files"]:
        object_key = os.path.join(output.objectDir, file)
        file_path = os.path.join(local_output_dir, file)

        logger.info(f"Uploading Potree File: {file_path}")
        if s3.uploadV2(output.bucketName, object_key, file_path) is None:
            return ext.error_response(stage,
                f"Failed to upload Potree viewer file to S3: {output.bucketName}/{object_key}"
            )
        uploaded_object_keys.append(object_key)

        #Final output filenames to append on stage
        #stage.outputFiles.fileNames.append(object_key)

    delete_superseded_objects(output.bucketName, superseded_object_keys, uploaded_object_keys)

    # send success response back to core | keep source bucket ,key, file extension the same as we are not making intermediate conversion files
    return ext.success_response(stage)


def existing_object_keys(bucket_name: str, object_dir: str) -> list:
    """The object keys already present under the stage's output directory.

    A listing failure yields no keys, which leaves those objects in place. Raising instead would end the
    container before it reports against the workflow's task token, since nothing above ``run`` catches an
    exception -- the parent workflow would then wait out its full task timeout.
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


def remove_run_working_files(stage: PipelineStage) -> None:
    """Remove the objects under the stage's ``temporaryFiles`` folder: the run's own working folder.

    A definition with no temporary folder (a LAS/LAZ run, or one built before run working folders
    existed) removes nothing. The folder must end in '/' so its prefix cannot match a sibling run's
    folder, and must neither hold nor sit inside the viewer folder, so this can never clear the preview.
    Errors are logged, not raised, for the same task-token reason as ``existing_object_keys``.
    """
    try:
        temporary = StageOutput(**stage.temporaryFiles) if isinstance(stage.temporaryFiles, dict) \
            else stage.temporaryFiles
        output = StageOutput(**stage.outputFiles) if isinstance(stage.outputFiles, dict) \
            else stage.outputFiles
        if not temporary.bucketName or not temporary.objectDir or not temporary.objectDir.endswith("/"):
            return
        if temporary.bucketName == output.bucketName and (
                output.objectDir.startswith(temporary.objectDir)
                or temporary.objectDir.startswith(output.objectDir)):
            logger.warning(
                f"Not removing working files: {temporary.objectDir} overlaps the viewer folder {output.objectDir}")
            return
        for object_key in existing_object_keys(temporary.bucketName, temporary.objectDir):
            logger.info(f"Deleting Potree Viewer run working file: {temporary.bucketName}:{object_key}")
            if s3.delete(temporary.bucketName, object_key) is None:
                logger.warning(f"Could not delete run working file: {temporary.bucketName}:{object_key}")
    except Exception as e:
        logger.exception(e)


def potree_conversion_pipeline(input_file_path: str, output_dir: str) -> dict:
    """
    Conversion Pipeline
    Converts LAS/LAZ to PotreeConverter
    """
    logger.info("Constructing LAS/LAZ to PotreeConverter Conversion Pipeline...")

    # Formulate local subprocess to run for PDAL Converter Build
    POTREE_CONVERTER_CMD = ['./PotreeConverter',
                            '--source', input_file_path,
                            '--outdir', output_dir,
                            '--encoding', 'UNCOMPRESSED',
                            '--method', 'poisson']

    logger.info("Executing LAS/LAZ to PotreeConverter Format 2.0...")

    # Run Potree Converter local subprocess
    returncode = subprocess.Popen(POTREE_CONVERTER_CMD).wait() # nosemgrep: dangerous-subprocess-use-audit

    # Get an array of all file names in output directory
    output_files = os.listdir(output_dir)

    return {
        "output_dir": output_dir,
        "output_files": output_files,
        "returncode": returncode
    }
