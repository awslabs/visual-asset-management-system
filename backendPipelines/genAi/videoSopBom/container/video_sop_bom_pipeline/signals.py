# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""SIGTERM handling for the Batch attempt timeout and TerminateJob (30 s grace, then SIGKILL)."""

import logging
import os
import signal
import time
from dataclasses import dataclass, field

from .errors import PIPELINE_ERROR
from .sfn import CAUSE_MAX_CHARS, TASK_TOKEN_ENV

logger = logging.getLogger("video_sop_bom_pipeline.signals")

EXIT_CODE_SIGTERM = 143
ORPHAN_MARKER = "TRANSCRIBE_ORPHANED job=%s stage=%s"


@dataclass
class RunState:
    """What the handler needs to name in its cause: the current stage and any in-flight Transcribe job."""

    stage_index: int = 0
    stage_name: str = "init"
    transcribe_job_name: str = None
    started_at: float = field(default_factory=time.monotonic)
    clients: object = None

    def enter(self, index, name):
        self.stage_index = index
        self.stage_name = name


def terminated_cause(state):
    elapsed = int(time.monotonic() - state.started_at)
    if state.transcribe_job_name:
        tail = f"Transcribe job {state.transcribe_job_name} left running"
    else:
        tail = "no Transcribe job had been started"
    return f"terminated at stage {state.stage_index} ({state.stage_name}) after {elapsed}s; {tail}"


def _flush_logs():
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:  # nosec B110 - a failing flush must not stop the exit
            pass


def install_sigterm_handler(state):
    """Install and return the handler. It never calls DeleteTranscriptionJob: the API rejects a
    non-terminal job, and an in-flight transcription completes on its own (cost bounded by the duration cap)."""

    def _handler(signum, frame):
        if state.transcribe_job_name:
            logger.warning(ORPHAN_MARKER, state.transcribe_job_name, state.stage_index)
        cause = terminated_cause(state)
        logger.error("SIGTERM: %s", cause)
        token = os.environ.get(TASK_TOKEN_ENV, "")
        if token and state.clients is not None:
            try:
                # Live on an attempt timeout; already invalid on an abort, where the error is swallowed.
                state.clients.sfn_signal.send_task_failure(
                    taskToken=token, error=PIPELINE_ERROR, cause=cause[:CAUSE_MAX_CHARS]
                )
            except Exception as exc:  # nosec B110 - best effort inside the 30 s grace window
                logger.warning("best-effort inner SendTaskFailure not delivered: %s", type(exc).__name__)
        _flush_logs()
        os._exit(EXIT_CODE_SIGTERM)

    signal.signal(signal.SIGTERM, _handler)
    return _handler
