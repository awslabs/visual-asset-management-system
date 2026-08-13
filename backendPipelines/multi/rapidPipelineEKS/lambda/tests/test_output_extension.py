#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for output-extension resolution in the multi/rapidPipelineEKS CONSTRUCT_PIPELINE op:
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
    "EKS_CLUSTER_NAME": "test-cluster",
    "CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:latest",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(k, v)


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


@pytest.mark.unit
class TestOutputExtensionResolution:
    def _load(self):
        if "consolidated_handler" in sys.modules:
            return importlib.reload(sys.modules["consolidated_handler"])
        return importlib.import_module("consolidated_handler")

    def _event(self, **overrides):
        event = {
            "operation": "CONSTRUCT_PIPELINE",
            "jobName": "PipelineJobEKS_x",
            "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/eks/p1",
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
            out = mod.lambda_handler(event, _ctx())
        container = out["jobManifest"]["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e["value"] for e in container["env"]}
        return container["args"][0], env

    def test_input_extension_used_when_config_and_event_supply_none(self):
        # outputFileType is threaded as the empty string rather than omitted, so the output name
        # falls through to the input file's extension instead of being emitted bare.
        command, env = self._run({"someRapidPipelineOption": True}, self._event())
        assert "-e pump.glb" in command
        assert "-e pump-" not in command
        assert env["OUTPUT_FILE_TYPE"] == ".glb"

    def test_config_outputType_overrides_input_extension(self):
        command, env = self._run({"outputType": ".gltf"}, self._event())
        # A differing target format is suffixed onto the name to keep it distinct from the input.
        assert "-e pump-gltf.gltf" in command
        assert env["OUTPUT_FILE_TYPE"] == ".gltf"

    def test_threaded_outputFileType_overrides_input_extension(self):
        command, env = self._run({}, self._event(outputFileType=".gltf"))
        assert "-e pump-gltf.gltf" in command
        assert env["OUTPUT_FILE_TYPE"] == ".gltf"

    def test_uploaded_object_carries_the_extension(self):
        command, _ = self._run({}, self._event())
        assert command.rstrip().endswith("/files/pump.glb")
