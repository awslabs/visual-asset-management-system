# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructPipeline-CoordinateTransform")


def _merge_metadata_into_params(
    input_parameters: str, input_metadata: str
) -> str:
    """Merge asset metadata keys into transform parameters.

    Asset metadata values override pipeline defaults, allowing
    per-asset CRS configuration via VAMS metadata fields.

    Recognized metadata keys (case-insensitive):
        sourceCrs, targetCrs, outputFormats,
        sourceScaleFactor, targetScaleFactor,
        applyScaleCorrection, combinedScaleFactor,
        chunkSize, enforceSourceCrs, onMismatch, compressLaz
    """
    RECOGNIZED_KEYS = {
        "sourcecrs": "sourceCrs",
        "targetcrs": "targetCrs",
        "outputformats": "outputFormats",
        "sourcescalefactor": "sourceScaleFactor",
        "targetscalefactor": "targetScaleFactor",
        "applyscalecorrection": "applyScaleCorrection",
        "combinedscalefactor": "combinedScaleFactor",
        "chunksize": "chunkSize",
        "enforcesourcecrs": "enforceSourceCrs",
        "onmismatch": "onMismatch",
        "compresslaz": "compressLaz",
    }

    # Parse base parameters
    params = {}
    if input_parameters:
        try:
            params = (
                json.loads(input_parameters)
                if isinstance(input_parameters, str)
                else input_parameters
            )
        except (json.JSONDecodeError, TypeError):
            params = {}

    # Extract asset metadata
    metadata = {}
    if input_metadata:
        try:
            meta_obj = (
                json.loads(input_metadata)
                if isinstance(input_metadata, str)
                else input_metadata
            )
            metadata = meta_obj.get("VAMS", {}).get("assetMetadata", {})
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    if not metadata:
        return json.dumps(params) if params else input_parameters

    # Merge recognized metadata keys into params (metadata wins)
    for raw_key, value in metadata.items():
        canonical = RECOGNIZED_KEYS.get(raw_key.lower())
        if canonical:
            # Handle outputFormats as comma-separated string → list
            if canonical == "outputFormats" and isinstance(value, str):
                value = [
                    f.strip() for f in value.split(",") if f.strip()
                ]
            params[canonical] = value

    logger.info(f"Merged params (metadata overrides): {params}")
    return json.dumps(params)


def lambda_handler(event, context):
    """
    ConstructPipeline - Coordinate Transform
    Builds pipeline definition for the Batch container job.
    """

    logger.info(f"Event: {event}")

    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath']
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')

    input_bucket, input_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    output_bucket, output_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)

    file_root, extension = os.path.splitext(input_key)

    # Merge asset metadata into pipeline parameters (metadata wins)
    input_parameters = _merge_metadata_into_params(
        event.get('inputParameters', ''),
        event.get('inputMetadata', ''),
    )

    # Build single-stage coordinate transform definition
    transform_stage = {
        "type": "COORD_TRANSFORM",
        "inputFile": {
            "bucketName": input_bucket,
            "objectKey": input_key,
            "fileExtension": extension,
        },
        "outputFiles": {
            "bucketName": output_bucket,
            "objectDir": output_key,
        },
        "outputMetadata": {
            "bucketName": output_bucket if output_s3_asset_metadata_uri else "",
            "objectDir": output_s3_asset_metadata_uri.replace(f"s3://{output_bucket}/", "") if output_s3_asset_metadata_uri else "",
        },
        "transformConfig": input_parameters,
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [transform_stage],
        "assetId": event.get("assetId", ""),
        "databaseId": event.get("databaseId", ""),
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": input_parameters,
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    logger.info(f"Definition: {definition}")

    return {
        "jobName": event.get("jobName"),
        "currentStageType": "COORD_TRANSFORM",
        "definition": [json.dumps(definition)],
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": input_parameters,
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING",
    }
