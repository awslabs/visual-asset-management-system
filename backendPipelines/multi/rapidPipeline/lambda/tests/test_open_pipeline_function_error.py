#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the openPipeline invoke-result check in the vamsExecute lambda.

A RequestResponse invoke of a function that raised still returns StatusCode 200 — the failure is
reported as FunctionError. openPipeline raises outside its own try block (its abort path calls
send_task_failure unguarded), so reading only StatusCode reports a launch failure as success: the
handler returns 200, nothing reports against the workflow's callback token, and the pipeline task
stays RUNNING for its full 14400-second taskTimeout.

The second class pins the other half of that contract: openPipeline's own abort must keep
propagating a send_task_failure error, because that propagation is what sets FunctionError and so
what the check above detects. Swallowing it there returns a payload-level 400 with a clean invoke,
which this caller reads as launch success — the same hang, with the guard powerless to see it.

Guards S4-PIPELINES-064."""

import os
import sys
import json
import types
import importlib
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# The pipeline lambdas import customLogging.logger and read env at import time. Provide a
# lightweight customLogging stub + the env vars so the modules import without the
# aws_lambda_powertools dependency or a real CDK env.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

os.environ.setdefault("OPEN_PIPELINE_FUNCTION_NAME", "test-open-pipeline")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:111122223333:stateMachine:sm")
os.environ.setdefault("ALLOWED_INPUT_FILEEXTENSIONS", ".stp,.step,.glb,.obj")

TASK_TOKEN = "tok-123"


def _load_module():
    if "vamsExecuteRapidPipeline" in sys.modules:
        return importlib.reload(sys.modules["vamsExecuteRapidPipeline"])
    return importlib.import_module("vamsExecuteRapidPipeline")


def _load_open_pipeline():
    """Load THIS pipeline's openPipeline by path under a unique module name.

    Every pipeline ships a top-level ``openPipeline.py``, so a bare import in a pytest process that
    also collected another pipeline's tests would return that pipeline's module from the cache and
    assert against the wrong file."""
    path = os.path.join(_LAMBDA_DIR, "openPipeline.py")
    spec = importlib.util.spec_from_file_location("rapidPipeline_openPipeline_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert os.path.abspath(mod.__file__) == os.path.abspath(path)
    return mod


def _body():
    """A payload that resolves without a manifest, so the run reaches the openPipeline invoke."""
    return {
        "TaskToken": TASK_TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xid/cad/part.stp",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/JOB/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/JOB/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/JOB/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/p1/E1/",
        "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/p1/config.json",
        "executingUserName": "user@x",
        "outputType": "glb",
    }


def _run_handler(invoke_response):
    """Run the handler with the openPipeline invoke returning invoke_response.

    Returns (response, sfn mock, invoke mock)."""
    mod = _load_module()
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("no manifest")
    invoke = MagicMock(return_value=invoke_response)
    sfn = MagicMock()
    with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
            patch.object(mod.lambda_client, "invoke", invoke):
        response = mod.lambda_handler({"body": json.dumps(_body())}, MagicMock())
    return response, sfn, invoke


@pytest.mark.unit
class TestOpenPipelineFunctionError:
    def test_a_raised_open_pipeline_fails_the_task_token(self):
        """The positive control: the unfixed check reads StatusCode only, so this invoke result
        returns 200 with no callback and the workflow task hangs until taskTimeout."""
        response, sfn, invoke = _run_handler({"StatusCode": 200, "FunctionError": "Unhandled"})
        invoke.assert_called_once()
        assert response["statusCode"] == 500
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TASK_TOKEN

    def test_a_handled_function_error_is_also_a_failure(self):
        """`Handled` is what a lambda that returned an error payload reports; the pipeline did not
        start either way."""
        response, sfn, _ = _run_handler({"StatusCode": 200, "FunctionError": "Handled"})
        assert response["statusCode"] == 500
        assert sfn.send_task_failure.call_count == 1

    def test_a_clean_invoke_still_succeeds(self):
        """The negative control: a successful invoke carries no FunctionError, so the guard must not
        turn every launch into a failure."""
        response, sfn, invoke = _run_handler({"StatusCode": 200})
        invoke.assert_called_once()
        assert response["statusCode"] == 200
        sfn.send_task_failure.assert_not_called()

    def test_execute_pipeline_raises_with_the_reported_error_type(self):
        mod = _load_module()
        invoke = MagicMock(return_value={"StatusCode": 200, "FunctionError": "Unhandled"})
        with patch.object(mod.lambda_client, "invoke", invoke):
            with pytest.raises(Exception) as raised:
                mod.execute_pipeline("s3://abkt/xid/part.stp", "s3://abkt/files/",
                                     "s3://abkt/previews/", "s3://abkt/metadata/",
                                     "s3://aux/tmp/", "s3://abkt/metadata.json",
                                     "s3://abkt/config.json", TASK_TOKEN, "user@x", "", "glb")
        assert "Unhandled" in str(raised.value)


@pytest.mark.unit
class TestOpenPipelineAbortPropagates:
    """openPipeline's abort deliberately does NOT wrap send_task_failure.

    The vamsExecute caller only ever learns that a launch failed because the error escapes
    openPipeline and shows up as FunctionError on the invoke. These assertions fail if the call is
    ever wrapped in try/except."""

    def test_abort_external_workflow_propagates_a_send_failure(self):
        mod = _load_open_pipeline()
        sfn = MagicMock()
        sfn.send_task_failure.side_effect = Exception("AccessDeniedException")
        with patch.object(mod, "sfn", sfn):
            with pytest.raises(Exception) as raised:
                mod.abort_external_workflow("Pipeline cannot process file type provided", TASK_TOKEN)
        assert "AccessDeniedException" in str(raised.value)
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TASK_TOKEN

    def test_the_handler_propagates_so_the_invoke_reports_function_error(self):
        """The pre-invoke rejection routes through the abort. A raise there must escape the handler:
        that is what sets FunctionError, which is what the caller's check reads."""
        mod = _load_open_pipeline()
        sfn = MagicMock()
        sfn.send_task_failure.side_effect = Exception("AccessDeniedException")
        event = {
            "inputS3AssetFilePath": "s3://abkt/xid/cad/",
            "outputS3AssetFilesPath": "s3://abkt/f/",
            "outputS3AssetPreviewPath": "s3://abkt/p/",
            "outputS3AssetMetadataPath": "s3://abkt/m/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/t/",
            "outputFileType": "glb",
            "sfnExternalTaskToken": TASK_TOKEN,
        }
        with patch.object(mod, "sfn", sfn):
            with pytest.raises(Exception):
                mod.lambda_handler(event, MagicMock())
        assert sfn.send_task_failure.call_count == 1

    def test_no_token_is_a_no_op(self):
        """A direct invoke carries no token, so the abort must not call Step Functions at all."""
        mod = _load_open_pipeline()
        sfn = MagicMock()
        with patch.object(mod, "sfn", sfn):
            mod.abort_external_workflow("some error", "")
            mod.abort_external_workflow("some error", None)
        sfn.send_task_failure.assert_not_called()
