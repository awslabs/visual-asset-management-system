#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import secrets
from datetime import datetime, timezone
from customLogging.logger import safeLogger
import manifestHelper
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenPipeline-VideoSopBom")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)
events_client = boto3.client('events', config=retry_config)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.m4v,.webm,.mkv")
# Orchestration bus + log group for optional sub-process registration (empty = skipped)
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

ERROR_INPUT_REJECTED = "VideoSopBomInputRejected"
ERROR_PIPELINE = "VideoSopBomPipelineError"
MAX_CAUSE_CHARS = 16384
# The Batch job name and the sub-state-machine execution name share this prefix; the smoke harness
# attributes both by `VideoSopBom_<pipelineExecutionId[:12]>`.
BATCH_JOB_NAME_PREFIX = "VideoSopBom_"


def abort_external_workflow(code, cause, task_token):
    # Not wrapped on purpose: this lambda is nested-invoked, so a failing callback must propagate
    # as FunctionError to vamsExecute, which then reports the token under its own role.
    if task_token:
        logger.error(f"Aborting external task ({code}): {cause}")
        sfn.send_task_failure(
            taskToken=task_token,
            error=code,
            cause=cause[:MAX_CAUSE_CHARS]
        )


def register_sub_execution(orchestration_bus_name, orchestration_event_prefix,
                           sub_execution_arn, state_machine_arn):
    """Best-effort report of this sub-SFN execution to the VAMS orchestration bus; failures are swallowed."""
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
            "EventBusName": orchestration_bus_name,
            "Source": orchestration_event_prefix,
            "DetailType": REGISTER_DETAIL_TYPE,
            "Detail": json.dumps(detail),
        }])
        logger.info(f"Registered sub-execution for pipeline execution {pipeline_execution_id}")
    except Exception as e:  # nosec B110 - registration is best-effort; never fail the pipeline
        logger.warning(f"Sub-process registration failed (non-critical): {e}")


def ids_from_event_prefix(orchestration_event_prefix):
    """(executionId, pipelineExecutionId) from
    '<source>.execution.<executionId>.pipeline.<pipelineExecutionId>'; empty strings when absent."""
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(
        orchestration_event_prefix)
    if not pipeline_execution_id:
        return "", ""
    head = orchestration_event_prefix[:orchestration_event_prefix.rfind(".pipeline.")]
    marker = ".execution."
    index = head.rfind(marker)
    execution_id = head[index + len(marker):] if index >= 0 else ""
    return execution_id, pipeline_execution_id


def build_batch_job_name(pipeline_execution_id):
    """VideoSopBom_<pipelineExecutionId[:12]>_<YYYYmmdd_HHMMSS>_<6 hex>: at most 47 characters,
    inside the 80-character Step Functions execution-name limit and free of ':' and '/'. The id
    segment is empty for an invocation without an orchestration prefix (no pipeline execution id);
    the container's definition_schema.json admits 0-12 characters there."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{BATCH_JOB_NAME_PREFIX}{pipeline_execution_id[:12]}_{stamp}_{secrets.token_hex(3)}"


def input_files_from_event(event):
    """The input list: the manifest entries vamsExecute forwarded, or one entry built from the
    legacy single inputS3AssetFilePath for a direct or pre-manifest invocation."""
    input_files = event.get("inputFiles") or []
    if input_files:
        return list(input_files)
    legacy_uri = event.get("inputS3AssetFilePath", "") or ""
    if not legacy_uri:
        return []
    bucket, key = manifestHelper.parse_s3_uri(legacy_uri)
    relative_path = "/" + key.rsplit("/", 1)[-1] if key else "/"
    return [{"bucket": bucket, "key": key, "versionId": "", "relativePath": relative_path}]


def first_offender(input_files):
    """(entry, cause) for the first entry the extension gate refuses, else (None, ''). Membership
    is exact against the PARSED allow list: a containment test on the joined string admits any
    prefix of a listed extension ('.mp' against '.mp4,.mov'), which the container cannot decode."""
    allowed = [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',') if ext.strip()]
    for entry in input_files:
        key = str(entry.get("key", ""))
        name = str(entry.get("relativePath") or "").rsplit("/", 1)[-1] or key
        if key.endswith("/"):
            return entry, (f"{key} is a whole-asset or folder selection; this pipeline needs "
                           f"explicit video files.")
        _, extension = os.path.splitext(key)
        if not extension or extension.lower() not in allowed:
            return entry, (f"{name} has the extension {extension or '(none)'}; this pipeline "
                           f"accepts {', '.join(allowed)}.")
    return None, ""


def lambda_handler(event, context):
    """
    OpenPipeline - Video SOP/BOM Extraction
    Starts the pipeline's state machine under the Batch job name.
    """

    logger.info(event)

    external_sfn_task_token = event.get('sfnExternalTaskToken', '')
    orchestration_event_prefix = event.get('orchestrationEventPrefix', '')

    input_files = input_files_from_event(event)
    if not input_files:
        cause = "no video files were selected; this pipeline needs at least 1."
        abort_external_workflow(ERROR_INPUT_REJECTED, cause, external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {"message": cause}
        }

    offender, cause = first_offender(input_files)
    if offender is not None:
        abort_external_workflow(ERROR_INPUT_REJECTED, cause, external_sfn_task_token)
        return {
            'statusCode': 400,
            'body': {"message": cause}
        }

    execution_id, pipeline_execution_id = ids_from_event_prefix(orchestration_event_prefix)
    batch_job_name = build_batch_job_name(pipeline_execution_id)

    # State machine input. Only S3 locations and the input list travel; consumers read content
    # from S3.
    sfn_input = {
        "batchJobName": batch_job_name,
        "executionId": execution_id,
        "pipelineExecutionId": pipeline_execution_id,
        "inputFiles": input_files,
        "inputS3AssetFilePath": event.get('inputS3AssetFilePath', ''),
        "outputS3AssetFilesPath": event.get('outputS3AssetFilesPath', ''),
        "outputS3AssetPreviewPath": event.get('outputS3AssetPreviewPath', ''),
        "outputS3AssetMetadataPath": event.get('outputS3AssetMetadataPath', ''),
        "inputOutputS3AssetAuxiliaryFilesPath": event.get('inputOutputS3AssetAuxiliaryFilesPath', ''),
        "assetId": event.get('assetId', ''),
        "databaseId": event.get('databaseId', ''),
        "inputManifestS3Location": event.get('inputManifestS3Location', ''),
        "inputMetadataS3Location": event.get('inputMetadataS3Location', ''),
        "inputConfigurationS3Location": event.get('inputConfigurationS3Location', ''),
        "externalSfnTaskToken": external_sfn_task_token,
        "orchestrationEventPrefix": orchestration_event_prefix,
    }

    try:
        logger.info(f"Starting SFN {STATE_MACHINE_ARN} as {batch_job_name}")
        sfn_response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=batch_job_name,
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
                "message": "Starting Video SOP/BOM Extraction pipeline",
                "execution": sfn_response,
                "batchJobName": batch_job_name,
            }
        }
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(
            ERROR_PIPELINE, f"the pipeline state machine could not be started: {e}",
            external_sfn_task_token)
        return {
            'statusCode': 500,
            'body': {"message": "Internal Server Error"}
        }
