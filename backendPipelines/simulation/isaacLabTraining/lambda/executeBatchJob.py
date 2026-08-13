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
import manifestHelper
from customLogging.logger import safeLogger

logger = safeLogger(service="ExecuteBatchJobIsaacLab")
batch = boto3.client("batch")
events_client = boto3.client("events")

BATCH_JOB_QUEUE = os.environ["BATCH_JOB_QUEUE"]
BATCH_JOB_DEFINITION = os.environ["BATCH_JOB_DEFINITION"]
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"

# Resource type reported for an AWS Batch job, so the abort path can terminate it by id.
RESOURCE_TYPE_BATCH_JOB = "batchJob"


def register_batch_job(orchestration_event_prefix, job_id):
    """Best-effort: report this Batch job to the orchestration bus so an abort can terminate it.

    Registration matters here specifically because this pipeline submits the job from a Lambda under
    WAIT_FOR_TASK_TOKEN rather than through the Step Functions ``.sync`` Batch integration. With
    ``.sync``, Step Functions owns the job's lifecycle and stopping the state machine stops the job;
    here nothing does, so an un-registered job keeps running (and billing) after an abort.

    Never fails the pipeline: a registration problem must not stop a job that was already submitted.
    """
    if not ORCHESTRATION_BUS_NAME or not orchestration_event_prefix or not job_id:
        logger.info("Orchestration bus/prefix/jobId not available; skipping Batch job registration")
        return
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(
        orchestration_event_prefix)
    if not pipeline_execution_id:
        logger.warning("Could not derive pipelineExecutionId from event prefix; skipping registration")
        return
    try:
        events_client.put_events(Entries=[{
            "EventBusName": ORCHESTRATION_BUS_NAME,
            "Source": orchestration_event_prefix,
            "DetailType": REGISTER_DETAIL_TYPE,
            "Detail": json.dumps({
                "pipelineExecutionId": pipeline_execution_id,
                "subExecution": {"resourceType": RESOURCE_TYPE_BATCH_JOB, "jobId": job_id},
            }),
        }])
        logger.info(f"Registered Batch job {job_id} for pipeline execution {pipeline_execution_id}")
    except Exception as e:  # nosec B110 - registration is best-effort; never fail the pipeline
        logger.warning(f"Batch job registration failed (non-critical): {e}")


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

    register_batch_job(event.get("orchestrationEventPrefix", ""), response["jobId"])

    return {
        "jobId": response["jobId"],
        "jobName": job_name,
        "status": "SUBMITTED",
    }
