# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``fetch_referenced_files`` records which file named which (``FetchReport.edges``, #1632).

``drop_left_out_references`` leaves a skipped file out of the incomplete-preview summary when only a root
prim the USD handler left out reaches it, and it reads that from ``report.edges``. The shared
``DependencyResolver`` walk fills the report, so it must record every reference a read file names: the
resolved path, or the reference as authored when it was refused, once each. With no edges recorded the
rig's missing files stayed in the summary (#316 D137). Past the edge bound the list is withheld, so a
partial graph never drops a skipped file.
"""

import os
import shutil
from unittest.mock import patch

import pytest

from conftest import requires_real
from preview_pipeline import dependencies

BUCKET = "asset-bucket"


class FakeBucket:
    """S3 HeadObject/GetObject served from a local directory tree keyed like the bucket."""

    def __init__(self, root):
        self.root = str(root)

    def put(self, key, data):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(data)
        return path

    def head_object_size(self, bucket, key, warn_if_missing=True):
        path = os.path.join(self.root, key)
        return os.path.getsize(path) if os.path.isfile(path) else None

    def download(self, bucket, key, path):
        shutil.copyfile(os.path.join(self.root, key), path)
        return path


def _fetch(tmp_path, bucket, input_key, ext, asset_id="xid"):
    root_key = dependencies.asset_root_key(input_key, asset_id)
    local_root = str(tmp_path / "local")
    local_input = dependencies.local_path_for(local_root, dependencies.relative_to_root(input_key, root_key))
    os.makedirs(os.path.dirname(local_input), exist_ok=True)
    shutil.copyfile(os.path.join(bucket.root, input_key), local_input)
    with patch.object(dependencies.s3, "head_object_size", bucket.head_object_size), \
            patch.object(dependencies.s3, "download", bucket.download):
        report = dependencies.fetch_referenced_files(BUCKET, input_key, local_input, local_root, root_key, ext)
    return report, local_input


def _obj_asset(tmp_path):
    bucket = FakeBucket(tmp_path / "bucket")
    bucket.put("xid/model.obj", "mtllib model.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    bucket.put("xid/model.mtl", "newmtl m\nmap_Kd tex/diffuse.png\nmap_Ks tex/diffuse.png\n"
                                "map_Bump ../../outside.png\n")
    bucket.put("xid/tex/diffuse.png", "png")
    return bucket


@pytest.mark.unit
class TestFetchRecordsEdges:
    def test_each_reference_a_read_file_names_is_an_edge(self, tmp_path):
        report, _ = _fetch(tmp_path, _obj_asset(tmp_path), "xid/model.obj", ".obj")
        assert ("model.obj", "model.mtl") in report.edges
        assert ("model.mtl", "tex/diffuse.png") in report.edges

    def test_a_refused_reference_is_an_edge_to_the_reference_as_authored(self, tmp_path):
        report, _ = _fetch(tmp_path, _obj_asset(tmp_path), "xid/model.obj", ".obj")
        refused = [entry.reference for entry in report.skipped if entry.rejected]
        assert refused, "the reference outside the asset is refused"
        assert all(("model.mtl", reference) in report.edges for reference in refused)

    def test_an_edge_named_twice_is_recorded_once(self, tmp_path):
        report, _ = _fetch(tmp_path, _obj_asset(tmp_path), "xid/model.obj", ".obj")
        assert report.edges.count(("model.mtl", "tex/diffuse.png")) == 1
        assert len(report.edges) == len(set(report.edges))

    def test_past_the_edge_bound_no_edges_are_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dependencies, "MAX_GRAPH_EDGES", 1)
        report, _ = _fetch(tmp_path, _obj_asset(tmp_path), "xid/model.obj", ".obj")
        assert report.fetched, "the walk itself still fetches"
        assert report.edges == []


_SCENE = """#usda 1.0
(
    defaultPrim = "World"
)
def Xform "World" ( references = @./model.usda@ )
{
}
def Xform "Tagging" ( payload = @./rig.usda@ )
{
}
"""

_MODEL = """#usda 1.0
def Mesh "Tri"
{
    point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0, 1, 2]
}
"""

_RIG = """#usda 1.0
def Xform "Rig" ( references = @./lights.usda@ )
{
}
"""


@pytest.mark.unit
@requires_real("pxr")
class TestRigOnlyFilesLeaveTheSummary:
    def _asset(self, tmp_path):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/scene.usda", _SCENE)
        bucket.put("xid/model.usda", _MODEL)
        bucket.put("xid/rig.usda", _RIG)
        return bucket

    def test_a_missing_file_only_the_left_out_rig_reaches_is_not_reported(self, tmp_path):
        report, local_input = _fetch(tmp_path, self._asset(tmp_path), "xid/scene.usda", ".usda")
        assert [entry.reference for entry in report.skipped] == ["lights.usda"]
        removed = dependencies.drop_left_out_references(report, "scene.usda", local_input, ["Tagging"])
        assert removed == 1
        assert dependencies.incomplete_preview(report) is None

    def test_a_missing_file_the_default_prim_reaches_is_still_reported(self, tmp_path):
        bucket = self._asset(tmp_path)
        bucket.put("xid/lights.usda", "#usda 1.0\n")
        os.remove(os.path.join(bucket.root, "xid/model.usda"))
        report, local_input = _fetch(tmp_path, bucket, "xid/scene.usda", ".usda")
        assert [entry.reference for entry in report.skipped] == ["model.usda"]
        assert dependencies.drop_left_out_references(report, "scene.usda", local_input, ["Tagging"]) == 0
        assert dependencies.incomplete_preview(report) is not None
