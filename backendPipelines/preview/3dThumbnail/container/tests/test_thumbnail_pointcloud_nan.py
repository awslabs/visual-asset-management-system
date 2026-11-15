#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Point clouds that hold NaN points.

An organized PCD (a depth camera's grid) stores NaN for every pixel that saw nothing. The NaN points
reached the percentile framing, which came out as NaN, so the preview was an empty, fully transparent
frame and the run reported plain success. Non-finite points are now dropped in the shared point-cloud
path, before any sampling or framing, and a cloud with no valid point fails with a clear message. The
point clouds the mesh handler loads (a glTF/GLB scene's point geometry, a vertex-only OBJ, a Draco file
without faces) go through the same filter; in a scene, a point cloud with no valid point is left out
and the scene's meshes still render.

The PCD tests read a real file through Open3D and render it, so they run inside the pipeline image.
"""

import numpy as np
import pytest

from conftest import requires_real


def _write_ascii_pcd(path, points):
    """An organized ASCII PCD (width x 1 grid) of ``points``, NaN rows included."""
    lines = ["# .PCD v0.7 - Point Cloud Data file format", "VERSION 0.7", "FIELDS x y z", "SIZE 4 4 4",
             "TYPE F F F", "COUNT 1 1 1", f"WIDTH {len(points)}", "HEIGHT 1", "VIEWPOINT 0 0 0 1 0 0 0",
             f"POINTS {len(points)}", "DATA ascii"]
    lines += [" ".join("nan" if np.isnan(v) else f"{v:.6f}" for v in point) for point in points]
    path.write_text("\n".join(lines) + "\n")


def _cloud_with_nan_holes(n_valid=4000, n_nan=12000):
    rng = np.random.default_rng(3)
    valid = rng.normal(0.0, 0.2, size=(n_valid, 3))
    holes = np.full((n_nan, 3), np.nan)
    points = np.vstack([valid, holes])
    return points[rng.permutation(len(points))], valid


@pytest.mark.unit
class TestFinitePoints:
    def test_points_with_a_nan_or_infinite_coordinate_are_dropped_with_their_colours(self):
        from preview_pipeline.format_handlers import pointcloud_handler

        points = np.array([[0.0, 0.0, 0.0], [np.nan, 1.0, 1.0], [1.0, np.inf, 0.0], [2.0, 2.0, 2.0]])
        colours = np.array([[1, 1, 1], [2, 2, 2], [3, 3, 3], [4, 4, 4]], dtype=np.uint8)
        kept, kept_colours = pointcloud_handler.finite_points(points, colours)
        np.testing.assert_array_equal(kept, [[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]])
        np.testing.assert_array_equal(kept_colours, [[1, 1, 1], [4, 4, 4]])

    def test_a_finite_cloud_is_returned_as_it_is(self):
        from preview_pipeline.format_handlers import pointcloud_handler

        points = np.arange(12, dtype=np.float64).reshape(4, 3)
        kept, colours = pointcloud_handler.finite_points(points, None)
        assert kept is points and colours is None

    def test_a_cloud_without_a_valid_point_fails_clearly(self):
        from preview_pipeline.format_handlers import pointcloud_handler

        with pytest.raises(ValueError, match="no valid points"):
            pointcloud_handler.finite_points(np.full((5, 3), np.nan))


@pytest.mark.unit
@requires_real("pyvista", "vtk", "open3d")
class TestOrganizedPcd:
    def test_a_pcd_with_nan_points_renders_the_valid_points(self, tmp_path):
        from preview_pipeline import core, renderer

        points, valid = _cloud_with_nan_holes()
        path = tmp_path / "organized.pcd"
        _write_ascii_pcd(path, points)
        data = core._load_file(str(path), ".pcd")
        assert data.n_points == len(valid)
        assert np.isfinite(np.asarray(data.points)).all()
        focal, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=False)
        assert np.isfinite(focal).all() and np.isfinite(radius)
        frame = renderer.generate_static_frame(data, resolution=(160, 120))
        assert (frame[..., 3] > 0).mean() > 0.01

    def test_a_pcd_of_nan_points_only_fails_the_load_with_no_valid_points(self, tmp_path):
        from preview_pipeline import core

        path = tmp_path / "empty.pcd"
        _write_ascii_pcd(path, np.full((50, 3), np.nan))
        with pytest.raises(ValueError, match="no valid points"):
            core._load_file(str(path), ".pcd")


# --- Point clouds the mesh handler loads: a scene's point geometry, a vertex-only OBJ, a Draco file ---

_CLOUD_RGBA = (60, 160, 220, 255)


def _nan_cloud(n_valid=2000, n_nan=6000, offset=(0.0, 0.0, 0.0)):
    """A trimesh PointCloud of ``n_valid`` coloured points and ``n_nan`` NaN points, shuffled."""
    import trimesh

    points, valid = _cloud_with_nan_holes(n_valid, n_nan)
    points = points + np.asarray(offset)
    colours = np.tile(np.asarray(_CLOUD_RGBA, dtype=np.uint8), (len(points), 1))
    return trimesh.PointCloud(points, colors=colours), valid + np.asarray(offset)


def _export_scene(path, *geometries):
    import trimesh

    scene = trimesh.Scene()
    for index, geom in enumerate(geometries):
        scene.add_geometry(geom, geom_name=f"part{index}")
    scene.export(str(path))
    return path


def _assert_renders(data):
    from preview_pipeline import renderer

    assert np.isfinite(np.asarray(data.points)).all()
    focal, radius, _ = renderer._compute_camera_framing(data, use_full_bounds=False)
    assert np.isfinite(focal).all() and np.isfinite(radius)
    frame = renderer.generate_static_frame(data, resolution=(160, 120))
    assert (frame[..., 3] > 0).mean() > 0.001


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestMeshHandlerPointClouds:
    def test_a_glb_point_cloud_with_nan_points_renders_the_valid_points(self, tmp_path):
        from preview_pipeline import core

        cloud, valid = _nan_cloud()
        data = core._load_file(str(_export_scene(tmp_path / "cloud.glb", cloud)), ".glb")
        assert data.n_points == len(valid)
        assert (np.asarray(data.point_data["RGB"]) == _CLOUD_RGBA[:3]).all()
        _assert_renders(data)

    def test_a_glb_mixing_a_mesh_and_a_nan_point_cloud_draws_the_mesh_and_the_valid_points(self, tmp_path):
        import trimesh

        from preview_pipeline import core

        box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        cloud, valid = _nan_cloud(offset=(2.0, 0.0, 0.0))
        data = core._load_file(str(_export_scene(tmp_path / "mixed.glb", box, cloud)), ".glb")
        assert data.n_verts == len(valid)
        assert data.n_points == len(box.vertices) + len(valid)
        _assert_renders(data)

    def test_a_glb_whose_point_cloud_has_no_valid_point_still_draws_its_mesh(self, tmp_path):
        import trimesh

        from preview_pipeline import core

        box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        cloud = trimesh.PointCloud(np.full((300, 3), np.nan))
        data = core._load_file(str(_export_scene(tmp_path / "mixed-empty.glb", box, cloud)), ".glb")
        assert data.n_points == len(box.vertices) and data.n_verts == 0
        _assert_renders(data)

    def test_a_glb_of_nan_points_only_fails_the_load_with_no_valid_points(self, tmp_path):
        import trimesh

        from preview_pipeline import core

        path = _export_scene(tmp_path / "empty.glb", trimesh.PointCloud(np.full((300, 3), np.nan)))
        with pytest.raises(ValueError, match="no valid points"):
            core._load_file(str(path), ".glb")

    def test_a_vertex_only_obj_with_nan_points_renders_the_valid_points(self, tmp_path):
        from preview_pipeline import core

        points, valid = _cloud_with_nan_holes(2000, 6000)
        path = tmp_path / "cloud.obj"
        path.write_text("".join("v %s %s %s\n" % tuple("nan" if np.isnan(v) else f"{v:.6f}" for v in point)
                                for point in points))
        data = core._load_file(str(path), ".obj")
        assert data.n_points == len(valid)
        _assert_renders(data)

    def test_a_finite_glb_point_cloud_is_loaded_unchanged(self, tmp_path):
        import trimesh

        from preview_pipeline import core

        points = np.random.default_rng(9).uniform(size=(500, 3))
        data = core._load_file(str(_export_scene(tmp_path / "finite.glb", trimesh.PointCloud(points))), ".glb")
        np.testing.assert_allclose(np.sort(np.asarray(data.points), axis=0), np.sort(points, axis=0), rtol=1e-6)


@pytest.mark.unit
def test_a_draco_point_cloud_drops_its_nan_points_with_their_colours(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from preview_pipeline.format_handlers import mesh_handler

    points, valid = _cloud_with_nan_holes(2000, 6000)
    colours = np.where(np.isnan(points).any(axis=1)[:, None], 0, 200).repeat(3, axis=1).astype(np.uint8)
    drc = tmp_path / "cloud.drc"
    drc.write_bytes(b"draco")
    fake_draco = MagicMock()
    fake_draco.decode.return_value = SimpleNamespace(points=points.ravel(), faces=None, colors=colours)
    fake_pv = MagicMock()
    with patch.dict("sys.modules", {"DracoPy": fake_draco}), patch.object(mesh_handler, "pv", fake_pv):
        mesh_handler._load_draco(str(drc))
    (built_points,), _ = fake_pv.PolyData.call_args
    np.testing.assert_array_equal(np.sort(built_points, axis=0), np.sort(valid, axis=0))
    (_, built_colours), _ = fake_pv.PolyData.return_value.point_data.__setitem__.call_args
    assert len(built_colours) == len(valid) and (built_colours == 200).all()
