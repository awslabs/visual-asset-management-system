#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A model inside environment geometry that encloses it (a room or environment box, a sky sphere).

Rendered whole, the preview showed the outside of the box or the sphere. The preview now leaves the
enclosure out when the scene's parts pass the enclosure screen, no environment point lies inside the
content, the content is drawn far finer than the environment and rays from the content meet the
environment in nearly every direction. What remains is exactly the content, so every frame of the
preview matches the model rendered alone. A hollow product whose shell is the asset, an open backdrop,
a floor and anything in doubt render exactly as before.

The screen tests run anywhere; the scene and render tests need the real pyvista/VTK and run inside the
pipeline image (or a venv holding its requirements).
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import scene_parts


def _box(lo, hi):
    return list(lo) + list(hi)


MODEL = _box((-0.5, 0.0, -0.5), (0.5, 1.0, 0.5))
ROOM = _box((-10.0, 0.0, -10.0), (10.0, 8.0, 10.0))


@pytest.mark.unit
class TestEnclosureScreen:
    def test_a_small_model_inside_a_closed_box_passes(self):
        lo, hi, kept, contact = scene_parts.enclosure_screen([MODEL, ROOM])
        np.testing.assert_allclose(np.concatenate([lo, hi]), MODEL)
        assert kept.tolist() == [True, False]
        assert contact == 1  # the model stands on the floor (ymin)

    def test_a_room_of_six_sheets_passes(self):
        walls = [_box((-10, 0, -10), (10, 0, 10)), _box((-10, 8, -10), (10, 8, 10)),
                 _box((-10, 0, -10), (-10, 8, 10)), _box((10, 0, -10), (10, 8, 10)),
                 _box((-10, 0, -10), (10, 8, -10)), _box((-10, 0, 10), (10, 8, 10))]
        _, _, kept, _ = scene_parts.enclosure_screen([MODEL] + walls)
        assert kept.tolist() == [True] + [False] * 6

    def test_a_light_card_on_a_wall_is_environment(self):
        card = _box((-9.0, 2.0, 1.0), (-9.0, 4.0, 3.0))
        lo, hi, kept, _ = scene_parts.enclosure_screen([MODEL, ROOM, card])
        np.testing.assert_allclose(np.concatenate([lo, hi]), MODEL)
        assert kept.tolist() == [True, False, False]

    def test_a_label_beside_the_model_is_kept_with_it(self):
        label = _box((0.55, 0.2, 0.0), (0.7, 0.4, 0.0))
        lo, hi, kept, _ = scene_parts.enclosure_screen([MODEL, ROOM, label])
        assert kept.tolist() == [True, False, True]
        assert hi[0] == pytest.approx(0.7)

    def test_a_ground_plane_under_a_model_is_no_enclosure(self):
        """A model on a large ground reaches the scene's top and bottom: the backdrop case, unchanged."""
        ground = _box((-25.0, 0.0, -25.0), (25.0, 0.0, 25.0))
        assert scene_parts.enclosure_screen([MODEL, ground]) is None

    def test_content_not_much_smaller_than_the_shell_is_no_enclosure(self):
        """A housing three times its internal part: the shell is the product."""
        housing = _box((-1.5, -1.5, -1.5), (1.5, 1.5, 1.5))
        part = _box((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))
        assert scene_parts.enclosure_screen([part, housing]) is None

    def test_content_touching_two_faces_is_no_enclosure(self):
        corner = _box((-10.0, 0.0, -10.0), (-9.0, 1.0, -9.0))
        assert scene_parts.enclosure_screen([corner, ROOM]) is None

    def test_a_flat_scene_and_one_part_have_no_enclosure(self):
        assert scene_parts.enclosure_screen([ROOM]) is None
        sheet = _box((-10, 0, -10), (10, 0, 10))
        assert scene_parts.enclosure_screen([_box((0, 0, 0), (1, 0, 1)), sheet]) is None

    def test_spread_furniture_in_a_room_is_no_enclosure(self):
        furniture = [_box((-8, 0, -8), (-6, 1, -6)), _box((6, 0, 6), (8, 2, 8))]
        assert scene_parts.enclosure_screen(furniture + [ROOM]) is None


def _part(mesh, rgb=None):
    import pyvista as pv

    mesh = mesh.triangulate()
    part = pv.PolyData(np.asarray(mesh.points), np.asarray(mesh.faces))
    if rgb is not None:
        part.point_data["RGB"] = np.tile(np.asarray(rgb, np.uint8), (part.n_points, 1))
    return part


def _scene(*parts):
    merged = parts[0] if len(parts) == 1 else parts[0].append_polydata(*parts[1:])
    return scene_parts.record_parts(merged, [part.n_points for part in parts], max_parts=1000)


def _box_mesh(bounds, level=0):
    import pyvista as pv

    lo, hi = bounds[:3], bounds[3:]
    return pv.Box(bounds=(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]), level=level)


MODEL_RGB = (230, 40, 40)
WHEEL_RGB = (40, 200, 40)
SHELL_RGB = (40, 40, 230)


def _model_parts(rgb=None):
    """A detailed 1 m ball standing on y = 0, and a thin ring around it: two parts."""
    import pyvista as pv

    ball = _part(pv.Sphere(radius=0.4, center=(0.0, 0.5, 0.0), theta_resolution=60, phi_resolution=60), rgb)
    ring = _part(pv.ParametricTorus(ringradius=0.45, crosssectionradius=0.03).translate((0.0, 0.5, 0.0)),
                 WHEEL_RGB if rgb is not None else None)
    return [ball, ring]


def _inward_room():
    """A 20 x 8 x 20 room box whose faces point inward, and a separate floor sheet in it."""
    import pyvista as pv

    room = _part(_box_mesh(ROOM), SHELL_RGB)
    room.flip_faces(inplace=True)
    floor = _part(pv.Plane(center=(0, 0.0, 0), direction=(0, 1, 0), i_size=19, j_size=19), SHELL_RGB)
    return [room, floor]


def _sky_sphere(theta=24, phi=12):
    import pyvista as pv

    return [_part(pv.Sphere(radius=30.0, center=(0.0, 0.5, 0.0), theta_resolution=theta, phi_resolution=phi),
                  SHELL_RGB)]


def _six_sheet_room():
    import pyvista as pv

    walls = []
    for centre, direction, size in (((0, 0, 0), (0, 1, 0), (20, 20)), ((0, 8, 0), (0, -1, 0), (20, 20)),
                                    ((-10, 4, 0), (1, 0, 0), (8, 20)), ((10, 4, 0), (-1, 0, 0), (8, 20)),
                                    ((0, 4, -10), (0, 0, 1), (20, 8)), ((0, 4, 10), (0, 0, -1), (20, 8))):
        walls.append(_part(pv.Plane(center=centre, direction=direction, i_size=size[0], j_size=size[1],
                                    i_resolution=1, j_resolution=1), SHELL_RGB))
    card = _part(pv.Plane(center=(-9.5, 3.0, 2.0), direction=(1, 0, 0), i_size=2, j_size=2,
                          i_resolution=1, j_resolution=1), SHELL_RGB)
    return walls + [card]


ENCLOSURES = {
    "closed box": lambda: [_part(_box_mesh(ROOM), SHELL_RGB)],
    "sky sphere": _sky_sphere,
    "inward room with a floor": _inward_room,
    "room of six sheets with a light card": _six_sheet_room,
}


def _same_data(left, right):
    np.testing.assert_array_equal(np.asarray(left.points), np.asarray(right.points))
    np.testing.assert_array_equal(np.asarray(left.faces), np.asarray(right.faces))
    assert sorted(left.point_data.keys()) == sorted(right.point_data.keys())
    for name in left.point_data.keys():
        np.testing.assert_array_equal(np.asarray(left.point_data[name]), np.asarray(right.point_data[name]))


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestWithoutEnclosure:
    @pytest.mark.parametrize("name", sorted(ENCLOSURES))
    def test_the_enclosure_is_left_out_and_the_model_is_kept_whole(self, name):
        model = _model_parts(MODEL_RGB)
        result = scene_parts.without_enclosure(_scene(*model, *ENCLOSURES[name]()))
        alone = _scene(*model)
        _same_data(result, alone)
        np.testing.assert_allclose(scene_parts.part_bounds(result), scene_parts.part_bounds(alone))

    def test_the_enclosure_may_come_before_the_model(self):
        model = _model_parts()
        result = scene_parts.without_enclosure(_scene(_part(_box_mesh(ROOM)), *model))
        _same_data(result, _scene(*model))

    def test_a_dense_sky_dome_holding_about_half_the_points_is_still_left_out(self):
        """A sky dome as finely tessellated as the model is coarse for its size: still environment."""
        model = _model_parts()
        dome = _sky_sphere(theta=160, phi=100)
        data = _scene(*model, *dome)
        assert dome[0].n_points >= 0.45 * data.n_points
        assert scene_parts.without_enclosure(data).n_points == _scene(*model).n_points

    def test_the_left_out_parts_are_logged(self, caplog):
        with caplog.at_level("INFO"):
            scene_parts.without_enclosure(_scene(*_model_parts(), _part(_box_mesh(ROOM))))
        assert "Enclosure left out of the preview: 1 of 3 parts, scene extents (20.00, 8.00, 20.00)" in caplog.text

    def test_texture_coordinates_cell_colours_and_loader_attributes_are_kept(self):
        import pyvista as pv

        ball = pv.Sphere(radius=0.4, center=(0.0, 0.5, 0.0)).triangulate()
        ball = pv.PolyData(np.asarray(ball.points), np.asarray(ball.faces))
        ball.active_texture_coordinates = np.random.default_rng(0).random((ball.n_points, 2)).astype(np.float32)
        box = _part(_box_mesh(ROOM))
        box.active_texture_coordinates = np.zeros((box.n_points, 2), np.float32)
        data = _scene(ball, box)
        data.cell_data["RGB"] = np.vstack([np.tile([200, 0, 0], (ball.n_cells, 1)),
                                           np.tile([0, 0, 200], (box.n_cells, 1))]).astype(np.uint8)
        data._preview_texture = "texture"
        data._preview_up_axis = "Z"
        result = scene_parts.without_enclosure(data)
        assert result.n_cells == ball.n_cells
        np.testing.assert_array_equal(result.active_texture_coordinates, ball.active_texture_coordinates)
        assert (np.asarray(result.cell_data["RGB"]) == [200, 0, 0]).all()
        assert result._preview_texture == "texture" and result._preview_up_axis == "Z"

    def test_a_hollow_product_shell_around_a_small_part_is_left_as_it_is(self):
        """The housing carries the scene's detail: it is the asset, not an environment."""
        import pyvista as pv

        housing = _part(_box_mesh(_box((-5, -5, -5), (5, 5, 5)), level=30))
        inner = _part(pv.Cube(center=(0, 0, 0), x_length=1, y_length=1, z_length=1))
        data = _scene(inner, housing)
        assert scene_parts.enclosure_screen(scene_parts.part_bounds(data)) is not None
        assert scene_parts.without_enclosure(data) is data

    def test_an_open_curved_backdrop_is_left_as_it_is(self):
        """A studio sweep (a floor curving up into a back wall) is open to the front, top and sides."""
        import pyvista as pv

        # Profile in (z, y): the floor from z = 6 to z = -2, a quarter circle of radius 8 rising behind
        # the model, and a back wall up to y = 16; extruded from x = -10 to x = 10.
        angles = np.linspace(0.0, np.pi / 2, 12)
        profile = ([(6.0, 0.0), (2.0, 0.0)] + [(-2.0 - 8.0 * np.sin(a), 8.0 - 8.0 * np.cos(a)) for a in angles]
                   + [(-10.0, 16.0)])
        xs = np.linspace(-10.0, 10.0, 5)
        points = np.array([(x, y, z) for x in xs for z, y in profile])
        n = len(profile)
        quads = [[4, i * n + j, i * n + j + 1, (i + 1) * n + j + 1, (i + 1) * n + j]
                 for i in range(len(xs) - 1) for j in range(n - 1)]
        data = _scene(*_model_parts(), _part(pv.PolyData(points, np.asarray(quads).ravel())))
        assert scene_parts.enclosure_screen(scene_parts.part_bounds(data)) is not None
        assert scene_parts.without_enclosure(data) is data

    def test_environment_reaching_into_the_model_is_left_as_it_is(self):
        """A large shell part with points inside the content (a panel through the model) is in doubt."""
        import pyvista as pv

        model = _model_parts()
        shell = _part(_box_mesh(ROOM)).append_polydata(_part(pv.Plane(center=(0, 0.5, 0), i_size=0.2, j_size=0.2)))
        data = _scene(*model, shell)
        assert scene_parts.enclosure_screen(scene_parts.part_bounds(data)) is not None
        assert scene_parts.without_enclosure(data) is data

    def test_point_counts_that_no_longer_match_leave_the_scene_as_it_is(self):
        data = _scene(*_model_parts(), _part(_box_mesh(ROOM)))
        data.field_data[scene_parts.PART_POINTS] = np.asarray(data.field_data[scene_parts.PART_POINTS]) + 1
        assert scene_parts.without_enclosure(data) is data
        del data.field_data[scene_parts.PART_POINTS]
        assert scene_parts.without_enclosure(data) is data

    def test_a_model_on_a_ground_plane_is_left_as_it_is(self):
        import pyvista as pv

        ground = _part(pv.Plane(center=(0, 0, 0), direction=(0, 1, 0), i_size=50, j_size=50))
        data = _scene(*_model_parts(), ground)
        assert scene_parts.without_enclosure(data) is data


def _coverage(frame):
    return float((frame[..., 3] > 0).mean())


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestEnclosureRender:
    @pytest.mark.parametrize("name", sorted(ENCLOSURES))
    def test_every_orbit_frame_matches_the_model_rendered_alone(self, name):
        from preview_pipeline import renderer

        model = _model_parts(MODEL_RGB)
        treated = renderer.generate_rotating_frames(
            scene_parts.without_enclosure(_scene(*model, *ENCLOSURES[name]())), n_frames=12, resolution=(160, 120))
        alone = renderer.generate_rotating_frames(_scene(*model), n_frames=12, resolution=(160, 120))
        for treated_frame, alone_frame in zip(treated, alone):
            assert 0.05 < _coverage(alone_frame) < 0.95
            assert np.array_equal(treated_frame, alone_frame)

    def test_the_whole_scene_rendered_showed_only_the_box(self):
        """Without the treatment the preview is the outside of the box: no pixel shows the model."""
        from conftest import colour_shares
        from preview_pipeline import renderer

        frame = renderer.generate_static_frame(_scene(*_model_parts(MODEL_RGB), _part(_box_mesh(ROOM), SHELL_RGB)),
                                               resolution=(160, 120))
        assert colour_shares(frame, [MODEL_RGB])[0] == 0.0

    def test_a_hollow_product_renders_as_before(self):
        import pyvista as pv

        from preview_pipeline import renderer

        housing = _part(_box_mesh(_box((-5, -5, -5), (5, 5, 5)), level=30), SHELL_RGB)
        inner = _part(pv.Cube(center=(0, 0, 0), x_length=1, y_length=1, z_length=1), MODEL_RGB)
        data = _scene(inner, housing)
        plain = data.copy()
        plain.field_data.clear()
        frame = renderer.generate_static_frame(scene_parts.without_enclosure(data), resolution=(160, 120))
        assert np.array_equal(frame, renderer.generate_static_frame(plain, resolution=(160, 120)))


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestPipelineLoad:
    def test_a_glb_model_inside_an_environment_box_loads_as_the_model_alone(self, tmp_path):
        import trimesh

        from preview_pipeline import core

        def scene(with_box):
            s = trimesh.Scene()
            s.add_geometry(trimesh.creation.icosphere(subdivisions=4, radius=0.4).apply_translation((0, 0.5, 0)),
                           geom_name="model")
            s.add_geometry(trimesh.creation.box(extents=(0.2, 0.2, 0.2)).apply_translation((0.0, 0.1, 0.45)),
                           geom_name="stand")
            if with_box:
                s.add_geometry(trimesh.creation.box(bounds=[[-10, 0, -10], [10, 8, 10]]), geom_name="environment")
            return s

        paths = {}
        for with_box in (True, False):
            paths[with_box] = tmp_path / f"scene-{with_box}.glb"
            scene(with_box).export(str(paths[with_box]))
        loaded = scene_parts.without_enclosure(core._load_file(str(paths[True]), ".glb"))
        alone = core._load_file(str(paths[False]), ".glb")
        np.testing.assert_allclose(np.asarray(loaded.points), np.asarray(alone.points))
        assert loaded.n_cells == alone.n_cells

    def test_the_run_leaves_the_enclosure_out_before_the_budget(self):
        """The orchestration calls ``without_enclosure`` on the loaded data, before the budgets."""
        import inspect

        from preview_pipeline import core

        source = inspect.getsource(core._run_preview_stage)
        assert source.index("scene_parts.without_enclosure(pv_data)") < source.index("apply_budgets(pv_data")


@pytest.mark.unit
@requires_real("pyvista", "vtk", "cadquery")
class TestStepEnclosure:
    def test_a_millimetre_part_inside_an_environment_box_tessellates_as_if_alone(self, tmp_path):
        """The tessellation's deflection follows the scene's size, so the part is tessellated again on
        its own once the enclosure is left out: as fine as from a file holding it alone."""
        import cadquery as cq

        from preview_pipeline import core

        def save(with_box):
            assembly = cq.Assembly()
            gear = (cq.Workplane().circle(3.0).extrude(1.5).faces(">Z").workplane().hole(1.2)
                    .edges("%CIRCLE").fillet(0.2))
            assembly.add(gear, name="part")
            if with_box:
                outer = cq.Workplane().box(60, 60, 60).translate((0, 0, 25))
                inner = cq.Workplane().box(58, 58, 58).translate((0, 0, 25))
                assembly.add(outer.cut(inner), name="environment")
            path = tmp_path / f"part-{with_box}.step"
            assembly.save(str(path))
            return path

        inside = core._load_file(str(save(True)), ".step")
        alone = core._load_file(str(save(False)), ".step")
        assert inside.n_points == alone.n_points
        np.testing.assert_allclose(np.asarray(inside.points), np.asarray(alone.points), atol=1e-9)
