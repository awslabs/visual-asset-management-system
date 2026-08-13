#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for output-extension resolution in the multi/rapidPipeline constructPipeline lambda:
outputType from the input configuration wins, the threaded outputFileType is next, and the input
file's own extension is the final fallback so the written object always carries an extension."""

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
}.items():
    os.environ.setdefault(k, v)


@pytest.mark.unit
class TestOutputExtensionResolution:
    def _load(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def _event(self, **overrides):
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/model.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/auxbkt/rapidPipeline",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
            "outputFileType": "",
        }
        event.update(overrides)
        return event

    def _run(self, config, event):
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))
        }
        s3.put_object = MagicMock()
        with patch.object(mod, "s3", s3):
            return mod.lambda_handler(event, MagicMock())["commands"][2]

    def test_input_extension_used_when_config_and_event_supply_none(self):
        # outputFileType is threaded as the empty string rather than omitted, so the output name
        # falls through to the input file's extension instead of being emitted bare.
        command = self._run({"settings": {"quality": "high"}}, self._event())
        assert "-e model.glb" in command
        assert "-e model " not in command

    def test_config_outputType_overrides_input_extension(self):
        command = self._run({"outputType": ".gltf"}, self._event())
        assert "-e model.gltf" in command

    def test_threaded_outputFileType_overrides_input_extension(self):
        command = self._run({}, self._event(outputFileType=".gltf"))
        assert "-e model.gltf" in command

    def test_uploaded_object_carries_the_extension(self):
        command = self._run({}, self._event())
        assert command.endswith("model.glb s3://abkt/pipelines/p1/MJOB/output/E1/files/")
