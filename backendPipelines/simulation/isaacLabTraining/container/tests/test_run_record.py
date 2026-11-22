#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for what the Isaac Lab container keeps in the run's recorded configuration file."""

import importlib.util
import json
import os

import pytest

_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"

_JOB_CONFIG = {
    "jobName": "isaaclab-job",
    "trainingConfig": {"mode": "train", "task": "Isaac-Cartpole-v0", "numEnvs": 64},
    "customEnvironmentS3Uri": "s3://asset-bucket/asset/env.zip",
    "inputS3AssetFilePath": "s3://asset-bucket/asset/config.json",
    "outputS3AssetFilesPath": _OUTPUT_PREFIX,
    "inputMetadataS3Location": "s3://asset-bucket/metadata.json",
    "inputConfigurationS3Location": "s3://asset-bucket/configuration.json",
    "externalSfnTaskToken": "callback-value",
}

_LEFT_OUT = (
    "inputS3AssetFilePath",
    "outputS3AssetFilesPath",
    "inputMetadataS3Location",
    "inputConfigurationS3Location",
    "externalSfnTaskToken",
)


class _RecordingS3:
    def __init__(self):
        self.uploads = []

    def upload_file(self, local_path, s3_path):
        self.uploads.append((local_path, s3_path))


@pytest.fixture(scope="module")
def main_module():
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "isaaclab_container_main_run_record", os.path.join(container_dir, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["train", "evaluate"])
def test_the_recorded_configuration_keeps_only_the_run_settings(
        main_module, monkeypatch, tmp_path, mode):
    real_open = open

    def _open(path, *args, **kwargs):
        text = str(path)
        if text.startswith("/tmp/"):
            path = str(tmp_path / text[len("/tmp/"):])
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(main_module, "open", _open, raising=False)
    config = main_module.PipelineConfig(
        job_name="isaaclab-job",
        mode=mode,
        task="Isaac-Cartpole-v0",
        rl_library="rsl_rl",
        input_s3_path="s3://in/asset/config.json",
        output_s3_path=_OUTPUT_PREFIX,
    )
    s3 = _RecordingS3()

    main_module.upload_config(s3, config, dict(_JOB_CONFIG))

    assert [path for _, path in s3.uploads] == [f"{_OUTPUT_PREFIX}{mode}-config.json"]
    recorded = json.loads((tmp_path / f"{mode}-config.json").read_text())
    for key in _LEFT_OUT:
        assert key not in recorded
    assert "callback-value" not in json.dumps(recorded)
    assert recorded["jobName"] == "isaaclab-job"
    assert recorded["trainingConfig"] == _JOB_CONFIG["trainingConfig"]
    assert recorded["customEnvironmentS3Uri"] == _JOB_CONFIG["customEnvironmentS3Uri"]
