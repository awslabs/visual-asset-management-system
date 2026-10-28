#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The shell descriptors: how many times a lattice ray crosses the surface.

A closed solid is crossed twice by every ray through it. A second closed wall just inside the first is
crossed four times, and it satisfies every check the pipeline already makes -- watertight, manifold,
one body, and a genus that reads as tunnels or as nothing depending on whether the walls touch. The ray
count is the one measurement that tells a solid from a shell within a shell.
"""

from unittest import mock

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
if isinstance(trimesh, mock.MagicMock):
    pytest.skip("trimesh is stubbed here", allow_module_level=True)

import export  # noqa: E402

pytestmark = pytest.mark.unit


def _nested_boxes(inner_extents=(3.0, 1.95, 1.95)):
    """A closed box with a second closed wall inside it facing inward, as one mesh.

    The default inner wall clears the lattice's inset, so every ray through the outer box crosses it too.
    """
    outer = trimesh.creation.box(extents=(4.0, 2.0, 2.0))
    inner = trimesh.creation.box(extents=inner_extents)
    inner.invert()
    return trimesh.util.concatenate([outer, inner])


class TestASolidReadsTwo:
    def test_every_ray_through_a_box_crosses_it_twice(self):
        described = export.shape_descriptors(trimesh.creation.box(extents=(2.0, 2.0, 2.0)))

        assert described["medianCrossings"] == 2.0
        assert described["shellLineShare"] == 0.0

    def test_a_ray_along_a_face_diagonal_meets_both_of_its_triangles_at_one_depth(self):
        """A box face is two triangles sharing a diagonal, and a lattice this regular puts rays exactly on
        it. Counting triangles would read those rays as 4; counting distinct depths reads them as 2."""
        _, counts = export.ray_crossings(trimesh.creation.box(extents=(2.0, 2.0, 2.0)))

        assert len(counts) == export.SHELL_RAY_LATTICE ** 2, "every ray meets a box that fills its footprint"
        assert set(counts.tolist()) == {2}

    def test_a_closed_sphere_reads_two_as_well(self):
        described = export.shape_descriptors(trimesh.creation.icosphere(subdivisions=4))

        assert described["medianCrossings"] == 2.0
        assert described["shellLineShare"] == 0.0


class TestAShellWithinAShellReadsFour:
    def test_a_wall_just_inside_the_outer_one_is_crossed_by_every_ray(self):
        described = export.shape_descriptors(_nested_boxes())

        assert described["medianCrossings"] == 4.0
        assert described["shellLineShare"] == 1.0

    def test_the_share_is_the_share_of_rays_that_meet_the_inner_wall(self):
        """An inner wall over part of the footprint is read by the rays through it and by no others, so
        the median stays at the solid's 2 while the share says how much of the footprint is doubled."""
        described = export.shape_descriptors(_nested_boxes(inner_extents=(3.0, 1.0, 1.0)))

        assert described["medianCrossings"] == 2.0
        assert 0.15 < described["shellLineShare"] < 0.35

    def test_genus_cannot_tell_the_nested_shells_from_a_solid(self):
        """The reason the descriptors exist: the same mesh passes as a closed surface with no tunnels."""
        nested = _nested_boxes()

        assert nested.is_watertight
        assert export.surface_genus(nested) == 0
        assert export.shape_descriptors(nested)["medianCrossings"] == 4.0


class TestTheRaysRunAlongTheLongestAxis:
    @pytest.mark.parametrize("extents, axis", [((10.0, 2.0, 2.0), 0), ((2.0, 10.0, 2.0), 1),
                                               ((2.0, 2.0, 10.0), 2)])
    def test_the_axis_is_the_longest_bounding_box_edge(self, extents, axis):
        found, counts = export.ray_crossings(trimesh.creation.box(extents=extents))

        assert found == axis
        assert set(counts.tolist()) == {2}

    def test_an_elongated_solid_still_reads_two(self):
        described = export.shape_descriptors(trimesh.creation.box(extents=(10.0, 2.0, 2.0)))

        assert described["medianCrossings"] == 2.0
        assert described["shellLineShare"] == 0.0


class TestNothingToMeasureReadsNone:
    def test_a_single_triangle_is_never_pierced(self):
        """One triangle is flat along its own normal, so the lattice across the longest axis has no depth
        to cast through and no ray meets it."""
        sheet = trimesh.Trimesh(vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
                                faces=np.array([[0, 1, 2]]), process=False)

        assert export.shell_descriptors(sheet) == {"medianCrossings": None, "shellLineShare": None}
        described = export.shape_descriptors(sheet)
        assert described.get("medianCrossings") is None and described.get("shellLineShare") is None

    def test_a_mesh_with_no_faces_reads_none(self):
        empty = trimesh.Trimesh()

        assert export.ray_crossings(empty) == (None, None)
        assert export.shell_descriptors(empty) == {"medianCrossings": None, "shellLineShare": None}
        assert export.shape_descriptors(empty).get("medianCrossings") is None

    def test_an_unmeasurable_mesh_reports_none_rather_than_raising(self):
        class Broken:
            faces = np.array([[0, 1, 2]])

            @property
            def vertices(self):
                raise RuntimeError("no vertices")

        assert export.shell_descriptors(Broken()) == {"medianCrossings": None, "shellLineShare": None}


class TestTheExistingDescriptorsAreUnchanged:
    def test_the_shell_fields_sit_beside_the_shape_fields(self):
        described = export.shape_descriptors(trimesh.creation.box(extents=(2.0, 2.0, 2.0)))

        assert set(described) == {"fillRatio", "footprintAspect", "heightOverWidth", "genus",
                                  "medianCrossings", "shellLineShare"}
        assert described["fillRatio"] == pytest.approx(1.0, abs=1e-3)
        assert described["genus"] == 0

    def test_a_mesh_whose_extents_cannot_be_read_still_reports_nothing(self):
        class Broken:
            @property
            def extents(self):
                raise RuntimeError("no extents")

        assert export.shape_descriptors(Broken()) == {}
