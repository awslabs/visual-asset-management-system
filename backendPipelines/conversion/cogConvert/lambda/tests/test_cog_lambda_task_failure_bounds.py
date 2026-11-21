#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Live smoke D1, lambda side: every callback that fails the VAMS workflow's task token stays inside the Step
Functions bounds (`error` 256 characters, `cause` 32,768) and carries the container's reason.

`pipelineEnd` is the one that decides what the VAMS execution shows: it turns the sub-state-machine's Catch
(`Error` = the container's failure name, `Cause` = its text) into the external task failure. It used to send a fixed
`cause` and an `error` built from the container's whole message, so a long refusal was rejected and the execution
stayed RUNNING."""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _pkg = types.ModuleType("customLogging")
    _logger = types.ModuleType("customLogging.logger")
    _logger.safeLogger = lambda **kw: MagicMock()
    _pkg.logger = _logger
    sys.modules["customLogging"] = _pkg
    sys.modules["customLogging.logger"] = _logger

for key, value in {
    "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CogConvert",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".tif,.tiff", "OPEN_PIPELINE_FUNCTION_NAME": "open",
}.items():
    os.environ.setdefault(key, value)

ANTIMERIDIAN = (
    "This raster crosses the antimeridian (longitude 180), which COG conversion does not support: in Web Mercator "
    "it would become a world-wide grid of almost empty pixels. Split the file at longitude 180 into a western and "
    "an eastern part (for example with gdal_translate -projwin on each side), upload the parts, and convert each one."
)


def _load(name):
    return importlib.reload(sys.modules[name]) if name in sys.modules else importlib.import_module(name)


def _service(error="", cause="", **_):
    """The real service's bounds."""
    if len(error) > 256 or len(cause) > 32768:
        raise ClientError({"Error": {"Code": "ValidationException", "Message": "length"}}, "SendTaskFailure")


def _failure_sent(module, run, client="sfn"):
    sent = []

    def fake(**kwargs):
        _service(**kwargs)
        sent.append(kwargs)

    with patch.object(getattr(module, client), "send_task_failure", fake):
        run()
    return sent


@pytest.mark.unit
class TestPipelineEndReportsTheContainersReason:
    def _event(self, error="CogConvertRefused", cause=ANTIMERIDIAN):
        return {"externalSfnTaskToken": "ext", "error": {"Error": error, "Cause": cause}}

    def test_the_cause_is_the_container_message_not_a_fixed_pointer_to_the_logs(self):
        module = _load("pipelineEnd")
        sent = _failure_sent(module, lambda: module.lambda_handler(self._event(), MagicMock()))
        assert len(sent) == 1
        assert sent[0]["cause"] == ANTIMERIDIAN
        assert sent[0]["error"] == "Pipeline Failure: CogConvertRefused"

    @pytest.mark.parametrize("error,cause", [("E" * 1000, "c" * 100000), ("", ""), ("States.Timeout", None)])
    def test_hostile_or_empty_values_stay_inside_the_bounds(self, error, cause):
        module = _load("pipelineEnd")
        sent = _failure_sent(module, lambda: module.lambda_handler(
            {"externalSfnTaskToken": "ext", "error": {"Error": error, "Cause": cause}}, MagicMock()))
        assert len(sent) == 1
        assert 0 < len(sent[0]["error"]) <= 256 and 0 < len(sent[0]["cause"]) <= 32768

    def test_a_success_still_reports_success(self):
        module = _load("pipelineEnd")
        success = MagicMock()
        with patch.object(module.sfn, "send_task_success", success):
            module.lambda_handler({"externalSfnTaskToken": "ext"}, MagicMock())
        success.assert_called_once()


@pytest.mark.unit
class TestEveryOtherCallbackIsBounded:
    def test_open_pipeline(self):
        module = _load("openPipeline")
        sent = _failure_sent(module, lambda: module.abort_external_workflow("e" * 5000, "tok"))
        assert len(sent) == 1 and len(sent[0]["error"]) <= 256 and "e" * 100 in sent[0]["cause"]

    def test_construct_pipeline(self):
        module = _load("constructPipeline")
        sent = _failure_sent(module, lambda: module.abort_external_workflow(ValueError("v" * 5000), "tok"))
        assert len(sent) == 1 and len(sent[0]["error"]) <= 256 and sent[0]["cause"].startswith("vvvv")

    def test_vams_execute(self):
        module = _load("vamsExecuteCogConvertPipeline")
        sent = _failure_sent(module, lambda: module.abort_external_workflow(ValueError("v" * 5000), "tok"),
                             client="sfn_client")
        assert len(sent) == 1 and len(sent[0]["error"]) <= 256 and sent[0]["cause"].startswith("vvvv")
