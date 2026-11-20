#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The time a run has left, so the container ends before anything outside it stops it.

Two limits end a run from outside: the inner state machine's timeout, counted from the execution's
start and so including the job queue wait, and the AWS Batch attempt duration, counted from the
job's start. The state machine passes both to the container in its environment. Kept free of
boto3/pxr imports so it can be tested in isolation.
"""
import datetime
from typing import Mapping, Optional

EXECUTION_START_TIME_ENV = "MIRIS_UPLOAD_EXECUTION_START_TIME"
EXECUTION_TIMEOUT_SECONDS_ENV = "MIRIS_UPLOAD_EXECUTION_TIMEOUT_SECONDS"
ATTEMPT_DURATION_SECONDS_ENV = "MIRIS_UPLOAD_ATTEMPT_DURATION_SECONDS"

# Kept back from the deadline for the container's exit, the Batch status change and the end Lambda.
END_RESERVE_SECONDS = 300

# The upload estimate: a fixed allowance for creating the Miris asset and marking the upload
# complete, plus the artifact at a conservative transfer rate.
UPLOAD_BASE_SECONDS = 120
UPLOAD_BYTES_PER_SECOND = 5 * 1024 * 1024


def _positive_seconds(text: Optional[str]) -> Optional[float]:
    try:
        seconds = float(text)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _epoch_seconds(timestamp: Optional[str]) -> Optional[float]:
    """An ISO 8601 UTC timestamp such as Step Functions' `$$.Execution.StartTime`, as epoch seconds."""
    if not timestamp:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def run_deadline(environ: Mapping[str, str], container_started_at: float) -> Optional[float]:
    """The earlier of the execution's and the attempt's end, as epoch seconds; None when neither
    limit is in `environ` in a readable form."""
    bounds = []
    started = _epoch_seconds(environ.get(EXECUTION_START_TIME_ENV))
    execution_timeout = _positive_seconds(environ.get(EXECUTION_TIMEOUT_SECONDS_ENV))
    if started is not None and execution_timeout is not None:
        bounds.append(started + execution_timeout)
    attempt = _positive_seconds(environ.get(ATTEMPT_DURATION_SECONDS_ENV))
    if attempt is not None:
        bounds.append(container_started_at + attempt)
    return min(bounds) if bounds else None


def seconds_left(deadline: Optional[float], now: float) -> Optional[float]:
    """Seconds until the deadline, less the end reserve; None without a deadline."""
    if deadline is None:
        return None
    return deadline - now - END_RESERVE_SECONDS


def upload_seconds_needed(size_bytes: int) -> float:
    """The time to allow for uploading an artifact of `size_bytes` to Miris."""
    return UPLOAD_BASE_SECONDS + size_bytes / UPLOAD_BYTES_PER_SECOND
