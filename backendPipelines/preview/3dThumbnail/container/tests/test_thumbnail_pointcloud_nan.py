#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Point clouds that hold NaN points.

An organized PCD (a depth camera's grid) stores NaN for every pixel that saw nothing. The NaN points
reached the percentile framing, which came out as NaN, so the preview was an empty, fully transparent
frame and the run reported plain success. Non-finite points are now dropped in the shared point-cloud
path, before any sampling or framing, and a cloud with no valid point fails with a clear message.

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
