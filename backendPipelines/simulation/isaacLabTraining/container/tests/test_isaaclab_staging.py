#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for staging the executing asset's files into the container (``stageAssetFiles``).

openPipeline passes the asset root and the validated ``stageAssetPaths``; the container lists them
again, applies the same limits, writes each file at its asset-relative path under the staging
folder, and exports that folder to the Isaac Lab process. The job role can read every asset bucket,
so nothing here may write outside the staging folder or read outside the asset root."""

import importlib.util
import json
import os
import sys
from collections import namedtuple

import pytest

from utils.aws import staging
from utils.training.config import PipelineConfig

_BUCKET = "asset-bucket"
_ROOT = "xid130a6/"
_ROOT_URI = f"s3://{_BUCKET}/{_ROOT}"
_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"

_OBJECTS = {
    _ROOT: b"",
    _ROOT + "Isaac/": b"",
    _ROOT + "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd": b"#usda robot",
    _ROOT + "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd.previewFile.gif": b"GIF",
    _ROOT + "Isaac/Robots/NVIDIA/NovaCarter/Parts/wheel.usd": b"#usda wheel",
    _ROOT + "Isaac/Sensors/HESAI/XT-32.usd": b"#usda lidar",
    _ROOT + "Isaac2/unrelated.usd": b"other",
    "xother999/Isaac/Robots/secret.usd": b"not this asset",
}


class FakeClient:
    """An S3 client over in-memory objects: prefix listings in key order and downloads to disk."""

    def __init__(self, objects, fail_on=None):
        self.objects = dict(objects)
        self.fail_on = fail_on
        self.downloads = []
        self.transfer_configs = []
        outer = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for k in outer.objects if k.startswith(Prefix))
                yield {"Contents": [{"Key": k, "Size": len(outer.objects[k])} for k in keys]}

        self._paginator = _Paginator()

    def get_paginator(self, name):
        return self._paginator

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"Contents": [{"Key": k, "Size": len(self.objects[k])} for k in keys]} if keys else {}

    def download_file(self, bucket, key, filename, Config=None):
        if key == self.fail_on:
            raise OSError("connection reset")
        assert bucket == _BUCKET and key.startswith(_ROOT)
        with open(filename, "wb") as handle:
            handle.write(self.objects[key])
        self.downloads.append(key)
        self.transfer_configs.append(Config)


class FakeS3:
    """The job's S3Client: hands out the bucket's client."""

    def __init__(self, client):
        self.client = client
        self.asked_for = []

    def client_for_bucket(self, bucket):
        self.asked_for.append(bucket)
        return self.client

    def register_bucket_regions(self, regions):
        pass

    def upload_file(self, local_path, s3_uri):
        pass


def _stage(tmp_path, paths, objects=None, **client_kwargs):
    client = FakeClient(_OBJECTS if objects is None else objects, **client_kwargs)
    local_root = str(tmp_path / "assets")
    staged = staging.stage_asset_files(FakeS3(client), {"rootS3Uri": _ROOT_URI, "paths": paths}, local_root)
    return staged, client, local_root


def _files_under(root):
    return sorted(os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs)


@pytest.mark.unit
class TestStagedLayout:
    def test_files_keep_their_asset_relative_paths(self, tmp_path):
        staged, _, root = _stage(tmp_path, ["Isaac/"])
        assert _files_under(root) == [
            "Isaac/Robots/NVIDIA/NovaCarter/Parts/wheel.usd",
            "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd",
            "Isaac/Sensors/HESAI/XT-32.usd",
        ]
        with open(os.path.join(root, "Isaac/Sensors/HESAI/XT-32.usd"), "rb") as handle:
            assert handle.read() == b"#usda lidar"
        assert staged["files"] == 3
        assert staged["bytes"] == len(b"#usda robot") + len(b"#usda wheel") + len(b"#usda lidar")
        assert staged["path"] == root

    def test_previews_folder_markers_and_prefix_siblings_are_not_staged(self, tmp_path):
        _, client, _ = _stage(tmp_path, ["Isaac"])
        assert not any(".previewFile." in key or key.endswith("/") for key in client.downloads)
        assert _ROOT + "Isaac2/unrelated.usd" not in client.downloads

    def test_the_whole_asset_does_not_reach_another_asset(self, tmp_path):
        _, client, _ = _stage(tmp_path, ["/"])
        assert all(key.startswith(_ROOT) for key in client.downloads)
        assert len(client.downloads) == 4

    def test_large_files_download_with_parallel_ranged_gets(self, tmp_path):
        _, client, _ = _stage(tmp_path, ["Isaac/Sensors/"])
        assert client.transfer_configs == [staging.TRANSFER_CONFIG]


@pytest.mark.unit
class TestStagingStaysInsideTheAssetAndTheFolder:
    @pytest.mark.parametrize("key", [
        _ROOT + "Isaac/../../etc/cron.d/x", _ROOT + "../outside", _ROOT + "Isaac//double.usd",
        _ROOT + "Isaac/./here.usd",
    ])
    def test_a_key_whose_path_leaves_the_folder_fails_the_run(self, tmp_path, key):
        objects = {key: b"payload"}
        with pytest.raises(ValueError, match="cannot be staged: its path has a '.', '..' or empty segment"):
            _stage(tmp_path, ["/"], objects=objects)
        assert not os.path.exists(tmp_path / "assets")

    @pytest.mark.parametrize("key", [
        _ROOT + "Isaac/../../etc/cron.d/x", _ROOT + "../outside", _ROOT + "Isaac//double.usd",
        _ROOT + "Isaac/./here.usd",
    ])
    def test_the_local_path_guard_refuses_the_same_keys(self, tmp_path, key):
        with pytest.raises(ValueError, match="leaves the staging folder"):
            staging.local_path_for(key, _ROOT, str(tmp_path / "assets"))

    def test_a_name_longer_than_the_file_system_holds_fails_before_any_download(self, tmp_path):
        objects = {_ROOT + "Isaac/" + "n" * 247 + ".usd": b"x", _ROOT + "Isaac/ok.usd": b"y"}
        with pytest.raises(ValueError, match="longer than 246 bytes"):
            _stage(tmp_path, ["Isaac/"], objects=objects)
        assert not os.path.exists(tmp_path / "assets")

    def test_a_file_and_a_folder_of_the_same_path_fail_before_any_download(self, tmp_path):
        objects = {_ROOT + "Isaac/scene": b"file", _ROOT + "Isaac/scene/part.usd": b"part"}
        with pytest.raises(ValueError, match="a folder of other selected files has the same path"):
            _stage(tmp_path, ["Isaac/"], objects=objects)
        assert not os.path.exists(tmp_path / "assets")

    def test_paths_over_the_total_length_are_rejected_again(self, tmp_path):
        paths = [f"{i:02d}/" + "p" * 60 + "/" for i in range(40)]
        with pytest.raises(ValueError, match="at most 2048 are allowed"):
            _stage(tmp_path, paths)

    def test_a_symlinked_folder_cannot_redirect_a_file(self, tmp_path):
        local_root = tmp_path / "assets"
        local_root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, local_root / "Isaac")
        with pytest.raises(ValueError, match="leaves the staging folder"):
            staging.local_path_for(_ROOT + "Isaac/Sensors/a.usd", _ROOT, str(local_root))

    @pytest.mark.parametrize("paths", [["../xother999/"], ["s3://asset-bucket/xother999/"],
                                       ["Isaac/../.."], ["Isaac\\Robots"]])
    def test_paths_are_validated_again(self, tmp_path, paths):
        with pytest.raises(ValueError, match="stageAssetPaths"):
            _stage(tmp_path, paths)

    @pytest.mark.parametrize("block", [
        {"rootS3Uri": "https://asset-bucket/xid130a6/", "paths": ["Isaac/"]},
        {"rootS3Uri": "s3://asset-bucket/xid130a6", "paths": ["Isaac/"]},
        {"rootS3Uri": "s3://asset-bucket/", "paths": ["Isaac/"]},
        {"rootS3Uri": _ROOT_URI, "paths": []},
        {"rootS3Uri": _ROOT_URI, "paths": "Isaac/"},
        {"rootS3Uri": _ROOT_URI, "paths": [f"f{i}/" for i in range(staging.MAX_STAGE_ASSET_PATHS + 1)]},
        ["Isaac/"],
    ])
    def test_a_malformed_block_is_rejected(self, block):
        with pytest.raises(ValueError):
            staging.parse_stage_config(block)

    def test_a_selection_that_names_no_file_fails_the_run(self, tmp_path):
        with pytest.raises(ValueError, match="names no file in the asset"):
            _stage(tmp_path, ["Isaac/Missing/"])


@pytest.mark.unit
class TestLimits:
    def test_the_file_limit_is_applied_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(staging, "MAX_STAGED_ASSET_FILES", 2)
        with pytest.raises(ValueError, match="more than 2 files"):
            _stage(tmp_path, ["Isaac/"])

    def test_the_byte_limit_is_applied_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(staging, "MAX_STAGED_ASSET_BYTES", 15)
        with pytest.raises(ValueError, match="GiB of files"):
            _stage(tmp_path, ["Isaac/"])

    def test_a_disk_without_room_fails_before_any_download(self, tmp_path, monkeypatch):
        usage = namedtuple("usage", "total used free")
        monkeypatch.setattr(staging.shutil, "disk_usage",
                            lambda path: usage(10 ** 12, 0, staging.DISK_HEADROOM_BYTES + 5))
        client = FakeClient(_OBJECTS)
        with pytest.raises(RuntimeError, match="bytes free"):
            staging.stage_asset_files(FakeS3(client), {"rootS3Uri": _ROOT_URI, "paths": ["Isaac/"]},
                                      str(tmp_path / "assets"))
        assert client.downloads == []

    def test_a_failed_download_fails_the_run_naming_the_file(self, tmp_path):
        with pytest.raises(RuntimeError, match="Isaac/Sensors/HESAI/XT-32.usd"):
            _stage(tmp_path, ["Isaac/"], fail_on=_ROOT + "Isaac/Sensors/HESAI/XT-32.usd")


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``)."""
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "isaaclab_container_main_staging", os.path.join(container_dir, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


@pytest.mark.unit
class TestTheRunSeesTheStagedFolder:
    def _run(self, main_module, monkeypatch, tmp_path, job_config):
        local_root = str(tmp_path / "assets")
        staged_file = os.path.join(local_root, "Isaac/Sensors/HESAI/XT-32.usd")
        events = []

        def fake_install(s3, s3_uri):
            events.append(("install", s3_uri, os.path.exists(staged_file)))

        def fake_run(cmd, *args, **kwargs):
            events.append(("run", list(cmd), os.environ.get(staging.STAGED_ASSET_FILES_ENV)))
            return _Completed()

        client = FakeClient(_OBJECTS)
        monkeypatch.setattr(main_module, "S3Client", lambda: FakeS3(client))
        monkeypatch.setattr(main_module, "download_and_install_custom_environment", fake_install)
        monkeypatch.setattr(main_module.subprocess, "run", fake_run)
        monkeypatch.setattr(main_module, "upload_training_results", lambda *a, **k: None)
        monkeypatch.setattr(main_module, "setup_checkpoint_dir", lambda config: str(tmp_path / "ckpt"))
        monkeypatch.setattr(main_module, "stage_asset_files",
                            lambda s3, block: staging.stage_asset_files(s3, block, local_root))
        monkeypatch.delenv(staging.STAGED_ASSET_FILES_ENV, raising=False)
        monkeypatch.setattr(sys, "argv", ["__main__.py", json.dumps(job_config)])
        main_module.run_pipeline()
        return events, local_root

    def test_files_are_staged_before_the_package_install_and_the_run(self, main_module, monkeypatch, tmp_path):
        job_config = {
            "jobName": "job1",
            "trainingConfig": {"mode": "train", "task": "Vams-Nav-v0", "rlLibrary": "rsl_rl"},
            "customEnvironmentS3Uri": f"{_ROOT_URI}environments/pkg.whl",
            "outputS3AssetFilesPath": _OUTPUT_PREFIX,
            "stageAssetFiles": {"rootS3Uri": _ROOT_URI, "paths": ["Isaac/"], "fileCount": 3, "totalBytes": 33},
        }
        events, local_root = self._run(main_module, monkeypatch, tmp_path, job_config)
        install, train = events
        assert install[0] == "install" and install[2], "the asset files were not staged before the install"
        assert train[0] == "run" and train[1][0] == "./isaaclab.sh"
        assert train[2] == local_root

    def test_a_run_without_the_block_stages_nothing_and_sets_no_variable(self, main_module, monkeypatch,
                                                                         tmp_path):
        job_config = {
            "jobName": "job1",
            "trainingConfig": {"mode": "train", "task": "Isaac-Cartpole-Direct-v0", "rlLibrary": "rsl_rl"},
            "outputS3AssetFilesPath": _OUTPUT_PREFIX,
        }
        events, local_root = self._run(main_module, monkeypatch, tmp_path, job_config)
        (train,) = events
        assert train[2] is None
        assert not os.path.exists(local_root)


@pytest.mark.unit
class TestJobConfigParsing:
    def test_the_block_is_read_into_the_config(self):
        block = {"rootS3Uri": _ROOT_URI, "paths": ["Isaac/"]}
        config = PipelineConfig.from_dict({"trainingConfig": {"mode": "train"}, "stageAssetFiles": block})
        assert config.stage_asset_files == block

    def test_no_block_stages_nothing(self):
        assert PipelineConfig.from_dict({"trainingConfig": {"mode": "train"}}).stage_asset_files == {}

    def test_a_block_that_is_not_an_object_is_rejected(self):
        with pytest.raises(ValueError, match="stageAssetFiles must be a configuration object"):
            PipelineConfig.from_dict({"trainingConfig": {"mode": "train"}, "stageAssetFiles": ["Isaac/"]})


@pytest.mark.unit
class TestTheImageGivesTheStagingFolderToTheRuntimeUser:
    def test_the_build_gives_the_staging_folder_to_the_runtime_user(self, image):
        """The build creates the folder as root; only its chown lets the non-root user write there."""
        assert os.path.normpath(staging.STAGED_ASSET_FILES_PATH) in image.created
        assert image.owns(staging.STAGED_ASSET_FILES_PATH)
