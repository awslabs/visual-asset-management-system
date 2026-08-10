# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow record -> Step Functions ASL adapter.

The shared ASL generator (common/workflows/workflowAslBuilder.generate_workflow_asl) consumes flat
pipeline dicts — it reads each pipeline's `name`, `pipelineExecutionType`, `waitForCallback`,
`taskTimeout`, `taskHeartbeatTimeout`, and a `userProvidedResource` JSON string. A pipeline record
stores that information structurally in its `executionConfig` map. This module bridges the two:
`to_asl_pipeline_dict` maps a pipeline record + its workflow job name into the flat dict the
generator/builders expect.

`deploy_state_machine` generates the full ASL (per-pipeline task states + interim-tracking /
error-handler / process-output scaffolding) and creates or updates the Step Functions state machine
with create_state_machine/update_state_machine. The scaffolding Lambda names + IAM role + partition it
needs are read LAZILY from the environment inside the function (not at import), so this module keeps
its no-AWS/no-env import contract: importing it — as the pipeline/workflow read paths and tests do —
triggers no boto3 client build and no env lookup.
"""

import json
import os
import uuid

import boto3
from botocore.config import Config

from customLogging.logger import safeLogger
from common.workflows.workflowAslBuilder import generate_workflow_asl
from common.workflows.stepfunctions_builder import create_state_machine, update_state_machine

logger = safeLogger(service_name="WorkflowAsl")

# ASL schema version stamped on the workflow record.
ASL_SCHEMA_VERSION = 1

# Configure AWS clients with retry configuration.
_retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})


def _execution_config_to_user_resource(execution_config):
    """Map an executionConfig map to the `userProvidedResource` dict the task builders read.

    The builders read: resourceId (Lambda fn / SQS QueueUrl / EventBridge bus), resourceType,
    eventSource / eventDetailType (EventBridge), and the deadline* fields (DeadlineCloud). The stored
    executionConfig nests these under per-type blocks (lambda/sqs/eventBridge/deadlineCloud)."""
    execution_config = execution_config or {}
    exec_type = execution_config.get("executionType", "Lambda")
    lam = execution_config.get("lambda") or {}
    sqs = execution_config.get("sqs") or {}
    eb = execution_config.get("eventBridge") or {}
    dc = execution_config.get("deadlineCloud") or {}

    if exec_type == "SQS":
        return {"resourceType": "SQS", "resourceId": sqs.get("queueUrl", "")}
    if exec_type == "EventBridge":
        return {
            "resourceType": "EventBridge",
            "resourceId": eb.get("busArn", "") or "default",
            "eventSource": eb.get("source", ""),
            "eventDetailType": eb.get("detailType", ""),
        }
    if exec_type == "DeadlineCloud":
        # The numeric fields are loaded from DynamoDB as Decimal; coerce to int so the resulting
        # dict is JSON-serializable (json.dumps cannot encode Decimal). Absent values stay None.
        def _as_int(v):
            return int(v) if v not in (None, "") else v
        return {
            "resourceType": "DeadlineCloud",
            "resourceId": "",
            "deadlineFarmId": dc.get("farmId", ""),
            "deadlineQueueId": dc.get("queueId", ""),
            "deadlineStorageProfileId": dc.get("storageProfileId", ""),
            "deadlineTemplate": dc.get("template", ""),
            "deadlineTemplateType": dc.get("templateType", "YAML") or "YAML",
            "deadlinePriority": _as_int(dc.get("priority")),
            "deadlineMaxRetriesPerTask": _as_int(dc.get("maxRetriesPerTask")),
            "deadlineMaxFailedTasksCount": _as_int(dc.get("maxFailedTasksCount")),
        }
    # Lambda (default): resourceId is the target Lambda function name/ARN.
    return {"resourceType": "Lambda", "resourceId": lam.get("resourceId", ""), "isProvided": True}


def to_asl_pipeline_dict(pipeline_record, job_name=""):
    """Map a pipeline record (+ workflow-ref job name) to the flat pipeline dict the shared ASL
    generator + task builders consume. `name` uses the workflow ref's job name when provided, else
    the pipeline id (the generator uses `name` for state/job names + output-path templates)."""
    execution_config = pipeline_record.get("executionConfig", {}) or {}
    return {
        "name": job_name or pipeline_record.get("pipelineId", ""),
        "pipelineId": pipeline_record.get("pipelineId", ""),
        "databaseId": pipeline_record.get("databaseId", ""),
        "pipelineExecutionType": execution_config.get("executionType", "Lambda"),
        "waitForCallback": execution_config.get("waitForCallback", "Disabled"),
        "taskTimeout": execution_config.get("taskTimeout", ""),
        "taskHeartbeatTimeout": execution_config.get("taskHeartbeatTimeout", ""),
        "userProvidedResource": json.dumps(_execution_config_to_user_resource(execution_config)),
    }


def to_asl_pipeline_dicts(ref_records):
    """Map a list of (ref, pipeline_record) tuples (workflow order) to flat pipeline dicts."""
    return [to_asl_pipeline_dict(rec, getattr(ref, "jobName", "") or "") for ref, rec in ref_records]


def _deploy_env():
    """Read the state-machine deployment values from the environment (lazily, so import stays
    AWS/env-free). Raises if a required value is unset."""
    return {
        "process_workflow_output_function": os.environ["PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME"],
        "interim_tracking_function": os.environ["INTERIM_PIPELINE_TRACKING_LAMBDA_FUNCTION_NAME"],
        "error_handler_function": os.environ["HANDLE_EXECUTION_ERROR_LAMBDA_FUNCTION_NAME"],
        "role_arn": os.environ["LAMBDA_ROLE_ARN"],
        "log_group_arn": os.environ["LOG_GROUP_ARN"],
        # Deployment partition for the service-integration ARNs embedded in the ASL (defaults to
        # commercial "aws"); GovCloud/China/ISO inject the matching partition.
        "aws_partition": os.environ.get("AWS_PARTITION", "aws") or "aws",
    }


def _sf_client():
    """Build a retry-configured Step Functions client lazily (kept out of import to preserve the
    no-AWS-at-import contract)."""
    return boto3.client("stepfunctions", config=_retry_config)


def _state_machine_exists(sf_client, workflow_arn):
    """True if the state machine ARN still resolves. Any other describe failure propagates: treating
    it as absent would create a second state machine and orphan the recorded one."""
    try:
        sf_client.describe_state_machine(stateMachineArn=workflow_arn)
        return True
    except sf_client.exceptions.StateMachineDoesNotExist:
        logger.warning(f"State machine does not exist: {workflow_arn}")
        return False
    except Exception as e:
        logger.exception(f"Error verifying state machine existence for {workflow_arn}: {e}")
        raise


def _generate_state_machine_name(workflow_id):
    """Build a unique, <=80-char, 'vams-'-prefixed state machine name."""
    suffix = uuid.uuid1().hex[:8]
    name = workflow_id[:80 - len("vams-") - len(suffix)]
    return "vams-" + name + suffix


def deploy_state_machine(database_id, workflow_id, ref_records, existing_arn=""):
    """Generate the workflow ASL from its referenced pipelines and deploy the Step Functions state
    machine, returning (state_machine_arn, job_names).

    Creates a new state machine when there is no existing ARN (or the recorded ARN no longer exists —
    the orphaned-record case), otherwise updates the existing one in place (preserving its ARN +
    execution history). The ASL is produced by the shared generator from the flat pipeline dicts
    to_asl_pipeline_dicts(ref_records) yields, using the scaffolding Lambda names + IAM role read
    lazily from the environment.

    job_names are the per-pipeline job names the generator baked into the ASL's output S3 paths
    (ordered to match ref_records). The caller persists them on the workflow record's `jobNames` so
    the execute handler reconstructs the identical output prefixes.

    Raises on a deployment failure (missing env, ASL/validation error, or a boto3 error) so the
    caller aborts the save rather than persisting a workflow whose state machine was not deployed."""
    if not ref_records:
        # No pipelines to deploy (empty workflow): keep any existing ARN, deploy nothing.
        return existing_arn or "", []

    env = _deploy_env()
    sf_client = _sf_client()

    pipelines = to_asl_pipeline_dicts(ref_records)
    workflow_definition, job_names = generate_workflow_asl(
        pipelines, database_id, workflow_id,
        process_workflow_output_function=env["process_workflow_output_function"],
        interim_tracking_function=env["interim_tracking_function"],
        error_handler_function=env["error_handler_function"],
        aws_partition=env["aws_partition"],
    )

    # Update in place only when the recorded ARN still resolves; otherwise (new workflow or orphaned
    # record whose state machine was deleted) create a fresh state machine.
    if existing_arn and _state_machine_exists(sf_client, existing_arn):
        logger.info(f"Updating existing state machine for workflow {workflow_id}")
        update_state_machine(
            sf_client=sf_client,
            state_machine_arn=existing_arn,
            definition=workflow_definition,
            role_arn=env["role_arn"],
            log_group_arn=env["log_group_arn"],
        )
        return existing_arn, job_names

    logger.info(f"Creating new state machine for workflow {workflow_id}")
    new_arn = create_state_machine(
        sf_client=sf_client,
        name=_generate_state_machine_name(workflow_id),
        definition=workflow_definition,
        role_arn=env["role_arn"],
        log_group_arn=env["log_group_arn"],
        state_machine_type="STANDARD",
    )
    return new_arn, job_names
