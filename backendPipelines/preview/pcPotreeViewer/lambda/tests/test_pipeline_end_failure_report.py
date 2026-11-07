#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""``pipelineEnd`` reports a failed run to the calling VAMS workflow with SendTaskFailure. The container
has already prefixed its error with "Pipeline Failure: ", so pipelineEnd adds the prefix only to an
error that lacks it (#1217).

SendTaskFailure rejects an ``error`` over 256 characters, and ``pipelineEnd`` swallows that exception,
so an over-long error left the workflow's task waiting out its full timeout. #1203 adds PDAL's text to
the container's message, so the report is bounded here, and the container's own cause (the log pointer,
or its full message when the error was cut) is passed on in place of the generic one. A cause from any other caught error is not passed
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
    def test_container_failure_keeps_one_prefix(self):
        """#1217: the container's error already starts "Pipeline Failure: "; the workflow is told it
        once. On development the error reads "Pipeline Failure: Pipeline Failure: ..."."""
        sent = _run({
            "Error": "Pipeline Failure: E57 file could not be read by the converter: PDAL: E57 exception",
            "Cause": "See AWS cloudwatch logs for full error log and cause.",
        })
        assert sent["taskToken"] == "tok-1"
        assert sent["error"] == ("Pipeline Failure: E57 file could not be read by the converter: "
                                 "PDAL: E57 exception")
        shown = f"{sent['error']}: {sent['cause']}"
        assert shown.count("Pipeline Failure:") == 1
        assert shown.count("E57 file could not be read by the converter") == 1

    def test_container_cause_is_passed_on(self):
        """A container cause (here, the full message of an error cut to fit) reaches the workflow."""
        sent = _run({"Error": "Pipeline Failure: " + "x" * 235 + "...", "Cause": "x" * 300 + " See logs"})
        assert sent["cause"] == "x" * 300 + " See logs"

    def test_error_at_the_container_limit_still_fits(self):
        """The container's error can be a full 256 characters and must still fit."""
        sent = _run({"Error": "Pipeline Failure: " + "x" * 238, "Cause": "c"})
        assert len(sent["error"]) <= 256
        assert sent["error"].count("Pipeline Failure:") == 1

    def test_other_errors_keep_the_generic_cause(self):
        sent = _run({"Error": "States.TaskFailed", "Cause": "{\"errorMessage\": \"Traceback ...\"}"})
        assert sent["error"] == "Pipeline Failure: States.TaskFailed"
        assert sent["cause"] == "See AWS cloudwatch logs for error cause."

    def test_missing_fields_do_not_raise(self):
        sent = _run({})
        assert sent["error"] == "Pipeline Failure: "
        assert sent["cause"] == "See AWS cloudwatch logs for error cause."
