#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""``pipelineEnd`` reports a failed run to the calling VAMS workflow with SendTaskFailure, adding its
own "Pipeline Failure: " prefix to the error the container already prefixed.

SendTaskFailure rejects an ``error`` over 256 characters, and ``pipelineEnd`` swallows that exception,
so an over-long error left the workflow's task waiting out its full timeout. #1203 adds PDAL's text to
the container's message, so the report is bounded here, and the container's own cause (its full
message) is passed on in place of the generic one. A cause from any other caught error is not passed
on: a Lambda or Batch fault's cause can carry stack traces and resource names."""

import os
import sys
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


def _load():
    if "pipelineEnd" in sys.modules:
        return importlib.reload(sys.modules["pipelineEnd"])
    return importlib.import_module("pipelineEnd")


def _run(error):
    mod = _load()
    client = MagicMock()
    with patch.object(mod, "sfn", client):
        mod.lambda_handler({"externalSfnTaskToken": "tok-1", "error": error}, MagicMock())
    return client.send_task_failure.call_args.kwargs


@pytest.mark.unit
class TestPipelineEndFailureReport:
    def test_container_cause_is_passed_on(self):
        sent = _run({
            "Error": "Pipeline Failure: E57 file could not be read by the converter: PDAL: E57 exception",
            "Cause": "E57 file could not be read by the converter: PDAL: E57 exception See AWS cloudwatch logs",
        })
        assert sent["taskToken"] == "tok-1"
        assert sent["error"] == ("Pipeline Failure: Pipeline Failure: E57 file could not be read by the "
                                 "converter: PDAL: E57 exception")
        assert sent["cause"].startswith("E57 file could not be read by the converter: PDAL: E57 exception")

    def test_error_at_the_container_limit_still_fits(self):
        """The container's error can be a full 256 characters; with the second prefix it must still
        fit. Unfixed, 274 characters are sent and the report is rejected."""
        sent = _run({"Error": "Pipeline Failure: " + "x" * 238, "Cause": "c"})
        assert len(sent["error"]) <= 256

    def test_other_errors_keep_the_generic_cause(self):
        sent = _run({"Error": "States.TaskFailed", "Cause": "{\"errorMessage\": \"Traceback ...\"}"})
        assert sent["error"] == "Pipeline Failure: States.TaskFailed"
        assert sent["cause"] == "See AWS cloudwatch logs for error cause."

    def test_missing_fields_do_not_raise(self):
        sent = _run({})
        assert sent["error"] == "Pipeline Failure: "
        assert sent["cause"] == "See AWS cloudwatch logs for error cause."
