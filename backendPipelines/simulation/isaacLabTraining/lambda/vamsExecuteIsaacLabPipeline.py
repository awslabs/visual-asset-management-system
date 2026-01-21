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

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

logger = safeLogger(service="VamsExecuteIsaacLabPipeline")
sfn_client = boto3.client("stepfunctions")


def lambda_handler(event, context):
    logger.info(f"Event: {event}")

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

        # Get optional parameters with defaults
        input_parameters = data.get("inputParameters", "")
        input_metadata = data.get("inputMetadata", "")
        executing_userName = data.get("executingUserName", "")
        executing_requestContext = data.get("executingRequestContext", "")

        # Parse inputParameters if it's a JSON string
        input_params = input_parameters
        if isinstance(input_params, str) and input_params:
            try:
                input_params = json.loads(input_params)
            except json.JSONDecodeError:
                input_params = {}

        # Generate unique job name for this execution
        job_name = f"isaaclab-training-{uuid.uuid4().hex[:8]}"

        # Build Step Functions input from standard VAMS messagePayload
        sfn_input = {
            "jobName": job_name,
            "inputS3AssetFilePath": data.get("inputS3AssetFilePath", ""),
            "outputS3AssetFilesPath": data.get("outputS3AssetFilesPath", ""),
            "outputS3AssetPreviewPath": data.get("outputS3AssetPreviewPath", ""),
            "outputS3AssetMetadataPath": data.get("outputS3AssetMetadataPath", ""),
            "inputOutputS3AssetAuxiliaryFilesPath": data.get("inputOutputS3AssetAuxiliaryFilesPath", ""),
            "trainingConfig": input_params.get("trainingConfig", {}) if isinstance(input_params, dict) else {},
            "computeConfig": input_params.get("computeConfig", {}) if isinstance(input_params, dict) else {},
            "inputMetadata": input_metadata,
            "inputParameters": input_parameters,
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
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Internal Server Error"}),
        }
