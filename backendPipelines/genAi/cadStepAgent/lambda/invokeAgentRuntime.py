# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Invoke Agent Runtime Lambda for the GenAI CAD STEP agent pipeline (agentcore runtime).
Hands one run to the Amazon Bedrock AgentCore Runtime and passes the task token for the async callback.
Called by the internal Step Functions state machine with the WAIT_FOR_TASK_TOKEN integration pattern.

The runtime acknowledges the run immediately (the agent works in a background task and reports the
token itself), so this Lambda only checks that the run was accepted. A session whose container is still
running an earlier job answers ``busy``: the token is failed with ``CadStepAgentBusy``, which the run
state retries, and the retry hashes onto the same session and so waits for that slot. Any other rejected
or malformed acknowledgement fails the token with ``CadStepAgentInvokeError`` and raises, since nothing
downstream would report it.
"""

import hashlib
import json
import os
import uuid

import boto3
from botocore.config import Config

from customLogging.logger import safeLogger

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="InvokeAgentRuntime-CadStepAgent")
agentcore = boto3.client("bedrock-agentcore", config=retry_config)
sfn = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "us-east-1"), config=retry_config)

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
AGENT_RUNTIME_QUALIFIER = os.environ.get("AGENT_RUNTIME_QUALIFIER", "DEFAULT")
# Number of fixed session ids reused across runs so a runtime session stays warm; 0 = fresh session.
WARM_SESSION_SLOTS = int(os.environ.get("WARM_SESSION_SLOTS", "0"))
# Namespaces the fixed session ids to this deployment (two stacks in one account must not share them).
SESSION_NAMESPACE = os.environ.get("SESSION_NAMESPACE", "vams-cad-step-agent")
# AgentCore requires a session id of at least 33 characters.
MIN_SESSION_ID_LENGTH = 33
# Error names reported on the inner token. The state machine retries the busy one only.
INVOKE_ERROR = "CadStepAgentInvokeError"
BUSY_ERROR = "CadStepAgentBusy"
# How the agent container's reply marks a session that is still running an earlier job.
BUSY_REPLY_PREFIX = "busy"


def session_id_for(job_name, slots=None, namespace=None):
    """The runtime session id a run uses.

    With warm slots configured the id is one of ``slots`` fixed values chosen by hashing the job
    name, so consecutive runs reuse a small pool of sessions that the runtime keeps warm for its idle
    timeout. With no slots every run gets a fresh id. Both shapes are at least 33 characters.
    """
    slots = WARM_SESSION_SLOTS if slots is None else slots
    namespace = SESSION_NAMESPACE if namespace is None else namespace
    if slots and slots > 0:
        slot = int(hashlib.sha256(str(job_name).encode("utf-8")).hexdigest(), 16) % slots
        base = f"{namespace}-warm-slot-{slot:02d}"
    else:
        base = f"{namespace}-run-{uuid.uuid4().hex}"
    if len(base) < MIN_SESSION_ID_LENGTH:
        base = base + "-" + hashlib.sha256(base.encode("utf-8")).hexdigest()
    return base[:100]


def _reply(response_body):
    """The agent app's own JSON reply as a dict, or None when the payload is not one."""
    try:
        body = json.loads(response_body) if isinstance(response_body, (str, bytes)) else response_body
    except (json.JSONDecodeError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def _acknowledged(response_body):
    """True when the runtime accepted the run."""
    reply = _reply(response_body)
    return reply is not None and reply.get("accepted") is True


def _busy(response_body):
    """True when the runtime refused the run because its session container is still running one."""
    reply = _reply(response_body)
    return reply is not None and reply.get("accepted") is False \
        and str(reply.get("error", "")).startswith(BUSY_REPLY_PREFIX)


def abort_external_workflow(error, task_token, error_name=INVOKE_ERROR):
    """Fail the inner task token so the sub-state-machine's catch runs pipelineEnd, which reports the
    OUTER token. Never raises: the original error is the one worth reading."""
    if not task_token:
        return
    try:
        sfn.send_task_failure(
            taskToken=task_token,
            error=error_name,
            cause=str(error)[:256]
        )
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    logger.info("Event", event=event)

    job_name = event["jobName"]
    definition = event["definition"]
    task_token = event.get("taskToken", "")

    definition_obj = json.loads(definition[0]) if isinstance(definition, list) else definition
    if isinstance(definition_obj, str):
        definition_obj = json.loads(definition_obj)

    payload = {
        "jobName": job_name,
        "definition": definition_obj,
        "taskToken": task_token,
    }
    session_id = session_id_for(job_name)

    try:
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            qualifier=AGENT_RUNTIME_QUALIFIER,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode("utf-8"),
        )
        raw = response.get("response")
        body = raw.read() if hasattr(raw, "read") else raw
        if _busy(body):
            # Failing the token with the busy error is the whole outcome: the state retries on that
            # error name, and a raise here would race it with a Lambda function error.
            logger.info(f"Agent session busy: job={job_name} session={session_id}")
            abort_external_workflow("The agent session is running an earlier job", task_token, BUSY_ERROR)
            return {
                "jobName": job_name,
                "runtimeSessionId": session_id,
                "status": "BUSY",
            }
        if not _acknowledged(body):
            raise RuntimeError("Agent runtime did not accept the run")
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, task_token)
        raise

    logger.info(f"Agent run accepted: job={job_name} session={session_id}")
    return {
        "jobName": job_name,
        "runtimeSessionId": session_id,
        "status": "ACCEPTED",
    }
