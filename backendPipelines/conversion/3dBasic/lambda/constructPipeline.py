#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os

import boto3
from botocore.config import Config

import manifestHelper
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructPipeline3dBasic")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
events_client = boto3.client('events', region_name=os.environ["AWS_REGION"], config=retry_config)

# AWS Batch bounds the size of a job's environment overrides, and the container reads the run from the
# manifest and the input configuration it names, so only locations and short identifiers travel.
MAX_EVENT_BYTES = 6000

# What a Batch task needs of the request to run the same conversion the Lambda attempt would have. The
# two execution ids go on every line the task logs.
REQUEST_KEYS = ("inputManifestS3Location", "inputConfigurationS3Location", "workflowExecutionId",
                "pipelineExecutionId")
ESCALATION_REASONS = ("size", "memory", "time")

# The Batch container's log source: the job's vended group, streamed under `<jobDefinitionName>/default/`.
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
BATCH_JOB_LOG_GROUP_NAME = os.environ.get("BATCH_JOB_LOG_GROUP_NAME", "")
BATCH_JOB_LOG_GROUP_ARN = os.environ.get("BATCH_JOB_LOG_GROUP_ARN", "")
BATCH_JOB_DEFINITION_NAME = os.environ.get("BATCH_JOB_DEFINITION_NAME", "")
# The Batch state each job size runs, by its memory in MiB, as the state machine's JobSizeChoice routes
# it (the construct writes this from the same list). A size the map does not hold runs the first state.
BATCH_STATE_NAME = "ConversionBatchJob"
BATCH_STATE_NAMES = json.loads(os.environ.get("BATCH_STATE_NAMES") or "{}")
REGISTER_DETAIL_TYPE = "pipeline.execution.register"


def conversion_event(request, escalation_reason=None, required_memory_mib=None):
    """The request body the Batch task's ``batch_entry.py`` passes to ``convert()``: the locations of the
    run, never the workflow's task token, with why the run continued on Batch."""
    event = {key: request[key] for key in REQUEST_KEYS if isinstance(request.get(key), str) and request.get(key)}
    if escalation_reason in ESCALATION_REASONS:
        event["escalationReason"] = escalation_reason
    if isinstance(required_memory_mib, int) and not isinstance(required_memory_mib, bool):
        event["requiredMemoryMiB"] = required_memory_mib
    return event


def batch_state_name(selection):
    """The Batch state the run is routed to, from the selected job size's memory."""
    job_size = selection.get("jobSize") if isinstance(selection.get("jobSize"), dict) else {}
    return BATCH_STATE_NAMES.get(str(job_size.get("memoryMiB")), BATCH_STATE_NAME)


def batch_container_log_entry(state_name):
    """The log source of the Batch task's container, under the state that runs it. None when the group
    or the job definition is not configured."""
    if not (BATCH_JOB_LOG_GROUP_NAME or BATCH_JOB_LOG_GROUP_ARN) or not BATCH_JOB_DEFINITION_NAME:
        return None
    return {
        "logGroupArn": BATCH_JOB_LOG_GROUP_ARN,
        "logGroupName": BATCH_JOB_LOG_GROUP_NAME,
        "logStreamName": "",
        "logStreamPrefix": f"{BATCH_JOB_DEFINITION_NAME}/default/",
        "stageName": state_name,
        "sourceType": "batch",
        "label": "Conversion job container (AWS Batch)",
    }


def register_batch_log_source(orchestration_event_prefix, state_name):
    """Best-effort: add the Batch container's log source to the pipeline execution, under the one state
    the run takes. Every size's state submits the same job definition, so they share one stream prefix,
    and the registration keeps the first stage name a location was registered with."""
    entry = batch_container_log_entry(state_name)
    pipeline_execution_id = manifestHelper.pipeline_execution_id_from_event_prefix(orchestration_event_prefix)
    if not (ORCHESTRATION_BUS_NAME and entry and pipeline_execution_id):
        logger.info("Batch log source not registered: no orchestration bus, log group or pipeline execution")
        return
    try:
        events_client.put_events(Entries=[{
            "EventBusName": ORCHESTRATION_BUS_NAME,
            "Source": orchestration_event_prefix,
            "DetailType": REGISTER_DETAIL_TYPE,
            "Detail": json.dumps({"pipelineExecutionId": pipeline_execution_id, "logs": [entry]}),
        }])
        logger.info(f"Registered the Batch container log source under {state_name}")
    except Exception as e:  # nosec B110 - registration is best-effort; never fail the pipeline
        logger.warning(f"Batch log source registration failed (non-critical): {e}")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds the AWS Batch task's environment value (VAMS_CONVERSION_EVENT) for a run that continues on
    Batch, from the state machine's input and the selector's answer, and registers the container's log
    source under the Batch state the run is routed to.
    """
    request = event.get("conversionRequest") if isinstance(event.get("conversionRequest"), dict) else {}
    selected = event.get("selection") if isinstance(event.get("selection"), dict) else {}
    body = json.dumps(conversion_event(request, selected.get("escalationReason") or event.get("escalationReason"),
                                       selected.get("requiredMemoryMiB")))
    if len(body.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("The conversion request is too large to pass to the job.")
    logger.info("Constructed the conversion job request")
    register_batch_log_source(event.get("orchestrationEventPrefix", ""), batch_state_name(selected))
    return {
        "jobName": event.get("jobName"),
        "conversionEvent": body,
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING",
    }
