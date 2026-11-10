#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Meshes coloured per cell render on every Mesa (#1116).

* STEP, 3MF and COLLADA colour faces, and a mesh decimated to the triangle budget carries cell colours.
  VTK draws cell scalars from a buffer texture read per primitive, and Mesa 26 (Ubuntu 26.04) draws such
  a mesh as a fully transparent frame, with no error. The renderer now draws a copy whose cells share no
  points, with each cell's colour on its own corners as point colours, which every Mesa draws.
* Each corner keeps the smooth-shading normal of the shared mesh, so shading is unchanged, and a colour
  never blends across a cell edge (averaging cell colours onto shared points would).
* Point colours, solid colour and textures still render the loaded mesh itself.

The render tests need the real pyvista/VTK and run inside the pipeline image or under Xvfb in CI.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from conftest import colour_shares, requires_real
from preview_pipeline import renderer

RED, BLUE = (255, 0, 0), (0, 0, 255)


def _two_coloured_triangles():
    """A unit square in the ground plane split along a diagonal into a red and a blue triangle that
    share two points."""
    import pyvista as pv

    points = np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], dtype=np.float64)
    mesh = pv.PolyData(points, faces=[3, 0, 1, 2, 3, 0, 2, 3])
    mesh.cell_data["RGB"] = np.array([RED, BLUE], dtype=np.uint8)
    return mesh


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestCornerCopy:
    def test_cells_share_no_points_and_each_corner_has_its_cells_colour(self):
        mesh = _two_coloured_triangles()
        copy = renderer.cell_colours_on_corners(mesh)
        faces = np.asarray(copy.faces).reshape(-1, 4)[:, 1:]
        assert copy.n_cells == 2 and copy.n_points == 6
        assert sorted(faces.ravel().tolist()) == list(range(6)), "no point is shared"
        assert np.asarray(copy.point_data["RGB"])[faces].tolist() == [[list(RED)] * 3, [list(BLUE)] * 3]
        assert np.allclose(np.asarray(copy.points)[faces], np.asarray(mesh.points)[[[0, 1, 2], [0, 2, 3]]])
        assert "RGB" in mesh.cell_data and mesh.point_data.active_normals is None, "the input is unchanged"

    def test_corners_keep_the_smooth_normals_of_the_shared_mesh(self):
        import pyvista as pv

        sphere = pv.Sphere(theta_resolution=24, phi_resolution=24)
        sphere.cell_data["RGB"] = np.tile(np.array([[200, 50, 50]], dtype=np.uint8), (sphere.n_cells, 1))
        copy = renderer.cell_colours_on_corners(sphere)
        normals = np.asarray(copy.point_data.active_normals)
        radial = np.asarray(copy.points) / np.linalg.norm(np.asarray(copy.points), axis=1, keepdims=True)
        assert copy.n_points == 3 * sphere.n_cells
        # A point normal of a sphere is radial; each facet's own normal is several degrees off at its
        # corners, so a copy shaded flat would fail this.
        assert np.abs((normals * radial).sum(axis=1)).min() > 0.995

    def test_a_mesh_that_carries_its_own_normals_keeps_them_on_the_corners(self):
        # Smooth shading draws a mesh's own active normals rather than computing new ones, so the copy
        # must carry those. The test sets normals no normals filter would compute: all along +y.
        import pyvista as pv

        sphere = pv.Sphere(theta_resolution=24, phi_resolution=24)
        sphere.cell_data["RGB"] = np.tile(np.array([[200, 50, 50]], dtype=np.uint8), (sphere.n_cells, 1))
        sphere.point_data["OwnNormals"] = np.tile(np.array([[0.0, 1.0, 0.0]], dtype=np.float32),
                                                  (sphere.n_points, 1))
        sphere.point_data.active_normals_name = "OwnNormals"
        copy = renderer.cell_colours_on_corners(sphere)
        assert copy.n_points == 3 * sphere.n_cells
        assert np.allclose(np.asarray(copy.point_data.active_normals), (0.0, 1.0, 0.0))

    def test_vertices_lines_polygons_and_strips_keep_their_colours(self):
        import pyvista as pv

        points = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0], [2, 1, 0],
                           [3, 0, 0], [3, 1, 0], [5, 5, 5], [6, 6, 6]], dtype=np.float64)
        mesh = pv.PolyData(points, verts=[1, 9], lines=[2, 8, 9], faces=[4, 0, 1, 2, 3, 3, 1, 4, 5],
                           strips=[4, 4, 5, 6, 7])
        colours = np.array([[1, 1, 1], [2, 2, 2], [3, 3, 3], [4, 4, 4], [5, 5, 5]], dtype=np.uint8)
        mesh.cell_data["RGB"] = colours
        copy = renderer.cell_colours_on_corners(mesh)
        assert (copy.n_verts, copy.n_lines) == (1, 1)
        # The normals filter splits the strip into its two triangles, as smooth shading did.
        assert copy.n_cells == 6 and copy.n_points == 1 + 2 + 4 + 3 + 3 + 3
        rgb = np.asarray(copy.point_data["RGB"])
        used = []
        for index, value in enumerate([1, 2, 3, 4, 5, 5]):
            cell = copy.GetCell(index)
            ids = [cell.GetPointId(k) for k in range(cell.GetNumberOfPoints())]
            assert (rgb[ids] == value).all(), f"cell {index}"
            used += ids
        assert sorted(used) == list(range(copy.n_points)), "every point belongs to exactly one cell"

    def test_only_a_mesh_coloured_per_cell_is_copied(self):
        import pyvista as pv

        plotter = MagicMock()
        point_coloured = pv.Sphere()
        point_coloured.point_data["RGB"] = np.zeros((point_coloured.n_points, 3), dtype=np.uint8)
        plain = pv.Sphere()
        cell_coloured = pv.Sphere()
        cell_coloured.cell_data["RGB"] = np.zeros((cell_coloured.n_cells, 3), dtype=np.uint8)
        for mesh in (point_coloured, plain, cell_coloured):
            renderer._add_mesh(plotter, mesh)
        drawn = [call.args[0] for call in plotter.add_mesh.call_args_list]
        assert drawn[0] is point_coloured and drawn[1] is plain
        assert drawn[2] is not cell_coloured and drawn[2].n_points == 3 * cell_coloured.n_cells
        assert "RGB" in drawn[2].point_data


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestCellColourRender:
    def test_two_faces_of_different_colours_render_distinct_with_no_blend_at_their_edge(self):
        frame = renderer.generate_static_frame(_two_coloured_triangles(), resolution=(160, 120))
        coverage = (frame[..., 3] == 255).mean()
        red, blue = colour_shares(frame, [RED, BLUE])
        assert coverage > 0.05, "the square fills part of the frame"
        assert red > 0.2 and blue > 0.2, (red, blue)
        # Averaged onto the two shared points, the colours would shade purple across both faces.
        assert red + blue > 0.98, f"{1 - red - blue:.3f} of the square is a blend of red and blue"

    def test_an_orbit_of_a_cell_coloured_mesh_has_its_colour_in_every_frame(self):
        import pyvista as pv

        sphere = pv.Sphere(theta_resolution=30, phi_resolution=30)
        sphere.cell_data["RGB"] = np.tile(np.array([[30, 160, 60]], dtype=np.uint8), (sphere.n_cells, 1))
        frames = renderer.generate_rotating_frames(sphere, n_frames=4, resolution=(80, 60))
        for frame in frames:
            assert (frame[..., 3] == 255).mean() > 0.1
            assert colour_shares(frame, [(30, 160, 60)])[0] > 0.95
