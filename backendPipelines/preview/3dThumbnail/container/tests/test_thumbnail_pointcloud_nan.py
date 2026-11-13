#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Point clouds that hold NaN points.

An organized PCD (a depth camera's grid) stores NaN for every pixel that saw nothing. The NaN points
reached the percentile framing, which came out as NaN, so the preview was an empty, fully transparent
frame and the run reported plain success. Non-finite points are now dropped in ``load()``, which every
point-cloud reader returns through, before any sampling or framing, and a cloud with no valid point
fails with a clear message.

The rendering dependencies are stubbed by tests/conftest.py; numpy is real, as the filtering is numpy.
"""

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
