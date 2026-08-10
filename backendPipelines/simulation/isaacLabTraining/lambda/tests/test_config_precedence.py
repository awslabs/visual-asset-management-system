#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for isaacLab run-configuration precedence.

The run configuration is the manifest-delivered input configuration — the template selection the
operator made on the execute screen. A JSON input file is an asset file, so anything it holds is a
standing default for the fields the configuration leaves blank."""

import os
import sys
import types
import json
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

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(k, v)

# A JSON file sitting in the asset. Every value differs from the configuration used below, and the
# evaluation pipeline's inputFileFilters allow "*.json", so this is a file an operator can select.
_ASSET_FILE = json.dumps({
    "trainingConfig": {
        "mode": "train",
        "task": "Isaac-Ant-v0",
        "numEnvs": 4096,
        "maxIterations": 9999,
        "checkpointPath": "checkpoints/from_file.pt",
    },
    "computeConfig": {"numNodes": 8},
})


def _load():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


def _s3_returning(body):
    """An s3 client mock whose get_object returns ``body`` and whose listings are empty."""
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body.encode()))}
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    s3.get_paginator.return_value = paginator
    return s3


def _event(**overrides):
    event = {
        "jobName": "isaaclab-job-abcd1234",
        "bucketAsset": "asset-bucket",
        "inputAssetLocationKey": "xid130a6/",
        "inputS3AssetFilePath": "s3://asset-bucket/xid130a6/unrelated-doc.json",
        "outputS3AssetFilesPath": "s3://asset-bucket/pipelines/p1/JOB/output/E1/files/",
        "externalSfnTaskToken": "tok-123",
    }
    event.update(overrides)
    return event


def _definition(event, file_body=_ASSET_FILE):
    mod = _load()
    with patch.object(mod, "s3_client", _s3_returning(file_body)):
        return json.loads(mod.lambda_handler(event, MagicMock())["definition"])


@pytest.mark.unit
class TestManifestConfigurationWins:
    def test_configuration_decides_the_mode(self):
        # The selected file says "train"; the operator chose the evaluation template.
        definition = _definition(_event(trainingConfig={
            "mode": "evaluate",
            "task": "Isaac-Cartpole-Direct-v0",
            "numEnvs": 100,
            "numEpisodes": 50,
            "checkpointPath": "checkpoints/model_499.pt",
        }))
        assert definition["trainingConfig"]["mode"] == "evaluate"

    def test_configuration_values_outrank_the_input_file(self):
        definition = _definition(_event(
            trainingConfig={
                "mode": "evaluate",
                "task": "Isaac-Cartpole-Direct-v0",
                "numEnvs": 100,
                "checkpointPath": "checkpoints/model_499.pt",
            },
            computeConfig={"numNodes": 1},
        ))
        assert definition["trainingConfig"]["task"] == "Isaac-Cartpole-Direct-v0"
        assert definition["trainingConfig"]["numEnvs"] == 100
        assert definition["trainingConfig"]["policyS3Uri"] == \
            "s3://asset-bucket/xid130a6/checkpoints/model_499.pt"
        assert definition["computeConfig"]["numNodes"] == 1

    def test_a_field_the_configuration_omits_falls_back_to_the_input_file(self):
        definition = _definition(_event(
            trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0"},
            computeConfig={},
        ))
        assert definition["trainingConfig"]["task"] == "Isaac-Cartpole-Direct-v0"
        assert definition["trainingConfig"]["maxIterations"] == 9999
        assert definition["computeConfig"]["numNodes"] == 8

    def test_no_configuration_falls_back_entirely_to_the_input_file(self):
        definition = _definition(_event())
        assert definition["trainingConfig"]["mode"] == "train"
        assert definition["trainingConfig"]["task"] == "Isaac-Ant-v0"
        assert definition["trainingConfig"]["maxIterations"] == 9999
        assert definition["computeConfig"]["numNodes"] == 8

    def test_a_blank_configuration_value_falls_through(self):
        definition = _definition(_event(trainingConfig={"mode": "train", "task": "   "}))
        assert definition["trainingConfig"]["task"] == "Isaac-Ant-v0"


@pytest.mark.unit
class TestInputFileIsNotTrustedAsConfig:
    def test_a_json_file_that_is_not_an_object_yields_no_defaults(self):
        mod = _load()
        with patch.object(mod, "s3_client", _s3_returning("[1, 2, 3]")):
            assert mod.load_config_from_s3("s3://asset-bucket/xid130a6/points.json") == {}

    def test_a_json_array_input_file_does_not_break_the_build(self):
        definition = _definition(_event(trainingConfig={"mode": "train"}), file_body="[1, 2, 3]")
        assert definition["trainingConfig"]["mode"] == "train"
        assert definition["trainingConfig"]["task"] == _load().DEFAULT_TASK

    def test_a_non_json_input_file_is_never_read(self):
        mod = _load()
        s3 = _s3_returning(_ASSET_FILE)
        with patch.object(mod, "s3_client", s3):
            assert mod.load_config_from_s3("s3://asset-bucket/xid130a6/scene.usd") == {}
        s3.get_object.assert_not_called()

    def test_unparseable_json_yields_no_defaults(self):
        mod = _load()
        with patch.object(mod, "s3_client", _s3_returning("{not json")):
            assert mod.load_config_from_s3("s3://asset-bucket/xid130a6/broken.json") == {}
