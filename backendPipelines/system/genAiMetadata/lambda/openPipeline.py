#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

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

logger = safeLogger(service="SystemGenAiMetadataOpenPipeline")

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

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]
# Orchestration bus + state-machine log group for sub-process registration
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
# The Fargate render job's vended container log group (the group its job definition writes to through
# the awslogs driver) + its job definition name. Set by the CDK only when the `useFargateRenderer`
# sub-flag deploys the Batch branch; the container log source is registered only when both are set.
BATCH_JOB_LOG_GROUP_NAME = os.environ.get("BATCH_JOB_LOG_GROUP_NAME", "")
BATCH_JOB_LOG_GROUP_ARN = os.environ.get("BATCH_JOB_LOG_GROUP_ARN", "")
BATCH_JOB_DEFINITION_NAME = os.environ.get("BATCH_JOB_DEFINITION_NAME", "")
# The Batch state of this pipeline's state machine (its CDK construct id).
BATCH_STATE_NAME = "FargateRenderJob"
REGISTER_DETAIL_TYPE = "pipeline.execution.register"
SUB_EXECUTION_LABEL = "GenAI metadata processing"
STATE_MACHINE_LOG_LABEL = "GenAI metadata state machine"

# Every field vamsExecute forwards that the state machine's Lambdas read. Passed through unchanged so
# the sub-state-machine input is the complete pipeline state from its first task; the executing
# identity stays with the vamsExecute hop.
_FORWARDED_FIELDS = (
    "inputS3AssetFilePath", "outputS3AssetFilesPath", "outputS3AssetPreviewPath",
    "outputS3AssetMetadataPath", "outputS3AssetResultsPath", "inputOutputS3AssetAuxiliaryFilesPath",
    "inputMetadataS3Location", "inputConfigurationS3Location", "assetId", "databaseId", "bucketId",
    "relativePath", "versionId", "workflowExecutionId", "orchestrationEventPrefix",
)


def abort_external_workflow(error, task_token):
    if task_token is not None and task_token != "":
        # The token is a bearer credential for the parent workflow's task; it is never logged.
        logger.info("Aborting pipeline: failing the external workflow task", error=error)
        sfn.send_task_failure(
            taskToken=task_token,
            error='Pipeline Failure: ' + error,
            cause='See AWS cloudwatch logs for error cause.'
        )


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
                           sub_execution_arn, state_machine_arn):
    """Best-effort: report this sub-SFN execution + its log sources to the orchestration bus;
    failures are swallowed."""
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
            "label": SUB_EXECUTION_LABEL,
        },
    }
    logs = []
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        logs.append({
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
            "sourceType": "stateMachine",
            "label": STATE_MACHINE_LOG_LABEL,
        })
    container_log = batch_container_log_entry(BATCH_JOB_DEFINITION_NAME, BATCH_STATE_NAME)
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
    Gates the input extension and starts the analysis state machine with the complete pipeline state.
    """

    # Identifiers only: the event carries the parent workflow's task token, so it is never rendered
    # whole into a log line.
    logger.info("Event", assetId=event.get("assetId", ""), databaseId=event.get("databaseId", ""),
                workflowExecutionId=event.get("workflowExecutionId", ""),
                inputS3AssetFilePath=event.get("inputS3AssetFilePath", ""), eventKeys=sorted(event))
    logger.info(f"Context: {context}")

    external_sfn_task_token = event.get('sfnExternalTaskToken', '') or ''
    input_s3_asset_file_uri = event['inputS3AssetFilePath']

    # Folder check
    if input_s3_asset_file_uri.endswith("/"):
        abort_external_workflow("Input S3 URI cannot be a folder for this pipeline", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Input S3 URI cannot be a folder"
            }
        }

    _file_root, extension = os.path.splitext(input_s3_asset_file_uri)

    # Validate the extension against exact members of the comma-separated allow list. A containment
    # test against the joined string accepts any prefix of a listed extension ('.gl' against
    # '.glb,.fbx'), which admits a file the classifier cannot place.
    allowed_extensions = [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',')
                          if ext.strip()]

    if not extension or extension.lower() not in allowed_extensions:
        logger.info("Aborting pipeline: Pipeline cannot process file type provided")
        abort_external_workflow("Pipeline cannot process file type provided", external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {
                "message": "Pipeline cannot process file type provided"
            }
        }

    # Generate new job name; the random suffix keeps concurrent starts within one millisecond distinct
    job_name = f"PipelineJob_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{uuid.uuid4().hex[:8]}"

    # StateMachine Execution Input: the complete pipeline state
    sfn_input = {
        "jobName": job_name,
        "externalSfnTaskToken": external_sfn_task_token,
    }
    for field in _FORWARDED_FIELDS:
        sfn_input[field] = event.get(field, "") or ""

    try:
        logger.info(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
        # The input carries externalSfnTaskToken; log its shape, not its content.
        logger.info("SFN Input", jobName=job_name, inputKeys=sorted(sfn_input),
                    inputS3AssetFilePath=sfn_input["inputS3AssetFilePath"],
                    workflowExecutionId=sfn_input["workflowExecutionId"])

        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_name,
            input=json.dumps(sfn_input)
        )

        logger.info("SFN Response", executionArn=sfn_response.get("executionArn", ""))

        # Best-effort: register this sub-SFN execution with the VAMS execution
        register_sub_execution(
            ORCHESTRATION_BUS_NAME, sfn_input["orchestrationEventPrefix"],
            sfn_response.get("executionArn", ""), STATE_MACHINE_ARN)

        # response datetime not JSON serializable
        sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')
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
            "message": "Starting Asset Processing State Machine",
            "execution": sfn_response
        }
    }
