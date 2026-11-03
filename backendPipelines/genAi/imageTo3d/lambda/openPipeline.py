#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import secrets
from botocore.config import Config
from customLogging.logger import safeLogger
import manifestHelper

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenImageTo3dPipeline")

sfn = boto3.client('stepfunctions', region_name=os.environ["AWS_REGION"], config=retry_config)
events_client = boto3.client('events', region_name=os.environ["AWS_REGION"], config=retry_config)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]
# Orchestration bus + state-machine log group for optional sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
# AWS Batch's default container log group; each enabled model's job definition name arrives as
# BATCH_JOB_DEFINITION_NAME_<MODEL>.
BATCH_JOB_LOG_GROUP_NAME = os.environ.get("BATCH_JOB_LOG_GROUP_NAME", "")
BATCH_JOB_LOG_GROUP_ARN = os.environ.get("BATCH_JOB_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

# The Batch state each model's job runs in, named as the state machine names it (the construct id of
# the model's BatchSubmitJob). Each model has its own job definition and state, so only the state the
# run is routed to is registered.
SAM3D_BATCH_STATE_NAME = "ImageTo3dBatchJob-sam3d"
STABLE3DGEN_BATCH_STATE_NAME = "ImageTo3dBatchJob-stable3dgen"
TRELLIS2_BATCH_STATE_NAME = "ImageTo3dBatchJob-trellis2"
TRIPOSR_BATCH_STATE_NAME = "ImageTo3dBatchJob-triposr"
STEP1X3D_BATCH_STATE_NAME = "ImageTo3dBatchJob-step1x3d"
HUNYUAN3D_BATCH_STATE_NAME = "ImageTo3dBatchJob-hunyuan3d"
BLENDERAGENT_BATCH_STATE_NAME = "ImageTo3dBatchJob-blenderagent"
BATCH_STATE_NAMES = {
    "sam3d": SAM3D_BATCH_STATE_NAME,
    "stable3dgen": STABLE3DGEN_BATCH_STATE_NAME,
    "trellis2": TRELLIS2_BATCH_STATE_NAME,
    "triposr": TRIPOSR_BATCH_STATE_NAME,
    "step1x3d": STEP1X3D_BATCH_STATE_NAME,
    "hunyuan3d": HUNYUAN3D_BATCH_STATE_NAME,
    "blenderagent": BLENDERAGENT_BATCH_STATE_NAME,
}


# Models that may take an existing MESH among their input files, as a starting point to refine. The
# environment allow-list describes photographs; these extensions are admitted on top of it, for these models
# only, so a mesh handed to a model that reconstructs from pixels is refused here rather than on a GPU.
MESH_INPUT_MODELS = ("blenderagent",)
MESH_INPUT_EXTENSIONS = (".glb", ".gltf", ".obj", ".stl", ".ply")


def allowed_extensions(model=None):
    """The allow-list as a list of lower-case dotted extensions. Membership is tested against the
    parsed list rather than the joined string so a near-miss ('.jp') cannot pass as a substring."""
    allowed = [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',') if ext.strip()]
    if model in MESH_INPUT_MODELS:
        allowed += list(MESH_INPUT_EXTENSIONS)
    return allowed


def input_file_extension(input_s3_uri):
    """The lower-case extension of an ``s3://bucket/key`` input, taken from the KEY. Taking it from
    the whole URI would pick up a dot in the bucket name for a key with no extension."""
    _bucket, key = manifestHelper.parse_s3_uri(input_s3_uri)
    if not key:
        return ""
    _root, extension = os.path.splitext(key)
    return extension.lower()


def abort_external_workflow(error, task_token):
    if task_token and task_token != "":
        logger.error(f"Aborting external task: {task_token}")
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )


def batch_container_log_entry(model):
    """The log source for the model's Batch state container: AWS Batch's default group, streamed under
    `<jobDefinitionName>/default/`. None when the group, the job definition or the state is not configured."""
    model_key = str(model or "").strip().lower()
    job_definition_name = os.environ.get(f"BATCH_JOB_DEFINITION_NAME_{model_key.upper()}", "")
    state_name = BATCH_STATE_NAMES.get(model_key, "")
    if not (BATCH_JOB_LOG_GROUP_NAME or BATCH_JOB_LOG_GROUP_ARN) or not job_definition_name \
            or not state_name:
        return None
    return {
        "logGroupArn": BATCH_JOB_LOG_GROUP_ARN,
        "logGroupName": BATCH_JOB_LOG_GROUP_NAME,
        "logStreamName": "",
        "logStreamPrefix": f"{job_definition_name}/default/",
        "stageName": state_name,
        "sourceType": "batch",
        "label": f"{state_name} container",
    }


def register_sub_execution(orchestration_event_prefix, sub_execution_arn, model=""):
    """Best-effort: report this sub-SFN execution + its log sources to the orchestration bus so VAMS
    can track it, attempt sub-aborts, and pull sub-logs. Never fails the pipeline."""
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
            "label": "Image to 3D processing",
        },
    }
    logs = []
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        logs.append({
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
            "sourceType": "stateMachine",
            "label": "Image to 3D state machine",
        })
    container_log = batch_container_log_entry(model)
    if container_log:
        logs.append(container_log)
    if logs:
        detail["logs"] = logs
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


def lambda_handler(event, context):
    """
    OpenPipeline
    Starts the StepFunctions State Machine for an image-to-3D pipeline.
    Validates that the input is a single supported image file.
    """
    logger.info("Event", event=event)

    model = event.get('model', '')
    # Metadata + input-configuration S3 LOCATIONS travel onward (never the inline content); the
    # container reads them from S3 as needed.
    input_metadata_s3_location = event.get('inputMetadataS3Location', '')
    input_configuration_s3_location = event.get('inputConfigurationS3Location', '')
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')
    external_sfn_task_token = event['sfnExternalTaskToken']
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    # Every view of the subject, for a model that reconstructs from several. Named here for the same
    # reason every output path is: this lambda enumerates the payload explicitly, so a field added in
    # vamsExecute reaches the container ONLY if it is also named at this hop. It was not, on the first
    # attempt, and the result was a four-view run that silently reconstructed from one view and returned a
    # result bit-identical to the single-view control.
    input_s3_asset_file_uris = event.get('inputS3AssetFilePaths') or []
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    output_s3_asset_results_uri = event.get('outputS3AssetResultsPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    asset_id = event.get('assetId', '')
    database_id = event.get('databaseId', '')

    if not input_s3_asset_file_uri:
        abort_external_workflow("Input S3 URI is required for this pipeline", external_sfn_task_token)
        return {'statusCode': 400, 'body': {"message": "Input S3 URI is required"}}

    if input_s3_asset_file_uri.endswith("/"):
        abort_external_workflow("Input S3 URI cannot be a folder for this pipeline", external_sfn_task_token)
        return {'statusCode': 400, 'body': {"message": "Input S3 URI cannot be a folder"}}

    extension = input_file_extension(input_s3_asset_file_uri)
    allowed = allowed_extensions(model)
    if not extension or extension not in allowed:
        abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {"message": f"Pipeline cannot process file type provided. Allowed: {', '.join(allowed)}"},
        }

    # EVERY view is checked, not just the primary. Checking only the first would let an unreadable file
    # through as a second view and fail inside the container, on a GPU instance, after the model loaded.
    for view_uri in input_s3_asset_file_uris:
        view_extension = input_file_extension(view_uri)
        if not view_extension or view_extension not in allowed:
            abort_external_workflow(
                f"Pipeline cannot process file type provided for view {view_uri}",
                external_sfn_task_token)
            return {
                'statusCode': 400,
                'body': {"message": f"Pipeline cannot process file type provided for {view_uri}. "
                                    f"Allowed: {', '.join(allowed)}"},
            }

    # The random suffix keeps two runs started in the same second from colliding on the execution
    # name, which Step Functions rejects.
    job_name = (f"ImageTo3dJob_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{secrets.token_hex(3)}")

    sfn_input = {
        "jobName": job_name,
        "model": model,
        "inputS3AssetFilePath": input_s3_asset_file_uri,
        "inputS3AssetFilePaths": input_s3_asset_file_uris,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        # Forwarded like the rest: this lambda enumerates the output paths explicitly, so a path
        # added upstream is dropped here unless it is named. That is what happened to the results
        # prefix -- vamsExecute forwarded it and constructPipeline read it, but this hop in
        # between silently lost it, so the container saw an empty results target.
        "outputS3AssetResultsPath": output_s3_asset_results_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputBucketRegion": event.get('inputBucketRegion', ''),
        "outputBucketRegion": event.get('outputBucketRegion', ''),
        "executingUserName": event.get('executingUserName', ''),
        "executingRequestContext": event.get('executingRequestContext', ''),
        "orchestrationEventPrefix": orchestration_event_prefix,
        "externalSfnTaskToken": external_sfn_task_token,
    }

    try:
        logger.info(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN, name=job_name, input=json.dumps(sfn_input)
        )
        # Best-effort: register this sub-SFN execution with the VAMS execution.
        register_sub_execution(orchestration_event_prefix, sfn_response.get("executionArn", ""), model)
        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')
    except Exception as e:
        logger.exception(e)
        abort_external_workflow("Internal Server Error", external_sfn_task_token)
        return {'statusCode': 500, 'body': {"message": "Internal Server Error"}}

    return {
        'statusCode': 200,
        'body': {"message": "Starting Image to 3D Pipeline State Machine", "execution": sfn_response},
    }
