# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entry point of the AWS Batch (Fargate) tier of the 3D Basic Conversion pipeline.

The `batch` target of the Dockerfile runs this file as its ENTRYPOINT. The run is the same conversion
the Lambda function performs: ``convert()`` in ``lambda.py`` (reached through
``conversion_service.load_implementation()``, because ``lambda`` is a keyword and cannot be imported),
so the size check, the worker child with its scrubbed environment and limits, the referenced-file
confinement, the output placement and the error text are shared, not copied.

The launcher passes the run through the job's environment (never a task token, never the workflow's
credentials):

- ``VAMS_CONVERSION_EVENT``: the request body as JSON (locations of the run manifest and the input
  configuration, the workflow execution id, and when the run continues from the Lambda tier its
  ``escalationReason`` and ``requiredMemoryMiB``),
- ``VAMS_TASK_MEMORY_MIB`` and ``VAMS_TASK_VCPU``: the job size the memory limits come from,
- ``VAMS_ATTEMPT_DEADLINE`` (epoch seconds) or ``VAMS_ATTEMPT_SECONDS``: when the attempt ends.

Exit status 0 is a finished conversion; its last stdout line is ``VAMS_CONVERSION_RESULT: <message>``
(``Success`` or the incomplete-conversion message). Any failure prints one line naming the error, so the job's log
carries the same text a Lambda run's error would, and exits 1; the state machine reports the failure to
the workflow.
"""

import ctypes
import json
import os
import sys

import conversion_service
import size_check

# prctl(2) options. The Batch job definition sets no ECS init process, so this file's process is the
# task's PID 1: it makes itself non-dumpable before the conversion worker starts, so a worker child of
# the same user cannot read this process's environment -- the task's credential endpoint
# (AWS_CONTAINER_CREDENTIALS_RELATIVE_URI) -- through /proc/1/environ, and it reaps the descendants
# that are reparented to it. The conversion worker already starts with that endpoint scrubbed from its
# own environment (conversion_service.is_scrubbed); this closes the remaining way to it.
PR_SET_DUMPABLE = 4

EVENT_VARIABLE = "VAMS_CONVERSION_EVENT"
ERROR_PREFIX = "VAMS_CONVERSION_ERROR: "
# The last line of a finished run on stdout: the Lambda handler's body (``Success``, or the message of a
# run that left referenced files out), after the log lines the logger also writes there.
RESULT_PREFIX = "VAMS_CONVERSION_RESULT: "


def read_event(environ=None):
    """The request body from ``VAMS_CONVERSION_EVENT``. Raises ``ValueError`` when it is missing, not
    JSON, not an object, or carries a Step Functions task token (the job role has no ``states:``
    permission, and a token has no use here)."""
    environ = os.environ if environ is None else environ
    raw = environ.get(EVENT_VARIABLE, "")
    if not raw:
        raise ValueError(f"{EVENT_VARIABLE} is not set")
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError(f"{EVENT_VARIABLE} is not valid JSON")
    if not isinstance(data, dict):
        raise ValueError(f"{EVENT_VARIABLE} is not a JSON object")
    if "TaskToken" in data:
        raise ValueError("A Step Functions task token reached the conversion job; it must not.")
    return data


def harden_process():
    """Make this process non-dumpable so a same-user process cannot read its /proc entry. Returns
    False where the call is not available (a non-Linux developer machine); nothing else depends on it.
    The conversion worker started later marks itself the same way through ``lambda.py``."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except Exception:  # noqa: BLE001 - a platform without it keeps the rest of the run
        return False


def reap_orphans():
    """Reap every descendant reparented to this process (PID 1 of the task), so no zombie is left.
    Orphans reparent to PID 1 by the kernel; ``lambda.run_worker`` already signals strays after each
    worker, and this collects their exit status. Returns the count reaped."""
    reaped = 0
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except (ChildProcessError, OSError):
            return reaped
        if pid == 0:
            return reaped
        reaped += 1


def main(environ=None, implementation=None):
    environ = os.environ if environ is None else environ
    # Before the conversion worker exists: close this process's /proc entry to a same-user reader.
    harden_process()
    if not size_check.task_memory_bytes(environ):
        print(ERROR_PREFIX + f"{size_check.TASK_MEMORY_VARIABLE} is not set; this entry runs on an AWS "
              f"Batch task only.", file=sys.stderr)
        return 2
    try:
        data = read_event(environ)
        module = implementation or conversion_service.load_implementation()
        module.remember_attempt_deadline_epoch(conversion_service.attempt_deadline_epoch(environ))
        # The Batch tier is the end of the line: a run it cannot finish fails with its error, so the
        # request never carries allowEscalation true here.
        data = {**data, "allowEscalation": False}
        result = module.convert(data)
    except Exception as e:  # noqa: BLE001 - every failure is reported on one line and in the exit status
        print(ERROR_PREFIX + f"{type(e).__name__}: {str(e)[:2000]}", file=sys.stderr)
        return 1
    finally:
        reap_orphans()
    body = result.get("body") if isinstance(result, dict) else None
    print(RESULT_PREFIX + (body if isinstance(body, str) and body else "Success"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
