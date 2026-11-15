#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Framing a model that sits on a large ground or environment plane.

A scene of a robot about 1 m across on a 50 m ground plane framed the whole plane, so the robot
rendered as a dot. A backdrop -- a near-flat sheet whose largest extent is at least
``scene_parts.BACKDROP_SCALE`` times the rest of the scene and centred on it -- is now left out of the
framing, and every loader that merges parts drops it before the merge, so it is not drawn either:
drawn, it filled the whole frame. The scene then loads and draws exactly as the model alone does. A
scene with no such part loads and frames exactly as before, and so does a scene made only of flat
parts (a sign, a floor plan).

The rule tests run anywhere; the loading and render tests need the real pyvista/VTK/trimesh/pxr and
run inside the pipeline image (or a venv holding its requirements).
"""

import zipfile

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

    def test_the_scale_is_fifty_times_the_content(self):
        assert scene_parts.BACKDROP_SCALE == 50.0

    def test_the_nested_sheet_check_is_fast_for_two_thousand_sheets(self):
        """2,000 equal backdrop sheets around one small item (the worst case: every sheet selected):
        the check compares each with the 64 largest only, well under half a second."""
        import time

        item = _box((-0.01, 0.0, -0.01), (0.01, 0.02, 0.01))
        sheets = [_box((-25.0 - i * 1e-3, 0.0, -25.0), (25.0 + i * 1e-3, 0.0, 25.0)) for i in range(2000)]
        start = time.perf_counter()
        mask = scene_parts.backdrop_parts([item] + sheets)
        elapsed = time.perf_counter() - start
        assert mask[1:].all() and not mask[0]  # none is at most half another's size
        assert elapsed < 0.5, f"{elapsed:.2f} s"

    def test_a_platform_on_a_ground_is_kept_beside_seventy_larger_vertical_sheets(self):
        """Seventy vertical backdrop sheets, each larger than the ground, do not push the ground out of
        the comparison: the cap applies per thin axis, so the platform is still found on the ground."""
        robot = _box((-0.5, 0.1, -0.5), (0.5, 1.1, 0.5))
        platform = _box((-30.0, 0.0, -30.0), (30.0, 0.1, 30.0))
        ground = _box((-500.0, 0.0, -500.0), (500.0, 0.0, 500.0))
        walls = [_box((-600.0 - i, -600.0, -0.5), (600.0 + i, 600.0, -0.5)) for i in range(70)]
        mask = scene_parts.backdrop_parts([robot, platform, ground] + walls)
        assert mask[:3].tolist() == [False, False, True]

    def test_a_platform_on_a_ground_plane_is_kept_and_only_the_ground_is_a_backdrop(self):
        """A 1 m robot on its own 60 m platform on a 1 km ground: only the outermost sheet goes."""
        robot = _box((-0.5, 0.1, -0.5), (0.5, 1.1, 0.5))
        platform = _box((-30.0, 0.0, -30.0), (30.0, 0.1, 30.0))
        ground = _box((-500.0, 0.0, -500.0), (500.0, 0.0, 500.0))
        assert scene_parts.backdrop_parts([robot, platform, ground]).tolist() == [False, False, True]

    def test_a_thin_sheet_with_one_centred_item_under_a_fiftieth_of_it_is_a_backdrop(self):
        """The documented limit: a 1.6 m solar panel (3 mm) with only a centred 30 mm junction box is
        treated as a backdrop; the preview shows the box alone."""
        panel = _box((-0.8, 0.0, -0.5), (0.8, 0.003, 0.5))
        junction = _box((-0.015, 0.003, -0.015), (0.015, 0.023, 0.015))
        assert scene_parts.backdrop_parts([junction, panel]).tolist() == [False, True]

    def test_a_panel_with_one_centred_40_mm_bracket_is_not_a_backdrop(self):
        """A 1 m x 0.6 m x 2 mm sheet-metal panel is 25 times a 40 mm bracket at its centre: the panel
        is part of the model, so it is drawn and framed."""
        panel = _box((-0.5, 0.0, -0.3), (0.5, 0.002, 0.3))
        bracket = _box((-0.02, 0.002, -0.02), (0.02, 0.042, 0.02))
        assert not scene_parts.backdrop_parts([bracket, panel]).any()

    def test_a_zero_thickness_board_with_one_centred_4_mm_chip_is_not_a_backdrop(self):
        """A 100 mm board of no thickness is 25 times a 4 mm chip at its centre."""
        board = _box((-50.0, 0.0, -40.0), (50.0, 0.0, 40.0))
        chip = _box((-2.0, 0.0, -2.0), (2.0, 1.0, 2.0))
        assert not scene_parts.backdrop_parts([chip, board]).any()

    def test_a_sheet_just_under_fifty_times_is_kept_and_at_fifty_is_a_backdrop(self):
        model = _box((-0.5, 0.0, -0.5), (0.5, 1.0, 0.5))
        assert not scene_parts.backdrop_parts([model, _box((-24.9, 0, -24.9), (24.9, 0, 24.9))]).any()
        assert scene_parts.backdrop_parts([model, _box((-25, 0, -25), (25, 0, 25))]).tolist() == [False, True]

    def test_one_part_has_no_backdrop(self):
        assert not scene_parts.backdrop_parts([GROUND]).any()

    def test_a_board_with_one_small_part_in_its_corner_is_not_a_backdrop(self):
        """A zero-thickness board 62.5 times a 1.6 mm corner part: the part does not stand in its middle."""
        board = _box((0.0, 0.0, 0.0), (100.0, 0.0, 100.0))
        chip = _box((0.0, 0.0, 0.0), (1.6, 0.6, 1.6))
        assert not scene_parts.backdrop_parts([chip, board]).any()

    def test_floor_tiles_around_a_small_object_are_not_backdrops(self):
        """Ten by ten 1 m tiles and a 1.6 cm object at the floor's centre: each tile is 62.5 times the
        object, but no tile is centred on it."""
        tiles = [_box((i - 5.0, 0.0, j - 5.0), (i - 4.0, 0.0, j - 4.0)) for i in range(10) for j in range(10)]
        thing = _box((-0.008, 0.0, -0.008), (0.008, 0.016, 0.008))
        assert not scene_parts.backdrop_parts([thing] + tiles).any()

    def test_a_ground_plane_with_the_model_well_off_its_centre_is_kept(self):
        model = _box((15.5, 0.0, -0.3), (16.5, 0.7, 0.3))
        assert not scene_parts.backdrop_parts([model, GROUND]).any()
        near_centre = _box((9.5, 0.0, 9.7), (10.5, 0.7, 10.3))
        assert scene_parts.backdrop_parts([near_centre, GROUND]).tolist() == [False, True]

    def test_a_skybox_of_six_sheets_around_the_model_is_left_out(self):
        s = 100.0
        sky = [_box((-s, -s, -s), (s, -s, s)), _box((-s, s, -s), (s, s, s)), _box((-s, -s, -s), (s, s, -s)),
               _box((-s, -s, s), (s, s, s)), _box((-s, -s, -s), (-s, s, s)), _box((s, -s, -s), (s, s, s))]
        model = _box((-0.5, -0.35, -0.3), (0.5, 0.35, 0.3))
        assert scene_parts.backdrop_parts([model] + sky).tolist() == [False] + [True] * 6

    def test_the_part_limit_is_bounded_by_the_cap_and_by_memory(self):
        assert scene_parts.part_limit(16 * 1024 ** 3) == scene_parts.MAX_PARTS
        small = 64 * 1024 ** 2
        assert scene_parts.part_limit(small) == int(
            small * scene_parts.PART_MEMORY_FRACTION / scene_parts.BYTES_PER_PART) < scene_parts.MAX_PARTS
        assert scene_parts.part_limit(0) == 2


def _corners(box):
    """The eight corners of a box, as a part's points."""
    lo, hi = box[:3], box[3:]
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


@pytest.mark.unit
class TestBackdropKeep:
    """Which parts a loader draws: the backdrops the framing rule selects are dropped, nothing else."""

    def test_the_ground_under_a_model_is_dropped(self, caplog):
        with caplog.at_level("INFO"):
            keep = scene_parts.backdrop_keep([_corners(MODEL), _corners(GROUND)], max_parts=100)
        assert keep.tolist() == [True, False]
        assert "Preview leaves out 1 of 2 parts as a backdrop" in caplog.text

    def test_a_part_without_points_is_kept(self):
        keep = scene_parts.backdrop_keep([_corners(MODEL), np.empty((0, 3)), _corners(GROUND)], max_parts=100)
        assert keep.tolist() == [True, True, False]

    @pytest.mark.parametrize("boxes", [
        [MODEL],
        [MODEL, MODEL],
        [_box((0.0, 0.0, 0.0), (2.0, 1.0, 0.0)), GROUND],  # flat parts only
        [_box((-4.0, 0.0, -3.0), (4.0, 2.5, 3.0)), _box((-5.0, 0.0, -4.0), (5.0, 0.0, 4.0))],  # a room
        [_box((0.0, 0.0, 0.0), (1.6, 0.6, 1.6)), _box((0.0, 0.0, 0.0), (100.0, 0.0, 100.0))],  # corner
        [_box((15.5, 0.0, -0.3), (16.5, 0.7, 0.3)), GROUND],  # off centre
        [_box((0.0, 0.0, 0.0), (100.0, 1.6, 100.0)), _box((10.0, 1.6, 10.0), (14.0, 3.0, 14.0))],  # PCB
    ])
    def test_scenes_without_a_backdrop_keep_every_part(self, boxes):
        assert scene_parts.backdrop_keep([_corners(box) for box in boxes], max_parts=100) is None

    def test_a_backdrop_that_does_not_change_the_bounds_is_kept(self):
        tiles = [_box((i - 5.0, 0.0, j - 5.0), (i - 4.0, 0.0, j - 4.0)) for i in range(10) for j in range(10)]
        thing = _box((-0.508, 0.0, -0.508), (-0.492, 0.016, -0.492))
        assert scene_parts.backdrop_keep([_corners(b) for b in [thing] + tiles], max_parts=1000) is None

    def test_over_the_part_limit_every_part_is_kept(self):
        parts = [_corners(MODEL), _corners(GROUND), _corners(MODEL)]
        assert scene_parts.backdrop_keep(parts, max_parts=2) is None
        assert scene_parts.backdrop_keep(parts, max_parts=3).tolist() == [True, False, True]

    def test_parts_with_non_finite_points_keep_every_part(self):
        bad = _corners(MODEL).astype(float)
        bad[0, 0] = np.nan
        assert scene_parts.backdrop_keep([bad, _corners(GROUND)], max_parts=100) is None


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

    def test_parts_are_tracked_up_to_the_limit_and_not_above_it(self, caplog):
        import pyvista as pv

        limit = 1000
        for n_parts, tracked in ((limit, True), (limit + 1, False)):
            data = pv.PolyData(np.random.default_rng(0).random((n_parts * 2, 3)))
            with caplog.at_level("INFO"):
                scene_parts.record_parts(data, np.full(n_parts, 2), max_parts=limit)
            assert (scene_parts.part_bounds(data) is not None) is tracked
        assert "over the 1,000 whose bounds are tracked" in caplog.text

    def test_a_corner_part_on_a_board_frames_the_board_as_before(self):
        from preview_pipeline import renderer

        board = _box((0.0, 0.0, 0.0), (100.0, 0.0, 100.0))
        chip = _box((0.0, 0.0, 0.0), (1.6, 0.6, 1.6))
        data = _merged(chip, board)
        plain = data.copy()
        plain.field_data.clear()
        with_parts = renderer._compute_camera_framing(data, use_full_bounds=True)
        without = renderer._compute_camera_framing(plain, use_full_bounds=True)
        assert np.array_equal(with_parts[0], without[0]) and with_parts[1:] == without[1:]

    def test_a_backdrop_that_does_not_change_the_bounds_is_not_reported(self, caplog):
        """An object at the centre of one interior tile: leaving that tile out frames the same floor."""
        tiles = [_box((i - 5.0, 0.0, j - 5.0), (i - 4.0, 0.0, j - 4.0)) for i in range(10) for j in range(10)]
        thing = _box((-0.508, 0.0, -0.508), (-0.492, 0.016, -0.492))
        data = _merged(thing, *tiles)
        assert scene_parts.backdrop_parts(scene_parts.part_bounds(data)).sum() == 1
        with caplog.at_level("INFO"):
            assert scene_parts.framing_bounds(data) is None
        assert "backdrop" not in caplog.text

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
    def test_framed_without_the_ground_the_model_fills_the_preview(self):
        """Data whose parts were recorded but whose backdrop was not dropped by a loader: the framing
        still leaves the ground out."""
        from preview_pipeline import renderer

        data = _merged(MODEL, GROUND, colours=[MODEL_RGB, GROUND_RGB])
        frame = renderer.generate_static_frame(data, resolution=(200, 150))
        model_share, ground_share = colour_shares(frame, [MODEL_RGB, GROUND_RGB])
        # Framed on the whole plane the model covered well under 1% of the frame.
        assert model_share > 0.1
        assert ground_share > 0.1


_CUBE_ONLY_USDA = """
def Xform "World"
{
    def Cube "Robot"
    {
        double size = 1
        double3 xformOp:translate = (0, 0, 0.5)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""

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


def _assert_drawn_as_alone(data, alone, ext):
    """The scene loaded with its backdrop is the model loaded alone: the same points and cells, drawn
    into the same pixels, with a transparent background around the model."""
    from preview_pipeline import core, renderer

    data = core._normalize_up_axis(data, ext)
    alone = core._normalize_up_axis(alone, ext)
    np.testing.assert_array_equal(np.asarray(data.points), np.asarray(alone.points))
    assert data.n_cells == alone.n_cells
    frame = renderer.generate_static_frame(data, resolution=(160, 120))
    np.testing.assert_array_equal(frame, renderer.generate_static_frame(alone, resolution=(160, 120)))
    assert (frame[..., 3] == 0).mean() > 0.3, "the backdrop fills the frame"


@pytest.mark.unit
@requires_real("pyvista", "vtk", "pxr")
class TestUsdStage:
    def test_a_z_up_stage_with_a_ground_plane_draws_the_robot_alone(self, tmp_path, caplog):
        from preview_pipeline import renderer
        from preview_pipeline.format_handlers import usd_handler

        path = tmp_path / "robot.usda"
        path.write_text(f'#usda 1.0\n(\n    upAxis = "Z"\n)\n{_PLANE_AND_CUBE_USDA}\n')
        alone = tmp_path / "robot_alone.usda"
        alone.write_text(f'#usda 1.0\n(\n    upAxis = "Z"\n)\n{_CUBE_ONLY_USDA}\n')
        with caplog.at_level("INFO"):
            data = usd_handler.load(str(path))
        assert "Preview leaves out 1 of 2 parts as a backdrop" in caplog.text
        assert "(50.00, 50.00, 0.00)" in caplog.text
        focal, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=True)
        np.testing.assert_allclose(focal, [0.0, 0.5, 0.0], atol=1e-6)
        assert radius < 5.0
        _assert_drawn_as_alone(data, usd_handler.load(str(alone)), ".usda")

    def test_a_single_prim_stage_records_no_parts(self, tmp_path):
        from preview_pipeline.format_handlers import usd_handler

        path = tmp_path / "plane.usda"
        path.write_text('#usda 1.0\n(\n    upAxis = "Y"\n)\ndef Cube "Only"\n{\n    double size = 2\n}\n')
        assert scene_parts.part_bounds(usd_handler.load(str(path))) is None


def _model_on_ground_scene(z_up=False, ground=True):
    """A trimesh scene of a 1 x 0.7 x 0.6 box standing on a 50 x 50 ground plane (or the box alone)."""
    import trimesh

    scene = trimesh.Scene()
    box = trimesh.creation.box(extents=(1.0, 0.6, 0.7) if z_up else (1.0, 0.7, 0.6))
    box.apply_translation((0.0, 0.0, 0.35) if z_up else (0.0, 0.35, 0.0))
    scene.add_geometry(box, geom_name="model")
    if ground:
        corners = ([[-25, -25, 0], [25, -25, 0], [25, 25, 0], [-25, 25, 0]] if z_up
                   else [[-25, 0, -25], [25, 0, -25], [25, 0, 25], [-25, 0, 25]])
        scene.add_geometry(trimesh.Trimesh(vertices=corners, faces=[[0, 1, 2], [0, 2, 3]]),
                           geom_name="ground")
    return scene


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestGlbScene:
    def test_a_glb_scene_with_a_ground_plane_draws_the_model_alone(self, tmp_path):
        from preview_pipeline import core

        path, alone = tmp_path / "scene.glb", tmp_path / "alone.glb"
        _model_on_ground_scene().export(str(path))
        _model_on_ground_scene(ground=False).export(str(alone))
        data = core._load_file(str(path), ".glb")
        assert scene_parts.part_bounds(data) is None  # one part is left
        _assert_drawn_as_alone(data, core._load_file(str(alone), ".glb"), ".glb")

    @pytest.mark.parametrize("sheet,item", [
        # A 1 m x 0.6 m x 2 mm panel with one 40 mm bracket at its centre (25 times).
        (((1.0, 0.002, 0.6), (0, 0.001, 0)), ((0.04, 0.04, 0.04), (0, 0.022, 0))),
        # A 100 mm board of no thickness with one 4 mm chip at its centre (25 times).
        (((0.1, 0.0, 0.08), (0, 0, 0)), ((0.004, 0.001, 0.004), (0, 0.0005, 0))),
    ])
    def test_a_sheet_that_belongs_to_the_model_is_drawn_and_framed(self, tmp_path, sheet, item):
        """Under 50 times its centred item, a flat sheet is part of the model: every part is drawn and
        the camera frames the full bounds, as on a scene with no recorded parts."""
        import trimesh

        from preview_pipeline import core, renderer

        scene = trimesh.Scene()
        extents, centre = item
        scene.add_geometry(trimesh.creation.box(extents=extents).apply_translation(centre), geom_name="item")
        (sx, _, sz), centre = sheet
        flat = trimesh.Trimesh(vertices=[[-sx / 2, 0, -sz / 2], [sx / 2, 0, -sz / 2], [sx / 2, 0, sz / 2],
                                         [-sx / 2, 0, sz / 2]], faces=[[0, 2, 1], [0, 3, 2]]) \
            if sheet[0][1] == 0 else trimesh.creation.box(extents=sheet[0]).apply_translation(centre)
        scene.add_geometry(flat, geom_name="sheet")
        path = tmp_path / "scene.glb"
        scene.export(str(path))
        data = core._load_file(str(path), ".glb")
        assert data.n_points == sum(len(g.vertices) for g in trimesh.load(str(path)).dump())
        plain = data.copy()
        plain.field_data.clear()
        framed = renderer._compute_camera_framing(data, use_full_bounds=True)
        full = renderer._compute_camera_framing(plain, use_full_bounds=True)
        assert np.array_equal(framed[0], full[0]) and framed[1:] == full[1:]

    def test_a_glb_robot_on_its_platform_on_a_ground_keeps_and_frames_the_platform(self, tmp_path):
        import trimesh

        from preview_pipeline import core, renderer

        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(extents=(1, 1, 1)).apply_translation((0, 0.6, 0)), geom_name="robot")
        scene.add_geometry(trimesh.creation.box(extents=(60, 0.1, 60)).apply_translation((0, 0.05, 0)),
                           geom_name="platform")
        scene.add_geometry(trimesh.Trimesh(vertices=[[-500, 0, -500], [500, 0, -500], [500, 0, 500], [-500, 0, 500]],
                                           faces=[[0, 2, 1], [0, 3, 2]]), geom_name="ground")
        path = tmp_path / "platform.glb"
        scene.export(str(path))
        data = core._normalize_up_axis(core._load_file(str(path), ".glb"), ".glb")
        assert data.n_points == 16  # robot and platform drawn, ground dropped
        plain = data.copy()
        plain.field_data.clear()
        framed = renderer._compute_camera_framing(data, use_full_bounds=True)
        full = renderer._compute_camera_framing(plain, use_full_bounds=True)
        assert np.array_equal(framed[0], full[0]) and framed[1:] == full[1:]
        frame = renderer.generate_static_frame(data, resolution=(160, 120))
        assert (frame[..., 3] == 0).mean() > 0.3, "the platform fills the frame"

    def test_a_textured_model_on_a_ground_plane_keeps_its_texture(self, tmp_path):
        """With the ground dropped before the merge, the one part left keeps its UV texture rather
        than being baked to vertex colours."""
        import trimesh
        from PIL import Image

        from preview_pipeline import core

        texture = Image.fromarray(np.kron([[0, 255], [255, 0]], np.ones((16, 16))).astype(np.uint8)).convert("RGB")
        box = trimesh.creation.box(extents=(1.0, 0.7, 0.6))
        box.apply_translation((0.0, 0.35, 0.0))
        uv = np.column_stack([(box.vertices[:, 0] + 0.5), box.vertices[:, 1] / 0.7])
        box.visual = trimesh.visual.TextureVisuals(uv=uv, image=texture)
        for ground in (True, False):
            scene = trimesh.Scene()
            scene.add_geometry(box, geom_name="model")
            if ground:
                scene.add_geometry(trimesh.Trimesh(
                    vertices=[[-25, 0, -25], [25, 0, -25], [25, 0, 25], [-25, 0, 25]],
                    faces=[[0, 2, 1], [0, 3, 2]]), geom_name="ground")
            scene.export(str(tmp_path / f"textured-{ground}.glb"))
        data = core._load_file(str(tmp_path / "textured-True.glb"), ".glb")
        assert getattr(data, "_preview_texture", None) is not None
        _assert_drawn_as_alone(data, core._load_file(str(tmp_path / "textured-False.glb"), ".glb"), ".glb")


_BOX_TRIANGLES = [[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                  [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]]


def _box_corners(lo, hi):
    return [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def _write_3mf(path, ground=True):
    """A 3MF (Z-up by specification) of a 1 x 0.6 x 0.7 box on a 50 x 50 ground, two build items (or
    the box alone)."""
    def obj(object_id, vertices, triangles):
        return (f'<object id="{object_id}" type="model"><mesh><vertices>'
                + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
                + "</vertices><triangles>"
                + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles)
                + "</triangles></mesh></object>")

    ground_vertices = [[-25, -25, 0], [25, -25, 0], [25, 25, 0], [-25, 25, 0]]
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="meter" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
             + obj(1, _box_corners((-0.5, -0.3, 0), (0.5, 0.3, 0.7)), _BOX_TRIANGLES)
             + (obj(2, ground_vertices, [[0, 1, 2], [0, 2, 3]]) if ground else "")
             + '</resources><build><item objectid="1"/>'
             + ('<item objectid="2"/>' if ground else "") + '</build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", model)


def _write_dae(path, ground=True):
    """A Y-up COLLADA scene of a 1 x 0.7 x 0.6 box on a 50 x 50 ground, one node per geometry (or the
    box alone)."""
    def geometry(name, vertices, triangles):
        flat = " ".join(str(c) for vertex in vertices for c in vertex)
        indices = " ".join(str(i) for triangle in triangles for i in triangle)
        return (f'<geometry id="{name}"><mesh><source id="{name}-pos">'
                f'<float_array id="{name}-arr" count="{3 * len(vertices)}">{flat}</float_array>'
                f'<technique_common><accessor source="#{name}-arr" count="{len(vertices)}" stride="3"/>'
                f'</technique_common></source><vertices id="{name}-v">'
                f'<input semantic="POSITION" source="#{name}-pos"/></vertices>'
                f'<triangles count="{len(triangles)}"><input semantic="VERTEX" source="#{name}-v" offset="0"/>'
                f"<p>{indices}</p></triangles></mesh></geometry>")

    ground_vertices = [[-25, 0, -25], [25, 0, -25], [25, 0, 25], [-25, 0, 25]]
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
        "<asset><up_axis>Y_UP</up_axis></asset><library_geometries>"
        + geometry("model", _box_corners((-0.5, 0, -0.3), (0.5, 0.7, 0.3)), _BOX_TRIANGLES)
        + (geometry("ground", ground_vertices, [[0, 2, 1], [0, 3, 2]]) if ground else "")
        + '</library_geometries><library_visual_scenes><visual_scene id="s">'
        '<node><instance_geometry url="#model"/></node>'
        + ('<node><instance_geometry url="#ground"/></node>' if ground else "")
        + '</visual_scene></library_visual_scenes><scene><instance_visual_scene url="#s"/></scene></COLLADA>')


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestXmlScenes:
    @pytest.mark.parametrize("ext,writer", [(".3mf", _write_3mf), (".dae", _write_dae)])
    def test_a_build_with_a_ground_draws_the_model_alone(self, tmp_path, ext, writer):
        from preview_pipeline import core

        path, alone = tmp_path / f"scene{ext}", tmp_path / f"alone{ext}"
        writer(path)
        writer(alone, ground=False)
        _assert_drawn_as_alone(core._load_file(str(path), ext), core._load_file(str(alone), ext), ext)


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestSceneLoaders:
    def test_a_glb_scene_mixing_a_mesh_a_point_cloud_and_a_ground_draws_the_mesh_and_the_cloud(self, tmp_path):
        import trimesh

        from preview_pipeline import core, renderer

        cloud = np.random.default_rng(1).uniform((-0.4, 0.7, -0.2), (0.4, 0.9, 0.2), size=(200, 3))
        for ground in (True, False):
            scene = _model_on_ground_scene(ground=ground)
            scene.add_geometry(trimesh.PointCloud(cloud), geom_name="scan")
            scene.export(str(tmp_path / f"mixed-{ground}.glb"))
        data = core._load_file(str(tmp_path / "mixed-True.glb"), ".glb")
        assert not renderer.is_point_cloud(data) and data.n_verts > 0
        assert len(scene_parts.part_bounds(data)) == 2
        _assert_drawn_as_alone(data, core._load_file(str(tmp_path / "mixed-False.glb"), ".glb"), ".glb")

    def test_an_obj_with_one_material_is_one_part(self, tmp_path):
        """trimesh merges the objects of an OBJ that share a material, so nothing is recorded or left
        out: the ground stays."""
        from preview_pipeline import core

        path = tmp_path / "scene.obj"
        _model_on_ground_scene().export(str(path))
        data = core._load_file(str(path), ".obj")
        assert scene_parts.part_bounds(data) is None
        assert np.ptp(np.asarray(data.points)[:, 0]) == pytest.approx(50.0)


@pytest.mark.unit
@requires_real("pyvista", "vtk", "cadquery")
class TestStepAssembly:
    def test_a_step_assembly_with_a_ground_draws_the_model_alone(self, tmp_path):
        """The model is tessellated again on its own, at the detail of a file holding it alone."""
        import cadquery as cq

        from preview_pipeline import core

        for ground in (True, False):
            assembly = cq.Assembly()
            assembly.add(cq.Workplane().cylinder(0.7, 0.4).translate((0, 0, 0.35)), name="model")
            if ground:
                assembly.add(cq.Workplane().box(50, 50, 0.01).translate((0, 0, -0.005)), name="ground")
            assembly.save(str(tmp_path / f"assembly-{ground}.step"))
        data = core._load_file(str(tmp_path / "assembly-True.step"), ".step")
        _assert_drawn_as_alone(data, core._load_file(str(tmp_path / "assembly-False.step"), ".step"), ".step")


@pytest.mark.unit
@requires_real("pyvista", "vtk", "open3d")
class TestFbxScene:
    def test_an_fbx_scene_with_a_ground_draws_the_model_alone(self, tmp_path):
        from test_thumbnail_fbx import CUBE_QUADS, CUBE_VERTICES, _budgets, ascii_fbx

        from preview_pipeline import core

        ground = [-25, 0, -25, 25, 0, -25, 25, 0, 25, -25, 0, 25]
        path, alone = tmp_path / "scene.fbx", tmp_path / "alone.fbx"
        path.write_text(ascii_fbx(
            geometries=[(10, CUBE_VERTICES, CUBE_QUADS, None), (11, ground, [[0, 3, 2, 1]], None)],
            models=[(20, (0, 0.5, 0)), (21, (0, 0, 0))],
            materials=[(30, (1, 0, 0)), (31, (0, 1, 0))],
            connections=[(20, 0), (21, 0), (10, 20), (11, 21), (30, 20), (31, 21)]))
        alone.write_text(ascii_fbx(
            geometries=[(10, CUBE_VERTICES, CUBE_QUADS, None)], models=[(20, (0, 0.5, 0))],
            materials=[(30, (1, 0, 0))], connections=[(20, 0), (10, 20), (30, 20)]))
        _assert_drawn_as_alone(core._load_file(str(path), ".fbx", budgets=_budgets()),
                               core._load_file(str(alone), ".fbx", budgets=_budgets()), ".fbx")

    def test_a_coloured_ground_does_not_colour_an_uncoloured_model(self, tmp_path):
        """A model with no colour beside a coloured ground got the fill colour; alone it has none."""
        from test_thumbnail_fbx import CUBE_QUADS, CUBE_VERTICES, _budgets, ascii_fbx

        from preview_pipeline import core

        ground = [-25, 0, -25, 25, 0, -25, 25, 0, 25, -25, 0, 25]
        path = tmp_path / "scene.fbx"
        path.write_text(ascii_fbx(
            geometries=[(10, CUBE_VERTICES, CUBE_QUADS, None), (11, ground, [[0, 3, 2, 1]], None)],
            models=[(20, (0, 0.5, 0)), (21, (0, 0, 0))], materials=[(31, (0, 1, 0))],
            connections=[(20, 0), (21, 0), (10, 20), (11, 21), (31, 21)]))
        data = core._load_file(str(path), ".fbx", budgets=_budgets())
        assert "RGB" not in data.point_data
