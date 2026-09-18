#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda to execute the Video SOP/BOM Extraction pipeline from VAMS workflows.
Note: Lambda function name must start with "vams" to allow invoke permissioning.

Holds the workflow's task token before any other work, resolves the manifest, applies the input
gates that need no download (container entries, count, extension, per-file and total bytes via one
HeadObject per entry) and only then invokes openPipeline. Every refusal is one SendTaskFailure on
the external token with a short code in `error` and one readable sentence in `cause`.
"""
import os
import boto3
import json
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger
import manifestHelper
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="VamsExecuteVideoSopBomPipeline")
lambda_client = boto3.client('lambda', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'), config=retry_config)

OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.m4v,.webm,.mkv")
# Deployment caps. Read without defaults so a builder that omits one fails at import, not at the
# first run that should have been refused.
MAX_VIDEO_FILES = int(os.environ["VIDEO_SOP_BOM_MAX_VIDEO_FILES"])
MAX_VIDEO_FILE_SIZE_MB = int(os.environ["VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB"])
MAX_TOTAL_INPUT_SIZE_MB = int(os.environ["VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB"])

MIB = 1024 * 1024
GIB = 1024 * MIB

ERROR_INPUT_REJECTED = "VideoSopBomInputRejected"
ERROR_PIPELINE = "VideoSopBomPipelineError"
# The stored executionError budget; the SendTaskFailure API itself accepts 32768.
MAX_CAUSE_CHARS = 16384


class PipelineRejection(Exception):
    """A refusal with a stable code for `error` and one readable sentence for `cause`."""

    def __init__(self, code, cause):
        super().__init__(cause)
        self.code = code
        self.cause = cause


def allowed_extensions():
    return [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',') if ext.strip()]


def _size(byte_count):
    """An observed size as the operator reads it: binary GB to one decimal from 1 GiB up, binary MB
    to one decimal below (a cap lowered to a few MB for a smoke arm still yields readable figures)."""
    if byte_count >= GIB:
        return f"{byte_count / GIB:.1f} GB"
    return f"{byte_count / MIB:.1f} MB"


def _cap(megabytes):
    """A configured cap as its operator set it: a whole number of GB reads as GB, anything else as
    the MB integer itself, so the cause always carries the configured figure."""
    if megabytes % 1024 == 0:
        return f"{megabytes / 1024:.1f} GB"
    return f"{megabytes} MB"


def _display_name(entry):
    relative = str(entry.get("relativePath") or "")
    return relative.rsplit("/", 1)[-1] or str(entry.get("key") or "<unnamed>")


def head_object_kwargs(entry):
    """HeadObject arguments for a manifest entry: VersionId only when the manifest carries a real
    one. An unversioned bucket reports the literal "null", and an empty query value is never sent."""
    kwargs = {"Bucket": entry["bucket"], "Key": entry["key"]}
    version_id = entry.get("versionId") or ""
    if version_id and version_id != "null":
        kwargs["VersionId"] = version_id
    return kwargs


def enforce_input_gates(input_files):
    """The gates that need no download, in the order that costs nothing until HeadObject:
    container entries, count, extension, then one HeadObject per entry for the per-file and total
    byte caps. Raises PipelineRejection; returns the summed byte count."""
    containers = [str(f.get("key", "")) for f in input_files if str(f.get("key", "")).endswith("/")]
    if containers:
        raise PipelineRejection(
            ERROR_INPUT_REJECTED,
            f"{containers[0]} is a whole-asset or folder selection; this pipeline needs explicit "
            f"video files.")
    if not input_files:
        raise PipelineRejection(
            ERROR_INPUT_REJECTED, "no video files were selected; this pipeline needs at least 1.")
    if len(input_files) > MAX_VIDEO_FILES:
        raise PipelineRejection(
            ERROR_INPUT_REJECTED,
            f"{len(input_files)} video files selected; this deployment allows at most "
            f"{MAX_VIDEO_FILES}.")

    # Exact membership of the parsed list, lower-cased on both sides: the platform filter is
    # case-insensitive and .MP4 is a camera default.
    allowed = allowed_extensions()
    for entry in input_files:
        _, extension = os.path.splitext(str(entry.get("key", "")))
        if not extension or extension.lower() not in allowed:
            raise PipelineRejection(
                ERROR_INPUT_REJECTED,
                f"{_display_name(entry)} has the extension {extension or '(none)'}; this pipeline "
                f"accepts {', '.join(allowed)}.")

    per_file_cap = MAX_VIDEO_FILE_SIZE_MB * MIB
    total_cap = MAX_TOTAL_INPUT_SIZE_MB * MIB
    total = 0
    for entry in input_files:
        try:
            size = int(s3_client.head_object(**head_object_kwargs(entry))["ContentLength"])
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise PipelineRejection(
                ERROR_PIPELINE,
                f"{entry.get('relativePath') or entry.get('key', '')}: HeadObject on "
                f"s3://{entry.get('bucket', '')}/{entry.get('key', '')} failed ({code}) — the "
                f"pipeline Lambda cannot read this asset bucket")
        if size > per_file_cap:
            raise PipelineRejection(
                ERROR_INPUT_REJECTED,
                f"{_display_name(entry)} is {_size(size)}; this deployment allows at most "
                f"{_cap(MAX_VIDEO_FILE_SIZE_MB)} per video.")
        total += size
    if total > total_cap:
        raise PipelineRejection(
            ERROR_INPUT_REJECTED,
            f"the {len(input_files)} selected videos total {_size(total)}; this deployment allows at "
            f"most {_cap(MAX_TOTAL_INPUT_SIZE_MB)} in total.")
    return total


def execute_pipeline(resolved, input_files, manifest_s3_location, external_task_token,
                     executing_userName, executing_requestContext):

    messagePayload = {
        "inputFiles": input_files,
        "inputS3AssetFilePath": resolved['inputS3AssetFilePath'],
        "outputS3AssetFilesPath": resolved['outputS3AssetFilesPath'],
        "outputS3AssetPreviewPath": resolved['outputS3AssetPreviewPath'],
        "outputS3AssetMetadataPath": resolved['outputS3AssetMetadataPath'],
        "inputOutputS3AssetAuxiliaryFilesPath": resolved['inputOutputS3AssetAuxiliaryFilesPath'],
        "assetId": resolved['assetId'],
        "databaseId": resolved['databaseId'],
        # constructPipeline reads the manifest envelope itself (outputs.results, auxBucket,
        # auxTempPrefix, outputTarget), so the pointer travels with the resolved locations.
        "inputManifestS3Location": manifest_s3_location,
        "inputMetadataS3Location": resolved['inputMetadataS3Location'],
        "inputConfigurationS3Location": resolved['inputConfigurationS3Location'],
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "orchestrationEventPrefix": resolved['orchestrationEventPrefix'],
    }

    logger.info("Invoking Open Pipeline Lambda")
    lambda_response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(messagePayload).encode('utf-8')
    )
    logger.info(f"Lambda response status: {lambda_response.get('StatusCode')}")

    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)

    # A handled invocation still returns StatusCode 200 when the invoked function raised: the
    # failure is reported via FunctionError. Without this check an unhandled error in
    # openPipeline reads as success here, so no task-token failure is ever sent and the
    # workflow's callback task blocks until taskTimeout.
    if lambda_response.get('FunctionError'):
        raise Exception(
            "Invoke Open Pipeline Lambda Failed: " + str(lambda_response.get('FunctionError')))


def failure_for(error):
    """(error code, cause) for the external token: a PipelineRejection carries its own; anything
    else is a pipeline error whose message is the cause."""
    if isinstance(error, PipelineRejection):
        return error.code, error.cause[:MAX_CAUSE_CHARS]
    return ERROR_PIPELINE, (str(error) or error.__class__.__name__)[:MAX_CAUSE_CHARS]


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so the pipeline task does not wait
    for the full taskTimeout when this lambda cannot start the pipeline."""
    if not task_token:
        return
    code, cause = failure_for(error)
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error=code,
            cause=cause
        )
        logger.info(f"Sent task failure callback to Step Functions ({code})")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    external_task_token = None

    try:
        if not event.get('body'):
            raise ValueError('Request body is required')

        if isinstance(event['body'], str):
            data = json.loads(event['body'])
        else:
            data = event['body']
        # A dict, so the formatter redacts TaskToken; an f-string of it would not be.
        logger.info(data)

        if 'TaskToken' in data:
            external_task_token = data['TaskToken']
        else:
            raise Exception(
                "VAMS Workflow TaskToken not found in pipeline input. "
                "Register this pipeline as needing a task token callback."
            )

        executing_userName = data.get('executingUserName', '')
        executing_requestContext = data.get('executingRequestContext', '')

        # Resolve input/output locations from the workflow manifest. A referenced manifest that
        # cannot be read raises here, after the token capture, so it reaches the abort below.
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        input_files = list(resolved.get("inputFiles") or [])
        total_bytes = enforce_input_gates(input_files)
        logger.info(f"Input gates passed: {len(input_files)} video files, {total_bytes} bytes "
                    f"(manifestUsed={resolved['manifestUsed']})")

        execute_pipeline(
            resolved,
            input_files,
            manifestHelper.manifest_location(data),
            external_task_token,
            executing_userName,
            executing_requestContext,
        )

        return {'statusCode': 200, 'body': 'Success'}

    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, external_task_token)
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "Internal Server Error"})
        }
