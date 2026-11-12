#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Framing a model that sits on a large ground or environment plane.

A scene of a robot about 1 m across on a 50 m ground plane framed the whole plane, so the robot
rendered as a dot. The camera now leaves a backdrop out of its framing: a near-flat sheet whose
largest extent is at least ``scene_parts.BACKDROP_SCALE`` times the rest of the scene. The backdrop
is still drawn. A scene with no such part frames exactly as before, and so does a scene made only of
flat parts (a sign, a floor plan).

The rule tests run anywhere; the loading and render tests need the real pyvista/VTK/trimesh/pxr and
run inside the pipeline image (or a venv holding its requirements).
"""

import numpy as np
import pytest

from conftest import colour_shares, requires_real
from preview_pipeline import scene_parts


def _box(lo, hi):
    return list(lo) + list(hi)


# A 1 x 0.7 x 0.6 model standing on a 50 x 0 x 50 plane centred under it.
MODEL = _box((-0.5, 0.0, -0.3), (0.5, 0.7, 0.3))
GROUND = _box((-25.0, 0.0, -25.0), (25.0, 0.0, 25.0))


@pytest.mark.unit
class TestBackdropRule:
    def test_a_large_ground_plane_under_a_small_model_is_a_backdrop(self):
        assert scene_parts.backdrop_parts([MODEL, GROUND]).tolist() == [False, True]

    def test_a_slab_of_some_thickness_is_still_a_backdrop(self):
        slab = _box((-25.0, -0.1, -25.0), (25.0, 0.0, 25.0))
        assert scene_parts.backdrop_parts([MODEL, slab]).tolist() == [False, True]

    def test_a_scene_of_flat_parts_only_has_no_backdrop(self):
        """A sign, a board or a floor plan: every part is flat, so nothing is left out."""
        sign = _box((0.0, 0.0, 0.0), (2.0, 1.0, 0.0))
        floor = _box((-25.0, 0.0, -25.0), (25.0, 0.0, 25.0))
        assert not scene_parts.backdrop_parts([sign, floor]).any()

    def test_a_plane_not_much_larger_than_the_model_is_kept(self):
        """A floor the furniture spans (a room) is part of the model."""
        furniture = _box((-4.0, 0.0, -3.0), (4.0, 2.5, 3.0))
        floor = _box((-5.0, 0.0, -4.0), (5.0, 0.0, 4.0))
        assert not scene_parts.backdrop_parts([furniture, floor]).any()

    def test_a_circuit_board_is_not_near_flat(self):
        """1.6 mm on 100 mm is thicker than a sheet, even with one small part on it."""
        board = _box((0.0, 0.0, 0.0), (100.0, 1.6, 100.0))
        chip = _box((10.0, 1.6, 10.0), (14.0, 3.0, 14.0))
        assert not scene_parts.backdrop_parts([board, chip]).any()

    def test_a_long_rod_is_not_a_sheet(self):
        rod = _box((-25.0, 0.0, 0.0), (25.0, 0.01, 0.01))
        assert not scene_parts.backdrop_parts([MODEL, rod]).any()

    def test_points_and_empty_parts_are_neither_backdrops_nor_the_rest(self):
        point = _box((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
        assert scene_parts.backdrop_parts([point, GROUND]).tolist() == [False, False]
        assert scene_parts.backdrop_parts([MODEL, point, GROUND]).tolist() == [False, False, True]

    def test_one_part_has_no_backdrop(self):
        assert not scene_parts.backdrop_parts([GROUND]).any()


def _merged(*boxes, colours=None):
    """One PolyData merged from triangulated pv.Box parts, with the part bounds recorded."""
    import pyvista as pv

    parts = []
    for index, bounds in enumerate(boxes):
        lo, hi = bounds[:3], bounds[3:]
        part = pv.Box(bounds=(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]), level=2).triangulate()
        part = pv.PolyData(np.asarray(part.points), np.asarray(part.faces))
        if colours is not None:
            part.point_data["RGB"] = np.tile(np.asarray(colours[index], np.uint8), (part.n_points, 1))
        parts.append(part)
    merged = parts[0].append_polydata(*parts[1:])
    return scene_parts.record_parts(merged, [part.n_points for part in parts])


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestFraming:
    def test_record_parts_stores_each_parts_bounds(self):
        data = _merged(MODEL, GROUND)
        np.testing.assert_allclose(scene_parts.part_bounds(data), [MODEL, GROUND], atol=1e-9)

    def test_counts_that_do_not_match_record_nothing(self):
        import pyvista as pv

        data = pv.Box().triangulate()
        scene_parts.record_parts(data, [1, 2])
        assert scene_parts.part_bounds(data) is None
        scene_parts.record_parts(data, [data.n_points])
        assert scene_parts.part_bounds(data) is None

    def test_the_camera_frames_the_model_not_the_ground_plane(self):
        from preview_pipeline import renderer

        model_only = _merged(MODEL, _box((0, 0, 0), (0.01, 0.01, 0.01)))
        _, model_radius, _ = renderer._compute_camera_framing(model_only, use_full_bounds=True)
        focal, radius, _ = renderer._compute_camera_framing(_merged(MODEL, GROUND), use_full_bounds=True)
        assert radius == pytest.approx(model_radius, rel=0.05)
        np.testing.assert_allclose(focal, [0.0, 0.35, 0.0], atol=1e-6)

    def test_the_backdrop_is_logged(self, caplog):
        from preview_pipeline import renderer

        with caplog.at_level("INFO"):
            renderer._compute_camera_framing(_merged(MODEL, GROUND), use_full_bounds=True)
        assert "leaves out 1 of 2 parts as a backdrop" in caplog.text
        assert "(50.00, 0.00, 50.00)" in caplog.text

    def test_with_no_backdrop_the_framing_is_unchanged(self):
        """The same data with and without recorded parts frames identically when no part qualifies,
        and percentile framing never looks at the parts."""
        from preview_pipeline import renderer

        furniture = _box((-4.0, 0.0, -3.0), (4.0, 2.5, 3.0))
        floor = _box((-5.0, 0.0, -4.0), (5.0, 0.0, 4.0))
        for boxes in ((furniture, floor), (MODEL, MODEL), (GROUND, _box((0, 0, 0), (2, 1, 0)))):
            data = _merged(*boxes)
            plain = data.copy()
            plain.field_data.clear()
            assert scene_parts.part_bounds(plain) is None
            for full in (True, False):
                with_parts = renderer._compute_camera_framing(data, use_full_bounds=full)
                without = renderer._compute_camera_framing(plain, use_full_bounds=full)
                assert np.array_equal(with_parts[0], without[0]) and with_parts[1:] == without[1:]
        ground = _merged(MODEL, GROUND)
        plain = ground.copy()
        plain.field_data.clear()
        with_parts = renderer._compute_camera_framing(ground, use_full_bounds=False)
        without = renderer._compute_camera_framing(plain, use_full_bounds=False)
        assert np.array_equal(with_parts[0], without[0]) and with_parts[1:] == without[1:]

    def test_the_z_up_rotation_moves_the_part_bounds_with_the_points(self):
        from preview_pipeline import core

        z_up_model = _box((-0.5, -0.3, 0.0), (0.5, 0.3, 0.7))
        z_up_ground = _box((-25.0, -25.0, 0.0), (25.0, 25.0, 0.0))
        merged = _merged(z_up_model, z_up_ground)
        n_model = merged.n_points // 2  # both boxes are tessellated alike
        data = core._rotate_z_up_to_y_up(merged)
        bounds = scene_parts.part_bounds(data)
        np.testing.assert_allclose(bounds[0], MODEL, atol=1e-9)
        np.testing.assert_allclose(bounds[1], GROUND, atol=1e-9)
        # The rotated bounds are those of the rotated points of each part.
        points = np.asarray(data.points)
        np.testing.assert_allclose(bounds[0], [*points[:n_model].min(axis=0), *points[:n_model].max(axis=0)])

    def test_the_triangle_budget_keeps_the_part_bounds(self):
        import types

        from preview_pipeline import geometry_budget

        data = _merged(MODEL, GROUND)
        budgets = types.SimpleNamespace(points=10 ** 6, triangles=data.n_cells // 4,
                                        memory_bytes=4 * 1024 ** 3)
        reduced = geometry_budget.apply_budgets(data, budgets)
        assert reduced is not data and reduced.n_cells <= data.n_cells // 4
        np.testing.assert_allclose(scene_parts.part_bounds(reduced), [MODEL, GROUND])


MODEL_RGB = (230, 40, 40)
GROUND_RGB = (40, 40, 230)


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestRender:
    def test_the_model_fills_the_preview_and_the_ground_is_still_drawn(self):
        from preview_pipeline import renderer

        data = _merged(MODEL, GROUND, colours=[MODEL_RGB, GROUND_RGB])
        frame = renderer.generate_static_frame(data, resolution=(200, 150))
        model_share, ground_share = colour_shares(frame, [MODEL_RGB, GROUND_RGB])
        # Framed on the whole plane the model covered well under 1% of the frame.
        assert model_share > 0.1
        assert ground_share > 0.1


_PLANE_AND_CUBE_USDA = """
def Xform "World"
{
    def Mesh "GroundPlane"
    {
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        point3f[] points = [(-25, -25, 0), (25, -25, 0), (25, 25, 0), (-25, 25, 0)]
    }
    def Cube "Robot"
    {
        double size = 1
        double3 xformOp:translate = (0, 0, 0.5)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""


@pytest.mark.unit
@requires_real("pyvista", "vtk", "pxr")
class TestUsdStage:
    def test_a_z_up_stage_with_a_ground_plane_frames_the_robot(self, tmp_path):
        from preview_pipeline import renderer
        from preview_pipeline.format_handlers import usd_handler

        path = tmp_path / "robot.usda"
        path.write_text(f'#usda 1.0\n(\n    upAxis = "Z"\n)\n{_PLANE_AND_CUBE_USDA}\n')
        data = usd_handler.load(str(path))
        np.testing.assert_allclose(scene_parts.part_bounds(data),
                                   [[-25, 0, -25, 25, 0, 25], [-0.5, 0, -0.5, 0.5, 1, 0.5]], atol=1e-6)
        focal, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=True)
        np.testing.assert_allclose(focal, [0.0, 0.5, 0.0], atol=1e-6)
        assert radius < 5.0

    def test_a_single_prim_stage_records_no_parts(self, tmp_path):
        from preview_pipeline.format_handlers import usd_handler

        path = tmp_path / "plane.usda"
        path.write_text('#usda 1.0\n(\n    upAxis = "Y"\n)\ndef Cube "Only"\n{\n    double size = 2\n}\n')
        assert scene_parts.part_bounds(usd_handler.load(str(path))) is None


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestGlbScene:
    def test_a_glb_scene_with_a_ground_plane_frames_the_model(self, tmp_path):
        import trimesh

        from preview_pipeline import renderer
        from preview_pipeline.format_handlers import mesh_handler

        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(extents=(1.0, 0.7, 0.6)), geom_name="model")
        plane = trimesh.Trimesh(vertices=[[-25, 0, -25], [25, 0, -25], [25, 0, 25], [-25, 0, 25]],
                                faces=[[0, 2, 1], [0, 3, 2]])
        scene.add_geometry(plane, geom_name="ground")
        path = tmp_path / "scene.glb"
        scene.export(str(path))
        data = mesh_handler.load(str(path))
        assert scene_parts.part_bounds(data) is not None
        _, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=True)
        assert radius < 5.0
