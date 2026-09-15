#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the openPipeline invoke-result check in the vamsExecute lambda.

A RequestResponse invoke of a function that raised still returns StatusCode 200 — the failure is
reported as FunctionError. openPipeline raises outside its own try block (its abort path calls
send_task_failure unguarded), so reading only StatusCode reports a launch failure as success: the
handler returns 200, nothing reports against the workflow's callback token, and the pipeline task
stays RUNNING for its full 3600-second taskTimeout.

Guards S4-PIPELINES-064."""

import os
import sys
import json
import types
import importlib
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

TASK_TOKEN = "tok-123"


def _load_module():
    if "vamsExecutePreview3dThumbnailPipeline" in sys.modules:
        return importlib.reload(sys.modules["vamsExecutePreview3dThumbnailPipeline"])
    return importlib.import_module("vamsExecutePreview3dThumbnailPipeline")


def _body():
    """A payload that resolves without a manifest, so the run reaches the openPipeline invoke."""
    return {
        "TaskToken": TASK_TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xid/test/pump.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/JOB/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/JOB/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/JOB/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xid/test/pump.glb/preview/p1/",
        "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/p1/config.json",
        "executingUserName": "user@x",
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
                mod.execute_pipeline("s3://abkt/xid/pump.glb", "s3://abkt/files/",
                                     "s3://abkt/previews/", "s3://abkt/metadata/",
                                     "s3://aux/tmp/", "s3://abkt/metadata.json",
                                     "s3://abkt/config.json", TASK_TOKEN, "user@x", "", "xid")
        assert "Unhandled" in str(raised.value)
