#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
# The largest AWS Batch job the deployment permits (app.pipelines.useConversion3dBasic.batchTier.maxMemoryGiB).
MAX_MEMORY_GIB = computeTier.max_memory_gib(os.environ.get("MAX_MEMORY_GIB"))
# Orchestration bus + state-machine log group for optional sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
# The converter Lambda's log group (the Lambda attempt), registered only when configured. The Batch
# task's container log is registered by constructPipeline, which knows the job size and so the Batch
# state the run takes.
CONVERTER_LOG_GROUP_NAME = os.environ.get("CONVERTER_LOG_GROUP_NAME", "")
CONVERTER_LOG_GROUP_ARN = os.environ.get("CONVERTER_LOG_GROUP_ARN", "")
# The state-machine state that runs the Lambda attempt (its CDK construct id), the stage name its log
# source is registered under.
LAMBDA_STATE_NAME = "ConversionLambdaAttempt"
REGISTER_DETAIL_TYPE = "pipeline.execution.register"


def abort_external_workflow(error, task_token):
    if (task_token != None and task_token != ""):
        logger.error("Aborting external task")
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )


def converter_log_entry():
    """The log source of the Lambda attempt, which VAMS reads for the lines that carry both this run's
    workflow and pipeline execution ids (the converter logs both). None when the converter's log group
    is not configured."""
    if not (CONVERTER_LOG_GROUP_NAME or CONVERTER_LOG_GROUP_ARN):
        return None
    return {
        "logGroupArn": CONVERTER_LOG_GROUP_ARN,
        "logGroupName": CONVERTER_LOG_GROUP_NAME,
        "logStreamName": "",
        "stageName": LAMBDA_STATE_NAME,
        "sourceType": "lambda",
        "label": "Conversion function (Lambda)",
    }


def register_sub_execution(orchestration_bus_name, orchestration_event_prefix,
                           sub_execution_arn, state_machine_arn):
    """Best-effort: report this pipeline's sub-SFN execution + its log sources (the state machine and
    the Lambda attempt) to the orchestration bus. A run that continues on Batch adds the container's
    source from constructPipeline."""
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
            "label": "3D conversion processing",
        },
    }
    logs = []
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        logs.append({
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
            "sourceType": "stateMachine",
            "label": "3D conversion state machine",
        })
    converter = converter_log_entry()
    if converter:
        logs.append(converter)
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


def forced_tier(event):
    """``(tier, size)`` the input configuration forces: ``auto`` (or an unreadable configuration) leaves
    the choice to the run, ``lambda`` keeps it on the function (no escalation), ``batch`` skips the
    function and runs on the job size ``batchMemoryGiB`` names (the smallest by default)."""
    location = event.get("inputConfigurationS3Location", "")
    try:
        parameters = manifestHelper.fetch_input_configuration(s3_client, location) if location else {}
    except Exception as e:
        logger.warning(f"Input configuration unreadable for compute tier selection; using auto: {e}")
        return computeTier.TIER_AUTO, None
    tier = computeTier.requested_tier(parameters)
    size = computeTier.forced_batch_size(parameters, MAX_MEMORY_GIB) if tier == computeTier.TIER_BATCH else None
    return tier, size


def conversion_request(event):
    """The request body the converter receives (the same fields the workflow's task body carries),
    with ``allowEscalation`` set by the state machine on the Lambda attempt. It also carries the
    pipeline execution id, which the converter and the Batch task put on every line beside the
    workflow execution id: VAMS reads a registered shared log group for lines that carry both."""
    request = {key: event.get(key, "") for key in ("inputManifestS3Location", "inputConfigurationS3Location",
                                                   "workflowExecutionId") if event.get(key)}
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(
        event.get("orchestrationEventPrefix", ""))
    if pipeline_execution_id:
        request["pipelineExecutionId"] = pipeline_execution_id
    return request


def lambda_handler(event, context):
    """
    OpenPipeline
    Starts StepFunctions State Machine for processing
    """

    logger.info("Event", event=event)
    logger.info(f"Context: {context}")

    responses = []

    # Get the orchestration event prefix for optional sub-process registration
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')

    # Get any given additional outer/external task token to report back to (when using this pipeline as part of another state machine)
    if ('sfnExternalTaskToken' in event):
        external_sfn_task_token = event['sfnExternalTaskToken']
    else:
        external_sfn_task_token = ''

    input_s3_asset_files_uri = event['inputS3AssetFilePath']

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

    logger.info("Checking for valid file")
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

    # Generate new job name
    job_name = f"PipelineJob_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{uuid.uuid4().hex[:8]}"

    tier, forced_size = forced_tier(event)

    # StateMachine Execution Input. The task token travels as externalSfnTaskToken for pipelineEnd and
    # never inside conversionRequest, which the converter and the Batch task receive.
    sfn_input = {
        "jobName": job_name,
        "computeTier": tier,
        "forcedJobSize": forced_size or {},
        "maxMemoryGiB": MAX_MEMORY_GIB,
        "conversionRequest": conversion_request(event),
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetResultsPath": event.get("outputS3AssetResultsPath", ""),
        "inputBucketRegion": event.get("inputBucketRegion", ""),
        "outputBucketRegion": event.get("outputBucketRegion", ""),
        # The S3 version of the input the run reads (empty reads the current object).
        "inputVersionId": event.get("inputVersionId", ""),
        "externalSfnTaskToken": external_sfn_task_token,
        # constructPipeline registers the Batch container's log source on this pipeline execution.
        "orchestrationEventPrefix": orchestration_event_prefix,
        "assetId": event.get('assetId', ''),
        "databaseId": event.get('databaseId', ''),
    }

    try:
        # Start the Step Functions state machine with the bucket key and name
        logger.info(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
        logger.info(f"Compute tier {tier}")

        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_name,
            input=json.dumps(sfn_input)
        )

        logger.info(f"SFN Response: {sfn_response}")

        # Best-effort: register this sub-SFN execution + its log groups
        register_sub_execution(
            ORCHESTRATION_BUS_NAME, orchestration_event_prefix,
            sfn_response.get("executionArn", ""), STATE_MACHINE_ARN)

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

    # Keyed on the status code, not on an "error" key: every failure route above appends a body
    # carrying only "message", and sfn_response is unbound when the failure came before it was set.
    for response in responses:
        if response.get('statusCode', 200) >= 400:
            return response

    return {
        'statusCode': 200,
        'body': {
            "message": "Starting Asset Processing State Machine",
            "execution": sfn_response
        }
    }
