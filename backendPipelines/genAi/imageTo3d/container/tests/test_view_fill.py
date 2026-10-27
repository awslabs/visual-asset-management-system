# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The view-fill arithmetic without Blender: which faces a camera sees, and where a picture's pixels land in
the atlas. The Blender side (`bl_view_fill.py`) is exercised in `test_blender_tools_live.py`."""

import os
import sys

import numpy as np
import pytest

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blenderagent_tools")
sys.path.insert(0, _TOOLS_DIR)
import view_fill  # noqa: E402

pytestmark = pytest.mark.unit


def test_a_face_turned_to_minus_y_faces_the_front_camera_and_not_the_back():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    normals = view_fill.face_normals(vertices, np.array([[0, 1, 2]]))
    assert np.allclose(normals, [[0, -1, 0]])
    assert view_fill.facing(normals, *view_fill.VIEWS["front"])[0] == pytest.approx(1.0)
    assert view_fill.facing(normals, *view_fill.VIEWS["back"])[0] == pytest.approx(-1.0)
    assert abs(view_fill.facing(normals, *view_fill.VIEWS["left"])[0]) < 1e-9


def test_the_nearer_triangle_wins_the_depth_buffer():
    # A far quad covering the frame as two triangles, and a near triangle over the lower-right half.
    cam_xy = np.array([[0, 0], [1, 0], [0, 1], [1, 1], [0, 0], [1, 0], [1, 1]], dtype=float)
    depth = np.array([5, 5, 5, 5, 1, 1, 1], dtype=float)
    triangles = np.array([[0, 1, 3], [0, 3, 2], [4, 5, 6]])
    zbuf, ids = view_fill.rasterise_view(cam_xy, depth, triangles, np.array([True, True, True]), 8, 8)
    assert (ids >= 0).all(), "the frame is covered"
    assert ids[7, 7] == 2 and zbuf[7, 7] == pytest.approx(1.0), "bottom-right: the near triangle"
    assert ids[0, 0] in (0, 1) and zbuf[0, 0] == pytest.approx(5.0), "top-left: only the far quad"
    zbuf, ids = view_fill.rasterise_view(cam_xy, depth, triangles, np.array([True, True, False]), 8, 8)
    assert ids[7, 7] in (0, 1) and zbuf[7, 7] == pytest.approx(5.0), "a triangle not kept is not seen"


def test_a_view_mask_marks_the_pixels_of_the_triangles_to_fill_and_grows_them():
    ids = np.array([[-1, 0, 0], [1, 1, -1], [-1, -1, -1]])
    eligible = np.array([False, True])
    mask = view_fill.view_mask(ids, eligible, dilation=0)
    assert mask.tolist() == [[False, False, False], [True, True, False], [False, False, False]]
    grown = view_fill.view_mask(ids, eligible, dilation=1)
    assert grown.sum() > mask.sum() and grown[0, 0] and grown[2, 1]


def test_the_mask_stays_clear_of_the_background_so_the_outline_is_pinned():
    # A 6x6 frame: background in the outer ring, one eligible triangle over the middle 4x4.
    ids = np.full((6, 6), -1)
    ids[1:5, 1:5] = 0
    eligible = np.array([True])
    full = view_fill.view_mask(ids, eligible, dilation=0, edge_margin=0)
    assert full.sum() == 16
    pinned = view_fill.view_mask(ids, eligible, dilation=2, edge_margin=1)
    assert pinned.sum() == 4 and pinned[2:4, 2:4].all(), "only the pixels a full pixel inside the outline"
    assert not pinned[0].any() and not pinned[:, 0].any(), "never the background, however much it is grown"


def test_a_view_already_tried_on_a_face_is_not_suggested_for_it_again():
    normals = np.array([[0, 1, 0]] * 5 + [[1, 0, 0]] * 2, dtype=float)
    remaining = np.ones(7, dtype=bool)
    tried = np.zeros(7, dtype=np.int64)
    assert view_fill.suggest_view(normals, remaining, 0.3, tried=tried) == ("back", 5)
    tried[:5] |= view_fill.view_bit("back")  # applied from the back; these five were hidden there
    assert view_fill.suggest_view(normals, remaining, 0.3, tried=tried) == ("left", 2)
    tried[5:] |= view_fill.view_bit("left")
    # The three-quarter view also faces +X, and has not been tried on those two.
    assert view_fill.suggest_view(normals, remaining, 0.3, tried=tried) == ("threequarter", 2)
    tried[5:] |= view_fill.view_bit("threequarter")
    assert view_fill.suggest_view(normals, remaining, 0.3, tried=tried) == (None, 0)


def test_the_picture_lands_on_the_texels_the_camera_saw_and_nowhere_else():
    # One triangle over the lower-left half of a 16x16 atlas, a flat card facing the camera so its frame
    # coordinates equal its uvs. The picture is a red ramp left to right: a texel's red says where it landed.
    tri_uv = np.array([[[0, 0], [1, 0], [0, 1]]], dtype=float)
    cam_xy = np.array([[0, 0], [1, 0], [0, 1]], dtype=float)
    depth = np.array([2.0, 2.0, 2.0])
    triangles = np.array([[0, 1, 2]])
    picture = np.zeros((32, 32, 3))
    picture[..., 0] = np.linspace(0, 1, 32)[None, :]
    zbuf = np.full((32, 32), 2.0)
    atlas = np.full((16, 16, 3), 0.5)
    written = view_fill.bake_view(atlas, tri_uv, triangles, np.array([True]), cam_xy, depth, zbuf, picture, 0.01)
    assert written[0] > 0
    assert atlas[15, 0, 0] < 0.1 and atlas[15, 8, 0] > 0.4, "left edge reads the ramp's left, further right more red"
    assert atlas[0, 15].tolist() == [0.5, 0.5, 0.5], "outside the triangle nothing moved"
    # Something nearer over the right half of the frame: the texels behind it are left alone.
    atlas = np.full((16, 16, 3), 0.5)
    zbuf[:, 16:] = 1.0
    view_fill.bake_view(atlas, tri_uv, triangles, np.array([True]), cam_xy, depth, zbuf, picture, 0.01)
    assert atlas[15, 2, 0] < 0.2 and atlas[15, 12].tolist() == [0.5, 0.5, 0.5]


def test_the_suggested_view_is_the_one_facing_the_most_unfilled_faces():
    normals = np.array([[1, 0, 0]] * 10 + [[0, 1, 0]] * 2, dtype=float)
    remaining = np.ones(12, dtype=bool)
    assert view_fill.suggest_view(normals, remaining, 0.3) == ("left", 10)
    remaining[:10] = False
    assert view_fill.suggest_view(normals, remaining, 0.3) == ("back", 2)
    assert view_fill.suggest_view(normals, np.zeros(12, dtype=bool), 0.3) == (None, 0)
