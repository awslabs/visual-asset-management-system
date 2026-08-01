#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests that openPipeline treats 'transfer' as an input-file mode.

The mode set must match vamsExecuteCosmos3Pipeline and the container, so a transfer run without a
valid input file fails at the lambda instead of after the GPU Batch job provisions."""

import os
import sys
import types
import datetime
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

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:Cosmos3",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".mp4,.mov,.jpg,.jpeg,.png,.webp",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
}.items():
    os.environ.setdefault(_k, _v)


def _load(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def _event(**overrides):
    event = {
        "modelVariant": "nano",
        "taskMode": "transfer",
        "cosmosPrompt": "A drone shot.",
        "inputS3AssetFilePath": "s3://abkt/xidM/clip.mp4",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/cosmos3/E1/",
        "sfnExternalTaskToken": "tok-123",
        "assetId": "xidM",
        "databaseId": "dbM",
    }
    event.update(overrides)
    return event


def _mock_start():
    return MagicMock(return_value={
        "executionArn": "arn:aws:states:us-east-1:1:execution:Cosmos3:cosmos3-nano-x",
        "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
    })


@pytest.mark.unit
class TestTransferInputFileMode:
    def test_mode_set_matches_vams_execute(self):
        open_pipeline = _load("openPipeline")
        vams_execute = _load("vamsExecuteCosmos3Pipeline")
        assert set(open_pipeline.INPUT_FILE_MODES) == set(vams_execute.INPUT_FILE_MODES)
        assert "transfer" in open_pipeline.INPUT_FILE_MODES

    def test_transfer_without_input_file_fails_at_the_lambda(self):
        mod = _load("openPipeline")
        start = _mock_start()
        fail = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_failure", fail):
            resp = mod.lambda_handler(_event(inputS3AssetFilePath=""), MagicMock())
        assert resp["statusCode"] == 400
        start.assert_not_called()
        assert fail.call_count == 1

    def test_transfer_with_rejected_extension_fails_at_the_lambda(self):
        mod = _load("openPipeline")
        start = _mock_start()
        fail = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_failure", fail):
            resp = mod.lambda_handler(
                _event(inputS3AssetFilePath="s3://abkt/xidM/clip.mkv"), MagicMock())
        assert resp["statusCode"] == 400
        start.assert_not_called()
        assert fail.call_count == 1

    def test_transfer_with_valid_input_starts_the_state_machine(self):
        mod = _load("openPipeline")
        start = _mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(_event(), MagicMock())
        assert resp["statusCode"] == 200
        assert start.call_count == 1
