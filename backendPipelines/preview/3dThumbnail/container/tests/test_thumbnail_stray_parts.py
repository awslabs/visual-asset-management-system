#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Framing a model that has one tiny part far away.

A mesh is framed on its full bounds, so a 2 cm cube left 50 m from a 6 cm model shrank the model to
nothing: an empty, fully transparent preview that still reported success. The camera now leaves out
of its framing a stray part -- outside the parts holding 95% of the points, at most half their size, and
widening the frame at least ``scene_parts.STRAY_SCALE`` times -- and still draws it. A single mesh is
framed on its 2nd-98th percentile bounds only when its full-bounds framing provably loses the model: its
dense region projects under a few pixels and a small full-bounds still covers almost nothing. A
near-flat sheet is never a stray; the backdrop rule decides it. Ordinary scenes, a large low-poly
model beside a dense label, a distant second object and a long mast with a dense antenna frame
exactly as before.
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import scene_parts


def _box(lo, hi):
    return list(lo) + list(hi)


MODEL = _box((-0.03, 0.0, -0.03), (0.03, 0.06, 0.03))
STRAY = _box((49.99, 0.0, -0.01), (50.01, 0.02, 0.01))


@pytest.mark.unit
class TestStrayRule:
    def test_a_tiny_far_part_is_a_stray(self):
        assert scene_parts.stray_parts([MODEL, STRAY], [400, 8]).tolist() == [False, True]

    def test_a_far_part_with_a_large_share_of_the_points_is_kept(self):
        """Two objects far apart, each a real share of the scene, are both framed."""
        assert not scene_parts.stray_parts([MODEL, STRAY], [400, 100]).any()

    def test_a_small_part_near_the_model_is_kept(self):
        """A screw beside an exploded assembly widens the frame less than ten times."""
        near = _box((0.2, 0.0, 0.0), (0.21, 0.01, 0.01))
        assert not scene_parts.stray_parts([MODEL, near], [400, 8]).any()

    def test_a_far_near_flat_sheet_is_never_a_stray(self):
        sheet = _box((40.0, 0.0, -5.0), (50.0, 0.0, 5.0))
        assert not scene_parts.stray_parts([MODEL, sheet], [400, 4]).any()

    def test_many_small_parts_together_holding_a_large_share_are_kept(self):
        """Parts are judged against the core holding 95% of the points: twenty 1-point-share parts far
        away make a fifth of the scene, so some of them are in the core and none is left out alone."""
        far = [_box((50.0 + i, 0.0, 0.0), (50.01 + i, 0.01, 0.01)) for i in range(20)]
        mask = scene_parts.stray_parts([MODEL] + far, [80] + [1] * 20)
        assert not mask.all() and not mask[0]

    def test_one_part_or_no_points_has_no_stray(self):
        assert not scene_parts.stray_parts([MODEL], [10]).any()
        assert not scene_parts.stray_parts([MODEL, STRAY], [0, 0]).any()

    def test_a_large_low_poly_model_is_not_a_stray_of_a_small_dense_label(self):
        """A 10 m model of 8 vertices and a 0.4 m label of thousands far away: the label holds the
        points, but the model is far larger, so it is kept and the scene frames as before."""
        model = _box((-5.0, -5.0, -5.0), (5.0, 5.0, 5.0))
        label = _box((149.8, -0.2, -0.2), (150.2, 0.2, 0.2))
        assert not scene_parts.stray_parts([model, label], [8, 2562]).any()

    def test_a_far_building_with_few_vertices_is_kept(self):
        """Two 10 m buildings 150 m apart, the far one under 5% of the vertices: the same size, so both
        are framed."""
        near = _box((-5.0, -5.0, -5.0), (5.0, 5.0, 5.0))
        far = _box((145.0, -5.0, -5.0), (155.0, 5.0, 5.0))
        assert not scene_parts.stray_parts([near, far], [10242, 8]).any()

    def test_an_exploded_ring_of_eight_identical_parts_is_framed_whole(self):
        """A dense hub and eight identical small parts in a ring far around it: whichever of them the
        point count puts in the core, the rest have a peer of their size in the core or at their
        distance, so none is left out."""
        ring = [_box((20 * np.cos(a) - 0.05, 0.0, 20 * np.sin(a) - 0.05),
                     (20 * np.cos(a) + 0.05, 0.1, 20 * np.sin(a) + 0.05))
                for a in np.linspace(0, 2 * np.pi, 8, endpoint=False)]
        hub = _box((-0.5, 0.0, -0.5), (0.5, 1.0, 0.5))
        for hub_points in (2000, 1000, 600):  # none, some or most of the ring in the core
            assert not scene_parts.stray_parts([hub] + ring, [hub_points] + [8] * 8).any(), hub_points

    def test_twin_leftovers_at_the_same_distance_are_both_strays(self):
        """Two identical small pieces 50 m out, on opposite sides: a pair is not a pattern."""
        left = _box((-50.01, 0.0, -0.01), (-49.99, 0.02, 0.01))
        right = _box((49.99, 0.0, -0.01), (50.01, 0.02, 0.01))
        assert scene_parts.stray_parts([MODEL, left, right], [400, 8, 8]).tolist() == [False, True, True]

    @pytest.mark.parametrize("cube", [0.5, 2.0])
    def test_a_far_cube_beside_a_car_with_wheels_of_its_size_is_a_stray(self, cube):
        """A 4.5 m car body with four 0.7 m wheels and a cube 100 m away: the wheels are in the core and
        of the cube's size, but near the car's centre, so they are no peers of the cube."""
        body = _box((-2.25, 0.3, -0.9), (2.25, 1.5, 0.9))
        wheels = [_box((x - 0.35, 0.0, z - 0.12), (x + 0.35, 0.7, z + 0.12))
                  for x in (-1.4, 1.4) for z in (-0.85, 0.85)]
        h = cube / 2.0
        far = _box((100.0 - h, 0.0, -h), (100.0 + h, cube, h))
        mask = scene_parts.stray_parts([body] + wheels + [far], [2000] + [400] * 4 + [8])
        assert mask.tolist() == [False] * 5 + [True]

    def test_two_leftovers_at_different_distances_are_both_strays(self):
        near_left = _box((49.99, 0.0, -0.01), (50.01, 0.02, 0.01))
        far_left = _box((-0.01, 0.0, 119.99), (0.01, 0.02, 120.01))
        assert scene_parts.stray_parts([MODEL, near_left, far_left], [400, 8, 8]).tolist() == [False, True, True]

    def test_a_far_part_over_half_the_core_is_kept_and_at_half_is_a_stray(self):
        core = _box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        over = _box((50.0, 0.0, 0.0), (50.51, 0.51, 0.51))
        half = _box((50.0, 0.0, 0.0), (50.5, 0.5, 0.5))
        assert not scene_parts.stray_parts([core, over], [1000, 8]).any()
        assert scene_parts.stray_parts([core, half], [1000, 8]).tolist() == [False, True]


def _merged(boxes, counts_per_box=None):
    import pyvista as pv

    parts = []
    for index, bounds in enumerate(boxes):
        lo, hi = bounds[:3], bounds[3:]
        level = 0 if counts_per_box is None else counts_per_box[index]
        part = pv.Box(bounds=(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]), level=level).triangulate()
        parts.append(pv.PolyData(np.asarray(part.points), np.asarray(part.faces)))
    merged = parts[0].append_polydata(*parts[1:])
    return scene_parts.record_parts(merged, [part.n_points for part in parts], max_parts=100)


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestStrayFraming:
    def test_the_camera_frames_the_model_and_logs_the_stray(self, caplog):
        from preview_pipeline import renderer

        alone = _merged([MODEL, _box((0, 0, 0), (0.0001, 0.0001, 0.0001))], [8, 0])
        _, model_radius, _ = renderer._compute_camera_framing(alone, use_full_bounds=True)
        with caplog.at_level("INFO"):
            focal, radius, _ = renderer._compute_camera_framing(_merged([MODEL, STRAY], [8, 0]),
                                                                use_full_bounds=True)
        assert radius == pytest.approx(model_radius, rel=0.05)
        np.testing.assert_allclose(focal, [0.0, 0.03, 0.0], atol=1e-6)
        assert "leaves out 1 of 2 parts as stray" in caplog.text

    def test_the_stray_is_still_drawn(self):
        from preview_pipeline import renderer

        data = _merged([MODEL, STRAY], [8, 0])
        assert np.asarray(data.points)[:, 0].max() == pytest.approx(50.01)  # the stray stays in the data
        frame = renderer.generate_static_frame(data, resolution=(160, 120))
        assert (frame[..., 3] > 0).mean() > 0.05

    def test_an_ordinary_scene_frames_as_before(self):
        from preview_pipeline import renderer

        near = _box((0.2, 0.0, 0.0), (0.21, 0.01, 0.01))
        data = _merged([MODEL, near], [8, 0])
        plain = data.copy()
        plain.field_data.clear()
        with_parts = renderer._compute_camera_framing(data, use_full_bounds=True)
        without = renderer._compute_camera_framing(plain, use_full_bounds=True)
        assert np.array_equal(with_parts[0], without[0]) and with_parts[1:] == without[1:]

    def test_part_points_travel_through_the_triangle_budget(self):
        import types

        from preview_pipeline import geometry_budget

        data = _merged([MODEL, STRAY], [8, 0])
        counts = scene_parts.part_points(data).tolist()
        budgets = types.SimpleNamespace(points=10 ** 6, triangles=data.n_cells // 4, memory_bytes=4 * 1024 ** 3)
        reduced = geometry_budget.apply_budgets(data, budgets)
        assert reduced is not data
        assert scene_parts.part_points(reduced).tolist() == counts


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestSparseMeshFraming:
    def test_one_mesh_with_a_far_vertex_cluster_is_framed_on_its_percentile_bounds(self, caplog):
        import pyvista as pv

        from preview_pipeline import renderer

        sphere = pv.Sphere(radius=0.03, theta_resolution=40, phi_resolution=40)
        cube = pv.Cube(center=(50, 0, 0), x_length=0.02, y_length=0.02, z_length=0.02).triangulate()
        mesh = sphere.append_polydata(cube)
        assert scene_parts.part_bounds(mesh) is None
        with caplog.at_level("INFO"):
            _, radius, _ = renderer._compute_camera_framing(mesh, use_full_bounds=True)
        assert radius < 1.0
        assert "framing the 2nd-98th percentile bounds" in caplog.text
        frame = renderer.generate_static_frame(mesh, resolution=(160, 120))
        assert (frame[..., 3] > 0).mean() > 0.05

    def test_a_mesh_with_dense_detail_is_framed_on_its_full_bounds(self):
        """Vertices crowded into a detailed region keep full-bounds framing: the full bounds are far
        under 25 times the percentile bounds."""
        import pyvista as pv

        from preview_pipeline import renderer

        body = pv.Cube(x_length=1.0, y_length=0.4, z_length=0.4).triangulate()
        detail = pv.Sphere(radius=0.05, center=(0.45, 0.2, 0.0), theta_resolution=120, phi_resolution=120)
        mesh = body.append_polydata(detail)
        focal, _, _ = renderer._compute_camera_framing(mesh, use_full_bounds=True)
        lo, hi = np.asarray(mesh.points).min(axis=0), np.asarray(mesh.points).max(axis=0)
        np.testing.assert_allclose(focal, (lo + hi) / 2)

    def test_a_long_low_poly_mast_with_a_dense_antenna_is_framed_whole(self, caplog):
        """A 30 m mast of 8 sides with a 1 m antenna of thousands of vertices at its top: the full
        bounds are 30 times the percentile bounds, but the mast draws on its full bounds, so it is not
        cropped to the antenna."""
        import pyvista as pv

        from preview_pipeline import renderer

        mast = pv.Cylinder(center=(0, 15, 0), direction=(0, 1, 0), radius=0.1, height=30, resolution=8).triangulate()
        antenna = pv.Sphere(radius=0.5, center=(0, 30, 0), theta_resolution=80, phi_resolution=80)
        mesh = mast.append_polydata(antenna)
        with caplog.at_level("INFO"):
            focal, _, _ = renderer._compute_camera_framing(mesh, use_full_bounds=True)
        lo, hi = np.asarray(mesh.points).min(axis=0), np.asarray(mesh.points).max(axis=0)
        np.testing.assert_allclose(focal, (lo + hi) / 2)
        assert "percentile bounds" not in caplog.text


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestGlbStray:
    def test_a_glb_model_with_a_far_stray_cube_is_framed_and_visible(self, tmp_path):
        import trimesh

        from preview_pipeline import core, renderer

        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.icosphere(subdivisions=3, radius=0.03), geom_name="model")
        scene.add_geometry(trimesh.creation.box(extents=(0.02, 0.02, 0.02)).apply_translation((50, 0, 0)),
                           geom_name="stray")
        path = tmp_path / "stray.glb"
        scene.export(str(path))
        data = core._normalize_up_axis(core._load_file(str(path), ".glb"), ".glb")
        frames = renderer.generate_rotating_frames(data, n_frames=4, resolution=(160, 120))
        assert all((frame[..., 3] > 0).mean() > 0.05 for frame in frames)

    def test_a_distant_second_object_is_framed_with_the_first(self, tmp_path):
        """A detailed sphere and a low-poly box of the same size 150 m away: the box holds under 5% of
        the vertices, but it is a second object, so the frame holds both, as before."""
        import trimesh

        from preview_pipeline import core, renderer

        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.icosphere(subdivisions=5, radius=5), geom_name="near")
        scene.add_geometry(trimesh.creation.box(extents=(10, 10, 10)).apply_translation((150, 0, 0)),
                           geom_name="far")
        path = tmp_path / "two.glb"
        scene.export(str(path))
        data = core._normalize_up_axis(core._load_file(str(path), ".glb"), ".glb")
        plain = data.copy()
        plain.field_data.clear()
        framed = renderer._compute_camera_framing(data, use_full_bounds=True)
        full = renderer._compute_camera_framing(plain, use_full_bounds=True)
        assert np.array_equal(framed[0], full[0]) and framed[1:] == full[1:]
