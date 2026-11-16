#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import uuid
from customLogging.logger import safeLogger
import manifestHelper
import computeTier
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)
events_client = boto3.client(
    'events',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)
s3_client = boto3.client('s3', config=retry_config)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]
# Media previews (useMediaPreviews.enabled): the image extensions the web shows as their own preview when the
# file is under PREVIEW_FILE_SIZE_LIMIT_BYTES, and that limit. Both are set by CDK from the shared
# constants (infra/lib/.../preview3dThumbnail-construct.ts) and are absent when media previews are off.
# An input of one of those extensions that is under the limit gets no run unless
# DOWNSCALE_SELF_PREVIEWABLE_IMAGES is "true" (useMediaPreviews.downscaleSelfPreviewableImages).
SELF_PREVIEWABLE_FILEEXTENSIONS = os.environ.get("SELF_PREVIEWABLE_FILEEXTENSIONS", "")
PREVIEW_FILE_SIZE_LIMIT_BYTES = os.environ.get("PREVIEW_FILE_SIZE_LIMIT_BYTES", "")
DOWNSCALE_SELF_PREVIEWABLE_IMAGES = os.environ.get(
    "DOWNSCALE_SELF_PREVIEWABLE_IMAGES", "false").strip().lower() == "true"
# Orchestration bus + state-machine log group for optional sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
# This pipeline's vended container log group (the group its Fargate job definition writes to through
# the awslogs driver) + its job definition name; the container log source is registered only when
# both are configured.
BATCH_JOB_LOG_GROUP_NAME = os.environ.get("BATCH_JOB_LOG_GROUP_NAME", "")
BATCH_JOB_LOG_GROUP_ARN = os.environ.get("BATCH_JOB_LOG_GROUP_ARN", "")
BATCH_JOB_DEFINITION_NAME = os.environ.get("BATCH_JOB_DEFINITION_NAME", "")
# The Batch states of this pipeline's state machine (their CDK construct ids), one per compute tier.
# Both submit the same job definition, so their containers stream under the same prefix; a run
# registers the container log source of the one state its tier routes it to.
STANDARD_BATCH_STATE_NAME = "Preview3dThumbnailBatchJob"
LARGE_BATCH_STATE_NAME = "Preview3dThumbnailLargeBatchJob"
BATCH_STATE_NAME_BY_TIER = {
    computeTier.TIER_STANDARD: STANDARD_BATCH_STATE_NAME,
    computeTier.TIER_LARGE: LARGE_BATCH_STATE_NAME,
}
LARGE_TIER_THRESHOLD_BYTES = computeTier.large_tier_threshold_bytes()
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

def abort_external_workflow(error, task_token):
    if (task_token != None and task_token != ""):
        logger.error("Aborting external task")
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )


def self_previewable_extensions():
    """The extensions (lowercase, with the dot) the web shows as their own preview; empty when media
    previews are off."""
    return [ext.strip().lower() for ext in SELF_PREVIEWABLE_FILEEXTENSIONS.split(',') if ext.strip()]


def preview_size_limit_bytes():
    """The size an image must be strictly under to be shown as its own preview, or None when the
    deployment sets none."""
    try:
        limit = int(PREVIEW_FILE_SIZE_LIMIT_BYTES)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def is_shown_as_own_preview(event, extension, input_uri):
    """True when the input is an image the web already shows as its own preview and no preview is to be
    written for it: its extension is a self-previewable one, the option that writes a smaller JPEG for
    such images is off, and its size (HEAD of the version the run reads) is known and strictly under the
    preview size limit. A size that cannot be read gives False, so the run starts and the container
    checks the size again before it writes anything."""
    limit = preview_size_limit_bytes()
    if DOWNSCALE_SELF_PREVIEWABLE_IMAGES or limit is None:
        return False
    if extension.lower() not in self_previewable_extensions():
        return False
    try:
        regions = computeTier.bucket_regions(event)
        size = computeTier.input_size_bytes(
            computeTier.s3_client_for(s3_client, regions, input_uri), input_uri,
            event.get("inputVersionId") or "")
    except Exception as e:
        logger.warning(f"Could not read the input size to decide whether it is its own preview; "
                       f"starting the run: {e}")
        return False
    return size < limit


# The result document the run records on the execution (the container writes the same one; a drift test
# keeps the name and schema equal to media_preview.py's).
NO_PREVIEW_RESULT_FILENAME = "preview-not-generated.json"
NO_PREVIEW_RESULT_SCHEMA = "vams.mediaPreview.noPreview/v1"


def self_preview_message(name):
    return (f"No preview was generated for '{name}': the file is under 5 MiB and is already shown as "
            f"its own preview.")


def write_no_preview_result(results_uri, name, message):
    """Write the no-preview result document under the execution's results folder, where the workflow's
    output step records it on the execution. Best effort: no results path, or a write that fails, leaves
    the run successful with the message in the task output and the log."""
    bucket, prefix = manifestHelper.parse_s3_uri(results_uri) if results_uri else (None, None)
    if not bucket or prefix is None:
        logger.info("No results path for this run; the no-preview message is in the task output only")
        return
    key = prefix + NO_PREVIEW_RESULT_FILENAME
    body = json.dumps({
        "schema": NO_PREVIEW_RESULT_SCHEMA,
        "file": name,
        "reason": "alreadyShownAsOwnPreview",
        "message": message,
    }).encode("utf-8")
    try:
        s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    except Exception as e:  # nosec B110 - best effort; the run still completes successfully
        logger.warning(f"Could not write the no-preview result ({type(e).__name__}): {e}")


def complete_without_preview(task_token, message):
    """Report the run successful on the workflow's task token with the shape the state machine's end
    reports, without starting the state machine."""
    if task_token:
        sfn.send_task_success(
            taskToken=task_token,
            output=json.dumps({'status': 'Pipeline Success', 'message': message})
        )
        logger.info("Sent external task token: success (no preview needed)")


def batch_container_log_entry(job_definition_name, state_name):
    """The log source for one Batch state's container: the job's vended group, streamed under
    `<jobDefinitionName>/default/`. None when the group or the job definition is not configured."""
    if not (BATCH_JOB_LOG_GROUP_NAME or BATCH_JOB_LOG_GROUP_ARN) or not job_definition_name:
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


def register_sub_execution(orchestration_bus_name, orchestration_event_prefix,
                           sub_execution_arn, state_machine_arn,
                           batch_state_name=STANDARD_BATCH_STATE_NAME):
    """Best-effort: report this pipeline's sub-SFN execution + its log sources to the orchestration bus.
    `batch_state_name` is the Batch state the run is routed to, whose container log source is registered."""
    if not orchestration_bus_name or not orchestration_event_prefix:
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
            "stateMachineArn": state_machine_arn or "",
            "executionArn": sub_execution_arn or "",
            "label": "3D thumbnail processing",
        },
    }
    logs = []
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        logs.append({
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
            "sourceType": "stateMachine",
            "label": "3D thumbnail state machine",
        })
    container_log = batch_container_log_entry(BATCH_JOB_DEFINITION_NAME, batch_state_name)
    if container_log:
        logs.append(container_log)
    if logs:
        detail["logs"] = logs
    try:
        events_client.put_events(Entries=[{
            "EventBusName": orchestration_bus_name,
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
    Starts StepFunctions State Machine for processing
    """

    logger.info("Event", event=event)
    logger.info(f"Context: {context}")

    responses = []

    # Get the input metadata + input-configuration S3 locations
    input_metadata_s3_location = event.get('inputMetadataS3Location', '')
    input_configuration_s3_location = event.get('inputConfigurationS3Location', '')

    # Get the orchestration event prefix for optional sub-process registration
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')

    # Get any given additional outer/external task token to report back to (when using this pipeline as part of another state machine)
    if ('sfnExternalTaskToken' in event):
        external_sfn_task_token = event['sfnExternalTaskToken']
    else:
        external_sfn_task_token = ''

    input_s3_asset_files_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath']
    output_s3_asset_preview_uri = event['outputS3AssetPreviewPath']
    output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath']
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    asset_id = event.get('assetId', '')
    database_id = event.get('databaseId', '')

    #Folder check
    if (input_s3_asset_files_uri.endswith("/")):
        abort_external_workflow("Input S3 URI cannot be a folder for this pipeline", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Input S3 URI cannot be a folder"
            }
        }

    # Extract the root name and extension from the input key
    file_root, extension = os.path.splitext(input_s3_asset_files_uri)

    logger.info(f"Checking for valid file")
    # Validate the extension against exact members of the comma-separated allow list. A containment
    # test against the joined string accepts any prefix of a listed extension ('.st' against
    # '.stl,.step'), which admits a file the container cannot read.
    allowed_extensions = [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',')
                          if ext.strip()]

    if (not extension or extension.lower() not in allowed_extensions):
        abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Pipeline cannot process file type provided"
            }
        }

    # An image the web already shows as its own preview needs no run: end it here, successfully, so no
    # container job starts and no object is written.
    if is_shown_as_own_preview(event, extension, input_s3_asset_files_uri):
        name = os.path.basename(input_s3_asset_files_uri)
        message = self_preview_message(name)
        logger.info(message)
        # Record why in the execution's results before the task ends, so the output step finds it
        write_no_preview_result(event.get("outputS3AssetResultsPath", ""), name, message)
        try:
            complete_without_preview(external_sfn_task_token, message)
        except Exception as e:
            logger.exception(e)
            abort_external_workflow("Internal Server Error", external_sfn_task_token)
            return {
                'statusCode': 500,
                'body': {
                    "message": "Internal Server Error",
                }
            }
        return {
            'statusCode': 200,
            'body': {
                "message": message,
            }
        }

    # Generate new job name
    job_name = f"PipelineJob_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{uuid.uuid4().hex[:8]}"

    # The compute tier the state machine routes the Batch job to
    compute_tier = computeTier.select_compute_tier(s3_client, event, LARGE_TIER_THRESHOLD_BYTES)

    # StateMachine Execution Input
    sfn_input = {
        "jobName": job_name,
        "computeTier": compute_tier,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        # Named here so the state machine carries it to constructPipeline; empty from an older Lambda.
        "outputS3AssetResultsPath": event.get("outputS3AssetResultsPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        # Regions of the input and output asset buckets (empty = deployment Region); constructPipeline
        # hands them to the container, which signs each bucket's requests for its Region.
        "inputBucketRegion": event.get("inputBucketRegion", ""),
        "outputBucketRegion": event.get("outputBucketRegion", ""),
        # The S3 version of the input the run reads (empty reads the current object).
        "inputVersionId": event.get("inputVersionId", ""),
        "externalSfnTaskToken": external_sfn_task_token,
        "assetId": asset_id,
        "databaseId": database_id,
    }

    try:
        # Start the Step Functions state machine with the bucket key and name
        logger.info(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
        logger.info(f"SFN Input: {json.dumps(sfn_input)}")

        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_name,
            input=json.dumps(sfn_input)
        )

        logger.info(f"SFN Response: {sfn_response}")

        # Best-effort: register this sub-SFN execution + its log group
        register_sub_execution(
            ORCHESTRATION_BUS_NAME, orchestration_event_prefix,
            sfn_response.get("executionArn", ""), STATE_MACHINE_ARN,
            BATCH_STATE_NAME_BY_TIER[compute_tier])

        # response datetime not JSON serializable
        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')

        responses.append({
            'statusCode': 200,
            'body': {
                "message": "Starting Asset Processing State Machine",
                "execution": sfn_response
            }
        })
    except Exception as e:
        logger.exception(e)
        abort_external_workflow("Internal Server Error", external_sfn_task_token)
        responses.append({
            'statusCode': 500,
            'body': {
                "message": "Internal Server Error",
            }
        })

    logger.info(f"Responses: {responses}")

    # Loop through responses and see if any have errors; If so return 500 error response
    for response in responses:
        # Keyed on the status code, not on an "error" key. Every failure route above appends a
        # body carrying only "message", so the key test matched nothing and execution fell
        # through to the success return below -- which dereferences sfn_response, unbound
        # whenever the failure happened before it was assigned.
        if response.get('statusCode', 200) >= 400:
            return response

    # Return success 200 response
    return {
        'statusCode': 200,
        'body': {
            "message": "Starting Asset Processing State Machine",
            "execution": sfn_response
        }
    }
