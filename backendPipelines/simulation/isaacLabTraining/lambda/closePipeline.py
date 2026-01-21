#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
PipelineEnd Lambda
Handles completion of IsaacLab training workflow.
"""

from customLogging.logger import safeLogger

logger = safeLogger(service="ClosePipelineIsaacLabTraining")


def lambda_handler(event, context):
    logger.info(f"Event: {event}")

    job_name = event.get("jobName")
    status = event.get("status", "COMPLETED")

    logger.info(f"Pipeline {job_name} ended with status: {status}")

    return {
        "jobName": job_name,
        "status": status,
        "outputS3AssetFilesPath": event.get("outputS3AssetFilesPath", ""),
    }
