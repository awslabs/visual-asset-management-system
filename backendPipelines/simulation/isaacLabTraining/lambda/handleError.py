#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Handle Error Lambda
Notifies external VAMS Step Function of pipeline failures (timeout, OOM, etc.).
Called by the internal Step Function's catch block.
"""

import json
import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="HandleErrorIsaacLab")
sfn = boto3.client("stepfunctions")


def lambda_handler(event, context):
    logger.info(f"Handling error event: {event}")

    external_task_token = event.get("externalSfnTaskToken")
    job_name = event.get("jobName", "unknown")
    error_info = event.get("error", {})
    
    # Extract error details
    error_type = error_info.get("Error", "UnknownError")
    error_cause = error_info.get("Cause", "Pipeline execution failed")
    
    # Build error message
    if "Timeout" in error_type:
        error_message = f"Job '{job_name}' failed: Heartbeat timeout - container may have crashed (OOM) or hung"
    elif "TaskFailed" in error_type:
        error_message = f"Job '{job_name}' failed: {error_cause}"
    else:
        error_message = f"Job '{job_name}' failed: {error_type} - {error_cause}"

    logger.error(f"Pipeline error: {error_message}")

    # Notify external VAMS Step Function of failure
    if external_task_token:
        try:
            sfn.send_task_failure(
                taskToken=external_task_token,
                error="PipelineExecutionFailed",
                cause=error_message[:256]
            )
            logger.info("External Step Function notified of failure")
        except Exception as e:
            logger.error(f"Failed to notify external Step Function: {e}")

    return {
        "status": "ERROR_HANDLED",
        "jobName": job_name,
        "errorType": error_type,
        "errorMessage": error_message,
    }
