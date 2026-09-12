#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from customLogging.logger import safeLogger
import manifestHelper
import configSchema
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="ConstructPipeline-VideoSopBom")

s3 = boto3.client('s3', config=retry_config)
sfn = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'), config=retry_config)

# Deployment caps and static values, embedded in the definition document as the container's one
# source of truth. Read without defaults so a builder that omits one fails at import.
MAX_VIDEO_FILES = int(os.environ["VIDEO_SOP_BOM_MAX_VIDEO_FILES"])
MAX_VIDEO_FILE_SIZE_MB = int(os.environ["VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB"])
MAX_TOTAL_INPUT_SIZE_MB = int(os.environ["VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB"])
MAX_TOTAL_DURATION_MINUTES = int(os.environ["VIDEO_SOP_BOM_MAX_TOTAL_DURATION_MINUTES"])
MAX_KEY_FRAMES_CEILING = int(os.environ["VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING"])
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
KMS_KEY_ARN = os.environ.get("KMS_KEY_ARN", "")

DEFINITION_SCHEMA_VERSION = 1
DEFINITION_FILENAME = "definition.json"
CONFIG_SCHEMA = configSchema.load_config_schema()

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


def failure_for(error):
    if isinstance(error, PipelineRejection):
        return error.code, error.cause[:MAX_CAUSE_CHARS]
    return ERROR_PIPELINE, (str(error) or error.__class__.__name__)[:MAX_CAUSE_CHARS]


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so a failure here does not leave the
    pipeline task waiting for its full taskTimeout. This task is the first state of the pipeline's
    state machine, so nothing further downstream can report on the token.

    Never raises: the caller re-raises the original error, which is the one worth reading."""
    if not task_token:
        return
    code, cause = failure_for(error)
    try:
        sfn.send_task_failure(
            taskToken=task_token,
            error=code,
            cause=cause
        )
        logger.info(f"Sent task failure callback to Step Functions ({code})")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def validate_rendered_config(config):
    """Reject — never clamp — a rendered configuration outside the schema or the deployment's
    key-frame ceiling; every sentence names the value and the bound."""
    errors = configSchema.validate_config(config, CONFIG_SCHEMA)
    if not errors:
        max_key_frames = config.get("maxKeyFrames")
        if isinstance(max_key_frames, int) and not isinstance(max_key_frames, bool) \
                and max_key_frames > MAX_KEY_FRAMES_CEILING:
            errors.append(
                f"maxKeyFrames is {max_key_frames}; this deployment allows at most "
                f"{MAX_KEY_FRAMES_CEILING}.")
    if errors:
        raise PipelineRejection(ERROR_INPUT_REJECTED, " ".join(errors))


def _execution_id(event, manifest):
    """The workflow execution id: from the state machine input, else the leaf of the aux temp
    prefix (pipelines/<pipelineName>/<executionId>/)."""
    execution_id = event.get("executionId") or ""
    if execution_id:
        return execution_id
    prefix = (manifest.get("auxTempPrefix") or "").strip("/")
    return prefix.rsplit("/", 1)[-1] if prefix else ""


def _asset_name(metadata_body, database_id, asset_id):
    """The write-back asset's name from the grouped metadata envelope; "" when absent. Only this
    scalar leaves the envelope — the metadata CONTENT never travels in the definition."""
    view = manifestHelper.to_legacy_vams_view(metadata_body, database_id, asset_id, "/")
    asset_data = ((view or {}).get("VAMS") or {}).get("assetData") or {}
    return str(asset_data.get("assetName") or "")


def lambda_handler(event, context):
    """
    ConstructPipeline - Video SOP/BOM Extraction
    Validates the rendered configuration and writes the container's definition document.
    """

    logger.info(event)

    try:
        return build_execution_params(event)
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get('externalSfnTaskToken', ''))
        raise


def build_execution_params(event):
    """The state machine payload for the Batch container job."""

    # The envelope carries outputs.results, auxBucket, auxTempPrefix and outputTarget, none of
    # which resolve_pipeline_inputs exposes, so it is read directly.
    manifest = manifestHelper.fetch_manifest(s3, manifestHelper.manifest_location(event))
    if manifest is None:
        raise ValueError(
            "the state machine input carries no inputManifestS3Location; the pipeline cannot "
            "locate its outputs.")

    config = manifestHelper.fetch_input_configuration(
        s3, event.get('inputConfigurationS3Location', ''))
    if not isinstance(config, dict) or not config:
        raise PipelineRejection(
            ERROR_INPUT_REJECTED,
            "no rendered template configuration was supplied; this pipeline requires a template.")
    validate_rendered_config(config)

    input_files = manifest.get("inputFiles") or event.get("inputFiles") or []
    output_target = manifest.get("outputTarget") or {}
    first_file = input_files[0] if input_files else {}
    asset_id = output_target.get("assetId") or first_file.get("assetId") or event.get("assetId", "")
    database_id = (output_target.get("databaseId") or first_file.get("databaseId")
                   or event.get("databaseId", ""))

    metadata_location = (manifest.get("inputMetadataS3Location")
                         or event.get("inputMetadataS3Location", ""))
    metadata_body = manifestHelper.fetch_metadata(s3, metadata_location) or {}
    asset_name = _asset_name(metadata_body, database_id, asset_id)

    outputs = manifest.get("outputs") or {}
    aux_bucket = manifest.get("auxBucket") or ""
    aux_temp_prefix = (manifest.get("auxTempPrefix") or "").rstrip("/")
    if not aux_bucket or not aux_temp_prefix:
        raise ValueError(
            "the workflow manifest carries no auxBucket/auxTempPrefix; the pipeline cannot stage "
            "its definition.")
    aux_temp_prefix = aux_temp_prefix + "/"

    # Both ids may be "" for an invocation without an orchestration prefix; the container's
    # definition_schema.json admits that (no minLength on either).
    pipeline_execution_id = (event.get("pipelineExecutionId")
                             or manifestHelper.pipeline_execution_id_from_event_prefix(
                                 event.get("orchestrationEventPrefix", "")))

    definition = {
        "schemaVersion": DEFINITION_SCHEMA_VERSION,
        "batchJobName": event.get("batchJobName", ""),
        "pipelineExecutionId": pipeline_execution_id,
        "executionId": _execution_id(event, manifest),
        "assetId": asset_id,
        "databaseId": database_id,
        "assetName": asset_name,
        "inputFiles": [{
            "bucket": entry.get("bucket", ""),
            "key": entry.get("key", ""),
            "versionId": entry.get("versionId") or "",
            "relativePath": entry.get("relativePath", ""),
        } for entry in input_files],
        "outputs": {
            "bucket": outputs.get("bucket", ""),
            "files": outputs.get("files", ""),
            "previews": outputs.get("previews", ""),
            "metadata": outputs.get("metadata", ""),
            "results": outputs.get("results", ""),
        },
        "outputTarget": {
            "assetId": output_target.get("assetId", ""),
            "databaseId": output_target.get("databaseId", ""),
            "fileBaseExecutionPathExtension":
                output_target.get("fileBaseExecutionPathExtension") or "/",
        },
        "auxBucket": aux_bucket,
        "auxTempPrefix": aux_temp_prefix,
        "kmsKeyArn": KMS_KEY_ARN,
        "bedrockModelId": BEDROCK_MODEL_ID,
        "limits": {
            "maxVideoFiles": MAX_VIDEO_FILES,
            "maxVideoFileSizeMb": MAX_VIDEO_FILE_SIZE_MB,
            "maxTotalInputSizeMb": MAX_TOTAL_INPUT_SIZE_MB,
            "maxTotalDurationMinutes": MAX_TOTAL_DURATION_MINUTES,
            "maxKeyFramesCeiling": MAX_KEY_FRAMES_CEILING,
        },
        "config": config,
    }

    # The definition travels as an S3 pointer: the ECS RunTask overrides that carry a Fargate job's
    # command are capped at 8192 characters, while a string tag may be 65536.
    definition_key = f"{aux_temp_prefix}{DEFINITION_FILENAME}"
    s3.put_object(
        Bucket=aux_bucket,
        Key=definition_key,
        Body=json.dumps(definition).encode("utf-8"),
        ContentType="application/json",
    )
    definition_uri = f"s3://{aux_bucket}/{definition_key}"
    logger.info(f"Definition for {len(input_files)} input files written to {definition_uri}")

    return {
        "batchJobName": event.get("batchJobName", ""),
        "definitionCommand": ["--definition-s3-uri", definition_uri],
        # Re-emitted because this task's outputPath is $.Payload, which REPLACES the state — a
        # value only present in the state machine's original input would be dropped here.
        # pipelineEnd reads the token to release the external workflow task.
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING",
    }
