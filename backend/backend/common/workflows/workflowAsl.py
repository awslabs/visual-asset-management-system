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

import hashlib
import json
import os
import time
import uuid

import boto3
from botocore.config import Config

from customLogging.logger import safeLogger
from common.workflows.workflowAslBuilder import generate_workflow_asl
from common.workflows.stepfunctions_builder import (
    create_state_machine,
    update_state_machine,
    publish_state_machine_version,
)

logger = safeLogger(service_name="WorkflowAsl")

# ASL schema version stamped on the workflow record.
ASL_SCHEMA_VERSION = 1

# Published versions kept per workflow state machine. Step Functions caps a state machine at 1000
# versions; older ones are deleted after each deploy. A launch reads the newest from the workflow
# record, so a handful of older ones only has to cover a launch that read the record just before a
# few redeploys landed.
MAX_RETAINED_STATE_MACHINE_VERSIONS = 5

# Bounded retry for the ConflictException Step Functions raises on overlapping publishes.
MAX_PUBLISH_CONFLICT_RETRIES = 3
PUBLISH_CONFLICT_BACKOFF_SECONDS = 0.5

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
    """Map a list of (ref, pipeline_record) tuples (workflow order) to flat pipeline dicts. A ref is
    either a request model (attribute access) or a stored specifiedPipelines entry (a dict)."""
    return [to_asl_pipeline_dict(rec, _ref_job_name(ref)) for ref, rec in ref_records]


def _ref_job_name(ref):
    if isinstance(ref, dict):
        return ref.get("jobName", "") or ""
    return getattr(ref, "jobName", "") or ""


def pipeline_asl_fingerprint(pipeline_record):
    """Stable hash of everything a pipeline record contributes to a workflow's state machine
    definition: the flat dict the ASL generator reads, minus the workflow-owned step `name`.

    Two records with the same fingerprint produce the same task state, so a workflow whose stored
    fingerprints equal its pipelines' current ones is running a definition built from their current
    settings. The resource block is compared parsed, so key order inside it does not matter."""
    flat = to_asl_pipeline_dict(pipeline_record or {})
    flat.pop("name", None)
    flat["userProvidedResource"] = json.loads(flat.get("userProvidedResource") or "{}")
    canonical = json.dumps(flat, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def workflow_pipeline_fingerprints(pipeline_records):
    """{"pipelineDatabaseId:pipelineId": fingerprint} for the pipelines a workflow references."""
    return {
        f"{rec.get('databaseId', '')}:{rec.get('pipelineId', '')}": pipeline_asl_fingerprint(rec)
        for rec in pipeline_records or []
    }


def deployment_matches(workflow_item, pipeline_records):
    """True when the workflow record names a published version AND its stored per-pipeline
    fingerprints equal these pipeline records' current ones — i.e. the version was built from exactly
    these settings. A record written before versions and fingerprints were stored never matches."""
    if not (workflow_item or {}).get("workflowVersionArn"):
        return False
    stored = (workflow_item or {}).get("pipelineFingerprints") or {}
    return dict(stored) == workflow_pipeline_fingerprints(pipeline_records)


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
    # uuid1 only makes a state-machine or job name unique; the value is neither a secret nor a
    # capability token, so predictability has no security consequence.
    # nosemgrep: python.lang.security.insecure-uuid-version.insecure-uuid-version
    suffix = uuid.uuid1().hex[:8]
    name = workflow_id[:80 - len("vams-") - len(suffix)]
    return "vams-" + name + suffix


def _is_conflict(error):
    """True for the Step Functions ConflictException raised when two publishes of one state machine
    (or a publish and a version delete) overlap."""
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
    return code == "ConflictException"


def _with_conflict_retry(operation):
    """Run a publishing call, retrying a ConflictException a bounded number of times. A workflow can
    be redeployed by a save, a pipeline-change refresh and a launch at the same moment, and Step
    Functions rejects overlapping publishes of one state machine rather than queueing them."""
    for attempt in range(MAX_PUBLISH_CONFLICT_RETRIES + 1):
        try:
            return operation()
        except Exception as e:
            if not _is_conflict(e) or attempt == MAX_PUBLISH_CONFLICT_RETRIES:
                raise
            logger.info(f"State machine publish conflicted; retrying (attempt {attempt + 1})")
            time.sleep(PUBLISH_CONFLICT_BACKOFF_SECONDS * (attempt + 1))


def prune_state_machine_versions(sf_client, state_machine_arn, keep_version_arns=()):
    """Delete published versions beyond the newest MAX_RETAINED_STATE_MACHINE_VERSIONS, never one
    named in keep_version_arns. Every deploy publishes one, and Step Functions caps a state machine's
    versions, so unpruned versions would eventually block every save of a long-lived workflow.

    Deleting a version stops new executions from starting on it and leaves its in-progress executions
    running. Best-effort: a failure is logged and the next deploy retries the sweep."""
    keep = {arn for arn in keep_version_arns if arn}
    try:
        seen = 0
        kwargs = {"stateMachineArn": state_machine_arn}
        while True:
            # Newest first (ListStateMachineVersions orders by creation time, descending).
            response = sf_client.list_state_machine_versions(**kwargs) or {}
            for version in response.get("stateMachineVersions", []) or []:
                version_arn = (version or {}).get("stateMachineVersionArn", "")
                if not version_arn:
                    continue
                seen += 1
                if seen <= MAX_RETAINED_STATE_MACHINE_VERSIONS or version_arn in keep:
                    continue
                sf_client.delete_state_machine_version(stateMachineVersionArn=version_arn)
            if not response.get("nextToken"):
                return
            kwargs["nextToken"] = response["nextToken"]
    except Exception as e:
        logger.warning(f"Pruning state machine versions for {state_machine_arn} failed "
                       f"(non-fatal): {e}")


def deploy_state_machine(database_id, workflow_id, ref_records, existing_arn="", job_names=None):
    """Generate the workflow ASL from its referenced pipelines, deploy the Step Functions state
    machine, and publish the deployed revision as a version. Returns
    (state_machine_arn, job_names, state_machine_version_arn).

    Creates a new state machine when there is no existing ARN (or the recorded ARN no longer exists —
    the orphaned-record case), otherwise updates the existing one in place (preserving its ARN +
    execution history). The ASL is produced by the shared generator from the flat pipeline dicts
    to_asl_pipeline_dicts(ref_records) yields, using the scaffolding Lambda names + IAM role read
    lazily from the environment.

    job_names are the per-pipeline job names the generator baked into the ASL's output S3 paths
    (ordered to match ref_records). Passing the previously stored names keeps them when they still
    fit the steps (workflowAslBuilder.reusable_job_names), so a redeploy of an unchanged step list
    leaves every output prefix where it was. The caller persists them on the workflow record's
    `jobNames` so the execute handler reconstructs the identical output prefixes.

    The version is immutable: an execution started on its ARN runs exactly this definition, which an
    execution started on the unqualified ARN right after an update is not guaranteed to do. The
    caller persists it with the job names, and the execute handler starts on it.

    Raises on a deployment failure (missing env, ASL/validation error, or a boto3 error) so the
    caller aborts the save rather than persisting a workflow whose state machine was not deployed."""
    if not ref_records:
        # No pipelines to deploy (empty workflow): keep any existing ARN, deploy nothing.
        return existing_arn or "", [], ""

    env = _deploy_env()
    sf_client = _sf_client()

    pipelines = to_asl_pipeline_dicts(ref_records)
    workflow_definition, job_names = generate_workflow_asl(
        pipelines, database_id, workflow_id,
        process_workflow_output_function=env["process_workflow_output_function"],
        interim_tracking_function=env["interim_tracking_function"],
        error_handler_function=env["error_handler_function"],
        aws_partition=env["aws_partition"],
        job_names=job_names,
    )

    # Update in place only when the recorded ARN still resolves; otherwise (new workflow or orphaned
    # record whose state machine was deleted) create a fresh state machine.
    if existing_arn and _state_machine_exists(sf_client, existing_arn):
        logger.info(f"Updating existing state machine for workflow {workflow_id}")
        version_arn = _with_conflict_retry(lambda: update_state_machine(
            sf_client=sf_client,
            state_machine_arn=existing_arn,
            definition=workflow_definition,
            role_arn=env["role_arn"],
            log_group_arn=env["log_group_arn"],
            publish=True,
        ))
        state_machine_arn = existing_arn
    else:
        logger.info(f"Creating new state machine for workflow {workflow_id}")
        state_machine_arn = create_state_machine(
            sf_client=sf_client,
            name=_generate_state_machine_name(workflow_id),
            definition=workflow_definition,
            role_arn=env["role_arn"],
            log_group_arn=env["log_group_arn"],
            state_machine_type="STANDARD",
        )
        version_arn = _with_conflict_retry(
            lambda: publish_state_machine_version(sf_client, state_machine_arn))

    prune_state_machine_versions(sf_client, state_machine_arn, keep_version_arns=[version_arn])
    return state_machine_arn, job_names, version_arn or ""
