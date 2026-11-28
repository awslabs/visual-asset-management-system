# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The hierarchy download of a single-file asset's object lists the asset's companion folder.

A single-file asset's location names one object (``p/model.gltf``, no trailing ``/``); what the asset
holds besides it is its companion folder ``p/model.gltf/``. The object's parent directory ``p/`` also
holds keys the asset does not hold (``p/other/``, ``p/model.gltf.previewFile.png/``, ``p/sibling.png``).
With ``includeAllAssetFileHierarchyFiles`` set, the stage lists the companion folder and stages the
object at its file name beside the folder's files."""

import io
import os

import pytest

import main.pipelines.blenderRenderer.pipeline as pipeline
from main.utils.pipeline.objects import PipelineStage, PipelineStatus

LOCATION = "p/model.gltf"
BUCKET = {
    LOCATION: "the object",
    LOCATION + "/tex.png": "own texture",
    LOCATION + "/sub/a.bin": "own buffer",
    LOCATION + "/model.gltf": "companion entry named like the object",
    LOCATION + "/model.gltf/x.png": "companion file under that entry",
    "p/other/": "",
    "p/other/tex.png": "not in the asset",
    "p/sibling.png": "not in the asset",
    "p/model.gltf.previewFile.png/x.png": "not in the asset",
}

_REAL_MAKEDIRS = os.makedirs


def _run(monkeypatch, tmp_path, asset_root, include_all="True", object_key=LOCATION):
    monkeypatch.chdir(tmp_path)
    listed, downloaded = [], []

    def _list(bucket, path):
        listed.append(path)
        return [{"key": k, "relativePath": k.removeprefix(path)} for k in BUCKET if k.startswith(path)]

    def _download(bucket, key, path):
        downloaded.append(key)
        assert os.path.realpath(path).startswith(os.path.realpath(str(tmp_path)) + os.sep)
        io.open(path, "w", encoding="utf-8").write(BUCKET[key.lstrip("/")])
        return path

    monkeypatch.setattr(pipeline.s3, "get_all_files_in_path", _list)
    monkeypatch.setattr(pipeline.s3, "download", _download)
    monkeypatch.setattr(pipeline.s3, "uploadV2", lambda bucket, key, path: key)
    monkeypatch.setattr(pipeline.s3, "delete_all_path_contents", lambda bucket, key: None)
    monkeypatch.setattr(pipeline.os, "makedirs", lambda path, exist_ok=False: _REAL_MAKEDIRS(path, exist_ok=exist_ok))
    rendered = {}

    def _render(input_file_path, output_dir):
        rendered["input"] = io.open(input_file_path, encoding="utf-8").read()
        return {"output_dir": output_dir, "output_files": ["render0.jpg"], "returncode": 0}

    monkeypatch.setattr(pipeline, "allconvert_blenderrenderer_pipeline", _render)
    stage = PipelineStage(
        type="BLENDERRENDERER",
        inputFile={"bucketName": "asset-bucket", "objectKey": object_key, "assetRootS3Key": asset_root},
        outputFiles={"bucketName": "aux-bucket", "objectDir": "p/model.gltf/genAi/"},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles={"bucketName": "aux-bucket", "objectDir": "p/model.gltf/genAi/"},
    )
    result = pipeline.run(stage, "", '{"includeAllAssetFileHierarchyFiles": "%s"}' % include_all, False)
    return result, listed, downloaded, rendered


@pytest.mark.unit
class TestASingleFileAssetsHierarchy:
    def test_only_the_asset_own_keys_are_listed_and_downloaded(self, monkeypatch, tmp_path):
        result, listed, downloaded, rendered = _run(monkeypatch, tmp_path, LOCATION)
        assert listed == [LOCATION + "/"]
        assert downloaded == [LOCATION, LOCATION + "/tex.png", LOCATION + "/sub/a.bin"]
        for key in downloaded:
            assert key == LOCATION or key.startswith(LOCATION + "/"), key
        # The object is what renders, not the companion entry named like it.
        assert rendered["input"] == "the object"
        assert result.status == PipelineStatus.COMPLETE

    def test_a_root_and_key_with_a_leading_slash(self, monkeypatch, tmp_path):
        _, listed, _, _ = _run(monkeypatch, tmp_path, "/" + LOCATION)
        assert listed == [LOCATION + "/"]

    def test_without_the_hierarchy_only_the_object_is_downloaded(self, monkeypatch, tmp_path):
        _, listed, downloaded, rendered = _run(monkeypatch, tmp_path, LOCATION, include_all="False")
        assert listed == [] and downloaded == [LOCATION] and rendered["input"] == "the object"

    def test_a_folder_asset_lists_the_input_directory(self, monkeypatch, tmp_path):
        _, listed, downloaded, _ = _run(monkeypatch, tmp_path, "p/")
        assert listed == ["p/"]
        assert "p/other/tex.png" in downloaded

    @pytest.mark.parametrize("asset_root", ["", "p/other.gltf"])
    def test_any_other_root_keeps_the_input_directory(self, monkeypatch, tmp_path, asset_root):
        _, listed, _, _ = _run(monkeypatch, tmp_path, asset_root)
        assert listed == ["p/"]
