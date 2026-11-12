#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A model that sits on a large ground or environment plane.

A scene of a robot about 1 m across on a 50 m ground plane framed the whole plane, so the robot
rendered as a dot. The preview now leaves a backdrop out: a near-flat sheet whose largest extent is at
least ``scene_parts.BACKDROP_SCALE`` times the rest of the scene and centred on it is cut out right
after loading (``scene_parts.without_backdrop``), so the model appears alone on the transparent
background, framed as a file holding the model alone. When the parts cannot be cut out the camera
still leaves the backdrop out of its framing and draws it. A scene with no such part renders exactly
as before, and so does a scene made only of flat parts (a sign, a floor plan).

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

    def test_one_part_has_no_backdrop(self):
        assert not scene_parts.backdrop_parts([GROUND]).any()

    def test_a_board_with_one_small_part_in_its_corner_is_not_a_backdrop(self):
        """A zero-thickness board 25 times a 4 mm corner part: the part does not stand in its middle."""
        board = _box((0.0, 0.0, 0.0), (100.0, 0.0, 100.0))
        chip = _box((0.0, 0.0, 0.0), (4.0, 1.4, 4.0))
        assert not scene_parts.backdrop_parts([chip, board]).any()

    def test_floor_tiles_around_a_small_object_are_not_backdrops(self):
        """Ten by ten 1 m tiles and a 4 cm object at the floor's centre: each tile is 25 times the
        object, but no tile is centred on it."""
        tiles = [_box((i - 5.0, 0.0, j - 5.0), (i - 4.0, 0.0, j - 4.0)) for i in range(10) for j in range(10)]
        thing = _box((-0.02, 0.0, -0.02), (0.02, 0.04, 0.02))
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
        chip = _box((0.0, 0.0, 0.0), (4.0, 1.4, 4.0))
        data = _merged(chip, board)
        plain = data.copy()
        plain.field_data.clear()
        with_parts = renderer._compute_camera_framing(data, use_full_bounds=True)
        without = renderer._compute_camera_framing(plain, use_full_bounds=True)
        assert np.array_equal(with_parts[0], without[0]) and with_parts[1:] == without[1:]

    def test_a_backdrop_that_does_not_change_the_bounds_is_not_reported(self, caplog):
        """An object at the centre of one interior tile: leaving that tile out frames the same floor."""
        tiles = [_box((i - 5.0, 0.0, j - 5.0), (i - 4.0, 0.0, j - 4.0)) for i in range(10) for j in range(10)]
        thing = _box((-0.52, 0.0, -0.52), (-0.48, 0.04, -0.48))
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
    def test_the_model_fills_the_preview_and_the_ground_is_not_drawn(self):
        from preview_pipeline import renderer

        data = scene_parts.without_backdrop(_merged(MODEL, GROUND, colours=[MODEL_RGB, GROUND_RGB]))
        frame = renderer.generate_static_frame(data, resolution=(200, 150))
        model_share, ground_share = colour_shares(frame, [MODEL_RGB, GROUND_RGB])
        # Framed on the whole plane the model covered well under 1% of the frame.
        assert model_share > 0.1
        assert ground_share == 0.0
        assert (frame[..., 3] == 0).mean() > 0.3  # the transparent background shows around the model

    def test_every_orbit_frame_matches_the_model_rendered_alone(self):
        """The ground's pixels are gone and the model's silhouette is the model-alone one (IoU >= 0.99)
        in every frame of the orbit."""
        from preview_pipeline import renderer

        model = _merged(MODEL, _box((-0.3, 0.7, -0.2), (0.3, 1.0, 0.2)), colours=[MODEL_RGB, MODEL_RGB])
        scene = _merged(MODEL, _box((-0.3, 0.7, -0.2), (0.3, 1.0, 0.2)), GROUND,
                        colours=[MODEL_RGB, MODEL_RGB, GROUND_RGB])
        treated = renderer.generate_rotating_frames(scene_parts.without_backdrop(scene), n_frames=12,
                                                    resolution=(160, 120))
        alone = renderer.generate_rotating_frames(model, n_frames=12, resolution=(160, 120))
        for treated_frame, alone_frame in zip(treated, alone):
            assert colour_shares(treated_frame, [GROUND_RGB])[0] == 0.0
            a, t = alone_frame[..., 3] > 0, treated_frame[..., 3] > 0
            assert (a & t).sum() / (a | t).sum() >= 0.99
            assert 0.05 < a.mean() < 0.95

    def test_the_ground_drawn_before_covered_the_whole_frame(self):
        """Without the cut, the ground fills the background behind the model."""
        from preview_pipeline import renderer

        frame = renderer.generate_static_frame(_merged(MODEL, GROUND, colours=[MODEL_RGB, GROUND_RGB]),
                                               resolution=(200, 150))
        assert colour_shares(frame, [GROUND_RGB])[0] > 0.1


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestWithoutBackdrop:
    def test_the_ground_plane_is_cut_out_and_the_model_kept_whole(self, caplog):
        data = _merged(MODEL, GROUND)
        with caplog.at_level("INFO"):
            result = scene_parts.without_backdrop(data)
        assert result.n_cells == data.n_cells // 2
        np.testing.assert_allclose(result.bounds, (-0.5, 0.5, 0.0, 0.7, -0.3, 0.3), atol=1e-9)
        assert scene_parts.part_bounds(result) is None  # one part remains
        assert "Backdrop left out of the preview: 1 of 2 parts" in caplog.text
        assert "(50.00, 0.00, 50.00)" in caplog.text

    def test_scenes_the_rule_keeps_whole_are_left_as_they_are(self):
        board = _box((0.0, 0.0, 0.0), (100.0, 0.0, 100.0))
        chip = _box((0.0, 0.0, 0.0), (4.0, 1.4, 4.0))
        furniture = _box((-4.0, 0.0, -3.0), (4.0, 2.5, 3.0))
        floor = _box((-5.0, 0.0, -4.0), (5.0, 0.0, 4.0))
        sign = _box((0.0, 0.0, 0.0), (2.0, 1.0, 0.0))
        tiles = [_box((i - 5.0, 0.0, j - 5.0), (i - 4.0, 0.0, j - 4.0)) for i in range(10) for j in range(10)]
        thing = _box((-0.52, 0.0, -0.52), (-0.48, 0.04, -0.48))
        for boxes in ((chip, board), (furniture, floor), (GROUND, sign), (thing, *tiles)):
            data = _merged(*boxes)
            assert scene_parts.without_backdrop(data) is data

    def test_a_scene_over_the_part_cap_is_left_as_it_is(self):
        import pyvista as pv

        model = pv.Box(bounds=(-0.5, 0.5, 0, 0.7, -0.3, 0.3)).triangulate()
        ground = pv.Plane(i_size=50, j_size=50, direction=(0, 1, 0)).triangulate()
        data = model.append_polydata(ground)
        scene_parts.record_parts(data, [model.n_points, ground.n_points], max_parts=1)
        assert scene_parts.without_backdrop(data) is data

    def test_counts_that_no_longer_match_leave_the_ground_drawn_and_out_of_the_framing(self):
        from preview_pipeline import renderer

        data = _merged(MODEL, GROUND)
        del data.field_data[scene_parts.PART_POINTS]
        assert scene_parts.without_backdrop(data) is data
        _, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=True)
        assert radius < 5.0


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


def _model_on_ground_scene(z_up=False):
    """A trimesh scene of a 1 x 0.7 x 0.6 box standing on a 50 x 50 ground plane."""
    import trimesh

    scene = trimesh.Scene()
    box = trimesh.creation.box(extents=(1.0, 0.6, 0.7) if z_up else (1.0, 0.7, 0.6))
    box.apply_translation((0.0, 0.0, 0.35) if z_up else (0.0, 0.35, 0.0))
    scene.add_geometry(box, geom_name="model")
    corners = ([[-25, -25, 0], [25, -25, 0], [25, 25, 0], [-25, 25, 0]] if z_up
               else [[-25, 0, -25], [25, 0, -25], [25, 0, 25], [-25, 0, 25]])
    scene.add_geometry(trimesh.Trimesh(vertices=corners, faces=[[0, 1, 2], [0, 2, 3]]), geom_name="ground")
    return scene


def _assert_frames_the_model(data, ext):
    """The loaded (and up-axis normalised) scene records its parts, leaves the ground out and frames
    the model."""
    from preview_pipeline import core, renderer

    data = core._normalize_up_axis(data, ext)
    bounds = scene_parts.part_bounds(data)
    assert bounds is not None
    mask = scene_parts.backdrop_parts(bounds)
    assert mask.sum() == 1
    np.testing.assert_allclose(np.sort(bounds[mask][0][3:] - bounds[mask][0][:3]), [0, 50, 50], atol=1e-4)
    _, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=True)
    assert radius < 5.0


_BOX_TRIANGLES = [[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                  [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]]


def _box_corners(lo, hi):
    return [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def _write_3mf(path):
    """A 3MF (Z-up by specification) of a 1 x 0.6 x 0.7 box on a 50 x 50 ground, two build items."""
    def obj(object_id, vertices, triangles):
        return (f'<object id="{object_id}" type="model"><mesh><vertices>'
                + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
                + "</vertices><triangles>"
                + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles)
                + "</triangles></mesh></object>")

    ground = [[-25, -25, 0], [25, -25, 0], [25, 25, 0], [-25, 25, 0]]
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="meter" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
             + obj(1, _box_corners((-0.5, -0.3, 0), (0.5, 0.3, 0.7)), _BOX_TRIANGLES)
             + obj(2, ground, [[0, 1, 2], [0, 2, 3]])
             + '</resources><build><item objectid="1"/><item objectid="2"/></build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", model)


def _write_dae(path):
    """A Y-up COLLADA scene of a 1 x 0.7 x 0.6 box on a 50 x 50 ground, one node per geometry."""
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

    ground = [[-25, 0, -25], [25, 0, -25], [25, 0, 25], [-25, 0, 25]]
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
        "<asset><up_axis>Y_UP</up_axis></asset><library_geometries>"
        + geometry("model", _box_corners((-0.5, 0, -0.3), (0.5, 0.7, 0.3)), _BOX_TRIANGLES)
        + geometry("ground", ground, [[0, 2, 1], [0, 3, 2]])
        + '</library_geometries><library_visual_scenes><visual_scene id="s">'
        '<node><instance_geometry url="#model"/></node><node><instance_geometry url="#ground"/></node>'
        '</visual_scene></library_visual_scenes><scene><instance_visual_scene url="#s"/></scene></COLLADA>')


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestXmlScenes:
    def test_a_3mf_build_records_its_items(self, tmp_path):
        from preview_pipeline import core

        path = tmp_path / "scene.3mf"
        _write_3mf(path)
        _assert_frames_the_model(core._load_file(str(path), ".3mf"), ".3mf")

    def test_a_collada_scene_records_its_instances(self, tmp_path):
        from preview_pipeline import core

        path = tmp_path / "scene.dae"
        _write_dae(path)
        _assert_frames_the_model(core._load_file(str(path), ".dae"), ".dae")


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestSceneLoaders:
    def test_a_glb_scene_mixing_a_mesh_a_point_cloud_and_a_ground_records_every_part(self, tmp_path):
        import trimesh

        from preview_pipeline import core, renderer

        scene = _model_on_ground_scene()
        cloud = np.random.default_rng(1).uniform((-0.4, 0.7, -0.2), (0.4, 0.9, 0.2), size=(200, 3))
        scene.add_geometry(trimesh.PointCloud(cloud), geom_name="scan")
        path = tmp_path / "mixed.glb"
        scene.export(str(path))
        data = core._load_file(str(path), ".glb")
        assert not renderer.is_point_cloud(data) and data.n_verts > 0
        assert len(scene_parts.part_bounds(data)) == 3
        _assert_frames_the_model(data, ".glb")

    def test_an_obj_with_one_material_is_one_part(self, tmp_path):
        """trimesh merges the objects of an OBJ that share a material, so nothing is recorded."""
        from preview_pipeline import core

        path = tmp_path / "scene.obj"
        _model_on_ground_scene().export(str(path))
        assert scene_parts.part_bounds(core._load_file(str(path), ".obj")) is None


@pytest.mark.unit
@requires_real("pyvista", "vtk", "cadquery")
class TestStepAssembly:
    def test_a_step_assembly_loads_without_its_ground(self, tmp_path):
        """The loader records the two parts, finds the ground a backdrop and tessellates the model alone."""
        import cadquery as cq

        from preview_pipeline import core

        assembly = cq.Assembly()
        assembly.add(cq.Workplane().box(1, 0.6, 0.7).translate((0, 0, 0.35)), name="model")
        assembly.add(cq.Workplane().box(50, 50, 0.01).translate((0, 0, -0.005)), name="ground")
        path = tmp_path / "assembly.step"
        assembly.save(str(path))
        data = core._load_file(str(path), ".step")
        assert scene_parts.part_bounds(data) is None  # the model alone, one part
        np.testing.assert_allclose(data.bounds, (-0.5, 0.5, -0.3, 0.3, 0.0, 0.7), atol=1e-6)
        from preview_pipeline import renderer

        _, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=True)
        assert radius < 5.0


@pytest.mark.unit
@requires_real("pyvista", "vtk", "open3d")
class TestFbxScene:
    def test_an_fbx_scene_records_its_meshes(self, tmp_path):
        from test_thumbnail_fbx import CUBE_QUADS, CUBE_VERTICES, _budgets, ascii_fbx

        from preview_pipeline import core

        ground = [-25, 0, -25, 25, 0, -25, 25, 0, 25, -25, 0, 25]
        path = tmp_path / "scene.fbx"
        path.write_text(ascii_fbx(
            geometries=[(10, CUBE_VERTICES, CUBE_QUADS, None), (11, ground, [[0, 3, 2, 1]], None)],
            models=[(20, (0, 0.5, 0)), (21, (0, 0, 0))],
            materials=[(30, (1, 0, 0)), (31, (0, 1, 0))],
            connections=[(20, 0), (21, 0), (10, 20), (11, 21), (30, 20), (31, 21)]))
        _assert_frames_the_model(core._load_file(str(path), ".fbx", budgets=_budgets()), ".fbx")


@pytest.mark.unit
@requires_real("pyvista", "vtk", "pxr")
class TestUsdGroundCut:
    def test_a_z_up_stage_with_a_ground_plane_loads_as_the_robot_alone(self, tmp_path):
        from preview_pipeline import core
        from preview_pipeline.format_handlers import usd_handler

        path = tmp_path / "robot.usda"
        path.write_text(f'#usda 1.0\n(\n    upAxis = "Z"\n)\n{_PLANE_AND_CUBE_USDA}\n')
        data = scene_parts.without_environment(usd_handler.load(str(path)))
        np.testing.assert_allclose(data.bounds, (-0.5, 0.5, 0.0, 1.0, -0.5, 0.5), atol=1e-6)
        source = __import__("inspect").getsource(core._run_preview_stage)
        assert "scene_parts.without_environment(pv_data)" in source

    def test_a_coloured_ground_under_an_uncoloured_robot_leaves_the_robot_as_drawn_alone(self, tmp_path):
        """The loader fills the robot with a neutral colour only because the ground has one; once the
        ground is cut out the robot draws as from a stage holding it alone, in every orbit frame."""
        from preview_pipeline import renderer
        from preview_pipeline.format_handlers import usd_handler

        coloured = _PLANE_AND_CUBE_USDA.replace(
            "point3f[] points", "color3f[] primvars:displayColor = [(0.1, 0.6, 0.1)]\n        point3f[] points")
        on_ground, alone = tmp_path / "on-ground.usda", tmp_path / "alone.usda"
        on_ground.write_text(f'#usda 1.0\n(\n    upAxis = "Z"\n)\n{coloured}\n')
        alone.write_text('#usda 1.0\n(\n    upAxis = "Z"\n    subLayers = [@./on-ground.usda@]\n)\n'
                         'over "World"\n{\n    over "GroundPlane" (\n        active = false\n    )\n    {\n    }\n}\n')
        loaded = usd_handler.load(str(on_ground))
        assert "RGB" in loaded.point_data
        cut = scene_parts.without_environment(loaded)
        reference = usd_handler.load(str(alone))
        assert "RGB" not in cut.point_data and "RGB" not in reference.point_data
        for a, b in zip(renderer.generate_rotating_frames(cut, n_frames=8, resolution=(120, 90)),
                        renderer.generate_rotating_frames(reference, n_frames=8, resolution=(120, 90))):
            assert np.array_equal(a, b)


@pytest.mark.unit
@requires_real("pyvista", "vtk", "cadquery")
class TestStepGroundCut:
    def test_a_part_on_a_ground_slab_tessellates_as_if_alone(self, tmp_path):
        """The ground slab is left out and the part is tessellated again at its own size."""
        import cadquery as cq

        from preview_pipeline import core

        def save(with_ground):
            assembly = cq.Assembly()
            assembly.add(cq.Workplane().cylinder(0.7, 0.5).translate((0, 0, 0.35)), name="model")
            if with_ground:
                assembly.add(cq.Workplane().box(50, 50, 0.01).translate((0, 0, -0.005)), name="ground")
            path = tmp_path / f"assembly-{with_ground}.step"
            assembly.save(str(path))
            return path

        on_ground = scene_parts.without_environment(core._load_file(str(save(True)), ".step"))
        alone = core._load_file(str(save(False)), ".step")
        assert on_ground.n_points == alone.n_points
        np.testing.assert_allclose(np.asarray(on_ground.points), np.asarray(alone.points), atol=1e-9)
