#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Files an input references are fetched from its own asset, and only from there (#464).

The container used to download only the input file (plus a .gltf's buffers and images), so a USD
stage that composes other layers rendered empty and an OBJ rendered without its materials. One
resolver now serves glTF, USD, OBJ/MTL and FBX. Every reference is untrusted (ARCC BSC1): it is
accepted only as a relative path that stays inside the input's asset prefix, and the fetch is bounded
by a file count, a byte total checked with HeadObject and free disk, and a recursion depth. A
rejected or missing reference is skipped and never fails the run.

The USD and OBJ render cases need the real pxr/pyvista/trimesh and run inside the pipeline image.
"""

import os
import shutil
import struct
from unittest.mock import patch

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import dependencies

BUCKET = "asset-bucket"


class FakeBucket:
    """S3 HeadObject/GetObject served from a local directory tree keyed like the bucket."""

    def __init__(self, root):
        self.root = str(root)
        self.downloads = []
        self.heads = []

    def put(self, key, data):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if isinstance(data, bytes) else "w") as handle:
            handle.write(data)
        return path

    def head_object_size(self, bucket, key):
        self.heads.append(key)
        path = os.path.join(self.root, key)
        return os.path.getsize(path) if os.path.isfile(path) else None

    def download(self, bucket, key, path):
        self.downloads.append(key)
        shutil.copyfile(os.path.join(self.root, key), path)
        return path


def _fetch(tmp_path, bucket, input_key, ext, asset_id="xid", limits=None, max_splats=None):
    root_key = dependencies.asset_root_key(input_key, asset_id)
    local_root = str(tmp_path / "local")
    local_input = dependencies.local_path_for(
        local_root, dependencies.relative_to_root(input_key, root_key))
    os.makedirs(os.path.dirname(local_input), exist_ok=True)
    shutil.copyfile(os.path.join(bucket.root, input_key), local_input)
    with patch.object(dependencies.s3, "head_object_size", bucket.head_object_size), \
            patch.object(dependencies.s3, "download", bucket.download):
        report = dependencies.fetch_referenced_files(BUCKET, input_key, local_input, local_root,
                                                     root_key, ext, limits=limits, max_splats=max_splats)
    return report, local_root, local_input


@pytest.mark.unit
class TestResolveReference:
    @pytest.mark.parametrize("reference", [
        "/abs/tex.png", "//server/share/tex.png", "../../other-asset/tex.png", "..", "",
        "s3://other-bucket/key.usd", "HTTPS://example.com/tex.png", "file:///etc/passwd",
        "C:\\textures\\wood.png", "c:/textures/wood.png", "anon:0x1234", "omniverse://host/x.usd",
        "tex\x00.png", "tex\n.png", "tex\x7f.png", "`if(${X}, a.usd)`", "a/" * 600 + "x.png",
    ])
    def test_rejected(self, reference):
        assert dependencies.resolve_reference(reference, "models") is None

    @pytest.mark.parametrize("reference", ["%2e%2e%2f%2e%2e%2fx", "%2F%2Fx", "..%2f..%2fx"])
    def test_a_percent_encoded_escape_is_rejected_where_references_are_uris(self, reference):
        assert dependencies.resolve_reference(reference, "models", percent_encoded=True) is None

    def test_percent_signs_are_literal_where_references_are_paths(self):
        """A USD, OBJ or FBX path is not a URI: ``%2e%2e`` is a directory of that name."""
        assert dependencies.resolve_reference("%2e%2e/x.png", "models") == "models/%2e%2e/x.png"

    @pytest.mark.parametrize("reference,expected", [
        ("tex/wood.png", "models/tex/wood.png"),
        ("./parts/part.usda", "models/parts/part.usda"),
        ("../shared/wood.png", "shared/wood.png"),
        ("tex\\wood.png", "models/tex/wood.png"),
        ("  my texture.png ", "models/my texture.png"),
    ])
    def test_accepted_inside_the_asset(self, reference, expected):
        assert dependencies.resolve_reference(reference, "models") == expected


@pytest.mark.unit
class TestAssetRoot:
    def test_an_original_asset_file_is_confined_to_its_asset(self):
        assert dependencies.asset_root_key("xid/models/a.usda", "xid") == "xid/"
        assert dependencies.asset_root_key("custom/base/xid/a/b.obj", "xid") == "custom/base/xid/"

    def test_a_workflow_output_is_confined_to_the_run_output_folder(self):
        out = "pipelines/p/job/output/e1/files/"
        assert dependencies.asset_root_key(out + "a/b.gltf", "xid", out, same_bucket_output=True) == out
        # In another bucket the output folder is not the input's prefix: the input's directory is.
        assert dependencies.asset_root_key(out + "a/b.gltf", "", out) == out + "a/"

    def test_without_an_asset_id_the_input_directory_is_the_root(self):
        assert dependencies.asset_root_key("xid/models/a.usda") == "xid/models/"
        assert dependencies.asset_root_key("a.usda") == ""

    def test_the_asset_id_must_be_a_directory_segment(self):
        assert dependencies.asset_root_key("other/xid", "xid") == "other/"


OBJ = "mtllib ../materials/model.mtl extra.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\n" \
      "usemtl wood\nf 1/1 2/2 3/3\n"
MTL = ("newmtl wood\nKd 1 1 1\nmap_Kd -s 1 1 1 -o 0 0 tex/wood.png\nmap_Bump -bm 0.5 bump.png\n"
       "map_d /etc/shadow\nmap_Ka ../../other-asset/secret.png\n")


@pytest.mark.unit
class TestFetchObj:
    def _bucket(self, tmp_path):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/models/model.obj", OBJ)
        bucket.put("xid/materials/model.mtl", MTL)
        bucket.put("xid/materials/tex/wood.png", b"png")
        bucket.put("xid/materials/bump.png", b"png")
        bucket.put("other-asset/secret.png", b"secret")
        return bucket

    def test_material_libraries_and_their_textures_are_fetched_beside_the_input(self, tmp_path):
        bucket = self._bucket(tmp_path)
        report, local_root, _ = _fetch(tmp_path, bucket, "xid/models/model.obj", ".obj")
        assert sorted(report.fetched) == ["materials/bump.png", "materials/model.mtl",
                                          "materials/tex/wood.png"]
        assert report.missing == ["models/extra.mtl"]
        assert sorted(report.rejected) == ["../../other-asset/secret.png", "/etc/shadow"]
        assert all(key.startswith("xid/") for key in bucket.downloads + bucket.heads)
        assert os.path.isfile(os.path.join(local_root, "materials", "tex", "wood.png"))

    def test_the_file_count_bound(self, tmp_path):
        bucket = self._bucket(tmp_path)
        report, _, _ = _fetch(tmp_path, bucket, "xid/models/model.obj", ".obj",
                              limits=dependencies.FetchLimits(max_files=2))
        assert len(report.fetched) == 2
        assert report.skipped_for_budget

    def test_the_byte_bound(self, tmp_path):
        bucket = self._bucket(tmp_path)
        report, _, _ = _fetch(tmp_path, bucket, "xid/models/model.obj", ".obj",
                              limits=dependencies.FetchLimits(max_total_bytes=len(MTL) + 3))
        assert report.total_bytes <= len(MTL) + 3
        assert "materials/model.mtl" in report.fetched
        assert report.skipped_for_budget

    def test_the_free_disk_bound(self, tmp_path):
        bucket = self._bucket(tmp_path)
        with patch.object(dependencies.shutil, "disk_usage",
                          lambda path: shutil._ntuple_diskusage(10, 10, 0)):
            report, _, _ = _fetch(tmp_path, bucket, "xid/models/model.obj", ".obj")
        assert report.fetched == []
        assert report.skipped_for_budget == ["materials/model.mtl"]

    def test_the_depth_bound(self, tmp_path, monkeypatch):
        bucket = FakeBucket(tmp_path / "bucket")
        for index in range(6):
            bucket.put(f"xid/c{index}.chain", f"c{index + 1}.chain")
        monkeypatch.setitem(dependencies._COLLECTORS, ".chain",
                            lambda path: [(open(path).read(), False)])
        report, _, _ = _fetch(tmp_path, bucket, "xid/c0.chain", ".chain",
                              limits=dependencies.FetchLimits(max_depth=2))
        assert report.fetched == ["c1.chain", "c2.chain", "c3.chain"]

    def test_an_unreadable_reference_list_does_not_fail(self, tmp_path):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/broken.gltf", "{not json")
        report, _, _ = _fetch(tmp_path, bucket, "xid/broken.gltf", ".gltf")
        assert report.fetched == [] and report.rejected == []


@pytest.mark.unit
class TestMtlTextureStatements:
    @pytest.mark.parametrize("line,name", [
        ("map_Kd wood.png", "wood.png"),
        ("map_Kd -s 1 1 1 wood.png", "wood.png"),
        ("map_Kd -o 0.5 0.5 -clamp on my wood.png", "my wood.png"),
        ("bump -bm 0.2 -imfchan l bump.tga", "bump.tga"),
        ("map_d -mm 0 1 alpha.png", "alpha.png"),
    ])
    def test_options_are_skipped(self, tmp_path, line, name):
        path = tmp_path / "m.mtl"
        path.write_text(line + "\n")
        assert dependencies._mtl_references(str(path)) == [(name, False)]


@pytest.mark.unit
class TestFbxTextureNames:
    def test_relative_names_and_the_bare_name_of_an_absolute_one(self, tmp_path):
        def record(text):  # a binary FBX string property: 'S', uint32 length, bytes
            return b"S" + struct.pack("<I", len(text)) + text

        path = tmp_path / "m.fbx"
        path.write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00" + b"\x00\x05\x00\x00\x00"
                         + record(b"C:\\Users\\artist\\maps\\Wood.PNG") + b"\x00\x00"
                         + record(b"maps/Wood.PNG") + record(b"Amaps/Wood.png") + b"\x00\x01")
        names = [name for name, _ in dependencies._fbx_references(str(path))]
        assert "maps/Wood.PNG" in names and "Wood.PNG" in names
        resolved = {dependencies.resolve_reference(name, "") for name in names}
        assert {"maps/Wood.PNG", "Wood.PNG"} <= resolved - {None}
        assert "C:/Users/artist/maps/Wood.PNG" not in resolved


# --------------------------------------------------------------------------------------------------
# Real loaders (pipeline image)
# --------------------------------------------------------------------------------------------------

def _checker_png(path):
    from PIL import Image

    tile = np.kron([[1, 0] * 4, [0, 1] * 4] * 4, np.ones((8, 8)))
    rgb = np.stack([tile * 255, tile * 64, (1 - tile) * 255], axis=-1).astype(np.uint8)
    Image.fromarray(rgb).save(path)


_USD_ROOT = """#usda 1.0
(
    subLayers = [@./layers/sub.usda@]
    upAxis = "Y"
)
def Xform "World"
{
    def Xform "Referenced" (references = @./parts/part.usda@)
    {
        double3 xformOp:translate = (10, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
    def Xform "Payloaded" (payload = @parts/payload.usda@)
    {
        double3 xformOp:translate = (-10, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
    def Xform "Outside" (references = @../../other-asset/secret.usda@)
    {
    }
}
"""
_USD_TRIANGLE = """#usda 1.0
(
    defaultPrim = "Part"
)
def Xform "Part"
{
    def Mesh "Tri"
    {
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        asset info:texture = @../tex/checker.png@
    }
}
"""
_USD_SUB = """#usda 1.0
def Mesh "FromSublayer"
{
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0, 1, 2]
    point3f[] points = [(0, 5, 0), (1, 5, 0), (0, 6, 0)]
}
"""


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestComposedUsdStage:
    def test_sublayer_reference_payload_and_texture_are_fetched_and_render(self, tmp_path):
        from preview_pipeline.format_handlers import usd_handler

        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/scene/root.usda", _USD_ROOT)
        bucket.put("xid/scene/layers/sub.usda", _USD_SUB)
        bucket.put("xid/scene/parts/part.usda", _USD_TRIANGLE)
        bucket.put("xid/scene/parts/payload.usda", _USD_TRIANGLE)
        os.makedirs(os.path.join(bucket.root, "xid/scene/tex"), exist_ok=True)
        _checker_png(os.path.join(bucket.root, "xid/scene/tex/checker.png"))
        bucket.put("other-asset/secret.usda", _USD_SUB)

        report, _, local_input = _fetch(tmp_path, bucket, "xid/scene/root.usda", ".usda")
        assert sorted(report.fetched) == ["scene/layers/sub.usda", "scene/parts/part.usda",
                                          "scene/parts/payload.usda", "scene/tex/checker.png"]
        assert report.rejected == ["../../other-asset/secret.usda"]
        assert not any(key.startswith("other-asset/") for key in bucket.downloads)

        data = usd_handler.load(local_input)
        xs = np.asarray(data.points)[:, 0]
        # The referenced part sits at x=+10, the payload at x=-10 and the sublayer mesh near 0.
        assert xs.max() > 9 and xs.min() < -9 and np.any(np.abs(xs) < 2)


@pytest.mark.unit
@requires_real("trimesh", "pyvista", "vtk", "PIL")
class TestObjWithMaterial:
    def test_the_fetched_material_and_texture_colour_the_mesh(self, tmp_path):
        from preview_pipeline.format_handlers import mesh_handler

        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/models/model.obj", OBJ.replace("../materials/model.mtl extra.mtl", "model.mtl"))
        bucket.put("xid/models/model.mtl", "newmtl wood\nKd 1 1 1\nmap_Kd tex/wood.png\n")
        os.makedirs(os.path.join(bucket.root, "xid/models/tex"), exist_ok=True)
        _checker_png(os.path.join(bucket.root, "xid/models/tex/wood.png"))

        report, _, local_input = _fetch(tmp_path, bucket, "xid/models/model.obj", ".obj")
        assert sorted(report.fetched) == ["models/model.mtl", "models/tex/wood.png"]

        data = mesh_handler.load(local_input)
        textured = getattr(data, "_preview_texture", None) is not None
        coloured = "RGB" in data.point_data and len(np.unique(data.point_data["RGB"], axis=0)) > 1
        assert textured or coloured, "the OBJ rendered without its material"


def _lcc2_meta(files, levels, counts=True, extra=None):
    """An LCC2 description: ``levels`` maps a level (0 = finest) to its chunk file indices."""
    total = max(levels) + 1

    def node(level, index):
        gs = {"name": index, "start": 0}
        if counts:
            gs["count"] = 1000 * (level + 1)
        return {"data": {"3dgs": gs}, "childNum": 0}

    # A level-L node sits at depth total - L below the root.
    root = {"splatFiles": files, "childNum": 0}
    parents = [root]
    for depth in range(1, total + 1):
        level = total - depth
        children = []
        for parent in parents:
            parent["child"] = {}
            for slot, index in enumerate(levels.get(level, [])):
                child = node(level, index)
                parent["child"][str(slot)] = child
                children.append(child)
        parents = children or parents
    meta = {"version": "0.0.3", "totalLevels": total, "root": root,
            "totalSplats": 0, "lodSplats": [1000 * (lv + 1) * len(levels.get(lv, [])) for lv in range(total)]}
    meta.update(extra or {})
    return meta


@pytest.mark.unit
class TestFetchLcc2:
    def test_only_the_chosen_level_is_fetched(self, tmp_path):
        import json

        bucket = FakeBucket(tmp_path / "bucket")
        files = ["data/3dgs/l0a.sog", "data/3dgs/l0b.sog", "data/3dgs/l1.sog"]
        bucket.put("xid/scan/scene.lcc2", json.dumps(_lcc2_meta(files, {0: [0, 1], 1: [2]})))
        for name in files:
            bucket.put(f"xid/scan/{name}", b"chunk")
        # LOD 0 holds 2,000 splats: within twice a budget of 1,000 -> LOD 0.
        report, local_root, _ = _fetch(tmp_path, bucket, "xid/scan/scene.lcc2", ".lcc2", max_splats=1000)
        assert sorted(report.fetched) == ["scan/data/3dgs/l0a.sog", "scan/data/3dgs/l0b.sog"]
        assert os.path.isfile(os.path.join(local_root, "scan", "data", "3dgs", "l0a.sog"))
        # A budget of 500 allows 1,000 splats: LOD 0 (2,000) is too many, LOD 1 (2,000 x 1) too ->
        # the coarsest level is read.
        shutil.rmtree(local_root)
        report, _, _ = _fetch(tmp_path, bucket, "xid/scan/scene.lcc2", ".lcc2", max_splats=500)
        assert report.fetched == ["scan/data/3dgs/l1.sog"]

    def test_names_outside_the_asset_are_not_fetched(self, tmp_path):
        import json

        bucket = FakeBucket(tmp_path / "bucket")
        files = ["../../other/secret.sog", "s3://other-bucket/a.sog", "data/3dgs/ok.sog"]
        bucket.put("xid/scene.lcc2", json.dumps(_lcc2_meta(files, {0: [0, 1, 2]})))
        bucket.put("xid/data/3dgs/ok.sog", b"chunk")
        bucket.put("other/secret.sog", b"secret")
        report, _, _ = _fetch(tmp_path, bucket, "xid/scene.lcc2", ".lcc2", max_splats=10 ** 6)
        assert report.fetched == ["data/3dgs/ok.sog"]
        assert sorted(report.rejected) == sorted(files[:2])
        assert "other/secret.sog" not in bucket.downloads

    def test_an_unreadable_description_fetches_nothing(self, tmp_path):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/scene.lcc2", "{not json")
        report, _, _ = _fetch(tmp_path, bucket, "xid/scene.lcc2", ".lcc2")
        assert report.fetched == [] and report.rejected == []
