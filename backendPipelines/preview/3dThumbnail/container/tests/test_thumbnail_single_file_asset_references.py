#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A single-file asset's .gltf input resolves its resources in the asset's companion folder.

A single-file asset's location names one object (``p/model.gltf``, no trailing ``/``); what the asset
holds besides it is its companion folder ``p/model.gltf/``. The object's parent directory ``p/`` also
holds keys the asset does not hold (``p/other/``, ``p/model.gltf.previewFile.png/``, ``p/tex.png``). The
manifest records the location as the input's ``assetRootS3Key``, and the resources the object names are
looked up under the companion folder."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from preview_pipeline import core
from preview_pipeline.utils.pipeline.objects import StageInput

BUCKET = "asset-bucket"
LOCATION = "p/model.gltf"
URIS = ["tex.png", "other/tex.png", "sibling.png", "model.gltf.previewFile.png/x.png", "model.gltf/x.png"]


def _requested(tmp_path, object_key, asset_root):
    """The S3 keys the dependency download asks for, with the .gltf already downloaded under tmp_path."""
    local_dir = str(tmp_path)
    gltf = {"buffers": [], "images": [{"uri": uri} for uri in URIS]}
    with open(os.path.join(local_dir, os.path.basename(object_key)), "w", encoding="utf-8") as f:
        json.dump(gltf, f)
    download = MagicMock(side_effect=lambda bucket, key, path: path)
    with patch.object(core.s3, "download", download):
        core._download_gltf_dependencies(BUCKET, object_key, local_dir, asset_root)
    return [c.args[1] for c in download.call_args_list]


@pytest.mark.unit
class TestASingleFileAssetsGltfResources:
    @pytest.mark.parametrize("object_key, asset_root", [
        (LOCATION, LOCATION),
        ("/" + LOCATION, "/" + LOCATION),
        (LOCATION, "/" + LOCATION),
    ])
    def test_resources_come_from_the_companion_folder_only(self, tmp_path, object_key, asset_root):
        keys = _requested(tmp_path, object_key, asset_root)
        assert keys, "nothing was requested"
        for key in keys:
            assert key.lstrip("/").startswith(LOCATION + "/"), key
        assert {k.lstrip("/") for k in keys} == {
            LOCATION + "/tex.png", LOCATION + "/other/tex.png", LOCATION + "/sibling.png",
            LOCATION + "/model.gltf.previewFile.png/x.png"}

    def test_a_resource_through_the_downloaded_object_is_skipped(self, tmp_path):
        # The .gltf sits at its file name locally, so model.gltf/x.png would need a folder there.
        assert LOCATION + "/model.gltf/x.png" not in _requested(tmp_path, LOCATION, LOCATION)

    def test_a_folder_asset_reads_beside_the_input(self, tmp_path):
        keys = _requested(tmp_path, LOCATION, "p/")
        assert "p/tex.png" in keys and "p/other/tex.png" in keys

    @pytest.mark.parametrize("asset_root", ["", "p/other.gltf"])
    def test_any_other_root_keeps_the_input_directory(self, tmp_path, asset_root):
        assert "p/tex.png" in _requested(tmp_path, LOCATION, asset_root)

    def test_the_stage_input_carries_the_asset_root(self):
        stage_input = StageInput(**{"bucketName": BUCKET, "objectKey": LOCATION, "fileExtension": ".gltf",
                                    "assetRootS3Key": LOCATION})
        assert stage_input.assetRootS3Key == LOCATION
        assert StageInput(bucketName=BUCKET, objectKey=LOCATION).assetRootS3Key == ""
