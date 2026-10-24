#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The error and cause a RapidPipeline EKS lambda sends AWS Step Functions SendTaskFailure.

SendTaskFailure accepts an `error` of at most 256 characters and a `cause` of at most 32768, and the
service rejects a longer value with a ValidationException (botocore checks only the minimum length).
Every failure callback in these lambdas is wrapped in an `except` that logs and carries on, so a
rejected callback leaves the parent workflow's task token unreported and its task waiting for its full
taskTimeout. Each `send_task_failure` call therefore takes its `error` and `cause` from
`bounded_task_failure`, which `tests/test_rapidpipeline_eks_task_failure_bounds.py` enforces.

This module has no dependencies, so both the consolidated handler and openPipeline import it: each
lambda's code asset is the whole `lambda/` directory.
"""

# SendTaskFailure length limits, in characters (AWS Step Functions API reference).
SEND_TASK_FAILURE_ERROR_MAX_CHARS = 256
SEND_TASK_FAILURE_CAUSE_MAX_CHARS = 32768

TRUNCATION_MARKER = " ... [truncated from {length} characters]"


def _bounded(value, limit):
    """`value` as text of at most `limit` characters.

    The head is kept: a Lambda error body and every message these lambdas compose put the error
    message first. The marker records the original length and is counted inside the limit.
    """
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    marker = TRUNCATION_MARKER.format(length=len(text))
    return text[:limit - len(marker)] + marker


def bounded_task_failure(error, cause):
    """The `(error, cause)` pair for a SendTaskFailure call, each within its API limit.

    A value within its limit is returned unchanged. A value that is not a string (`None`, a dict) is
    converted with `str()`, since botocore refuses a non-string parameter before sending anything.
    """
    return (
        _bounded(error, SEND_TASK_FAILURE_ERROR_MAX_CHARS),
        _bounded(cause, SEND_TASK_FAILURE_CAUSE_MAX_CHARS),
    )
