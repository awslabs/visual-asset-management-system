#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Start the inner Step Functions state machine for Miris upload processing."""
import datetime
import json
import os
import uuid

import boto3
from botocore.config import Config
from customLogging.logger import safeLogger

import manifestHelper
import mirisClaim

logger = safeLogger(service="OpenMirisUploadPipeline")

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
sfn = boto3.client("stepfunctions", region_name=os.environ["AWS_REGION"], config=retry_config)
events_client = boto3.client("events", region_name=os.environ["AWS_REGION"], config=retry_config)
s3_client = boto3.client("s3", region_name=os.environ["AWS_REGION"], config=retry_config)

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
# The objects under the run's results path where the container lists tied roots on an
# ambiguous_root failure, read back by the end Lambda, and the pre-flight warnings of a run that
# uploads, which the execution records as a result.
AMBIGUOUS_ROOT_REPORT_NAME = "ambiguous_root.json"
PREFLIGHT_WARNINGS_REPORT_NAME = "preflight_warnings.txt"
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
# The vended group the Fargate job definition writes to through the awslogs driver, and that job
# definition's name; the container log source is registered only when both are configured.
BATCH_JOB_LOG_GROUP_NAME = os.environ.get("BATCH_JOB_LOG_GROUP_NAME", "")
BATCH_JOB_LOG_GROUP_ARN = os.environ.get("BATCH_JOB_LOG_GROUP_ARN", "")
BATCH_JOB_DEFINITION_NAME = os.environ.get("BATCH_JOB_DEFINITION_NAME", "")
# The Batch state of the inner state machine (its CDK construct id).
BATCH_STATE_NAME = "SubmitMirisUploadBatchJob"
REGISTER_DETAIL_TYPE = "pipeline.execution.register"


def allowed_extensions():
    """The exact members of the comma-separated allow list, lower-cased."""
    return [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(",") if ext.strip()]


def new_job_name():
    """A state machine execution name unique at millisecond concurrency (80 characters at most)."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return f"MirisUpload_{stamp}_{uuid.uuid4().hex[:8]}"


def _abort_external_workflow(error, task_token, claim=None):
    """Fail the workflow task and release the gate's claim, so the same content can run again."""
    if claim:
        mirisClaim.release_claim(s3_client, logger, *claim)
    if task_token:
        logger.error("Aborting external task")
        sfn.send_task_failure(
            taskToken=task_token,
            error="Pipeline Failure: " + error,
            cause="See AWS CloudWatch logs for error cause.",
        )


def results_report_uri(results_uri, name):
    """s3:// URI of the run report `name` under the results path, or '' when the run has none."""
    bucket, _, prefix = (results_uri or "").replace("s3://", "", 1).partition("/")
    if not (results_uri or "").startswith("s3://") or not bucket or not prefix.strip("/"):
        return ""
    return f"s3://{bucket}/{prefix.rstrip('/')}/{name}"


def ambiguous_root_report_uri(results_uri):
    """s3:// URI of the run's ambiguous_root report, or '' when the run has no results path."""
    return results_report_uri(results_uri, AMBIGUOUS_ROOT_REPORT_NAME)


def batch_container_log_entry(job_definition_name, state_name):
    """The log source for the Batch state's container: the job's vended group, streamed under
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
    """Best-effort report of this pipeline's sub-SFN execution and its log sources to the orchestration bus."""
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
            "label": "Miris upload processing",
        },
    }
    logs = []
    if STATE_MACHINE_LOG_GROUP_NAME or STATE_MACHINE_LOG_GROUP_ARN:
        logs.append({
            "logGroupArn": STATE_MACHINE_LOG_GROUP_ARN,
            "logGroupName": STATE_MACHINE_LOG_GROUP_NAME,
            "logStreamName": "",
            "sourceType": "stateMachine",
            "label": "Miris upload state machine",
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
    logger.info("OpenMirisUploadPipeline received event")

    input_configuration_s3_location = event.get("inputConfigurationS3Location", "")
    miris_asset_name = event.get("mirisAssetName", "")
    orchestration_event_prefix = event.get("orchestrationEventPrefix", "")
    external_task_token = event.get("sfnExternalTaskToken", "")
    input_s3_uri = event["inputS3AssetFilePath"]
    input_version_id = event.get("inputVersionId", "")
    output_s3_files = event.get("outputS3AssetFilesPath", "")
    output_s3_preview = event.get("outputS3AssetPreviewPath", "")
    output_s3_metadata = event.get("outputS3AssetMetadataPath", "")
    aux_s3 = event["inputOutputS3AssetAuxiliaryFilesPath"]
    asset_id = event.get("assetId", "")
    database_id = event.get("databaseId", "")
    asset_root_s3 = event.get("assetRootS3Path", "")
    claim_fingerprint = event.get("claimFingerprint", "")
    manual_run = event.get("manualRun") is True
    miris_root_file = event.get("mirisRootFile", "") or ""
    claim = (aux_s3, database_id, asset_id, claim_fingerprint, mirisClaim.owner_id(external_task_token))

    if input_s3_uri.endswith("/"):
        _abort_external_workflow(
            "Input S3 URI cannot be a folder for this pipeline", external_task_token, claim
        )
        return {"statusCode": 400, "body": "Input S3 URI cannot be a folder"}

    _, extension = os.path.splitext(input_s3_uri)
    if not extension or extension.lower() not in allowed_extensions():
        _abort_external_workflow(
            f"Pipeline cannot process file extension {extension!r}",
            external_task_token,
            claim,
        )
        return {"statusCode": 400, "body": f"Unsupported extension {extension!r}"}

    job_name = new_job_name()

    sfn_input = {
        "jobName": job_name,
        "inputS3AssetFilePath": input_s3_uri,
        "inputVersionId": input_version_id,
        "outputS3AssetFilesPath": output_s3_files,
        "outputS3AssetPreviewPath": output_s3_preview,
        "outputS3AssetMetadataPath": output_s3_metadata,
        "inputOutputS3AssetAuxiliaryFilesPath": aux_s3,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "mirisAssetName": miris_asset_name,
        "externalSfnTaskToken": external_task_token,
        "assetId": asset_id,
        "databaseId": database_id,
        "assetRootS3Path": asset_root_s3,
        # Read back by the execution-ended rule to release the claim.
        "claimFingerprint": claim_fingerprint,
        # The container roots a manual run at the selected file, and an automatic run at
        # mirisRootFile when it is set.
        "manualRun": manual_run,
        "mirisRootFile": miris_root_file,
        # Every end route reads this off the state, so it is always present ('' for none).
        "ambiguousRootReportS3Uri": ambiguous_root_report_uri(event.get("outputS3AssetResultsPath", "")),
        "preflightWarningsReportS3Uri": results_report_uri(
            event.get("outputS3AssetResultsPath", ""), PREFLIGHT_WARNINGS_REPORT_NAME),
    }

    try:
        logger.info(f"Starting SFN: {STATE_MACHINE_ARN}")
        response = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_name,
            input=json.dumps(sfn_input),
        )

        register_sub_execution(
            ORCHESTRATION_BUS_NAME, orchestration_event_prefix,
            response.get("executionArn", ""), STATE_MACHINE_ARN)

        response["startDate"] = response["startDate"].strftime("%m-%d-%Y %H:%M:%S")
        return {"statusCode": 200, "body": {"message": "Started", "execution": response}}
    except Exception as e:
        logger.exception(e)
        _abort_external_workflow("Internal Server Error", external_task_token, claim)
        return {"statusCode": 500, "body": "Internal Server Error"}
