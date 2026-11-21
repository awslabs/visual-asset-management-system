# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import uuid
from customLogging.logger import safeLogger
import manifestHelper
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenPipeline-CogConvert")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)
events_client = boto3.client('events', config=retry_config)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]
# Orchestration bus + log group for optional sub-process registration (empty = skipped)
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"
# The Lambdas the state machine runs, by the state that runs them (each a CDK construct id, so a key of
# the machine's States). The CDK passes each one's function name in STATE_FUNCTION_NAMES, a JSON object
# keyed by these names; their log groups are registered as log sources, which VAMS reads for the lines
# that carry this run's workflow and pipeline execution ids (each handler binds them, executionLogKeys).
CONSTRUCT_PIPELINE_STATE_NAME = "ConstructPipelineTask"
BATCH_JOB_STATE_NAME = "CogConvertBatchJob"
PIPELINE_END_STATE_NAME = "PipelineEndTask"
LAMBDA_STATE_NAMES = (CONSTRUCT_PIPELINE_STATE_NAME, BATCH_JOB_STATE_NAME, PIPELINE_END_STATE_NAME)
STATE_FUNCTION_NAMES = os.environ.get("STATE_FUNCTION_NAMES", "")
LAMBDA_LOG_GROUP_PREFIX = "/aws/lambda/"


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
    if (task_token != None and task_token != ""):
        logger.error("Aborting external task")
        sfn.send_task_failure(
            taskToken=task_token,
            error=_bounded_error('Pipeline Failure: ' + str(error)),
            cause=_bounded_cause('See AWS cloudwatch logs for error cause. ' + str(error))
        )


def _state_function_names():
    """{state name: function name} from STATE_FUNCTION_NAMES; {} when unset or not a JSON object."""
    try:
        names = json.loads(STATE_FUNCTION_NAMES or "{}")
    except ValueError:
        logger.warning("STATE_FUNCTION_NAMES is not JSON; the Lambda log sources are not registered")
        return {}
    return names if isinstance(names, dict) else {}


def lambda_log_entries():
    """The log source of each Lambda state, in the order the machine runs them: the function's
    /aws/lambda/<function> group, in the account, Region and partition of the state machine's group.
    Every run shares the group, so VAMS reads it for this run's execution ids. A state with no function
    name is left out."""
    names = _state_function_names()
    arn_prefix, separator, _ = STATE_MACHINE_LOG_GROUP_ARN.partition(":log-group:")
    entries = []
    for state_name in LAMBDA_STATE_NAMES:
        function_name = names.get(state_name)
        if not isinstance(function_name, str) or not function_name:
            continue
        group_name = LAMBDA_LOG_GROUP_PREFIX + function_name
        entries.append({
            "logGroupArn": arn_prefix + separator + group_name if separator else "",
            "logGroupName": group_name,
            "logStreamName": "",
            "logStreamPrefix": "",
            "stageName": state_name,
            "sourceType": "lambda",
            "label": f"{state_name} Lambda",
        })
    return entries


def register_sub_execution(orchestration_bus_name, orchestration_event_prefix,
                           sub_execution_arn, state_machine_arn):
    """Best-effort report of this sub-SFN execution and its log sources (the state machine and its
    Lambda states) to the VAMS orchestration bus; failures are swallowed. executeBatchJob adds the
    container's source when it submits the job."""
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
            "label": "COG conversion processing",
        },
    }
    logs = []
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        logs.append({
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
            "sourceType": "stateMachine",
            "label": "COG conversion state machine",
        })
    logs.extend(lambda_log_entries())
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
    OpenPipeline - COG conversion
    Starts StepFunctions State Machine for COG conversion.
    """

    logger.info("Event", event=event)

    # Get the input metadata + input-configuration S3 locations
    input_metadata_s3_location = event.get('inputMetadataS3Location', '')
    input_configuration_s3_location = event.get('inputConfigurationS3Location', '')

    # Orchestration event prefix for optional sub-process registration
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')

    external_sfn_task_token = event.get('sfnExternalTaskToken', '')

    input_s3_asset_files_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath']
    output_s3_asset_preview_uri = event.get('outputS3AssetPreviewPath', '')
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '')
    output_s3_asset_results_uri = event.get('outputS3AssetResultsPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event.get('inputOutputS3AssetAuxiliaryFilesPath', '')

    # Folder check
    if input_s3_asset_files_uri.endswith("/"):
        abort_external_workflow("Input S3 URI cannot be a folder", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {"message": "Input S3 URI cannot be a folder"}
        }

    # Validate file extension against exact members of the comma-separated allow list. A
    # containment test against the joined string accepts any prefix of a listed extension
    # ('.ti' against '.tif,.tiff'), which admits a file the container cannot read.
    file_root, extension = os.path.splitext(input_s3_asset_files_uri)
    allowed_extensions = [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',')
                          if ext.strip()]

    if not extension or extension.lower() not in allowed_extensions:
        abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {"message": f"Pipeline cannot process file type: {extension}"}
        }

    # Generate job name
    job_name = f"CogConvert_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{uuid.uuid4().hex[:8]}"

    # State machine input
    sfn_input = {
        "jobName": job_name,
        "inputS3AssetFilePath": input_s3_asset_files_uri,
        "outputS3AssetFilesPath": output_s3_asset_files_uri,
        "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
        "outputS3AssetResultsPath": output_s3_asset_results_uri,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
        "assetId": event.get('assetId', ''),
        "databaseId": event.get('databaseId', ''),
        # Metadata + config S3 locations only; consumers read content from S3
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        # Regions of the input and output asset buckets (empty = deployment Region); constructPipeline
        # hands them to the container, which signs each bucket's requests for its Region.
        "inputBucketRegion": event.get("inputBucketRegion", ""),
        "outputBucketRegion": event.get("outputBucketRegion", ""),
        # The S3 version of the input the run reads (empty reads the current object).
        "inputVersionId": event.get("inputVersionId", ""),
        "externalSfnTaskToken": external_sfn_task_token,
        # Carried through the state machine so executeBatchJob can register the Batch job itself as
        # abortable. Stopping this sub-state-machine does not stop the job — it is submitted from a
        # Lambda under WAIT_FOR_TASK_TOKEN, so Step Functions does not own its lifecycle.
        "orchestrationEventPrefix": orchestration_event_prefix,
        # With the prefix's pipeline execution id, the id every state's Lambda puts on its log lines.
        "workflowExecutionId": event.get("workflowExecutionId", "") or "",
    }

    try:
        logger.info(f"Starting SFN: {STATE_MACHINE_ARN}")
        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_name,
            input=json.dumps(sfn_input)
        )

        # Register this sub-SFN execution with the VAMS execution (best-effort)
        register_sub_execution(
            ORCHESTRATION_BUS_NAME, orchestration_event_prefix,
            sfn_response.get("executionArn", ""), STATE_MACHINE_ARN)

        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')

        return {
            'statusCode': 200,
            'body': {
                "message": "Starting COG Conversion Pipeline",
                "execution": sfn_response
            }
        }
    except Exception as e:
        logger.exception(e)
        abort_external_workflow("Internal Server Error", external_sfn_task_token)
        return {
            'statusCode': 500,
            'body': {"message": "Internal Server Error"}
        }
