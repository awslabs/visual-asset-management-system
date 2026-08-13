#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.

This function starts the internal Isaac Lab SFN state machine directly.
"""

import os
import json
import uuid
import boto3
from customLogging.logger import safeLogger
import manifestHelper

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
# Orchestration bus + log group for optional sub-process registration (empty = skipped)
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
STATE_MACHINE_LOG_GROUP_NAME = os.environ.get("STATE_MACHINE_LOG_GROUP_NAME", "")
STATE_MACHINE_LOG_GROUP_ARN = os.environ.get("STATE_MACHINE_LOG_GROUP_ARN", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

logger = safeLogger(service="VamsExecuteIsaacLabPipeline")
sfn_client = boto3.client("stepfunctions")
s3_client = boto3.client("s3")
events_client = boto3.client("events")


def register_sub_execution(orchestration_event_prefix, sub_execution_arn):
    """Best-effort: report the internal SFN execution + log group to the orchestration bus."""
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


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so the pipeline task does not wait
    for the full taskTimeout when this lambda cannot start the pipeline."""
    if not task_token:
        return
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error="IsaacLabPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    logger.info(f"Event: {event}")

    external_task_token = None

    try:
        response = {
            "statusCode": 200,
            "body": "",
            "headers": {"Content-Type": "application/json"},
        }

        # Parse request body
        if not event.get("body"):
            message = "Request body is required"
            response["body"] = json.dumps({"message": message})
            response["statusCode"] = 400
            logger.error(response)
            abort_external_workflow(message, external_task_token)
            return response

        if isinstance(event["body"], str):
            data = json.loads(event["body"])
        else:
            data = event["body"]

        # Get external task token (required for VAMS callback)
        if "TaskToken" in data:
            external_task_token = data["TaskToken"]
        else:
            raise Exception(
                "VAMS Workflow TaskToken not found in pipeline input. "
                "Make sure to register this pipeline in VAMS as needing a task token callback."
            )

        executing_userName = data.get("executingUserName", "")
        executing_requestContext = data.get("executingRequestContext", "")

        # Resolve input/output/aux locations from the workflow manifest (legacy-payload fallback)
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        # Read the input configuration from S3 to extract training/compute config
        input_params = manifestHelper.fetch_input_configuration(s3_client, resolved["inputConfigurationS3Location"])

        # Generate unique job name for this execution
        job_name = f"isaaclab-training-{uuid.uuid4().hex[:8]}"

        # The asset-files root (bucket + base location key) used by openPipeline to resolve
        # relative paths (checkpointPath, customEnvironmentPath). It comes from the manifest's
        # first resolved input file: its own bucket + assetRootS3Key (a bucket-relative asset
        # root key), falling back to the legacy payload fields for non-manifest invocations.
        first_input_file = (resolved.get("inputFiles") or [{}])[0]
        asset_bucket = first_input_file.get("bucket", "") or data.get("workflowExecutionS3InputOutputBucket", "")
        asset_location_key = first_input_file.get("assetRootS3Key", "") or data.get("inputAssetLocationKey", "")
        input_asset_file_key = first_input_file.get("key", "") or data.get("inputAssetFileKey", "")

        # Build Step Functions input from standard VAMS messagePayload. bucketAsset +
        # inputAssetLocationKey define the asset root in S3 (the asset-files location, from the
        # manifest) and are required by openPipeline to resolve relative paths and scope policy
        # auto-discovery; deriving the root from inputS3AssetFilePath alone is unreliable because
        # assets can have files nested at arbitrary depths under the asset root.
        sfn_input = {
            "jobName": job_name,
            "bucketAsset": asset_bucket,
            "inputAssetLocationKey": asset_location_key,
            "inputAssetFileKey": input_asset_file_key,
            "assetId": resolved["assetId"],
            "databaseId": resolved["databaseId"],
            "inputS3AssetFilePath": resolved["inputS3AssetFilePath"],
            "outputS3AssetFilesPath": resolved["outputS3AssetFilesPath"],
            "outputS3AssetPreviewPath": resolved["outputS3AssetPreviewPath"],
            "outputS3AssetMetadataPath": resolved["outputS3AssetMetadataPath"],
            "inputOutputS3AssetAuxiliaryFilesPath": resolved["inputOutputS3AssetAuxiliaryFilesPath"],
            "trainingConfig": input_params.get("trainingConfig", {}) if isinstance(input_params, dict) else {},
            "computeConfig": input_params.get("computeConfig", {}) if isinstance(input_params, dict) else {},
            # Metadata + config S3 locations only; container reads from S3 if needed
            "inputMetadataS3Location": resolved["inputMetadataS3Location"],
            "inputConfigurationS3Location": resolved["inputConfigurationS3Location"],
            "orchestrationEventPrefix": resolved["orchestrationEventPrefix"],
            "externalSfnTaskToken": external_task_token,
            "executingUserName": executing_userName,
            "executingRequestContext": executing_requestContext,
        }

        logger.info(f"Starting Step Functions execution: {job_name}")
        logger.info(f"SFN Input: {sfn_input}")

        # Start the internal SFN state machine
        sfn_response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_name,
            input=json.dumps(sfn_input),
        )

        logger.info(f"SFN execution started: {sfn_response['executionArn']}")

        # Best-effort: register this internal SFN execution with the VAMS execution
        register_sub_execution(resolved["orchestrationEventPrefix"], sfn_response["executionArn"])

        return {
            "statusCode": 200,
            "body": json.dumps({
                "jobId": job_name,
                "executionArn": sfn_response["executionArn"],
                "status": "SUBMITTED",
            }),
        }

    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, external_task_token)
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Internal Server Error"}),
        }
