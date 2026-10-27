#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="ConstructImageTo3dPipeline")

SUPPORTED_MODELS = ("sam3d", "stable3dgen", "trellis2", "triposr", "step1x3d", "hunyuan3d", "blenderagent")
DEFAULT_MODEL = "stable3dgen"


def _as_dir(key):
    """A bucket-relative prefix the container can append filenames to."""
    if not key:
        return ""
    return key if key.endswith("/") else key + "/"


def _bucket_and_dir(s3_uri):
    """``(bucketName, objectDir)`` for an ``s3://`` prefix, or ``("", "")`` when absent."""
    bucket, key = manifestHelper.parse_s3_uri(s3_uri)
    if not bucket:
        return "", ""
    return bucket, _as_dir(key)


def bucket_regions(event) -> dict:
    """Bucket name -> Region for the asset buckets this job reads and writes, from the Regions the
    vamsExecute Lambda resolved from the manifest. The container addresses each registered bucket in
    that Region. A bucket in the deployment Region (an empty Region, or an event from an older
    Lambda) is left out, as is the auxiliary bucket: requests for those use the default client."""
    input_region = event.get("inputBucketRegion") or ""
    output_region = event.get("outputBucketRegion") or ""
    input_uris = [event.get("inputS3AssetFilePath")] + list(event.get("inputS3AssetFilePaths") or [])
    output_uris = [event.get(path_key) for path_key in
                   ("outputS3AssetFilesPath", "outputS3AssetMetadataPath", "outputS3AssetResultsPath")]
    regions = {}
    for uris, region in ((input_uris, input_region), (output_uris, output_region)):
        for uri in uris:
            bucket, _key = manifestHelper.parse_s3_uri(uri or "")
            if bucket and region:
                regions[bucket] = region
    return regions


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds the pipeline input definition to run the Batch application for image-to-3D generation.
    """
    logger.info("Event", event=event)

    job_name = event.get("jobName", "")
    # modelKey is the state machine's Choice discriminator, so it is normalized here as well as at
    # the vamsExecute boundary; an unrecognized value routes to the not-enabled failure state.
    model = str(event.get("model", "") or DEFAULT_MODEL).strip().lower()
    if model not in SUPPORTED_MODELS:
        logger.warning(f"Unrecognized image-to-3D model '{model}'; no job definition will match it")
    asset_id = event.get("assetId", "")
    database_id = event.get("databaseId", "")
    input_s3_asset_file_uri = event["inputS3AssetFilePath"]
    output_s3_asset_files_uri = event.get("outputS3AssetFilesPath", "")
    output_s3_asset_metadata_uri = event.get("outputS3AssetMetadataPath", "")
    output_s3_asset_results_uri = event.get("outputS3AssetResultsPath", "")
    inputOutput_s3_assetAuxiliary_files_uri = event["inputOutputS3AssetAuxiliaryFilesPath"]
    # Metadata + input-configuration S3 LOCATIONS travel onward (never the inline content); the
    # container reads them from S3 as needed.
    input_metadata_s3_location = event.get("inputMetadataS3Location", "")
    input_configuration_s3_location = event.get("inputConfigurationS3Location", "")
    external_sfn_task_token = event.get("externalSfnTaskToken", "")

    input_bucket, input_key = manifestHelper.parse_s3_uri(input_s3_asset_file_uri)
    input_root, input_extension = os.path.splitext(input_key)

    # The workflow output path is what process-output reads; the input-adjacent prefix is only a
    # fallback for a direct/local invocation with no workflow context.
    output_bucket, output_dir = _bucket_and_dir(output_s3_asset_files_uri)
    if not output_bucket:
        output_bucket = input_bucket
        output_dir = f"{input_root}/genAi/imageTo3d/"

    metadata_bucket, metadata_dir = _bucket_and_dir(output_s3_asset_metadata_uri)
    results_bucket, results_dir = _bucket_and_dir(output_s3_asset_results_uri)
    temporary_bucket, temporary_dir = _bucket_and_dir(inputOutput_s3_assetAuxiliary_files_uri)

    definition = {
        "jobName": job_name,
        "model": model,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputFile": {
            "bucketName": input_bucket,
            "objectKey": input_key,
            "fileExtension": input_extension,
        },
        # Every view of the subject, in manifest order, for a model that reconstructs from several.
        # ADDITIVE: inputFile above stays the primary and is what asset identity, the relative output
        # subdirectory and every single-view model read, so an existing run is unchanged. A single-view
        # run carries one entry here, and the container falls back to inputFile when the list is absent.
        "inputFiles": [
            {
                "bucketName": bucket,
                "objectKey": key,
                "fileExtension": os.path.splitext(key)[1],
            }
            for bucket, key in (
                manifestHelper.parse_s3_uri(uri)
                for uri in (event.get("inputS3AssetFilePaths") or [input_s3_asset_file_uri])
            )
            if bucket and key
        ],
        "outputFiles": {
            "bucketName": output_bucket,
            "objectDir": output_dir,
        },
        "outputMetadata": {
            "bucketName": metadata_bucket,
            "objectDir": metadata_dir,
        },
        # Read by the workflow onto the EXECUTION record rather than written into the asset, so a
        # report belongs here and not among the asset's files. Empty for a direct invocation with no
        # workflow context, in which case the container keeps the report with its other output.
        "outputResults": {
            "bucketName": results_bucket,
            "objectDir": results_dir,
        },
        "temporaryFiles": {
            "bucketName": temporary_bucket,
            "objectDir": temporary_dir,
        },
        # Metadata + input-configuration S3 locations travel with the definition, not their content
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        # The Region of each asset bucket outside the deployment Region; the container registers it
        # before its first S3 call.
        "bucketRegions": bucket_regions(event),
    }

    logger.info(f"Definition: {definition}")

    return {
        "jobName": job_name,
        "modelKey": model,
        "definition": ["python", "__main__.py", json.dumps(definition)],
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token,
        "status": "STARTING",
    }
