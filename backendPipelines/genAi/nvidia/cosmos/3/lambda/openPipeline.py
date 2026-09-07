#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import math
import uuid
import datetime
from customLogging.logger import safeLogger
import manifestHelper
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenCosmos3Pipeline")

sfn = boto3.client('stepfunctions', region_name=os.environ["AWS_REGION"], config=retry_config)
events_client = boto3.client('events', region_name=os.environ["AWS_REGION"], config=retry_config)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.jpg,.jpeg,.png,.webp")
# Orchestration bus + state-machine log group for optional sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

# Task modes / variants that require an input file
INPUT_FILE_MODES = ("image2video", "video2video", "transfer")

# Variants whose model supports control-signal transfer. The container downgrades a transfer request
# on any other variant to that variant's default mode and ignores the control settings, so those runs
# are not gated on them here either.
TRANSFER_CAPABLE_VARIANTS = ("nano", "super")


def numeric_setting_error(raw, setting_name, integer=False, minimum=None):
    """The reason `raw` cannot be used as a number, or None when it can.

    Mirrors `parse_number_setting` in container/__main__.py so a value accepted here is one the
    container accepts: blank and absent both mean "not supplied", a boolean is not read as 1/0, and a
    fractional value for an integer setting is not truncated.
    """
    if raw is None or (not isinstance(raw, bool) and str(raw).strip() == ""):
        return None
    if isinstance(raw, bool):
        return f"{setting_name} must be a number, but the boolean {raw} was supplied"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return f"{setting_name} must be a number, but {raw!r} was supplied"
    if not math.isfinite(value):
        return f"{setting_name} must be a finite number, but {raw!r} was supplied"
    if integer and not value.is_integer():
        return f"{setting_name} must be a whole number, but {raw!r} was supplied"
    if minimum is not None and value < minimum:
        return f"{setting_name} must be at least {minimum}, but {raw!r} was supplied"
    return None


def numeric_list_setting_error(raw, setting_name, integer=False, minimum=None):
    """The reason any entry of a comma-aligned numeric setting cannot be used, or None.

    A control setting carries one entry per control type, aligned by position, so a value is usable
    only when every entry is.
    """
    entries = raw.split(",") if isinstance(raw, str) else [raw]
    for index, entry in enumerate(entries):
        error = numeric_setting_error(
            entry, f"{setting_name} (entry {index + 1})", integer=integer, minimum=minimum)
        if error:
            return error
    return None


def control_path_setting_error(raw):
    """The reason a control-signal path cannot be used, or None.

    The value is a complete `s3://bucket/key` URI, comma-aligned to the control types, and a blank
    entry means the control signal is computed from the source video. The container restricts the
    bucket to the deployment's own asset buckets, which only it can resolve; what is checkable here is
    the shape, because an asset-relative value reaches `aws s3 cp` as a local path and fails on a
    provisioned GPU instance.
    """
    entries = raw.split(",") if isinstance(raw, str) else [raw]
    for index, entry in enumerate(entries):
        value = str(entry or "").strip()
        if not value:
            continue
        label = f"cosmosControlPath (entry {index + 1})"
        if not value.startswith("s3://"):
            return (f"{label} must be a complete S3 URI of the form s3://bucket/key, "
                    f"but {entry!r} was supplied")
        bucket, _, key = value[len("s3://"):].partition("/")
        if not bucket or not key or key.endswith("/"):
            return f"{label} must name an object, not a bucket or a prefix: {entry!r}"
    return None


def run_setting_error(event, model_variant, task_mode):
    """The first reason this run's settings cannot be used, or None.

    The container is where these are coerced, and that happens after the Batch job has provisioned a
    GPU instance and pulled its image. They arrive as free text -- an asset-metadata value or a
    hand-edited configuration body -- so a typo is fully knowable at launch.
    """
    errors = [
        numeric_setting_error(event.get('cosmosSeed', ''), "cosmosSeed", integer=True),
        numeric_setting_error(
            event.get('cosmosNumFrames', ''), "cosmosNumFrames", integer=True, minimum=1),
        numeric_setting_error(event.get('cosmosGuidance', ''), "cosmosGuidance"),
    ]
    if task_mode == "transfer" and model_variant in TRANSFER_CAPABLE_VARIANTS:
        errors.extend([
            numeric_list_setting_error(
                event.get('cosmosControlWeight', ''), "cosmosControlWeight"),
            numeric_setting_error(
                event.get('cosmosControlGuidance', ''), "cosmosControlGuidance"),
            control_path_setting_error(event.get('cosmosControlPath', '')),
        ])
    for error in errors:
        if error:
            return error
    return None


def abort_external_workflow(error, task_token):
    if task_token and task_token != "":
        logger.error(f"Aborting external task: {task_token}")
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )


def register_sub_execution(orchestration_event_prefix, sub_execution_arn):
    """Best-effort: report this sub-SFN execution + log group to the orchestration bus so VAMS can
    track it, attempt sub-aborts, and pull sub-logs. Never fails the pipeline."""
    if not ORCHESTRATION_BUS_NAME or not orchestration_event_prefix:
        logger.info("Orchestration bus/prefix not configured; skipping sub-process registration")
        return
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(
        orchestration_event_prefix)
    if not pipeline_execution_id:
        logger.warning("Could not derive pipelineExecutionId from event prefix; skipping registration")
        return
    detail = {
        "pipelineExecutionId": pipeline_execution_id,
        "subExecution": {
            "stateMachineArn": STATE_MACHINE_ARN,
            "executionArn": sub_execution_arn or "",
        },
    }
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        detail["logs"] = [{
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
        }]
    try:
        events_client.put_events(Entries=[{
            "EventBusName": ORCHESTRATION_BUS_NAME,
            "Source": orchestration_event_prefix,
            "DetailType": REGISTER_DETAIL_TYPE,
            "Detail": json.dumps(detail),
        }])
        logger.info(f"Registered sub-execution for pipeline execution {pipeline_execution_id}")
    except Exception as e:  # nosec B110 - registration is best-effort; never fail the pipeline
        logger.warning(f"Sub-process registration failed (non-critical): {e}")


def build_job_name(model_variant, orchestration_event_prefix):
    """The name this pipeline's own state machine runs under.

    A workflow may carry several triggers of one type, so one upload can fan out to simultaneous
    runs of the same variant and Step Functions rejects a repeated name with
    ExecutionAlreadyExists. The pipeline execution id encoded in the orchestration event prefix
    makes the name unique per run while keeping it DERIVED: an SFN retry re-invokes this lambda
    with the same body and must produce the same name rather than starting a second sub-execution.
    A direct/local invocation carries no prefix, so it falls back to a timestamp plus a random
    suffix. Kept within the 80-character limit and free of ':' and '/'.
    """
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(
        orchestration_event_prefix)
    if pipeline_execution_id:
        return f"cosmos3-{model_variant}-{pipeline_execution_id}"[:80]
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
    return f"cosmos3-{model_variant}-{stamp}-{uuid.uuid4().hex[:8]}"[:80]


def lambda_handler(event, context):
    """
    OpenPipeline
    Starts the StepFunctions State Machine for a Cosmos 3 pipeline.
    Validates input based on task mode (input-file modes require a valid file).
    """
    logger.info(f"Event: {event}")

    model_variant = event.get('modelVariant', 'nano')
    task_mode = event.get('taskMode', '')
    # Metadata + input-configuration S3 LOCATIONS travel onward (never the inline content); the
    # container reads them from S3 as needed.
    input_metadata_s3_location = event.get('inputMetadataS3Location', '')
    input_configuration_s3_location = event.get('inputConfigurationS3Location', '')
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')
    external_sfn_task_token = event.get('sfnExternalTaskToken', '')
    input_s3_asset_files_uri = event.get('inputS3AssetFilePath', '')
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    cosmos_prompt = event.get('cosmosPrompt', '')
    cosmos_negative_prompt = event.get('cosmosNegativePrompt', '')
    cosmos_seed = event.get('cosmosSeed', '')
    cosmos_guidance = event.get('cosmosGuidance', '')
    cosmos_num_frames = event.get('cosmosNumFrames', '')
    cosmos_control_type = event.get('cosmosControlType', '')
    cosmos_control_path = event.get('cosmosControlPath', '')
    cosmos_control_weight = event.get('cosmosControlWeight', '')
    cosmos_control_guidance = event.get('cosmosControlGuidance', '')
    asset_id = event.get('assetId', '')
    database_id = event.get('databaseId', '')

    # The container treats assetId and the file output path as hard requirements, but only checks
    # them after the Batch job has provisioned a GPU instance. Both are resolved from the workflow
    # manifest, so an unreadable manifest reaches here as blanks; gating them at launch turns that
    # into an immediate failure carrying the real reason instead of a paid-for job that dies on
    # startup.
    if not asset_id:
        abort_external_workflow("Asset identity could not be resolved for this run", external_sfn_task_token)
        return {'statusCode': 400, 'body': {"message": "Asset identity could not be resolved for this run. The workflow manifest was unreadable or carried no asset."}}
    if not output_s3_asset_files_uri:
        abort_external_workflow("Output file path could not be resolved for this run", external_sfn_task_token)
        return {'statusCode': 400, 'body': {"message": "Output file path could not be resolved for this run. The workflow manifest was unreadable or carried no outputs."}}

    # The numeric run settings and the control-signal path are checked for the same reason: they are
    # first read inside the container, once a GPU instance has been provisioned and its image pulled.
    setting_error = run_setting_error(event, model_variant, task_mode)
    if setting_error:
        abort_external_workflow(setting_error, external_sfn_task_token)
        return {'statusCode': 400, 'body': {"message": setting_error}}

    needs_input = task_mode in INPUT_FILE_MODES or model_variant == "super-image2video"

    if needs_input:
        if not input_s3_asset_files_uri:
            abort_external_workflow("Input S3 URI is required for this mode", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": "Input S3 URI is required for this mode"}}
        if input_s3_asset_files_uri.endswith("/"):
            abort_external_workflow("Input S3 URI cannot be a folder", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": "Input S3 URI cannot be a folder"}}
        file_parts = input_s3_asset_files_uri.split('.')
        extension = ('.' + file_parts[-1].lower()) if len(file_parts) > 1 else ''
        allowed_extensions = [ext.strip() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',')]
        if not extension or extension not in allowed_extensions:
            abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": f"Pipeline cannot process file type provided. Allowed: {ALLOWED_INPUT_FILEEXTENSIONS}"}}
    else:
        # Text-input modes require a prompt
        if not cosmos_prompt:
            abort_external_workflow("Cosmos prompt is required for this mode", external_sfn_task_token)
            return {'statusCode': 400, 'body': {"message": "Cosmos prompt is required for this mode"}}

    job_name = build_job_name(model_variant, orchestration_event_prefix)

    sfn_input = {
        "jobName": job_name,
        "modelVariant": model_variant,
        "taskMode": task_mode,
        "cosmosPrompt": cosmos_prompt,
        "cosmosNegativePrompt": cosmos_negative_prompt,
        "cosmosSeed": cosmos_seed,
        "cosmosGuidance": cosmos_guidance,
        "cosmosNumFrames": cosmos_num_frames,
        "cosmosControlType": cosmos_control_type,
        "cosmosControlPath": cosmos_control_path,
        "cosmosControlWeight": cosmos_control_weight,
        "cosmosControlGuidance": cosmos_control_guidance,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "externalSfnTaskToken": external_sfn_task_token,
    }

    try:
        logger.info(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN, name=job_name, input=json.dumps(sfn_input)
        )
        # Best-effort: register this sub-SFN execution with the VAMS execution.
        register_sub_execution(orchestration_event_prefix, sfn_response.get("executionArn", ""))
        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')
    except Exception as e:
        logger.exception(e)
        abort_external_workflow("Internal Server Error", external_sfn_task_token)
        return {'statusCode': 500, 'body': {"message": "Internal Server Error"}}

    return {
        'statusCode': 200,
        'body': {"message": "Starting Cosmos 3 Pipeline State Machine", "execution": sfn_response},
    }
