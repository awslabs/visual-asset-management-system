# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the two compute tiers of the 3D Basic Conversion pipeline share.

The pipeline runs in one container image with two entry points: ``lambda.py`` (the Lambda function) and
``batch_entry.py`` (an AWS Batch task, used when a model is too large for the function). Both call
``convert()`` in ``lambda.py``, which is the whole conversion: the same size check, the same worker
child, the same output placement, the same error text. ``lambda`` is a keyword, so no module can
import that file by name; ``load_implementation()`` loads it by path, which is how ``batch_entry.py``
reaches ``convert()``.

This module holds the parts that differ by tier or that neither entry owns:

- where the run's memory and deadline come from (``size_check.memory_limit_bytes`` reads the Lambda
  function's or the Batch task's memory; ``attempt_deadline_epoch`` reads the Batch attempt's time),
- the named results a run that cannot finish on the Lambda tier returns instead of an error, when the
  state machine asks for them (``allowEscalation``),
- the refusals both tiers make before any download (a PLY the loader cannot read),
- the run document ``conversion-run.json`` that says which tier ran, and
- the names of the environment variables the worker child never receives.

It imports neither boto3 nor trimesh.
"""

import importlib.util
import os
import time

# --- named escalation results ------------------------------------------------------------------

REASON_SIZE = "size"
REASON_MEMORY = "memory"
REASON_TIME = "time"
ESCALATION_REASONS = (REASON_SIZE, REASON_MEMORY, REASON_TIME)

# Plain-language phrases of each reason, for the run document and the log.
REASON_TEXT = {
    REASON_SIZE: "the input is over what the Lambda function converts",
    REASON_MEMORY: "the conversion ran out of memory on the Lambda function",
    REASON_TIME: "the conversion did not finish within the Lambda function's time limit",
}


class WorkerKilledForMemory(RuntimeError):
    """The worker child was ended by SIGKILL, which is how a task that runs out of memory loses its
    child (the container's memory limit is reached before the child's address-space limit). The text is
    the one the run has always reported for a killed converter."""


class WorkerOutOfTime(RuntimeError):
    """The worker child did not finish within the time the attempt had left and was stopped."""


class EscalationRequired(Exception):
    """A run the Lambda tier cannot finish, raised inside ``convert()`` when the caller asked for the
    named result (``allow_escalation``). ``result`` is the dict the handler returns."""

    def __init__(self, result):
        super().__init__(result.get("message", ""))
        self.result = result


def escalation_result(reason, message, input_name="", input_bytes=None, pair="", required_memory_mib=None,
                      limit_bytes_by_task_mib=None, min_task_memory_mib=0, resource=None):
    """The result a run returns instead of an error when it should continue on AWS Batch.

    ``requiredMemoryMiB`` is the least task memory the size check admits the input on (None when the
    run died without a size to compare); ``limitBytesByTaskMemoryMiB`` is the size check's limit for each
    job size, so the router picks the smallest size that admits ``inputBytes`` and names the largest
    size's limit when none does; ``minTaskMemoryMiB`` is the memory a retry must exceed (a run that ran
    out of memory on N MiB needs more than N)."""
    if reason not in ESCALATION_REASONS:
        raise ValueError(f"unknown escalation reason {reason!r}")
    return {
        "escalate": True,
        "reason": reason,
        "message": message,
        "inputName": input_name,
        "inputBytes": input_bytes,
        "formatPair": pair,
        "resource": resource,
        "requiredMemoryMiB": required_memory_mib,
        "limitBytesByTaskMemoryMiB": {str(k): v for k, v in (limit_bytes_by_task_mib or {}).items()},
        "minTaskMemoryMiB": int(min_task_memory_mib or 0),
    }


def allow_escalation(data):
    """Whether the request asks for named results: the state machine sets ``allowEscalation`` true on
    the Lambda attempt of a deployment with the Batch tier. Anything but the JSON value true is
    false, so a direct invocation or a deployment without the tier raises as it always has."""
    return isinstance(data, dict) and data.get("allowEscalation") is True


# --- time ------------------------------------------------------------------------------------------

DEADLINE_VARIABLE = "VAMS_ATTEMPT_DEADLINE"
ATTEMPT_SECONDS_VARIABLE = "VAMS_ATTEMPT_SECONDS"
# The longest an AWS Batch attempt runs when the launcher names no bound: the 3D thumbnail's large
# tier uses the same four hours, and the slowest admitted input measured converts in under 40 minutes.
DEFAULT_ATTEMPT_SECONDS = 4 * 3600


def attempt_deadline_epoch(environ=None, now=None):
    """The wall-clock time (epoch seconds) an AWS Batch attempt ends: ``VAMS_ATTEMPT_DEADLINE`` when it
    is a positive number, else ``now`` plus ``VAMS_ATTEMPT_SECONDS`` (the default four hours when that
    is not a positive number either). Both are read at the task's start, so a job that waited in the
    queue has its whole attempt."""
    environ = os.environ if environ is None else environ
    now = time.time() if now is None else now
    try:
        absolute = float(environ.get(DEADLINE_VARIABLE, ""))
    except (TypeError, ValueError):
        absolute = 0
    if absolute > 0:
        return absolute
    try:
        seconds = float(environ.get(ATTEMPT_SECONDS_VARIABLE, ""))
    except (TypeError, ValueError):
        seconds = 0
    return now + (seconds if seconds > 0 else DEFAULT_ATTEMPT_SECONDS)


# --- refusals both tiers make before a download ------------------------------------------------------

# The PLY loader keeps a 32-bit size, so a file of 4 GiB or more fails at read with an overflow error
# after the whole file is downloaded and parsed (measured at 6,144 MiB on the 120 GiB task).
PLY_MAX_BYTES = 2 ** 32 - 1


class FormatLimitError(ValueError):
    """The input is past a limit of its format that no amount of memory lifts, so no tier can convert
    it. Never escalated."""


def check_format_limit(name, extension, size_bytes):
    """Raise ``FormatLimitError`` for an input of a format the loader cannot read at this size."""
    if (extension or "").lower() == ".ply" and size_bytes > PLY_MAX_BYTES:
        raise FormatLimitError(
            f"{name} is {size_bytes / 1024 ** 3:,.1f} GiB, over the 4 GiB a PLY file can be read at "
            f"(the loader keeps its sizes in 32 bits). Save the model in another format, such as .glb "
            f".usdc or .stl, or split it, and run the conversion again.")


# --- the run document ---------------------------------------------------------------------------------

RUN_DOCUMENT_FILENAME = "conversion-run.json"
RUN_DOCUMENT_SCHEMA = "vams.conversion3dBasic.run/v1"


class PhaseTimer:
    """Seconds spent in each named phase of a run (head, download, references, convert, upload)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.reset()

    def reset(self):
        self.seconds = {}
        self._name = None
        self._start = 0.0

    def phase(self, name):
        self.stop()
        self._name, self._start = name, self._clock()

    def stop(self):
        if self._name is not None:
            self.seconds[self._name] = round(self.seconds.get(self._name, 0.0) + self._clock() - self._start, 3)
            self._name = None


def run_document(tier, task_memory_mib, task_vcpu, phase_seconds, reason=None, escalated_from=None,
                 required_memory_mib=None, message=None, failed=False, peak_memory_mib=None):
    """The ``conversion-run.json`` body of a run: which tier ran it, with how much memory, how long each
    phase took and, for a run that continued from another tier, why. Small text; no model content, no
    S3 key, no identity."""
    document = {
        "schema": RUN_DOCUMENT_SCHEMA,
        "tier": tier,
        "taskMemoryMiB": task_memory_mib,
        "taskVcpu": task_vcpu,
        "phaseSeconds": dict(phase_seconds or {}),
    }
    if escalated_from:
        document["escalatedFrom"] = escalated_from
    if reason:
        document["reason"] = reason
        document["reasonText"] = REASON_TEXT.get(reason, "")
    if required_memory_mib is not None:
        document["requiredMemoryMiB"] = required_memory_mib
    if isinstance(peak_memory_mib, (int, float)) and not isinstance(peak_memory_mib, bool):
        # Measured: the most resident memory a conversion worker held (the size check's
        # requiredMemoryMiB is an estimate from the input's size).
        document["peakWorkerMemoryMiB"] = peak_memory_mib
    if failed:
        document["failed"] = True
    if message:
        document["message"] = str(message)[:2000]
    return document


# --- the worker child's environment ------------------------------------------------------------------

# Variables the worker child never receives, whatever the passthrough list says. The credentials of an
# AWS Batch (Fargate) task come from a link-local endpoint that a random relative path in
# AWS_CONTAINER_CREDENTIALS_RELATIVE_URI addresses, and the task metadata endpoint is named in
# ECS_CONTAINER_METADATA_URI*; a child that held either could read the job role's credentials.
SCRUBBED_ENV_PREFIXES = ("AWS_", "ECS_", "VAMS_", "AMAZON_", "_AWS_", "_HANDLER", "LAMBDA_")
SCRUBBED_ENV_NAMES = (
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE", "ECS_CONTAINER_METADATA_URI", "ECS_CONTAINER_METADATA_URI_V4",
    "ECS_AGENT_URI", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
)


def is_scrubbed(name):
    """Whether the worker child must not receive the environment variable ``name``."""
    upper = str(name).upper()
    return upper in SCRUBBED_ENV_NAMES or upper.startswith(SCRUBBED_ENV_PREFIXES)


# --- the shared entry ---------------------------------------------------------------------------------

_IMPLEMENTATION = None


def load_implementation(path=None):
    """The module of ``lambda.py``, loaded once by path (the file name is a keyword, so it cannot be
    imported). ``batch_entry.py`` calls ``convert()`` on it; the Lambda runtime imports the same file as
    ``lambda`` for its handler."""
    global _IMPLEMENTATION
    if path is None and _IMPLEMENTATION is not None:
        return _IMPLEMENTATION
    explicit = path is not None
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "lambda.py")
    spec = importlib.util.spec_from_file_location("vams_conversion_implementation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not explicit:
        _IMPLEMENTATION = module
    return module
