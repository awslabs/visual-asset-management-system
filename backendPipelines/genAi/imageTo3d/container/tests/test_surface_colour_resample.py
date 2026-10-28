#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Vertex colour carried across a cleanup is read off the captured SURFACE, not off the nearest captured vertex.

A cleaned vertex standing between captured vertices takes the colour a renderer interpolates at that point
of the original surface, so a decimated surface does not step between the colours of whichever captured
vertices happen to be nearest. Where the surface cannot be searched -- no captured faces, no scipy -- the
nearest captured vertex still answers.
"""

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

import texturing

_TRIMESH_INSTALLED = not isinstance(sys.modules.get("trimesh"), MagicMock)
requires_trimesh = pytest.mark.skipif(
    not _TRIMESH_INSTALLED, reason="trimesh is not installed in this environment")

# A flat unit quad of two triangles, each corner a different RGBA colour, so any blend is visible in
# every channel and no blend equals a corner.
QUAD_VERTICES = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
QUAD_FACES = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
QUAD_COLOURS = np.array([[0, 0, 0, 255], [255, 0, 0, 128], [255, 255, 0, 64], [0, 255, 0, 0]], dtype=np.uint8)


def _coloured(vertices, faces, colours):
    import trimesh

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colours)
    return mesh


def _quad():
    return _coloured(QUAD_VERTICES, QUAD_FACES, QUAD_COLOURS)


def _standing_on(points, faces=None):
    """A cleaned mesh whose vertices are ``points``; faceless unless faces are given."""
    import trimesh

    faces = np.zeros((0, 3), dtype=np.int64) if faces is None else np.asarray(faces)
    return trimesh.Trimesh(vertices=np.asarray(points, dtype=float), faces=faces, process=False)


def _blend(weights, corners):
    """The barycentric blend of the given corner colours, rounded the way the resample rounds."""
    return np.rint(np.asarray(weights, dtype=float) @ QUAD_COLOURS[list(corners)].astype(float)).astype(np.uint8)


def _restored_colours(cleaned, snapshot):
    restored, status = texturing.restore_texture(cleaned, snapshot)
    assert status == texturing.TEXTURE_FROM_VERTEX_COLORS
    return np.asarray(restored.visual.vertex_colors)


@requires_trimesh
@pytest.mark.unit
class TestTheCaptureKeepsTheSurface:

    def test_the_faces_are_captured_beside_the_vertex_colour(self):
        snapshot = texturing.capture_texture(_quad())

        assert snapshot.has_vertex_colors()
        assert not snapshot.has_texture()
        assert np.array_equal(snapshot.faces, QUAD_FACES)

    def test_a_mesh_without_faces_captures_none(self):
        snapshot = texturing.capture_texture(_coloured(QUAD_VERTICES, np.zeros((0, 3), dtype=np.int64), QUAD_COLOURS))

        assert snapshot.has_vertex_colors()
        assert snapshot.faces is None


@requires_trimesh
@pytest.mark.unit
class TestColourIsReadOffTheCapturedSurface:

    def test_a_vertex_inside_a_triangle_takes_the_blend_of_its_three_corners(self):
        snapshot = texturing.capture_texture(_quad())
        # The centroid of the first triangle, and an off-centre point in it with weights 0.4, 0.4, 0.2.
        cleaned = _standing_on([[2 / 3, 1 / 3, 0], [0.6, 0.2, 0]])

        sampled = _restored_colours(cleaned, snapshot)

        assert np.array_equal(sampled[0], _blend([1 / 3, 1 / 3, 1 / 3], (0, 1, 2)))
        assert np.array_equal(sampled[1], _blend([0.4, 0.4, 0.2], (0, 1, 2)))
        # Neither is the colour of any captured vertex, which is what a nearest-vertex read would give.
        assert not any(np.array_equal(row, corner) for row in sampled for corner in QUAD_COLOURS)

    def test_a_vertex_on_the_shared_edge_blends_only_that_edge_s_two_ends(self):
        snapshot = texturing.capture_texture(_quad())

        sampled = _restored_colours(_standing_on([[0.25, 0.25, 0]]), snapshot)

        assert np.array_equal(sampled[0], _blend([0.75, 0.25], (0, 2)))

    def test_a_vertex_standing_on_a_captured_vertex_keeps_that_vertex_s_colour_exactly(self):
        snapshot = texturing.capture_texture(_quad())

        sampled = _restored_colours(_standing_on(QUAD_VERTICES[::-1]), snapshot)

        assert np.array_equal(sampled, QUAD_COLOURS[::-1])

    def test_a_vertex_far_off_the_surface_takes_the_colour_of_the_nearest_surface_point(self):
        snapshot = texturing.capture_texture(_quad())
        # Five units above the quad: its nearest surface point is (0.25, 0.25, 0) on the shared edge,
        # while its nearest captured VERTEX is the black corner. Beyond the quad's edge, the nearest
        # surface point is the midpoint of the edge from the red corner to the yellow one.
        cleaned = _standing_on([[0.25, 0.25, 5.0], [2.0, 0.5, 0.0]])

        sampled = _restored_colours(cleaned, snapshot)

        assert np.array_equal(sampled[0], _blend([0.75, 0.25], (0, 2)))
        assert np.array_equal(sampled[1], _blend([0.5, 0.5], (1, 2)))
        assert not np.array_equal(sampled[0], QUAD_COLOURS[0])

    def test_every_channel_is_blended_and_the_dtype_kept(self):
        snapshot = texturing.capture_texture(_quad())

        sampled = _restored_colours(_standing_on([[2 / 3, 1 / 3, 0]]), snapshot)

        assert sampled.shape == (1, 4)
        assert sampled.dtype == np.uint8
        # The alpha channel is (255 + 128 + 64) / 3, not any corner's alpha.
        assert sampled[0, 3] == 149

    def test_a_cleaned_mesh_with_faces_of_its_own_is_coloured_the_same_way(self):
        snapshot = texturing.capture_texture(_quad())
        cleaned = _standing_on([[2 / 3, 1 / 3, 0], [0.25, 0.25, 0], [0.6, 0.2, 0]], faces=[[0, 1, 2]])

        sampled = _restored_colours(cleaned, snapshot)

        assert np.array_equal(sampled[0], _blend([1 / 3, 1 / 3, 1 / 3], (0, 1, 2)))
        assert np.array_equal(sampled[1], _blend([0.75, 0.25], (0, 2)))

    def test_a_single_captured_triangle_is_searched(self):
        # With one triangle the centroid tree is asked for one neighbour, which it answers in a
        # different shape than it answers several.
        snapshot = texturing.capture_texture(_coloured(QUAD_VERTICES[:3], [[0, 1, 2]], QUAD_COLOURS[:3]))

        sampled = _restored_colours(_standing_on([[2 / 3, 1 / 3, 0]]), snapshot)

        assert np.array_equal(sampled[0], _blend([1 / 3, 1 / 3, 1 / 3], (0, 1, 2)))

    def test_a_float_colour_is_blended_in_place_without_rounding(self):
        snapshot = texturing.TextureSnapshot(
            vertex_colors=QUAD_COLOURS.astype(float) / 255.0, vertices=QUAD_VERTICES, faces=QUAD_FACES,
            vertex_count=4, face_count=2)

        sampled = texturing.nearest_surface_colors(snapshot, [[2 / 3, 1 / 3, 0]])

        assert sampled.dtype == float
        assert np.allclose(sampled[0], QUAD_COLOURS[:3].astype(float).mean(axis=0) / 255.0)


@requires_trimesh
@pytest.mark.unit
class TestTheSurfaceReadIsCloserThanTheNearestVertex:

    def test_a_linear_colour_field_is_reproduced_where_the_nearest_vertex_is_not(self):
        """A colour that varies linearly over a sphere is reproduced at any surface point by blending the
        corners of the triangle it lies in, to rounding, where the nearest vertex is off by the colour
        change across half an edge."""
        import trimesh

        source = trimesh.creation.icosphere(subdivisions=3)
        field = np.rint((np.asarray(source.vertices) + 1.0) * 127.5).astype(np.uint8)
        source.visual = trimesh.visual.ColorVisuals(
            mesh=source, vertex_colors=np.column_stack([field, np.full(len(field), 255, dtype=np.uint8)]))
        snapshot = texturing.capture_texture(source)
        # The same sphere turned so that none of its vertices lands on a captured one.
        target = trimesh.creation.icosphere(subdivisions=3)
        target.apply_transform(trimesh.transformations.rotation_matrix(0.3, [1.0, 0.7, 0.2]))
        points = np.asarray(target.vertices)
        truth = (points + 1.0) * 127.5

        blended = texturing.nearest_surface_colors(snapshot, points)[:, :3].astype(float)
        nearest = np.asarray(snapshot.vertex_colors)[
            texturing._nearest_source_indices(snapshot.vertices, points)][:, :3].astype(float)

        blend_error = np.abs(blended - truth).mean()
        nearest_error = np.abs(nearest - truth).mean()
        assert blend_error < 1.0
        assert nearest_error > 2.0
        assert blend_error < nearest_error / 2

    def test_a_blend_never_leaves_the_range_of_the_captured_colours(self):
        import trimesh

        source = trimesh.creation.icosphere(subdivisions=3)
        rng = np.random.default_rng(7)
        colours = rng.integers(0, 256, size=(len(source.vertices), 4), dtype=np.uint8)
        source.visual = trimesh.visual.ColorVisuals(mesh=source, vertex_colors=colours)
        snapshot = texturing.capture_texture(source)
        points = rng.uniform(-2.0, 2.0, size=(500, 3))

        sampled = texturing.nearest_surface_colors(snapshot, points)

        assert sampled.shape == (500, 4)
        assert sampled.dtype == np.uint8
        assert np.all(sampled >= colours.min(axis=0))
        assert np.all(sampled <= colours.max(axis=0))


@requires_trimesh
@pytest.mark.unit
class TestWithoutASurfaceTheNearestVertexAnswers:

    def test_a_snapshot_without_faces_colours_from_the_nearest_captured_vertex(self):
        snapshot = texturing.capture_texture(_quad())
        snapshot.faces = None
        # Nearest to the red corner, well inside the first triangle.
        sampled = _restored_colours(_standing_on([[0.6, 0.2, 0]]), snapshot)

        assert np.array_equal(sampled[0], QUAD_COLOURS[1])

    def test_a_snapshot_with_no_faces_at_all_does_the_same(self):
        snapshot = texturing.capture_texture(_quad())
        snapshot.faces = np.zeros((0, 3), dtype=np.int64)

        sampled = _restored_colours(_standing_on([[0.6, 0.2, 0]]), snapshot)

        assert np.array_equal(sampled[0], QUAD_COLOURS[1])

    def test_without_scipy_the_nearest_captured_vertex_answers(self, monkeypatch):
        snapshot = texturing.capture_texture(_quad())
        monkeypatch.setitem(sys.modules, "scipy.spatial", None)

        sampled = _restored_colours(_standing_on([[0.6, 0.2, 0]]), snapshot)

        assert np.array_equal(sampled[0], QUAD_COLOURS[1])

    def test_a_degenerate_captured_triangle_yields_a_finite_in_range_colour(self):
        # The first triangle is collapsed onto the edge from the black corner to the red one.
        snapshot = texturing.capture_texture(_coloured(QUAD_VERTICES, [[0, 1, 1], [0, 2, 3]], QUAD_COLOURS))

        sampled = _restored_colours(_standing_on([[0.5, 0.0, 0.0], [0.9, 0.05, 0.0]]), snapshot)

        assert sampled.dtype == np.uint8
        assert np.all(sampled >= QUAD_COLOURS.min(axis=0))
        assert np.all(sampled <= QUAD_COLOURS.max(axis=0))

    def test_no_cleaned_vertices_yields_no_colours(self):
        snapshot = texturing.capture_texture(_quad())

        sampled = texturing.nearest_surface_colors(snapshot, np.zeros((0, 3)))

        assert sampled.shape == (0, 4)
        assert sampled.dtype == np.uint8
