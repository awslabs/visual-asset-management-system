#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the CONSTRUCT_PIPELINE container command construction.

The command runs under /bin/sh, so every interpolated S3 key / filename / output path must appear as
an inert single-quoted literal — mirroring the ECS rapidPipeline constructPipeline quoting."""

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

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:latest",
    "EKS_CLUSTER_NAME": "test-cluster",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(_k, _v)


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


def _load():
    if "consolidated_handler" in sys.modules:
        return importlib.reload(sys.modules["consolidated_handler"])
    return importlib.import_module("consolidated_handler")


# An input key carrying shell metacharacters: unquoted interpolation would run the injected command.
_MALICIOUS_KEY = "xidM/a;touch$IFS/tmp/pwned;`id`.glb"


def _event(output_file_type=".glb", key=_MALICIOUS_KEY):
    return {
        "operation": "CONSTRUCT_PIPELINE",
        "jobName": "PipelineJobEKS_x",
        "inputS3AssetFilePath": f"s3://abkt/{key}",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/eks/p1",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "externalSfnTaskToken": "tok",
        "outputFileType": output_file_type,
    }


def _command(out):
    return out["jobManifest"]["spec"]["template"]["spec"]["containers"][0]["args"][0]


def _unquoted_segments(command):
    """The parts of the command OUTSIDE single quotes (shell-interpreted text)."""
    return command.split("'")[::2]


@pytest.mark.unit
class TestCommandQuoting:
    def _run(self, event, config=None):
        mod = _load()
        s3 = MagicMock()
        if config is None:
            s3.get_object.side_effect = Exception("AccessDenied")
        else:
            s3.get_object.return_value = {
                "Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
        s3.put_object = MagicMock()
        with patch.object(mod, "s3", s3):
            return _command(mod.lambda_handler(event, _ctx()))

    def test_single_format_command_quotes_metacharacters(self):
        command = self._run(_event())
        shell_text = "".join(_unquoted_segments(command))
        assert "touch" not in shell_text
        assert "`id`" not in shell_text
        assert "$IFS" not in shell_text
        assert f"'s3://abkt/{_MALICIOUS_KEY}'" in command

    def test_all_formats_command_quotes_metacharacters(self):
        command = self._run(_event(output_file_type=".all"))
        shell_text = "".join(_unquoted_segments(command))
        assert "touch" not in shell_text
        assert "`id`" not in shell_text
        # The glob '*' stays outside the quoted stem so it still expands.
        assert "'*" in command
        assert "for file in" in command

    def test_config_download_path_is_quoted(self):
        command = self._run(_event(), config={"someRapidPipelineOption": True})
        assert "s3://aux/xidM/eks/p1/rp_config.json rp_config.json" in command
        assert "--read_config rp_config.json" in command
        shell_text = "".join(_unquoted_segments(command))
        assert "touch" not in shell_text

    def test_benign_key_command_is_still_functional(self):
        # A metacharacter-free key needs no quoting, so the command reads exactly as before.
        command = self._run(_event(key="xidM/test/pump.glb"))
        assert command == (
            "aws s3 cp s3://abkt/xidM/test/pump.glb . && /rpdx/rpdx -i pump.glb -c -e pump.glb "
            "&& aws s3 cp pump.glb s3://abkt/pipelines/p1/MJOB/output/E1/files/pump.glb"
        )
