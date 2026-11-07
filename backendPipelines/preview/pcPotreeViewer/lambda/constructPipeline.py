#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import uuid
import boto3
from customLogging.logger import safeLogger
from botocore.config import Config
import lasHeader

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="ConstructPipelineVisualizer")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

PDAL_INPUT_EXTENSIONS = (".e57", ".ply")
POTREE_INPUT_EXTENSIONS = (".las", ".laz")

# The LAS/LAZ input's point data record format, which openPipeline reads from the file's header.
INPUT_LAS_POINT_FORMAT_KEY = "inputLasPointFormat"


def las_input_needs_pdal(event) -> bool:
    """True when openPipeline read a point data record format PotreeConverter cannot read (8 to 10).
    A missing or malformed value (an unreadable header, or an execution started before the field)
    keeps the direct path to the POTREE stage."""
    point_format = event.get(INPUT_LAS_POINT_FORMAT_KEY)
    if isinstance(point_format, bool) or not isinstance(point_format, int):
        return False
    return lasHeader.needs_pdal_conversion(point_format)

# The input's launch version travels as a second Batch command argument, {"inputVersions": {<stage
# type>: <version id>}}, never inside the definition. An image built before the field parses the
# definition into dataclasses that reject an unknown key at every level, but reads only the first
# argument, so a run that reaches such an image during an upgrade still starts.
INPUT_VERSIONS_KEY = "inputVersions"


def definition_command(definition, event) -> list:
    """The Batch command for ``definition``: the definition, then the first stage's launch version when
    the run has one. Without a version it is the one-element command written before the field. Only
    the first stage reads the selected input; a POTREE stage after PDAL reads PDAL's output, and the
    command PDAL hands it carries no version."""
    command = [json.dumps(definition)]
    version_id = event.get("inputVersionId") or ""
    if version_id:
        command.append(json.dumps({INPUT_VERSIONS_KEY: {definition["stages"][0]["type"]: version_id}}))
    return command


# Sibling of the PotreeViewer folder, not inside it and not sharing its key prefix: the POTREE stage
# lists the viewer folder and removes what it did not write, so a working folder there would be cleared.
RUN_WORKING_FOLDER = "PotreeViewerRuns"


def run_working_dir(base_asset_auxiliary_key_path, job_name) -> str:
    """The aux-bucket folder one two-stage run (E57, PLY, or a LAS/LAZ in a point format PotreeConverter
    cannot read) keeps its PDAL intermediate in, with a trailing slash.

    Keyed on the run's job name, so two runs on one file never share an intermediate; it stays under
    the input file's preview prefix, so deleting, moving or copying the file's auxiliary data covers it."""
    run_id = job_name or f"PipelineJob_{uuid.uuid4().hex}"
    return f"{base_asset_auxiliary_key_path}/preview/{RUN_WORKING_FOLDER}/{run_id}/"


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token. The state machine carries no catch on
    this task and the following choice reads a field an error return does not have, so reporting
    here is what ends the waiting task instead of leaving it until the full taskTimeout."""
    if not task_token:
        return
    try:
        sfn.send_task_failure(
            taskToken=task_token,
            error="PcPotreeViewerPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application
    """

    logger.info("Event", event=event)
    logger.info(f"Context: {context}")

    try:
        input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(event['inputS3AssetFilePath'])

        # extensions are matched case-insensitively, as the allowed-extension gate in openPipeline is
        input_s3_asset_extension = input_s3_asset_extension.lower()

        # construct different pipeline definitions based on file type
        if input_s3_asset_extension in PDAL_INPUT_EXTENSIONS:
            definition = construct_pdal_definition(event)
        elif input_s3_asset_extension in POTREE_INPUT_EXTENSIONS:
            if las_input_needs_pdal(event):
                # PotreeConverter cannot read this point format: PDAL rewrites it as LAS 1.2 / format 3
                # first, as it does an E57 or PLY input.
                logger.info(f"LAS point data record format {event.get(INPUT_LAS_POINT_FORMAT_KEY)} is "
                            f"not read by PotreeConverter; converting it with PDAL first")
                definition = construct_pdal_definition(event)
            else:
                definition = construct_potree_definition(event)
        else:
            raise ValueError(
                "Unsupported file type for point cloud potree viewer pipeline conversion. "
                "Currently only supports E57, PLY, LAZ, and LAS.")

        logger.info(f"Definition: {definition}")

        return {
            "jobName": event.get("jobName"),
            "currentStageType": definition["stages"][0]["type"],
            "definition": definition_command(definition, event),
            "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
            "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
            "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
            "status": "STARTING"
        }
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get("externalSfnTaskToken", ""))
        raise


def bucket_regions(event) -> dict:
    """Bucket name -> Region for the asset buckets this job reads and writes, from the Regions the
    vamsExecute Lambda resolved from the manifest. The container signs each bucket's requests with a
    client for that Region. A bucket in the deployment Region (an empty Region, or an event from an
    older Lambda) is left out: requests for it use the container's default client, as do the
    auxiliary bucket's."""
    regions = {}
    for path_key, region_key in (("inputS3AssetFilePath", "inputBucketRegion"),
                                 ("outputS3AssetFilesPath", "outputBucketRegion")):
        uri, region = event.get(path_key) or "", event.get(region_key) or ""
        if uri.startswith("s3://") and region:
            regions[uri[len("s3://"):].split("/", 1)[0]] = region
    return regions


def construct_pdal_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    #output_s3_asset_files_uri = event['outputS3AssetFilesPath'] #unused in this pipeline
    #output_s3_asset_preview_uri = event['outputS3AssetPreviewPath'] #unused in this pipeline
    #output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath'] #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    #output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_preview_bucket, output_s3_asset_preview_key = output_s3_asset_preview_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_metadata_bucket, output_s3_asset_metadata_key = output_s3_asset_metadata_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]

    #Override output path as preview pipeline path is very specific for the potree viewer
    #Go up in the directory structure two and then add the hardcoded path 
    baseAssetAuxiliaryKeyPath = os.path.dirname(os.path.dirname(inputOutput_s3_assetAuxiliary_files_key))
    inputOutput_s3_assetAuxiliary_files_key = baseAssetAuxiliaryKeyPath + '/preview/PotreeViewer/'

    # The PDAL intermediate goes to this run's own working folder, named as both stages' temporaryFiles:
    # the POTREE stage reads it from there and removes it, and only the POTREE stage writes the viewer
    # folder, so the previous preview stays until a replacement exists.
    run_working_files = {
        "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
        "objectDir": run_working_dir(baseAssetAuxiliaryKeyPath, event.get("jobName")),
    }

    pdal_stage = {
        "type": "PDAL",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            # The launch version is not written here: it travels beside the definition (definition_command).
            "fileExtension": input_s3_asset_extension,
        },
        "outputFiles": dict(run_working_files),
        "outputMetadata": {
            "bucketName": "",
            "objectDir": "",
        },
        "temporaryFiles": dict(run_working_files),
    }

    potree_stage = {
        "type": "POTREE",
        "inputFile": {
            "bucketName": run_working_files["bucketName"],
            "objectKey": os.path.join(run_working_files["objectDir"], input_s3_asset_file_filename + ".laz"),
            "fileExtension": ".laz"
        },
        "outputFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": inputOutput_s3_assetAuxiliary_files_key,
        },
        "outputMetadata": {
            "bucketName": "",
            "objectDir": "",
        },
        "temporaryFiles": dict(run_working_files),
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [pdal_stage, potree_stage],
        "bucketRegions": bucket_regions(event),
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    return definition


def construct_potree_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    #output_s3_asset_files_uri = event['outputS3AssetFilesPath'] #unused in this pipeline
    #output_s3_asset_preview_uri = event['outputS3AssetPreviewPath'] #unused in this pipeline
    #output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath'] #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    #output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_preview_bucket, output_s3_asset_preview_key = output_s3_asset_preview_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    #output_s3_asset_metadata_bucket, output_s3_asset_metadata_key = output_s3_asset_metadata_uri .replace("s3://", "").split("/", 1) #unused in this pipeline
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]

    #Override output path as preview pipeline path is very specific for the potree viewer
    #Split provided AssetAuxiliary Path and keep everything but the last two path elements
    baseAssetAuxiliaryKeyPath = os.path.dirname(os.path.dirname(inputOutput_s3_assetAuxiliary_files_key))
    inputOutput_s3_assetAuxiliary_files_key = baseAssetAuxiliaryKeyPath + '/preview/PotreeViewer/'

    potree_stage = {
        "type": "POTREE",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            # The launch version is not written here: it travels beside the definition (definition_command).
            "fileExtension": input_s3_asset_extension,
        },
        "outputFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": inputOutput_s3_assetAuxiliary_files_key,
        },
        "outputMetadata": {
            "bucketName": "",
            "objectDir": "",
        },
        "temporaryFiles": {
            "bucketName": "",
            "objectDir": "",
        }
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [potree_stage],
        "bucketRegions": bucket_regions(event),
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    return definition
