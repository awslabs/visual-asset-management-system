# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from customLogging.logger import safeLogger
import manifestHelper
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="ConstructPipeline-CoordinateTransform")

s3 = boto3.client('s3', config=retry_config)
sfn = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'), config=retry_config)


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


# String spellings of a false boolean. Asset metadata values arrive as strings, so a metadata
# compressLaz of "false" is truthy in plain Python — a truthiness test would pass every one of them
# through, on exactly the route the recognized-metadata-key table advertises.
_FALSE_SPELLINGS = {"false", "0", "no", "off"}
_TRUE_SPELLINGS = {"true", "1", "yes", "on"}

# The compressed LAS format. compressLaz and a laz entry in outputFormats are two controls over the
# same property, so they have to agree.
_LAZ_FORMAT = "laz"


def _as_bool(value):
    """The boolean a compressLaz value stands for, or None when it names neither.

    A value that names neither keeps the field's default rather than being rejected: the container
    reads compressLaz with a default of true, so an unrecognized spelling has always meant true.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _FALSE_SPELLINGS:
            return False
        if lowered in _TRUE_SPELLINGS:
            return True
    return None


def _output_formats_list(value):
    """The output-format list a parameter value stands for, whichever form it arrived in."""
    if isinstance(value, str):
        return [entry.strip() for entry in value.split(",") if entry.strip()]
    if isinstance(value, (list, tuple)):
        return [str(entry).strip() for entry in value]
    return []


def _validated_transform_params(input_parameters):
    """The merged parameter set with compressLaz resolved to a real boolean.

    LAZ is the compressed LAS format, so `compressLaz: false` with `laz` in `outputFormats` asks for
    a compressed file and for it not to be compressed. That run is refused rather than served with
    one of the two settings discarded.

    The asymmetric case is deliberate: only false + laz is refused. compressLaz defaults to true, so
    rejecting true + a laz-free format list would fail every ordinary `outputFormats: ["las"]` run
    that never mentioned compressLaz.
    """
    if not input_parameters:
        return input_parameters
    try:
        params = (json.loads(input_parameters)
                  if isinstance(input_parameters, str) else input_parameters)
    except (json.JSONDecodeError, TypeError):
        return input_parameters
    if not isinstance(params, dict) or "compressLaz" not in params:
        return input_parameters

    compress = _as_bool(params["compressLaz"])
    if compress is None:
        compress = True
    params["compressLaz"] = compress

    if not compress:
        formats = [entry.lower() for entry in _output_formats_list(params.get("outputFormats"))]
        if _LAZ_FORMAT in formats:
            raise ValueError(
                "compressLaz is false but outputFormats requests laz. LAZ is the compressed LAS "
                "format, so the two settings contradict: request las for uncompressed output, or "
                "leave compressLaz at its default."
            )

    return json.dumps(params)


def _asset_relative_file_key(input_key, asset_id):
    """The asset-relative file key ('/folder/file.ext') of an input S3 key, sliced at the
    threaded assetId path segment. Returns the asset-level key ('/') when the asset id is not a
    segment of the key."""
    parts = input_key.split("/")
    if asset_id and asset_id in parts:
        return "/" + "/".join(parts[parts.index(asset_id) + 1:])
    return "/"


def _split_s3_uri(field_name, s3_uri):
    """The bucket and key of an 's3://bucket/key' location, named by the payload field it came from.

    Raises rather than unpacking short, so a payload whose path field is absent or carries no key
    reports which field was empty instead of a bare 'not enough values to unpack'."""
    bucket, _, key = (s3_uri or "").replace("s3://", "").partition("/")
    if not bucket or not key:
        raise ValueError(f"{field_name} is not an s3://bucket/key location: '{s3_uri}'")
    return bucket, key


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so a failure here does not leave the
    pipeline task waiting for its full taskTimeout. This task is the first state of the pipeline's
    state machine, so nothing further downstream can report on the token.

    Never raises: the caller re-raises the original error, which is the one worth reading."""
    if not task_token:
        return
    try:
        sfn.send_task_failure(
            taskToken=task_token,
            error="CoordinateTransformPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    """
    ConstructPipeline - Coordinate Transform
    Builds pipeline definition for the Batch container job.
    """

    logger.info(f"Event: {event}")

    try:
        return _build_execution_params(event)
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get('externalSfnTaskToken', ''))
        raise


def _build_execution_params(event):
    """The state machine payload for the Batch container job."""

    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')

    input_bucket, input_key = _split_s3_uri(
        'inputS3AssetFilePath', event.get('inputS3AssetFilePath'))
    output_bucket, output_key = _split_s3_uri(
        'outputS3AssetFilesPath', event.get('outputS3AssetFilesPath'))

    file_root, extension = os.path.splitext(input_key)

    # Read the input configuration + shared metadata content from their S3 locations
    # (only the locations travel in the SFN payload), then merge asset metadata into
    # pipeline parameters (metadata wins).
    input_configuration = manifestHelper.fetch_input_configuration(
        s3, event.get('inputConfigurationS3Location', '')) or {}
    metadata_body = manifestHelper.fetch_metadata(
        s3, event.get('inputMetadataS3Location', '')) or {}
    # The metadata file is the grouped-by-asset envelope; project it onto the legacy
    # {"VAMS": {...}} view for this pipeline's (databaseId, assetId, fileKey).
    input_metadata = manifestHelper.to_legacy_vams_view(
        metadata_body,
        event.get('databaseId', ''),
        event.get('assetId', ''),
        _asset_relative_file_key(input_key, event.get('assetId', '')),
    ) if metadata_body else {}
    input_parameters = _validated_transform_params(_merge_metadata_into_params(
        json.dumps(input_configuration) if input_configuration else '',
        json.dumps(input_metadata) if input_metadata else '',
    ))

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
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [transform_stage],
        "assetId": event.get("assetId", ""),
        "databaseId": event.get("databaseId", ""),
        # Declared by the container's PipelineDefinition, so the key travels; the metadata CONTENT
        # does not. It is resolved into inputParameters above, and the metadata file's S3 location
        # travels in the state machine input for a consumer that needs the raw document — which is
        # what keeps a large metadata file out of the Step Functions payload and out of the Batch
        # command override that carries this definition.
        "inputMetadata": "",
        # The single copy of the transform configuration: this is the field the container reads.
        "inputParameters": input_parameters,
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    logger.info(f"Definition: {definition}")

    return {
        "jobName": event.get("jobName"),
        "currentStageType": "COORD_TRANSFORM",
        "definition": [json.dumps(definition)],
        # Both of these are re-emitted because this task's outputPath is $.Payload, which REPLACES
        # the state — a value only present in the state machine's original input would be dropped
        # here. pipelineEnd reads the token to release the external workflow task, and the batch
        # task reads the event prefix to register the Batch job as abortable.
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "orchestrationEventPrefix": event.get("orchestrationEventPrefix", ""),
        "status": "STARTING",
    }
