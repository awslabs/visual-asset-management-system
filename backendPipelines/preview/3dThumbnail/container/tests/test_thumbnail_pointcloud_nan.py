#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Point clouds that hold NaN points.

An organized PCD (a depth camera's grid) stores NaN for every pixel that saw nothing. The NaN points
reached the percentile framing, which came out as NaN, so the preview was an empty, fully transparent
frame and the run reported plain success. Non-finite points are now dropped in ``load()``, which every
point-cloud reader returns through, before any sampling or framing, and a cloud with no valid point
fails with a clear message. Point clouds that the mesh handler loads (a vertex-only PLY, a scene's point
geometry, a Draco file without faces) go through the same filter.

The rendering dependencies are stubbed by tests/conftest.py; numpy is real, as the filtering is numpy.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

np = pytest.importorskip("numpy", reason="the NaN filtering operates on real numpy arrays")

# Every reader ``load()`` dispatches to, with a file extension it handles.
_READERS = [
    ("_load_las", "cloud.las"),
    ("_load_las", "cloud.laz"),
    ("_load_e57", "cloud.e57"),
    ("_load_ptx", "cloud.ptx"),
    ("_load_pcd", "cloud.pcd"),
    ("_load_faro", "cloud.fls"),
]


def _handler():
    from preview_pipeline.format_handlers import pointcloud_handler
    return pointcloud_handler


def _cloud_with_nan_holes(n_valid=400, n_nan=1200):
    rng = np.random.default_rng(3)
    valid = rng.normal(0.0, 0.2, size=(n_valid, 3))
    points = np.vstack([valid, np.full((n_nan, 3), np.nan)])
    order = rng.permutation(len(points))
    colours = np.zeros((len(points), 3), dtype=np.uint8)
    colours[:n_valid] = 200  # valid points are grey, NaN holes black
    return points[order], colours[order], valid


def _load_through(reader, filename, points, colours):
    """Run ``load()`` with ``reader`` returning ``points``/``colours``; return what reached PolyData."""
    handler = _handler()
    fake_pv = MagicMock()
    with patch.object(handler, reader, return_value=(points, colours)), patch.object(handler, "pv", fake_pv):
        handler.load(f"/tmp/{filename}")
    (built_points,), _ = fake_pv.PolyData.call_args
    built_colours = fake_pv.PolyData.return_value.point_data.__setitem__.call_args
    return built_points, (built_colours[0][1] if built_colours else None)


@pytest.mark.unit
class TestFinitePoints:
    def test_points_with_a_nan_or_infinite_coordinate_are_dropped_with_their_colours(self):
        points = np.array([[0.0, 0.0, 0.0], [np.nan, 1.0, 1.0], [1.0, np.inf, 0.0], [2.0, 2.0, 2.0]])
        colours = np.array([[1, 1, 1], [2, 2, 2], [3, 3, 3], [4, 4, 4]], dtype=np.uint8)
        kept, kept_colours = _handler().finite_points(points, colours)
        np.testing.assert_array_equal(kept, [[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]])
        np.testing.assert_array_equal(kept_colours, [[1, 1, 1], [4, 4, 4]])

    def test_a_finite_cloud_is_returned_as_it_is(self):
        points = np.arange(12, dtype=np.float64).reshape(4, 3)
        kept, colours = _handler().finite_points(points, None)
        assert kept is points and colours is None

    def test_a_cloud_without_a_valid_point_fails_clearly(self):
        with pytest.raises(ValueError, match="no valid points"):
            _handler().finite_points(np.full((5, 3), np.nan))


@pytest.mark.unit
class TestLoadDropsNonFinitePoints:
    @pytest.mark.parametrize("reader,filename", _READERS)
    def test_only_the_valid_points_and_their_colours_reach_the_renderer(self, reader, filename):
        points, colours, valid = _cloud_with_nan_holes()
        built_points, built_colours = _load_through(reader, filename, points, colours)
        assert len(built_points) == len(valid)
        assert np.isfinite(built_points).all()
        np.testing.assert_array_equal(np.sort(built_points, axis=0), np.sort(valid, axis=0))
        assert built_colours is not None and (np.asarray(built_colours) == 200).all()

    @pytest.mark.parametrize("reader,filename", _READERS)
    def test_a_cloud_of_nan_points_only_fails_the_load_with_no_valid_points(self, reader, filename):
        with pytest.raises(ValueError, match="no valid points"):
            _load_through(reader, filename, np.full((50, 3), np.nan), None)


# --- Point clouds loaded by the mesh handler -------------------------------------------------------

class _FakeTrimesh:
    def __init__(self):
        self.vertices = np.zeros((3, 3))
        self.faces = np.array([[0, 1, 2]])
        self.visual = None


class _FakePointCloud:
    def __init__(self, vertices, colors=None):
        self.vertices = vertices
        self.colors = colors


class _FakeScene:
    def __init__(self, geometries):
        self._geometries = geometries

    def dump(self):
        return list(self._geometries)


def _mesh_handler():
    from preview_pipeline.format_handlers import mesh_handler
    return mesh_handler


def _reviewer_ply_cloud():
    """The vertex-only PLY that rendered blank: 2,000 valid points and 6,000 NaN points, RGBA colours."""
    rng = np.random.default_rng(7)
    valid = rng.normal(0.0, 1.0, size=(2000, 3))
    points = np.vstack([valid, np.full((6000, 3), np.nan)])
    order = rng.permutation(len(points))
    colours = np.zeros((len(points), 4), dtype=np.uint8)
    colours[:2000] = (200, 100, 50, 255)
    return points[order], colours[order], valid


def _mesh_load(loaded, filename="cloud.ply"):
    """Run mesh_handler.load() with trimesh returning ``loaded``; return what reached PolyData."""
    handler = _mesh_handler()
    fake_trimesh = SimpleNamespace(
        load=MagicMock(return_value=loaded), Scene=_FakeScene, Trimesh=_FakeTrimesh, PointCloud=_FakePointCloud,
    )
    fake_pv = MagicMock()
    with patch.object(handler, "trimesh", fake_trimesh), patch.object(handler, "pv", fake_pv):
        handler.load(f"/tmp/{filename}")
    (built_points, *_), _ = fake_pv.PolyData.call_args
    built_colours = fake_pv.PolyData.return_value.point_data.__setitem__.call_args
    return built_points, (built_colours[0][1] if built_colours else None)


def _assert_only_valid(built_points, built_colours, valid):
    assert len(built_points) == len(valid)
    assert np.isfinite(built_points).all()
    np.testing.assert_array_equal(np.sort(built_points, axis=0), np.sort(valid, axis=0))
    assert built_colours is not None
    np.testing.assert_array_equal(np.unique(np.asarray(built_colours), axis=0), [[200, 100, 50]])


@pytest.mark.unit
class TestMeshHandlerPointCloudsDropNonFinitePoints:
    def test_a_vertex_only_ply_keeps_only_its_valid_points_and_their_colours(self):
        points, colours, valid = _reviewer_ply_cloud()
        built_points, built_colours = _mesh_load(_FakePointCloud(points, colours))
        _assert_only_valid(built_points, built_colours, valid)

    def test_a_scene_point_cloud_next_to_a_mesh_keeps_only_its_valid_points(self):
        points, colours, valid = _reviewer_ply_cloud()
        scene = _FakeScene([_FakeTrimesh(), _FakePointCloud(points, colours)])
        built_points, built_colours = _mesh_load(scene, "scene.glb")
        _assert_only_valid(built_points, built_colours, valid)

    def test_a_vertex_only_ply_of_nan_points_only_fails_with_no_valid_points(self):
        with pytest.raises(ValueError, match="no valid points"):
            _mesh_load(_FakePointCloud(np.full((50, 3), np.nan)))

    def test_a_scene_point_cloud_of_nan_points_only_leaves_the_scene_mesh_to_render(self):
        mesh = _FakeTrimesh()
        scene = _FakeScene([_FakePointCloud(np.full((50, 3), np.nan)), mesh])
        built_points, _ = _mesh_load(scene, "scene.glb")
        np.testing.assert_array_equal(built_points, mesh.vertices)

    def test_a_scene_of_nan_points_only_fails_with_no_valid_points(self):
        with pytest.raises(ValueError, match="no valid points"):
            _mesh_load(_FakeScene([_FakePointCloud(np.full((50, 3), np.nan))]), "scene.glb")

    def test_a_finite_vertex_only_ply_reaches_the_renderer_unchanged(self):
        points = np.arange(30, dtype=np.float64).reshape(10, 3)
        colours = np.full((10, 4), 9, dtype=np.uint8)
        built_points, built_colours = _mesh_load(_FakePointCloud(points, colours))
        np.testing.assert_array_equal(built_points, points)
        np.testing.assert_array_equal(built_colours, colours[:, :3])

    def test_a_draco_file_without_faces_keeps_only_its_valid_points(self, tmp_path):
        points, _, valid = _reviewer_ply_cloud()
        drc = tmp_path / "cloud.drc"
        drc.write_bytes(b"draco")
        fake_draco = MagicMock()
        fake_draco.decode.return_value = SimpleNamespace(points=points.ravel(), faces=None)
        handler = _mesh_handler()
        fake_pv = MagicMock()
        with patch.dict("sys.modules", {"DracoPy": fake_draco}), patch.object(handler, "pv", fake_pv):
            handler.load(str(drc))
        (built_points,), _ = fake_pv.PolyData.call_args
        assert len(built_points) == len(valid) and np.isfinite(built_points).all()
