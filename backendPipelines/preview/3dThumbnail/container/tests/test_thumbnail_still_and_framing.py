#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Still views, framing and mixed scenes (#466).

* A still output (``outputType`` jpg or png) took the middle frame of a 36-frame orbit -- the 180°
  view, the back of the model. It now renders one front three-quarter view and skips the orbit; the
  GIF size-limit fallback writes that same view.
* Framing used vertex percentiles for every format except USD and STEP, which crops the coarser parts
  of a mesh whose vertices concentrate in a finely detailed region. A mesh now frames on its full
  bounds; a point cloud keeps percentile framing, which exists to ignore stray points.
* A glTF/GLB/OBJ scene holding a point cloud returned that point cloud alone and dropped every mesh.

The orchestration and framing tests run anywhere; the render and scene-loading tests need the real
pyvista/VTK/trimesh and run inside the pipeline image.
"""

import types
from unittest.mock import MagicMock

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import renderer
from test_output_type import _run, OUTPUT_DIR, CONFIG_KEY


def _fake_data(points, n_cells, n_verts=None):
    data = types.SimpleNamespace(points=np.asarray(points, dtype=float), n_cells=n_cells)
    data.n_points = len(data.points)
    if n_verts is not None:
        data.n_verts = n_verts
    return data


@pytest.mark.unit
class TestStillView:
    @pytest.mark.parametrize("n_frames,index", [(1, 0), (8, 1), (36, 5), (12, 2)])
    def test_the_still_frame_index_is_the_orbit_frame_nearest_45_degrees(self, n_frames, index):
        assert renderer.still_view_frame_index(n_frames) == index
        if n_frames > 1:
            step = 360.0 / n_frames
            assert abs(index * step - renderer.STILL_VIEW_DEG) <= step / 2

    @pytest.mark.parametrize("output_type,writer", [("png", "save_png"), ("jpg", "save_jpeg")])
    def test_a_still_output_renders_the_still_view_only(self, tmp_path, output_type, writer):
        calls = {}
        _, key, used = _run(tmp_path, {CONFIG_KEY: output_type}, frame_count=36, calls=calls)
        assert key == f"{OUTPUT_DIR}test/pump.e57.previewFile.{output_type}"
        assert used == writer
        calls["static"].assert_called_once()
        calls["rotating"].assert_not_called()
        assert calls[writer].call_args.args[0] == "still-view"

    def test_the_gif_size_fallback_receives_the_front_view(self, tmp_path):
        calls = {}
        _run(tmp_path, {}, frame_count=36, calls=calls)
        calls["static"].assert_not_called()
        kwargs = calls["ensure_under_size_limit"].call_args.kwargs
        assert kwargs["still_frame"] == renderer.still_view_frame_index(36)

    def test_auto_with_a_single_orbit_frame_writes_that_frame(self, tmp_path):
        calls = {}
        _, key, used = _run(tmp_path, {}, frame_count=1, calls=calls)
        assert key.endswith(".previewFile.jpg") and used == "save_jpeg"
        assert calls["save_jpeg"].call_args.args[0] == 0


@pytest.mark.unit
class TestFraming:
    def test_point_cloud_detection(self):
        assert renderer.is_point_cloud(_fake_data([[0, 0, 0]], n_cells=0))
        assert renderer.is_point_cloud(_fake_data([[0, 0, 0], [1, 1, 1]], n_cells=2, n_verts=2))
        # A mixed scene: two vertex cells beside two faces.
        assert not renderer.is_point_cloud(_fake_data(np.zeros((5, 3)), n_cells=4, n_verts=2))
        # A mesh with as many faces as vertices is still a mesh when the vertex-cell count is known.
        assert not renderer.is_point_cloud(_fake_data(np.zeros((4, 3)), n_cells=4, n_verts=0))

    def test_a_mesh_frames_on_its_full_bounds_and_a_point_cloud_on_percentiles(self):
        assert renderer._resolve_full_bounds(_fake_data(np.zeros((3, 3)), 1, 0), None) is True
        assert renderer._resolve_full_bounds(_fake_data(np.zeros((3, 3)), 3, 3), None) is False
        assert renderer._resolve_full_bounds(_fake_data(np.zeros((3, 3)), 3, 3), True) is True

    def test_a_mesh_with_concentrated_detail_is_framed_whole(self):
        """A fine detail of 10,000 vertices inside a coarse 8-vertex box ten times its size: on
        percentiles the box's corners fall outside the framed region."""
        rng = np.random.default_rng(3)
        detail = rng.uniform(-0.5, 0.5, size=(10000, 3))
        box = np.array([[x, y, z] for x in (-5, 5) for y in (-5, 5) for z in (-5, 5)], float)
        mesh = _fake_data(np.vstack([detail, box]), n_cells=6000, n_verts=0)
        _, full_radius, _ = renderer._compute_camera_framing(
            mesh, use_full_bounds=renderer._resolve_full_bounds(mesh, None))
        _, percentile_radius, _ = renderer._compute_camera_framing(mesh, use_full_bounds=False)
        assert full_radius > 5 * percentile_radius
        # The framing distance covers the box's half-diagonal in the orbit plane.
        assert full_radius > np.hypot(5, 5)


def _cube_with_detail():
    import pyvista as pv

    return pv.Cube(x_length=2.0, y_length=1.0, z_length=0.5).triangulate()


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestStillRender:
    def test_the_still_frame_is_the_45_degree_orbit_view(self):
        """With 8 frames the orbit has a frame at exactly 45°; the still must match it."""
        data = _cube_with_detail()
        orbit = renderer.generate_rotating_frames(data, n_frames=8, resolution=(160, 120))
        still = renderer.generate_static_frame(data, resolution=(160, 120))
        front = orbit[renderer.still_view_frame_index(8)]
        back = orbit[4]
        assert np.mean(np.abs(still.astype(int) - front.astype(int))) < 1.0
        assert np.mean(np.abs(still.astype(int) - back.astype(int))) > 1.0

    def test_the_orbit_has_the_default_frame_count(self):
        frames = renderer.generate_rotating_frames(_cube_with_detail(), resolution=(80, 60))
        assert len(frames) == renderer.DEFAULT_N_FRAMES == 36


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestMixedScene:
    def test_a_scene_with_a_point_cloud_keeps_its_meshes(self, tmp_path):
        import trimesh

        from preview_pipeline.format_handlers import mesh_handler

        box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        cloud = trimesh.PointCloud(np.random.default_rng(5).uniform(2, 3, size=(50, 3)),
                                   colors=np.tile([255, 0, 0, 255], (50, 1)))
        scene = trimesh.Scene([box, cloud])
        path = tmp_path / "mixed.glb"
        scene.export(str(path))

        data = mesh_handler.load(str(path))
        assert data.n_points == len(box.vertices) + 50
        assert data.n_verts == 50
        assert data.n_cells == len(box.faces) + 50
        assert not renderer.is_point_cloud(data)
        assert data.point_data["RGB"].shape == (data.n_points, 3)
        frame = renderer.generate_static_frame(data, resolution=(160, 120))
        assert (frame[..., 3] == 255).any()

    def test_a_scene_of_point_clouds_only_still_loads(self, tmp_path):
        import trimesh

        from preview_pipeline.format_handlers import mesh_handler

        clouds = [trimesh.PointCloud(np.random.default_rng(i).uniform(size=(20, 3))) for i in (1, 2)]
        path = tmp_path / "clouds.glb"
        trimesh.Scene(clouds).export(str(path))

        data = mesh_handler.load(str(path))
        assert data.n_points == 40
        assert renderer.is_point_cloud(data)
