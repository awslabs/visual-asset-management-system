#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A conversion that would write an empty model fails instead of reporting success (#508).

trimesh writes a point cloud (an ``.xyz`` file, or a ``.ply`` with vertices and no faces) to STL as a
header and a zero triangle count -- an 84-byte file -- and raises nothing. STL stores triangles only,
so the run is rejected with a message naming the formats that do store points. OBJ, PLY, GLB and GLTF
keep the points and are unaffected. An input that loads no geometry at all is rejected for every
target.

The geometry objects here are plain stand-ins with the attributes trimesh's Trimesh, PointCloud and
Scene expose; ``test_3dbasic_real_conversions.py`` drives the same check with trimesh itself.
"""

import os
import sys
import types
import importlib.util
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "trimesh" not in sys.modules:
    sys.modules["trimesh"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


def _load():
    spec = importlib.util.spec_from_file_location(
        "trimesh_pipeline_lambda_point_clouds", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _mesh(vertices=3, faces=1):
    return SimpleNamespace(vertices=[[0, 0, 0]] * vertices, faces=[[0, 1, 2]] * faces)


def _points(count=50):
    return SimpleNamespace(vertices=[[0, 0, 0]] * count)


def _scene(*parts):
    return SimpleNamespace(geometry={f"g{i}": part for i, part in enumerate(parts)})


@pytest.mark.unit
class TestGeometryCounts:
    def test_a_mesh(self):
        assert _load().geometry_counts(_mesh(8, 12)) == (8, 12)

    def test_a_point_cloud_has_no_triangles(self):
        assert _load().geometry_counts(_points(100)) == (100, 0)

    def test_a_scene_sums_its_geometry(self):
        assert _load().geometry_counts(_scene(_mesh(8, 12), _points(20))) == (28, 12)

    def test_an_empty_scene(self):
        assert _load().geometry_counts(_scene()) == (0, 0)


@pytest.mark.unit
class TestCheckExportable:
    def test_the_mesh_only_targets_are_stl(self):
        mod = _load()
        assert mod.MESH_ONLY_OUTPUT_FORMATS == {".stl"}
        assert mod.MESH_ONLY_OUTPUT_FORMATS <= mod.SUPPORTED_OUTPUT_FORMATS

    @pytest.mark.parametrize("loaded", [_points(), _scene(_points())])
    def test_a_point_cloud_to_stl_is_rejected(self, loaded):
        with pytest.raises(ValueError, match=r"cloud\.xyz contains no triangles .* \.stl stores triangle"):
            _load().check_exportable(loaded, ".stl", "cloud.xyz")

    @pytest.mark.parametrize("target", [".ply", ".obj", ".glb", ".gltf"])
    def test_a_point_cloud_to_a_point_capable_target_is_allowed(self, target):
        _load().check_exportable(_points(), target, "cloud.xyz")

    def test_a_scene_with_any_triangles_still_goes_to_stl(self):
        _load().check_exportable(_scene(_mesh(), _points()), ".stl", "mixed.glb")

    @pytest.mark.parametrize("target", [".stl", ".ply", ".glb"])
    def test_an_input_with_no_geometry_is_rejected_for_every_target(self, target):
        with pytest.raises(ValueError, match="contains no geometry"):
            _load().check_exportable(_scene(), target, "empty.glb")


@pytest.mark.unit
class TestTheHandlerAppliesTheCheckBeforeExporting:
    def _convert(self, mod, loaded, input_key, output_filetype):
        """Run one conversion with ``loaded`` as what trimesh returns; report export and upload calls."""
        loaded.export = MagicMock(side_effect=lambda path, file_type=None, **kw: open(path, "w").close())
        trimesh_stub = MagicMock()
        trimesh_stub.load.return_value = loaded
        upload = MagicMock()
        with patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p: open(p, "w").close())), \
                patch.object(mod, "uploadV2", upload), \
                patch.object(mod, "trimesh", trimesh_stub):
            mod.convert_input_output(f"s3://abkt/{input_key}", "s3://obkt/files/", output_filetype)
        return loaded.export, upload

    def test_xyz_to_stl_fails_without_exporting_or_uploading(self):
        mod = _load()
        loaded = _points()
        with pytest.raises(ValueError, match="contains no triangles"):
            self._convert(mod, loaded, "xid/cloud.xyz", ".stl")
        loaded.export.assert_not_called()

    def test_xyz_to_ply_still_converts(self):
        # The positive control: the check narrows STL only.
        mod = _load()
        export, upload = self._convert(mod, _points(), "xid/cloud.xyz", ".ply")
        export.assert_called_once()
        assert [call.args[1] for call in upload.call_args_list] == ["files/cloud.ply"]
