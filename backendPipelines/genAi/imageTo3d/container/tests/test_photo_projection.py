# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The photo-projection arithmetic without Blender: which vertices the photograph saw, how much of the
photograph each takes, and how a reconstruction's own colour is prepared to continue beside it. The Blender
side (`bl_project_photo.py`) is exercised in `test_blender_tools_live.py`."""

import os
import sys

import numpy as np
import pytest

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blenderagent_tools")
sys.path.insert(0, _TOOLS_DIR)
import photo_projection  # noqa: E402
import view_fill  # noqa: E402

pytestmark = pytest.mark.unit


def test_a_vertex_behind_the_front_surface_is_not_visible():
    # A near quad covering the frame as two triangles, and a far triangle behind its lower-right half.
    cam_xy = np.array([[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0.1], [0.9, 0.1], [0.9, 0.5]], dtype=float)
    depth = np.array([1, 1, 1, 1, 5, 5, 5], dtype=float)
    triangles = np.array([[0, 1, 3], [0, 3, 2], [4, 5, 6]])
    zbuf, _ = view_fill.rasterise_view(cam_xy, depth, triangles, np.ones(3, dtype=bool), 32, 32)
    visible = photo_projection.visible_vertices(cam_xy, depth, zbuf, epsilon=0.05)
    assert visible[:4].all(), "the near quad's corners are what the camera sees"
    assert not visible[4:].any(), "the far triangle sits behind the quad"


def test_photo_weight_rises_smoothly_from_the_threshold_and_is_zero_off_the_photograph():
    facing = np.array([0.1, 0.25, 0.425, 0.6, 1.0, 1.0])
    visible = np.array([True, True, True, True, True, False])
    on_photo = np.array([True, True, True, True, False, True])
    weight = photo_projection.photo_weights(facing, visible, on_photo, threshold=0.25, band=0.35)
    assert weight[0] == 0.0 and weight[1] == 0.0, "edge-on and threshold faces take none of the photograph"
    assert 0.4 < weight[2] < 0.6, "halfway through the band takes about half"
    assert weight[3] == pytest.approx(1.0), "past the band the photograph is taken whole"
    assert weight[4] == 0.0, "a vertex that lands outside the cutout keeps its own colour"
    assert weight[5] == 0.0, "a vertex the camera did not see keeps its own colour"
    assert (np.diff(weight[:4]) >= 0).all()


def test_near_black_vertices_take_their_lit_neighbours_colour():
    brown = np.array([0.35, 0.18, 0.06])
    colours = np.tile(brown, (5, 1))
    colours[2] = 0.0
    edges = np.array([[0, 1], [1, 2], [2, 3], [3, 4]])
    flooded, count = photo_projection.flood_dark(colours, edges, threshold=0.007)
    assert count == 1
    assert np.allclose(flooded[2], brown), "the hole takes the mean of its lit neighbours"
    assert np.allclose(flooded[[0, 1, 3, 4]], brown), "lit vertices are left alone"
    assert np.shares_memory(colours, colours) and colours[2].sum() == 0.0, "the input is not written"

    dark_run = np.tile(brown, (5, 1))
    dark_run[1:4] = 0.0
    flooded, count = photo_projection.flood_dark(dark_run, edges, threshold=0.007)
    assert count == 3 and np.allclose(flooded, brown), "a run of holes fills inward round by round"

    all_dark, count = photo_projection.flood_dark(np.zeros((5, 3)), edges, threshold=0.007)
    assert count == 0 and (all_dark == 0).all(), "with nothing lit there is nothing to copy"


def test_band_gains_carry_the_captured_colour_to_the_photographs_tone_band_by_band():
    heights = np.linspace(0.0, 1.0, 40)
    captured = np.full((40, 3), 0.2)
    photo = np.full((40, 3), 0.3)
    gains = photo_projection.band_gains(captured, photo, heights, heights, bands=10)
    assert gains.shape == (40, 3)
    assert np.allclose(gains, 1.5), "a photograph half again as bright lifts every band by half"

    photo[heights > 0.5] = 0.1
    gains = photo_projection.band_gains(captured, photo, heights, heights, bands=10)
    assert np.allclose(gains[heights < 0.4], 1.5) and np.allclose(gains[heights > 0.6], 0.5)
    assert (np.diff(gains[:, 0]) <= 1e-9).all(), "the gain changes smoothly with height, not in steps"


def test_band_gains_are_clamped_and_fall_back_where_the_photograph_saw_nothing():
    heights = np.linspace(0.0, 1.0, 40)
    seen = heights < 0.5
    captured = np.full((seen.sum(), 3), 0.05)
    photo = np.full((seen.sum(), 3), 0.9)
    gains = photo_projection.band_gains(captured, photo, heights[seen], heights, bands=10, clamp=(0.5, 2.0))
    assert np.allclose(gains, 2.0), "a ratio of eighteen is held to the clamp, and the unseen half takes the seen half's ratio"
    gains = photo_projection.band_gains(captured, photo * 0 + 0.01, heights[seen], heights, bands=10, clamp=(0.5, 2.0))
    assert np.allclose(gains, 0.5)
    gains = photo_projection.band_gains(captured[:0], photo[:0], heights[:0], heights, bands=10)
    assert np.allclose(gains, 1.0), "with nothing to compare the colour is left as it is"


def test_shading_gains_lift_the_unseen_side_to_the_photographed_sides_brightness_band_by_band():
    """A reconstruction's colour carries the photograph's lighting: on the cat it read 71 in luminance on the
    photographed side, 62 on the far side and 56 on the grazing sides, so the kept colour shipped darker than
    the front it meets. The unseen vertices take a luminance gain per band that brings their mean to the seen
    vertices' mean in that band; seen vertices are left alone."""
    heights = np.tile(np.linspace(0.0, 1.0, 20), 2)
    seen = np.array([True] * 20 + [False] * 20)
    colours = np.zeros((40, 3))
    colours[:20] = [0.40, 0.20, 0.10]  # the photographed side, brighter
    colours[20:] = [0.20, 0.10, 0.05]  # the far side, half as bright, same hue
    gains = photo_projection.shading_gains(colours, seen, heights, bands=4)
    assert gains.shape == (40,)
    assert np.allclose(gains[:20], 1.0), "the photographed side keeps its captured brightness"
    assert np.allclose(gains[20:], 2.0), "the far side is lifted to the photographed side's mean"
    lifted = colours * gains[:, None]
    assert np.allclose(lifted[20:], colours[:20]), "same hue, now the same brightness"


def test_shading_gains_are_clamped_and_leave_an_all_seen_or_all_unseen_mesh_alone():
    heights = np.tile(np.linspace(0.0, 1.0, 20), 2)
    seen = np.array([True] * 20 + [False] * 20)
    colours = np.zeros((40, 3))
    colours[:20] = 0.9
    colours[20:] = 0.05
    gains = photo_projection.shading_gains(colours, seen, heights, bands=4, clamp=(0.5, 2.0))
    assert np.allclose(gains[20:], 2.0), "an eighteenfold gap is held to the clamp"
    assert np.allclose(photo_projection.shading_gains(colours, np.ones(40, dtype=bool), heights), 1.0)
    assert np.allclose(photo_projection.shading_gains(colours, np.zeros(40, dtype=bool), heights), 1.0)


def test_the_cutouts_edge_colour_is_carried_into_its_transparent_surround():
    rgb = np.zeros((6, 6, 3))
    alpha = np.zeros((6, 6), dtype=bool)
    rgb[2:4, 2:4] = (0.5, 0.2, 0.1)
    alpha[2:4, 2:4] = True
    out = photo_projection.extend_edge_colours(rgb, alpha, rounds=1)
    assert np.allclose(out[2:4, 2:4], (0.5, 0.2, 0.1)), "the subject is untouched"
    assert np.allclose(out[1, 2], (0.5, 0.2, 0.1)) and np.allclose(out[4, 3], (0.5, 0.2, 0.1)), "one ring out takes the edge"
    assert np.allclose(out[0, 0], 0.0), "two rings out is not reached in one round"
    assert np.allclose(rgb[1, 2], 0.0), "the input is not written"
    out = photo_projection.extend_edge_colours(rgb, alpha, rounds=4)
    assert np.allclose(out[0, 0], (0.5, 0.2, 0.1)), "more rounds reach further"


def test_srgb_and_linear_round_trip():
    srgb = np.array([[0.0, 0.5, 1.0], [0.627451, 0.462745, 0.270588]])
    linear = photo_projection.srgb_to_linear(srgb)
    assert linear[0] == pytest.approx([0.0, 0.2140, 1.0], abs=1e-3)
    assert linear[1] == pytest.approx([0.35, 0.18, 0.06], abs=2e-3)
    assert np.allclose(photo_projection.linear_to_srgb(linear), srgb, atol=1e-5)


def test_vertex_normals_are_unit_length_point_out_of_a_closed_body_and_are_zero_on_an_unused_vertex():
    corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)] + [[9, 9, 9]], dtype=float)
    faces = np.array([[3, 2, 0], [1, 3, 0], [7, 5, 4], [6, 7, 4], [5, 1, 0], [4, 5, 0],
                      [7, 6, 2], [3, 7, 2], [6, 4, 0], [2, 6, 0], [7, 3, 1], [5, 7, 1]])  # wound outwards
    normals = photo_projection.vertex_normals(corners, faces)
    assert np.linalg.norm(normals[:8], axis=1) == pytest.approx(np.ones(8))
    assert (np.einsum("ij,ij->i", normals[:8], corners[:8] - 0.5) > 0).all(), "each corner's normal points out"
    assert not normals[8].any(), "a vertex no triangle uses has no normal rather than NaN"
