#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
import datetime
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="OpenCosmos3Pipeline")

sfn = boto3.client('stepfunctions', region_name=os.environ["AWS_REGION"])
events_client = boto3.client('events', region_name=os.environ["AWS_REGION"])

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.jpg,.jpeg,.png,.webp")
# Orchestration bus + state-machine log group for optional sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

# Task modes / variants that require an input file
INPUT_FILE_MODES = ("image2video", "video2video", "transfer")


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
