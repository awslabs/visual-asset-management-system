# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Amazon Bedrock AgentCore Runtime entrypoint.

The runtime posts the invocation payload (``{"jobName", "definition", "taskToken"}``) to
``/invocations``. The handler validates it, starts the job as a background task and returns an
acknowledgement at once; the job reports the task token itself when it finishes, and the runtime keeps
the session alive while the task runs (its ping reports busy).
"""

import asyncio
import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from . import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cad_step_agent.agentcore")

app = BedrockAgentCoreApp()


def validate_payload(payload):
    """The (definition, task token, job name) of an invocation payload; raises ValueError when malformed."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    definition = payload.get("definition")
    if definition is None:
        raise ValueError("payload is missing 'definition'")
    task_token = payload.get("taskToken", "") or ""
    if not task_token:
        raise ValueError("payload is missing 'taskToken'")
    return definition, task_token, str(payload.get("jobName", "") or "")


@app.async_task
async def run_in_background(definition, task_token, job_name):
    logger.info("background run start job=%s", job_name)
    try:
        await asyncio.to_thread(run.run_job, definition, task_token)
    except Exception:
        # run_job has already reported the token and logged the cause.
        logger.error("background run failed job=%s", job_name)
    else:
        logger.info("background run done job=%s", job_name)


@app.entrypoint
async def invoke(payload, context=None):
    logger.info("container.runtime_uid uid=%s euid=%s", os.getuid(), os.geteuid())
    try:
        definition, task_token, job_name = validate_payload(payload)
    except ValueError as exc:
        logger.error("rejected invocation: %s", exc)
        return {"accepted": False, "error": str(exc)}
    asyncio.create_task(run_in_background(definition, task_token, job_name))
    return {"accepted": True, "jobName": job_name}


if __name__ == "__main__":
    app.run()
