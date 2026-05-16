#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Execute Batch Job Lambda for Coordinate Transform pipeline.
Submits AWS Batch job and passes task token for async callback.
Called by the internal Step Functions state machine with
WAIT_FOR_TASK_TOKEN integration pattern.
"""

import json
import os

import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="ExecuteBatchJobCoordTransform")
batch = boto3.client("batch")

BATCH_JOB_QUEUE = os.environ["BATCH_JOB_QUEUE"]
BATCH_JOB_DEFINITION = os.environ["BATCH_JOB_DEFINITION"]


def lambda_handler(event, context):
    logger.info(f"Event: {event}")

    job_name = event["jobName"]
    definition = event["definition"]
    task_token = event.get("taskToken", "")

    submit_params = {
        "jobName": job_name,
        "jobQueue": BATCH_JOB_QUEUE,
        "jobDefinition": BATCH_JOB_DEFINITION,
        "containerOverrides": {
            "command": definition
            if isinstance(definition, list)
            else [json.dumps(definition)],
            "environment": [
                {"name": "TASK_TOKEN", "value": task_token},
                {
                    "name": "AWS_REGION",
                    "value": os.environ.get("AWS_REGION", "us-east-1"),
                },
            ],
        },
    }

    logger.info(f"Submitting Batch job: {job_name}")
    response = batch.submit_job(**submit_params)

    logger.info(f"Batch job submitted: {response['jobId']}")

    return {
        "jobId": response["jobId"],
        "jobName": job_name,
        "status": "SUBMITTED",
    }
