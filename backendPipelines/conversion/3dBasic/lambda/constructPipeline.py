#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructPipeline3dBasic")

# AWS Batch bounds the size of a job's environment overrides, and the container reads the run from the
# manifest and the input configuration it names, so only locations and short identifiers travel.
MAX_EVENT_BYTES = 6000

# What a Batch task needs of the request to run the same conversion the Lambda attempt would have.
REQUEST_KEYS = ("inputManifestS3Location", "inputConfigurationS3Location", "workflowExecutionId")
ESCALATION_REASONS = ("size", "memory", "time")


def conversion_event(request, escalation_reason=None, required_memory_mib=None):
    """The request body the Batch task's ``batch_entry.py`` passes to ``convert()``: the locations of the
    run, never the workflow's task token, with why the run continued on Batch."""
    event = {key: request[key] for key in REQUEST_KEYS if isinstance(request.get(key), str) and request.get(key)}
    if escalation_reason in ESCALATION_REASONS:
        event["escalationReason"] = escalation_reason
    if isinstance(required_memory_mib, int) and not isinstance(required_memory_mib, bool):
        event["requiredMemoryMiB"] = required_memory_mib
    return event


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds the AWS Batch task's environment value (VAMS_CONVERSION_EVENT) for a run that continues on
    Batch, from the state machine's input and the selector's answer.
    """
    request = event.get("conversionRequest") if isinstance(event.get("conversionRequest"), dict) else {}
    selected = event.get("selection") if isinstance(event.get("selection"), dict) else {}
    body = json.dumps(conversion_event(request, selected.get("escalationReason") or event.get("escalationReason"),
                                       selected.get("requiredMemoryMiB")))
    if len(body.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("The conversion request is too large to pass to the job.")
    logger.info("Constructed the conversion job request")
    return {
        "jobName": event.get("jobName"),
        "conversionEvent": body,
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING",
    }
