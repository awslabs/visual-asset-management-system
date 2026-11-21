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

logger = safeLogger(service="ConstructPipeline-CogConvert")

s3 = boto3.client('s3', config=retry_config)
sfn = boto3.client('stepfunctions', region_name=os.environ['AWS_REGION'], config=retry_config)

# The input's launch version travels as a second Batch command argument, {"inputVersions": {<stage
# type>: <version id>}}, never inside the definition. An image built before the field parses the
# definition into dataclasses that reject an unknown key at every level, but reads only the first
# argument, so a run that reaches such an image during an upgrade still starts. executeBatchJob
# submits the whole list as the command.
INPUT_VERSIONS_KEY = "inputVersions"
# Where the input sits in its asset, from its manifest entry, in the same second argument:
# {"relativePath", "assetRootS3Key", "assetRootBucket"}. That document always carries INPUT_VERSIONS_KEY
# (empty when the run has no version): an image that reads only the version accepts an empty one and
# ignores the other key, while it refuses a document without it.
INPUT_ASSET_KEY = "inputAsset"


def input_asset(event) -> dict:
    """The input's asset-relative path and asset root from the event, or None when the event names
    neither (a payload without a manifest, or an event from an older Lambda)."""
    location = {
        "relativePath": event.get("inputRelativePath") or "",
        "assetRootS3Key": event.get("inputAssetRootS3Key") or "",
        "assetRootBucket": event.get("inputAssetRootBucket") or "",
    }
    return location if location["relativePath"] or location["assetRootS3Key"] else None


def definition_command(definition, event) -> list:
    """The Batch command for ``definition``: the definition, then, when the run has a launch version or
    an input asset location, one document naming them. With neither it is the one-element command
    written before those fields."""
    command = [json.dumps(definition)]
    version_id = event.get("inputVersionId") or ""
    asset = input_asset(event)
    if version_id or asset:
        extra = {INPUT_VERSIONS_KEY: {definition["stages"][0]["type"]: version_id} if version_id else {}}
        if asset:
            extra[INPUT_ASSET_KEY] = asset
        command.append(json.dumps(extra))
    return command


# Settings the template's configBody may carry. Everything else in the input configuration is dropped
# rather than forwarded, so the container only ever sees values this lambda has checked.
ALLOWED_COMPRESSION = ("DEFLATE",)
DEFAULT_PARAMS = {"compression": "DEFLATE"}


def _cog_params(input_configuration) -> str:
    """The container's parameter JSON: the defaults, overridden by the recognized keys of the input
    configuration. A value outside the allowed set is refused, not silently replaced, so a run never
    produces a file other than the one the operator asked for. Asset metadata does not feed this
    pipeline."""
    params = dict(DEFAULT_PARAMS)
    config = input_configuration if isinstance(input_configuration, dict) else {}
    compression = config.get("compression")
    if compression is not None:
        if str(compression).upper() not in ALLOWED_COMPRESSION:
            raise ValueError(
                f"compression must be one of {list(ALLOWED_COMPRESSION)}, not '{compression}'")
        params["compression"] = str(compression).upper()
    return json.dumps(params)


def _split_s3_uri(field_name, s3_uri):
    """The bucket and key of an 's3://bucket/key' location, named by the payload field it came from.

    Raises rather than unpacking short, so a payload whose path field is absent or carries no key
    reports which field was empty instead of a bare 'not enough values to unpack'."""
    bucket, _, key = (s3_uri or "").replace("s3://", "").partition("/")
    if not bucket or not key:
        raise ValueError(f"{field_name} is not an s3://bucket/key location: '{s3_uri}'")
    return bucket, key


# AWS Step Functions rejects a task failure whose `error` is over 256 characters or whose `cause` is over 32,768,
# and the failure it carried is lost. The error stays a short name; the text goes in `cause`.
ERROR_NAME_LIMIT = 256
CAUSE_LIMIT = 32768


def _bounded_error(name):
    return str(name or "CogConvertFailed")[:ERROR_NAME_LIMIT]


def _bounded_cause(text):
    text = str(text or "See AWS CloudWatch logs for the error cause.")
    marker = " ... [truncated]"
    return text if len(text) <= CAUSE_LIMIT else text[:CAUSE_LIMIT - len(marker)] + marker


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
            error=_bounded_error("CogConvertPipelineError"),
            cause=_bounded_cause(error)
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    """
    ConstructPipeline - COG conversion
    Builds pipeline definition for the Batch container job.
    """

    logger.info("Event", event=event)

    try:
        return _build_execution_params(event)
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get('externalSfnTaskToken', ''))
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


def _build_execution_params(event):
    """The state machine payload for the Batch container job."""

    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')

    input_bucket, input_key = _split_s3_uri(
        'inputS3AssetFilePath', event.get('inputS3AssetFilePath'))
    output_bucket, output_key = _split_s3_uri(
        'outputS3AssetFilesPath', event.get('outputS3AssetFilesPath'))
    output_s3_asset_results_uri = event.get('outputS3AssetResultsPath', '')

    _, extension = os.path.splitext(input_key)

    # Read the input configuration from its S3 location (only the location travels in the SFN payload).
    input_configuration = manifestHelper.fetch_input_configuration(
        s3, event.get('inputConfigurationS3Location', '')) or {}
    input_parameters = _cog_params(input_configuration)

    # Build single-stage COG conversion definition
    convert_stage = {
        "type": "COG_CONVERT",
        "inputFile": {
            "bucketName": input_bucket,
            "objectKey": input_key,
            # The launch version is not written here: it travels beside the definition (definition_command).
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
        # The run's results folder: a skipped run (already a COG, not georeferenced) leaves a note here.
        "outputResults": {
            "bucketName": output_bucket if output_s3_asset_results_uri else "",
            "objectDir": output_s3_asset_results_uri.replace(f"s3://{output_bucket}/", "") if output_s3_asset_results_uri else "",
        },
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [convert_stage],
        "bucketRegions": bucket_regions(event),
        "assetId": event.get("assetId", ""),
        "databaseId": event.get("databaseId", ""),
        # Declared by the container's PipelineDefinition; this pipeline reads no asset metadata, so
        # no content travels (which also keeps the Batch command override small).
        "inputMetadata": "",
        # The single copy of the conversion configuration: this is the field the container reads.
        "inputParameters": input_parameters,
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    logger.info(f"Definition: {definition}")

    return {
        "jobName": event.get("jobName"),
        "currentStageType": "COG_CONVERT",
        "definition": definition_command(definition, event),
        # Both of these are re-emitted because this task's outputPath is $.Payload, which REPLACES
        # the state — a value only present in the state machine's original input would be dropped
        # here. pipelineEnd reads the token to release the external workflow task, and the batch
        # task reads the event prefix to register the Batch job as abortable.
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "orchestrationEventPrefix": event.get("orchestrationEventPrefix", ""),
        "status": "STARTING",
    }
