# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Points with a NaN or infinite coordinate do not turn the BLENDER branch's geometry into NaN (D107).

The 3D thumbnail's mesh handler drops such points from the point clouds a glTF/GLB scene, a
vertex-only OBJ or a faceless Draco file carries (#1460), and this branch's RENDER3D files run in that
image. The glTF, GLB and OBJ files run in the Blender branch instead, which measured them with trimesh's
scene bounds: one NaN point in a scene's point geometry made every `sys_geometry` value NaN (bounds,
dimensions, extent, centroid), which the attribute file then wrote as the JSON-invalid `NaN` and the
metadata catalogue promoted. The camera framing had the same exposure through `combine_bounds`, whose
`min`/`max` keep a NaN that comes first. Both now measure the finite points only; the facts of a file
without non-finite points do not change.
"""

import json
import math

import pytest

from test_blender_render_scene_dispatch import exec_named_nodes

trimesh = pytest.importorskip("trimesh")
np = pytest.importorskip("numpy")

import meshAttributes  # noqa: E402

NAN = float("nan")


def _scene_glb(tmp_path, points):
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(extents=[1.0, 1.0, 1.0]), node_name="box", geom_name="box")
    scene.add_geometry(trimesh.PointCloud(np.array(points, dtype=float)), node_name="pts", geom_name="pts")
    path = str(tmp_path / "scene.glb")
    scene.export(path)
    return path


def _finite(value):
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


@pytest.mark.unit
class TestTheTrimeshMeasurement:

    def test_a_nan_point_in_a_scene_point_cloud_leaves_the_geometry_finite(self, tmp_path):
        path = _scene_glb(tmp_path, [[0, 0, 0], [NAN, 1, 1], [2, 2, 2], [math.inf, 0, 0]])
        attributes, warnings = meshAttributes.compute_trimesh_attributes(path, ".glb")
        assert warnings == []
        geometry = attributes["sys_geometry"]
        assert _finite(geometry), geometry
        assert geometry["boundsMin"] == [-0.5, -0.5, -0.5]
        assert geometry["boundsMax"] == [2.0, 2.0, 2.0]
        assert geometry["extentMax"] == 2.5
        # The attribute file carries valid JSON.
        json.loads(json.dumps(geometry, allow_nan=False))

    def test_a_scene_without_non_finite_points_measures_as_before(self, tmp_path):
        path = _scene_glb(tmp_path, [[0, 0, 0], [2, 2, 2]])
        loaded = trimesh.load(path, force=None)
        attributes, _ = meshAttributes.compute_trimesh_attributes(path, ".glb")
        assert attributes["sys_geometry"]["boundsMin"] == [round(float(v), 6) for v in loaded.bounds[0]]
        assert attributes["sys_geometry"]["boundsMax"] == [round(float(v), 6) for v in loaded.bounds[1]]

    def test_a_scene_whose_only_points_are_non_finite_measures_its_meshes(self, tmp_path):
        path = _scene_glb(tmp_path, [[NAN, NAN, NAN]])
        attributes, warnings = meshAttributes.compute_trimesh_attributes(path, ".glb")
        assert warnings == []
        assert attributes["sys_geometry"]["boundsMin"] == [-0.5, -0.5, -0.5]
        assert attributes["sys_geometry"]["boundsMax"] == [0.5, 0.5, 0.5]


_FRAMING_NAMES = ["combine_bounds", "BACKDROP_FLAT_RATIO", "BACKDROP_SHEET_RATIO", "BACKDROP_SCALE",
                  "BACKDROP_CENTRE_SHARE", "backdrop_mask", "framing_bounds"]


@pytest.fixture
def framing():
    return exec_named_nodes(_FRAMING_NAMES, {"math": math})


def _cube(center, half=0.5):
    cx, cy, cz = center
    return [(cx + sx * half, cy + sy * half, cz + sz * half) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]


@pytest.mark.unit
class TestTheCameraFraming:

    def test_a_non_finite_corner_first_does_not_make_the_frame_nan(self, framing):
        center, size = framing["combine_bounds"]([(NAN, 0.0, 0.0), (0.0, math.inf, 0.0)] + _cube((0, 0, 0)))
        assert center == (0.0, 0.0, 0.0)
        assert size == (1.0, 1.0, 1.0)

    def test_only_non_finite_corners_have_nothing_to_frame(self, framing):
        with pytest.raises(ValueError):
            framing["combine_bounds"]([(NAN, NAN, NAN)])

    def test_a_box_with_a_non_finite_bound_is_left_out_of_the_framing(self, framing):
        boxes = [((NAN, 0.0, 0.0), (1.0, 1.0, 1.0)), ((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))]
        center, size = framing["framing_bounds"](boxes)
        assert (center, size) == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
