# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rejection codes and the exception that carries a readable cause to the task-token callback."""

INPUT_REJECTED = "VideoSopBomInputRejected"
LIMIT_EXCEEDED = "VideoSopBomLimitExceeded"
TRANSCRIBE_FAILED = "VideoSopBomTranscribeFailed"
MODEL_OUTPUT_INVALID = "VideoSopBomModelOutputInvalid"
CONNECTIVITY_ERROR = "VideoSopBomConnectivityError"
PIPELINE_ERROR = "VideoSopBomPipelineError"

# The `error` argument of every SendTaskFailure, in registry order; `cause` never starts with one.
ERROR_CODES = (
    INPUT_REJECTED,
    LIMIT_EXCEEDED,
    TRANSCRIBE_FAILED,
    MODEL_OUTPUT_INVALID,
    CONNECTIVITY_ERROR,
    PIPELINE_ERROR,
)


class PipelineRejection(Exception):
    """A handled failure: `code` is the SendTaskFailure `error`, `cause` the operator-readable sentence.

    The platform renders `executionError = "<code>: <cause>"`, so the cause never starts with the code.
    """

    def __init__(self, code, cause):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown rejection code {code!r}")
        super().__init__(f"{code}: {cause}")
        self.code = code
        self.cause = cause
