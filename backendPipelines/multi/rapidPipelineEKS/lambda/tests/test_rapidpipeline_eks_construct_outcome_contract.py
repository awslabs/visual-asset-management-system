#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""CONSTRUCT_PIPELINE's result carries `jobManifest` exactly when it succeeded.

The RapidPipeline EKS state machine decides what follows ConstructPipeline from the result alone:
its `ConstructPipelineSucceeded?` Choice sends a result whose `Payload.jobManifest` is absent to
PipelineEnd as a failure, and any other result to RunJob, which submits that manifest. The Lambda
reports a rejected input by returning a body rather than raising, so the invocation succeeds and
no Catch is involved. These tests pin the Lambda side of that contract: the result of a successful
construction has a `jobManifest`, and no error result does, whether `handle_construct_pipeline` or
`lambda_handler` produced it.

The results are taken from `lambda_handler`, because its return value is the `Payload` the Choice
reads.
"""

import ast
import os
import sys
import types
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

for _key, _value in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "EKS_CLUSTER_NAME": "test-cluster",
    "CONTAINER_IMAGE_URI": "example.com/rapid-pipeline:test",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(_key, _value)

import consolidated_handler as ch  # noqa: E402

IMAGE_URI = "example.com/rapid-pipeline:test"


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.function_name = "test-fn"
    ctx.function_version = "1"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


def _construct_event(**overrides):
    """A CONSTRUCT_PIPELINE event shaped like the ConstructPipeline task payload."""
    event = {
        "operation": "CONSTRUCT_PIPELINE",
        "jobName": "job-test",
        "inputS3AssetFilePath": "s3://abkt/org/area/xidM/model.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/rapidPipelineEKS/E1/",
        "isTest": True,
        "inputMetadataS3Location": "",
        "inputConfigurationS3Location": "",
        "externalSfnTaskToken": "token-test",
        "outputFileType": ".gltf",
        "assetId": "xidM",
    }
    event.update(overrides)
    return event


def _invoke(event, image_uri=IMAGE_URI):
    """lambda_handler's return value, with S3 and the container image pinned for this test."""
    with patch.object(ch, "s3", MagicMock()), patch.object(ch, "CONTAINER_IMAGE_URI", image_uri):
        return ch.lambda_handler(event, _ctx())


@pytest.mark.unit
class TestASuccessfulConstructionCarriesTheManifest:
    def test_the_result_has_job_manifest_and_no_error(self):
        out = _invoke(_construct_event())
        assert "error" not in out, out
        assert "jobManifest" in out, out
        assert out["jobManifest"]["kind"] == "Job"
        assert out["jobManifest"]["metadata"]["name"].startswith("rapid-pipeline-job-")


@pytest.mark.unit
class TestNoErrorResultCarriesAManifest:
    """Each case asserts the error it expects as well as the absence, so a case that took the success
    path instead fails rather than passing on a result that happens to lack the field."""

    @pytest.mark.parametrize(
        "overrides, expected_error",
        [
            pytest.param({"inputS3AssetFilePath": None}, "ConstructPipelineParameterError",
                         id="missing-required-parameter"),
            pytest.param({"outputS3AssetFilesPath": "https://abkt/files/"},
                         "ConstructPipelineParameterError", id="not-an-s3-uri"),
            pytest.param({"outputS3AssetFilesPath": "s3://abkt/"}, "ConstructPipelineError",
                         id="s3-uri-without-key"),
        ],
    )
    def test_handle_construct_pipeline_errors(self, overrides, expected_error):
        out = _invoke(_construct_event(**overrides))
        assert out.get("error", {}).get("Error") == expected_error, out
        assert "jobManifest" not in out, out

    def test_an_unconfigured_container_image(self):
        out = _invoke(_construct_event(), image_uri="CONTAINER_IMAGE_PLACEHOLDER")
        assert out.get("error", {}).get("Error") == "ConstructPipelineError", out
        assert "jobManifest" not in out, out

    @pytest.mark.parametrize(
        "event",
        [
            pytest.param(None, id="null-event"),
            pytest.param(["CONSTRUCT_PIPELINE"], id="non-dict-event"),
            pytest.param({"jobName": "job-test"}, id="missing-operation"),
            pytest.param({"operation": 5}, id="non-string-operation"),
            pytest.param(_construct_event(operation="CONSTRUCT"), id="unknown-operation"),
        ],
    )
    def test_lambda_handler_validation_errors(self, event):
        out = _invoke(event)
        assert out.get("statusCode", 0) >= 400, out
        assert "jobManifest" not in out, out


@pytest.mark.unit
class TestEveryReturnOfHandleConstructPipelineIsAccountedFor:
    """The cases above drive each return site that exists. This pins the set of sites: one return
    names `jobManifest`, and every other returns only an `error` object, so a new return shape is
    visible here rather than silently reaching RunJob or PipelineEnd."""

    def _return_keys(self):
        with open(os.path.join(_LAMBDA_DIR, "consolidated_handler.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        func = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "handle_construct_pipeline")
        keys = []
        for node in ast.walk(func):
            if isinstance(node, ast.Return):
                assert isinstance(node.value, ast.Dict), ast.dump(node)
                keys.append([k.value for k in node.value.keys])
        return keys

    def test_one_return_has_the_manifest_and_the_rest_only_an_error(self):
        keys = self._return_keys()
        assert len(keys) >= 2, keys
        assert sum("jobManifest" in k for k in keys) == 1, keys
        assert all(k == ["error"] for k in keys if "jobManifest" not in k), keys
