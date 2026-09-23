# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Amazon Bedrock AgentCore Runtime entrypoint.

The runtime posts the invocation payload (``{"jobName", "definition", "taskToken"}``) to
``/invocations``. The handler validates it, starts the job as a background task and returns an
acknowledgement at once; the job reports the task token itself when it finishes, and the runtime keeps
the session alive while the task runs (its ping reports busy). One run per session container: a
payload that arrives while a run is active is answered ``accepted: false``. A SIGTERM (the session
ending) stops the active run through ``cancellation`` and then lets the server shut down.
"""

import asyncio
import contextlib
import logging
import os
import threading

from . import sandbox

# Before any thread exists (the runtime SDK starts its server threads on import below): the agent's
# /proc entry must be closed to the scripts it runs.
_NON_DUMPABLE = sandbox.harden_agent_process()

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402

from . import cancellation, run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cad_step_agent.agentcore")


@contextlib.asynccontextmanager
async def _lifespan(app):
    # Installed once the server is up, so the stop request runs first and the server's own
    # graceful-exit handler (installed before the loop started) runs after it.
    cancellation.install_signal_handlers()
    yield


app = BedrockAgentCoreApp(lifespan=_lifespan)

# One run per session container at a time. A warm session slot is one microVM, and two runs sharing
# it would contend for CadQuery memory and share process-wide state; a second run arriving while one
# is active is refused, the invoke Lambda fails its task token and the workflow retries.
_active_lock = threading.Lock()
_active = {"job": None}


def _claim(job_name):
    with _active_lock:
        if _active["job"] is not None:
            return False
        _active["job"] = job_name or "run"
        return True


def _release():
    with _active_lock:
        _active["job"] = None


def active_job():
    """The job name of the run this container is executing, or None."""
    with _active_lock:
        return _active["job"]


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
    except run.RunCancelled:
        # run_job has logged the cancellation; the token belongs to the workflow that stopped the run.
        pass
    except Exception:
        # run_job has already reported the token and logged the cause.
        logger.error("background run failed job=%s", job_name)
    else:
        logger.info("background run done job=%s", job_name)
    finally:
        _release()


@app.entrypoint
async def invoke(payload, context=None):
    logger.info("container.runtime_uid uid=%s euid=%s non_dumpable=%s", os.getuid(), os.geteuid(), _NON_DUMPABLE)
    try:
        definition, task_token, job_name = validate_payload(payload)
    except ValueError as exc:
        logger.error("rejected invocation: %s", exc)
        return {"accepted": False, "error": str(exc)}
    if not _claim(job_name):
        logger.warning("rejected invocation job=%s: a run is already active in this session", job_name)
        return {"accepted": False, "error": "busy: a run is already active in this session"}
    asyncio.create_task(run_in_background(definition, task_token, job_name))
    return {"accepted": True, "jobName": job_name}


if __name__ == "__main__":
    app.run()
