#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A single-file asset's referenced files come from its own companion folder.

A single-file asset's location names one object (``scans/model.gltf``, no trailing ``/``); what the
asset holds besides it is its companion folder ``scans/model.gltf/``. The object's parent directory
``scans/`` holds keys the asset does not hold, such as another asset's files. The manifest records the
location as the input's ``assetRootS3Key``; the root ``core`` derives from it confines the object's
references to the companion folder, and a reference that leaves the folder is reported as not fetched.

The bucket is a dict: an object and a folder of the same name cannot both sit on a local disk."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from preview_pipeline import core, dependencies
from preview_pipeline.utils.pipeline.objects import StageInput, StageOutput

BUCKET = "asset-bucket"
LOCATION = "scans/model.gltf"
COMPANION = LOCATION + "/tex.png"
# Another asset's file beside the single-file asset's object.
SIBLING = "scans/sibling.png"
GLTF = json.dumps({"asset": {"version": "2.0"},
                   "images": [{"uri": "tex.png"}, {"uri": "sibling.png"}, {"uri": "../sibling.png"}]})


class DictBucket:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.heads = []
        self.downloads = []

    def head_object_size(self, bucket, key, warn_if_missing=True):
        self.heads.append(key)
        return len(self.objects[key]) if key in self.objects else None

    def download(self, bucket, key, path):
        self.downloads.append(key)
        with open(path, "wb") as handle:
            handle.write(self.objects[key])
        return path


def _fetch(tmp_path, asset_root, gltf=GLTF, objects=None):
    """Stage ``LOCATION`` of an asset whose location is ``asset_root`` and fetch what it references, as
    ``core`` does; return the bucket and the report."""
    bucket = DictBucket({LOCATION: gltf.encode(), COMPANION: b"png", SIBLING: b"other asset", **(objects or {})})
    stage_input = StageInput(bucketName=BUCKET, objectKey=LOCATION, fileExtension=".gltf")
    stage_output = StageOutput(bucketName=BUCKET, objectDir="pipelines/p/j1/output/E1/files/")
    root_key = core._reference_root_key(stage_input, stage_output, "xid", {
        "relativePath": "/model.gltf", "assetRootS3Key": asset_root, "assetRootBucket": BUCKET})
    local_root = str(tmp_path / "asset")
    local_input = dependencies.local_path_for(local_root, dependencies.relative_to_root(LOCATION, root_key))
    os.makedirs(os.path.dirname(local_input), exist_ok=True)
    with open(local_input, "w", encoding="utf-8") as handle:
        handle.write(gltf)
    with patch.object(dependencies.s3, "head_object_size", bucket.head_object_size), \
            patch.object(dependencies.s3, "download", bucket.download):
        report = dependencies.fetch_referenced_files(BUCKET, LOCATION, local_input, local_root, root_key, ".gltf")
    return bucket, report, local_root


@pytest.mark.unit
class TestASingleFileAssetsReferences:
    def test_only_the_companion_folder_is_read(self, tmp_path):
        bucket, report, local_root = _fetch(tmp_path, LOCATION)
        assert bucket.downloads == [COMPANION]
        # The sibling exists, so a fetch confined to the parent directory would have read it.
        assert SIBLING not in bucket.heads
        assert all(key.startswith(LOCATION + "/") for key in bucket.heads)
        # The companion file lands beside the object, as the asset presents them.
        assert os.path.isfile(os.path.join(local_root, "tex.png"))
        # The two references that do not name a file in the folder are left out and reported.
        assert report.rejected == ["../sibling.png"] and report.missing == ["sibling.png"]
        assert sorted(entry.reference for entry in report.skipped) == ["../sibling.png", "sibling.png"]

    def test_a_folder_asset_still_reads_beside_the_input(self, tmp_path):
        # Control: in a folder asset the input's directory is the asset, so its sibling is the asset's
        # file and is read.
        bucket, _, _ = _fetch(tmp_path, "scans/")
        assert SIBLING in bucket.downloads
        assert COMPANION not in bucket.heads

    def test_the_confinement_root_and_the_objects_path_in_it(self):
        assert dependencies.confinement_root(LOCATION, "xid", "/model.gltf", LOCATION) == LOCATION + "/"
        assert dependencies.relative_to_root(LOCATION, LOCATION + "/") == "model.gltf"
        # A file of the companion folder keeps the root it had.
        assert dependencies.confinement_root(COMPANION, "xid", "/tex.png", LOCATION) == LOCATION + "/"

    def test_a_root_recorded_in_another_bucket_is_not_applied(self):
        stage_input = StageInput(bucketName=BUCKET, objectKey=LOCATION, fileExtension=".gltf")
        stage_output = StageOutput(bucketName=BUCKET, objectDir="pipelines/p/j1/output/E1/files/")
        assert core._reference_root_key(stage_input, stage_output, "xid", {
            "relativePath": "/model.gltf", "assetRootS3Key": LOCATION, "assetRootBucket": "other-bucket"}) == "scans/"

    def test_a_reference_into_a_folder_named_like_the_object_is_reported_not_fetched(self, tmp_path):
        # The object sits at its file name locally, so model.gltf/x.png (the companion file the asset
        # lists at /model.gltf/x.png) would need a folder where the object's file is. It is reported as
        # not fetched instead of ending the walk.
        gltf = json.dumps({"asset": {"version": "2.0"}, "images": [{"uri": "tex.png"}, {"uri": "model.gltf/x.png"}]})
        bucket, report, _ = _fetch(tmp_path, LOCATION, gltf, {LOCATION + "/model.gltf/x.png": b"png"})
        assert bucket.downloads == [COMPANION]
        assert report.rejected == ["model.gltf/x.png"]


class _Store:
    """The resolver's store over a dict of key -> bytes, recording every HeadObject."""

    def __init__(self, objects, modified):
        self.objects, self.modified, self.heads = dict(objects), modified, []

    def head(self, key, warn_if_missing=True):
        self.heads.append(key)
        if key not in self.objects:
            return None
        return dependencies.ObjectInfo(len(self.objects[key]), "v1", self.modified)

    def download(self, key, version_id, path):
        with open(path, "wb") as handle:
            handle.write(self.objects[key])
        return True


@pytest.mark.unit
def test_the_late_arrival_check_heads_the_single_file_assets_object(tmp_path):
    # The scene walk (RENDER3D and the Blender renderer) looks again for a required file that is not in
    # the asset yet when the input was written moments ago, reading the input's write time. For a
    # single-file asset's object that is LOCATION itself, not {key}/{name}.
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    gltf = json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": "mesh.bin", "byteLength": 1}]})
    store = _Store({LOCATION: gltf.encode()}, modified=now - timedelta(seconds=10))
    root = dependencies.confinement_root(LOCATION, "xid", "/model.gltf", LOCATION)
    local_root = str(tmp_path / "asset")
    local_input = dependencies.local_path_for(local_root, dependencies.relative_to_root(LOCATION, root))
    os.makedirs(os.path.dirname(local_input), exist_ok=True)
    with open(local_input, "w", encoding="utf-8") as handle:
        handle.write(gltf)
    sleeps = []
    report = dependencies.resolve(store, root, LOCATION, local_input, local_root, ".gltf",
                                  late_arrival=True, sleep=sleeps.append, now=lambda: now)
    assert LOCATION in store.heads and LOCATION + "/model.gltf" not in store.heads
    # The required buffer is looked up in the companion folder, then once more after the wait.
    assert store.heads.count(LOCATION + "/mesh.bin") == 2
    assert report.late_arrival_retry is True and len(sleeps) == 1
