#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for openPipeline's input-extension gate: membership of the comma-separated
ALLOWED_INPUT_FILEEXTENSIONS list is exact, so an extension that is merely a prefix of a listed one
is rejected before a 16 vCPU / 64 GiB Fargate job is started."""

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

for k, v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CoordTransform",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".e57,.las,.laz,.ply",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/CoordTransform",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/CoordTransform:*",
}.items():
    os.environ.setdefault(k, v)


def _load(allowed=None):
    """Import (or reload) openPipeline, optionally against a different allow list."""
    if allowed is not None:
        os.environ["ALLOWED_INPUT_FILEEXTENSIONS"] = allowed
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


def _event(file_uri):
    return {
        "inputS3AssetFilePath": file_uri,
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/CJOB/output/E1/files/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/CJOB/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidC/scan.e57/pipelines/coordinateTransform",
        "assetId": "xidC",
        "databaseId": "dbC",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "sfnExternalTaskToken": "tok-123",
        "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
    }


def _invoke(mod, file_uri):
    """Run the handler with the state machine and orchestration bus stubbed.

    Returns (response, start_execution mock, send_task_failure mock)."""
    import datetime
    start = MagicMock(return_value={
        "executionArn": "arn:aws:states:us-east-1:1:execution:CoordTransform:CoordXform_x",
        "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
    })
    send_failure = MagicMock()
    with patch.object(mod.sfn, "start_execution", start), \
            patch.object(mod.sfn, "send_task_failure", send_failure), \
            patch.object(mod.events_client, "put_events", MagicMock()):
        resp = mod.lambda_handler(_event(file_uri), MagicMock())
    return resp, start, send_failure


@pytest.mark.unit
class TestExtensionGate:
    # Each of these is a strict prefix of a listed extension, so a containment test against the
    # joined '.e57,.las,.laz,.ply' string admits it.
    @pytest.mark.parametrize("extension", [".la", ".p", ".e5", ".e", ".pl", ".las,"])
    def test_prefix_of_allowed_extension_is_rejected(self, extension):
        mod = _load()
        resp, start, send_failure = _invoke(mod, f"s3://abkt/xidC/pump{extension}")
        assert resp["statusCode"] == 400
        start.assert_not_called()
        # The workflow task waits on the callback token, so the rejection is reported, not only
        # returned.
        assert send_failure.call_count == 1
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"

    @pytest.mark.parametrize("extension", [".e57", ".las", ".laz", ".ply", ".LAS", ".Ply"])
    def test_listed_extension_is_accepted(self, extension):
        mod = _load()
        resp, start, send_failure = _invoke(mod, f"s3://abkt/xidC/pump{extension}")
        assert resp["statusCode"] == 200
        start.assert_called_once()
        send_failure.assert_not_called()

    def test_no_extension_is_rejected(self):
        mod = _load()
        resp, start, _ = _invoke(mod, "s3://abkt/xidC/pump")
        assert resp["statusCode"] == 400
        start.assert_not_called()

    def test_whitespace_around_list_members_is_tolerated(self):
        """A CDK-supplied list written with spaces still matches, so the gate does not reject a
        supported format on formatting alone."""
        try:
            mod = _load(allowed=".e57, .las , .laz")
            resp, start, _ = _invoke(mod, "s3://abkt/xidC/pump.las")
            assert resp["statusCode"] == 200
            start.assert_called_once()
        finally:
            _load(allowed=".e57,.las,.laz,.ply")

    def test_cdk_builder_supplies_the_allow_list(self):
        """openPipeline reads ALLOWED_INPUT_FILEEXTENSIONS at import with no default, so the
        function's own builder must set it — otherwise every cold start raises KeyError while CDK
        synth and these tests still pass."""
        builder = os.path.join(
            _LAMBDA_DIR, "..", "..", "..", "..", "infra", "lib", "nestedStacks", "pipelines",
            "conversion", "coordinateTransform", "lambdaBuilder", "coordinateTransformFunctions.ts")
        builder = os.path.normpath(builder)
        assert os.path.isfile(builder), builder
        with open(builder, encoding="utf-8") as fh:
            source = fh.read()
        start = source.index("export function buildOpenPipelineFunction")
        end = source.find("\nexport function", start + 1)
        open_pipeline_builder = source[start:end if end != -1 else len(source)]
        assert "ALLOWED_INPUT_FILEEXTENSIONS" in open_pipeline_builder
