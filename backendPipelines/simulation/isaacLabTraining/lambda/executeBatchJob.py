#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Execute Batch Job Lambda
Submits AWS Batch job and passes task token for async callback.
This is an internal function called by the Step Functions state machine.
"""

import json
import os
import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="ExecuteBatchJobIsaacLab")
batch = boto3.client("batch")

BATCH_JOB_QUEUE = os.environ["BATCH_JOB_QUEUE"]
BATCH_JOB_DEFINITION = os.environ["BATCH_JOB_DEFINITION"]


def lambda_handler(event, context):
    logger.info(f"Event: {event}")

    job_name = event["jobName"]
    definition = json.loads(event["definition"])
    task_token = event.get("taskToken", "")
    output_s3_path = event.get("outputS3AssetFilesPath", "")
    input_s3_path = event.get("inputS3AssetFilePath", "")

    # Add output path and input path to job config
    definition["outputS3AssetFilesPath"] = output_s3_path
    definition["inputS3AssetFilePath"] = input_s3_path

    submit_params = {
        "jobName": job_name,
        "jobQueue": BATCH_JOB_QUEUE,
        "jobDefinition": BATCH_JOB_DEFINITION,
        "containerOverrides": {
            "command": [json.dumps(definition)],
            "environment": [
                {"name": "SFN_TASK_TOKEN", "value": task_token},
                {"name": "OUTPUT_S3_PATH", "value": output_s3_path},
                {"name": "INPUT_S3_PATH", "value": input_s3_path or ""},
            ],
        },
    }

    # Multi-node configuration
    num_nodes = event.get("numNodes", 1)
    if num_nodes > 1:
        submit_params["nodeOverrides"] = {
            "numNodes": num_nodes,
            "nodePropertyOverrides": [
                {
                    "targetNodes": "0:",
                    "containerOverrides": submit_params["containerOverrides"],
                }
            ],
        }
        del submit_params["containerOverrides"]

    logger.info(f"Submitting Batch job: {submit_params}")
    response = batch.submit_job(**submit_params)

    logger.info(f"Batch job submitted: {response['jobId']}")

    return {
        "jobId": response["jobId"],
        "jobName": job_name,
        "status": "SUBMITTED",
    }
