# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Delivering a chart-packed atlas as per-vertex colour, at the vertices that sit on an island's border.

Every vertex an atlas model exports is split at a uv seam, so every vertex sits on the border of its island,
where the bake margin of the NEXT island bleeds in. Sampling exactly the texel a vertex names read that mixed
border texel and shipped it as an isolated speckle: 0.9% of a refined horse's vertices against 0.1% of the
reconstruction's own, and a blind judge called the surface pitted. Sampling a texel and a half inside each
face and averaging a vertex's corners reads the island's own colour, and the weld then averages a position's
split copies instead of keeping whichever came first. A sample that reads exactly black is the cleared
background the bake never wrote -- an island narrower than a texel has no texel of its own -- and is left out;
a vertex with no written sample takes the reading of the copies that meet at its position."""

import numpy as np
import pytest

pytest.importorskip("trimesh")
import trimesh  # noqa: E402
from PIL import Image  # noqa: E402

import export  # noqa: E402
import texturing  # noqa: E402

pytestmark = pytest.mark.unit

BROWN = (120, 70, 30)


def _texel(uv, size):
    """The (column, row) sample_atlas_colors reads for a uv, so a test can paint exactly that texel."""
    return int(np.clip((uv[0] % 1.0) * (size - 1), 0, size - 1)), int(np.clip((1.0 - uv[1] % 1.0) * (size - 1), 0, size - 1))


def test_a_vertex_whose_own_texel_is_the_border_takes_the_colour_inside_its_face():
    size = 64
    uv = np.array([[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]])
    pixels = np.tile(np.array(BROWN, dtype=np.uint8), (size, size, 1))
    for corner in uv:
        x, y = _texel(corner, size)
        pixels[y, x] = 0
    mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]], process=False)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(pixels))
    exact = np.asarray(texturing.sample_atlas_colors(uv, mesh.visual.material))
    assert (exact[:, :3] == 0).all(), "the texels the vertices name are the black border"

    out, status = texturing.collapse_atlas_to_vertex_colors(mesh)

    assert status == texturing.TEXTURE_RESAMPLED
    assert (np.asarray(out.visual.vertex_colors)[:, :3] == BROWN).all(), "each vertex reads inside its face"


def test_a_vertex_shared_by_two_islands_averages_what_each_face_reads():
    size = 64
    pixels = np.zeros((size, size, 3), dtype=np.uint8)
    pixels[:, : size // 2, 0] = 255  # left half red
    pixels[:, size // 2 :, 2] = 255  # right half blue
    # Two triangles sharing the edge 0-1; vertices 0 and 1 name the boundary column, 2 sits left, 3 right.
    mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [0, 1, 0], [-1, 0.5, 0], [1, 0.5, 0]],
                           faces=[[0, 1, 2], [1, 0, 3]], process=False)
    uv = np.array([[0.5, 0.2], [0.5, 0.8], [0.1, 0.5], [0.9, 0.5]])
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(pixels))

    out, _ = texturing.collapse_atlas_to_vertex_colors(mesh)

    colours = np.asarray(out.visual.vertex_colors)[:, :3].astype(int)
    assert (colours[2] == [255, 0, 0]).all() and (colours[3] == [0, 0, 255]).all()
    for shared in (0, 1):
        assert abs(colours[shared][0] - 127) <= 2 and abs(colours[shared][2] - 127) <= 2, colours[shared]


def test_the_weld_averages_the_colours_of_a_positions_copies():
    box = trimesh.creation.box()
    vertices = box.vertices[box.faces].reshape(-1, 3)
    faces = np.arange(len(vertices)).reshape(-1, 3)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    colours = np.tile([*BROWN, 255], (len(vertices), 1)).astype(np.uint8)
    colours[0, :3] = 0  # one speckled copy of the first corner
    mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colours)
    copies = int((np.abs(vertices - vertices[0]).max(axis=1) < 1e-9).sum())
    assert copies > 1

    welded = export.welded_for_gltf(mesh)

    assert len(welded.vertices) == 8
    at = int(np.argmin(np.abs(np.asarray(welded.vertices) - vertices[0]).max(axis=1)))
    got = np.asarray(welded.visual.vertex_colors)[at, :3].astype(float)
    expected = np.array(BROWN, dtype=float) * (copies - 1) / copies
    assert np.abs(got - expected).max() <= 1.0, f"{got} is not the mean of {copies} copies, {expected}"
    others = np.delete(np.asarray(welded.visual.vertex_colors)[:, :3], at, axis=0)
    assert (others == BROWN).all(), "positions without a speckled copy are unchanged"


def _textured(vertices, faces, uv, pixels):
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.visual = trimesh.visual.TextureVisuals(uv=np.asarray(uv, dtype=float), image=Image.fromarray(pixels))
    return mesh


def _bands(size, *colours):
    """An atlas of equal vertical bands, left to right, one per colour."""
    pixels = np.zeros((size, size, 3), dtype=np.uint8)
    width = size // len(colours)
    for index, colour in enumerate(colours):
        pixels[:, index * width:(index + 1) * width if index < len(colours) - 1 else size] = colour
    return pixels


def _face_uv(band, bands=3):
    """A face laid well inside one band: its corners' inset samples all read that band."""
    left = band / bands
    return [[left + 0.05, 0.1], [left + 1 / bands - 0.05, 0.1], [left + 0.5 / bands, 0.45]]


def test_a_vertex_whose_island_the_bake_never_wrote_takes_the_colour_of_its_coincident_copy():
    """An island narrower than a texel has no texel of its own, so every sample inside its faces reads the
    cleared background, exactly black, and the collapse shipped it as a black dot on a brown coat: 0.38% of
    a refined cat's welded vertices where the reconstruction it started from carried 0.03%. The copies a uv
    seam split share the position, and the islands they sit on were written."""
    positions = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    mesh = _textured(positions + positions, [[0, 1, 2], [3, 4, 5]],
                     _face_uv(0, bands=2) + _face_uv(1, bands=2), _bands(64, (0, 0, 0), BROWN))

    colours, counts = texturing.sample_atlas_inside_faces(mesh, mesh.visual.uv, mesh.visual.material)

    assert (colours[:, :3] == BROWN).all(), colours
    assert counts["unwrittenSamples"] == 3 and counts["verticesFromCopies"] == 3
    assert counts["positionsWithoutColour"] == 0 and counts["positions"] == 3


def test_a_texel_painted_dark_is_a_colour_and_is_kept():
    """Only a sample reading (0, 0, 0) exactly is the cleared background. A dark stripe the bake wrote is
    the surface's colour, and its vertices keep it rather than taking a brighter copy's."""
    dark = (30, 20, 10)
    positions = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    mesh = _textured(positions + positions, [[0, 1, 2], [3, 4, 5]],
                     _face_uv(0, bands=2) + _face_uv(1, bands=2), _bands(64, dark, BROWN))

    colours, counts = texturing.sample_atlas_inside_faces(mesh, mesh.visual.uv, mesh.visual.material)

    assert (colours[:3, :3] == dark).all() and (colours[3:, :3] == BROWN).all(), colours
    assert counts["unwrittenSamples"] == 0 and counts["verticesFromCopies"] == 0


def test_an_island_painted_black_with_no_coloured_copy_stays_black():
    """A position no island wrote has nothing else in the atlas to take: it stays black rather than being
    invented, and the read reports how many such positions there were."""
    mesh = _textured([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], _face_uv(0, bands=2),
                     _bands(64, (0, 0, 0), BROWN))

    colours, counts = texturing.sample_atlas_inside_faces(mesh, mesh.visual.uv, mesh.visual.material)

    assert (colours[:, :3] == 0).all(), colours
    assert counts["unwrittenSamples"] == 3 and counts["verticesFromCopies"] == 0
    assert counts["positionsWithoutColour"] == 3


def test_a_vertex_with_one_sample_on_the_background_takes_the_mean_of_its_written_samples_only():
    """A vertex two faces share, one on a written island and one stepped onto the background, read half
    the island's colour with the background averaged in as black. Only the written samples count."""
    # Vertex 0 names the column where black meets red; face 0-1-2 lies to its left, face 0-3-4 to its right.
    mesh = _textured([[0, 0, 0], [-1, -1, 0], [-1, 1, 0], [1, -1, 0], [1, 1, 0]], [[0, 1, 2], [0, 3, 4]],
                     [[0.5, 0.5], [0.1, 0.3], [0.1, 0.7], [0.9, 0.3], [0.9, 0.7]], _bands(64, (0, 0, 0), (255, 0, 0)))

    colours, counts = texturing.sample_atlas_inside_faces(mesh, mesh.visual.uv, mesh.visual.material)

    assert (colours[0, :3] == [255, 0, 0]).all(), colours[0]
    assert (colours[3:, :3] == [255, 0, 0]).all()
    assert counts["unwrittenSamples"] == 3, "vertex 0's left corner and both corners of the left face"


def test_a_rescued_vertex_takes_its_copies_samples_weighted_by_corner_not_by_copy():
    """A copy on two faces contributed two samples and a copy on one face one, so the rescued vertex reads
    the mean of three samples, not of two copies."""
    shared = [0, 0, 0]
    vertices = [shared, [1, 0, 0], [0, 1, 0],  # copy 0 on the background
                shared, [-1, 0, 0], [0, -1, 0],  # copy 3 on one red face
                shared, [0, 0, 1], [1, 0, 1], [0, 1, 1]]  # copy 6 on two blue faces
    faces = [[0, 1, 2], [3, 4, 5], [6, 7, 8], [6, 8, 9]]
    uv = _face_uv(0) + _face_uv(1) + [[0.72, 0.1], [0.95, 0.1], [0.95, 0.5], [0.72, 0.5]]
    mesh = _textured(vertices, faces, uv, _bands(64, (0, 0, 0), (255, 0, 0), (0, 0, 255)))

    colours, counts = texturing.sample_atlas_inside_faces(mesh, mesh.visual.uv, mesh.visual.material)

    assert counts["verticesFromCopies"] == 1
    assert np.abs(colours[0, :3].astype(int) - [85, 0, 170]).max() <= 1, colours[0]


def test_a_mesh_with_no_faces_keeps_the_texels_its_vertices_name_and_counts_its_positions():
    """A vertex on no face has no inside to read, so it keeps its own texel; the counts still describe the
    mesh, positions being the distinct ones however many copies share them."""
    positions = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    mesh = _textured(positions + positions, np.zeros((0, 3), dtype=np.int64),
                     _face_uv(0, bands=2) + _face_uv(1, bands=2), _bands(64, (0, 0, 0), BROWN))

    colours, counts = texturing.sample_atlas_inside_faces(mesh, mesh.visual.uv, mesh.visual.material)

    assert (colours[:3, :3] == 0).all() and (colours[3:, :3] == BROWN).all()
    assert counts == {"positions": 3, "unwrittenSamples": 0, "verticesFromCopies": 0, "positionsWithoutColour": 0}
