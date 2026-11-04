#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Decimation welds uv seams first, so reducing a textured mesh cannot open its surface.

A textured mesh carries a separate vertex per face corner wherever its atlas has a seam. The decimator
treats those as distinct points, so an edge collapse across a seam leaves a junction the surface no
longer closes over. Measured on a seam-split sphere before this was fixed: reducing by a tenth gave 127
non-manifold edges and 124 broken faces, and by a quarter gave 321, while the same sphere welded reduced
to a quarter of its faces with none. TripoSR hit exactly this on a live run -- 54,399 non-manifold edges
after a reduction the repair ladder could not undo.
"""

import numpy as np
import pytest
import trimesh

import export
import meshcleanup
import texturing


def _seam_split_sphere(subdivisions=4):
    """A closed sphere carrying a real xatlas atlas, so its uv seams split vertices."""
    from PIL import Image

    sphere = trimesh.creation.icosphere(subdivisions=subdivisions)
    vmapping, indices, uvs = texturing._unwrap(np.asarray(sphere.vertices), np.asarray(sphere.faces))
    split = trimesh.Trimesh(vertices=np.asarray(sphere.vertices)[np.asarray(vmapping)],
                            faces=np.asarray(indices), process=False)
    split.visual = trimesh.visual.TextureVisuals(
        uv=uvs, material=trimesh.visual.material.SimpleMaterial(
            image=Image.new("RGB", (64, 64), (200, 120, 60))))
    return sphere, split


@pytest.mark.unit
class TestASeamSplitMeshSurvivesDecimation:

    def test_the_sphere_starts_closed_and_split(self):
        # Guards the fixture itself: a sphere with no seams would make the tests below vacuous.
        welded, split = _seam_split_sphere()

        assert len(split.vertices) > len(welded.vertices)
        assert meshcleanup.evaluate_gate(split).passed

    @pytest.mark.parametrize("fraction", (0.9, 0.75, 0.55, 0.4, 0.25))
    def test_every_reduction_depth_stays_printable(self, fraction):
        _welded, split = _seam_split_sphere()
        target = int(len(split.faces) * fraction)

        reduced = export.decimate(split, target)
        report = meshcleanup.evaluate_gate(reduced)

        assert report.non_manifold_edges == 0, (
            f"reducing to {fraction:.0%} left {report.non_manifold_edges} non-manifold edges")
        assert not report.broken_faces
        assert report.passed

    def test_the_reduction_actually_happens(self):
        # A weld that silently skipped the reduction would pass the gate assertions above while
        # ignoring the face budget.
        _welded, split = _seam_split_sphere()
        target = int(len(split.faces) * 0.5)

        reduced = export.decimate(split, target)

        assert len(reduced.faces) <= target


@pytest.mark.unit
class TestTheWeldOnlyAppliesToAReduction:

    def test_a_mesh_under_its_budget_keeps_its_own_vertex_set(self):
        # The texture is re-attached as it was when the vertex set is untouched, which is a more
        # faithful path than re-projecting. Welding a mesh nobody asked to reduce would lose that.
        _welded, split = _seam_split_sphere()
        vertex_count = len(split.vertices)

        untouched = export.decimate(split, len(split.faces) + 1)

        assert len(untouched.vertices) == vertex_count
        assert untouched.visual.uv is not None

    def test_decimation_disabled_is_still_a_no_op(self):
        _welded, split = _seam_split_sphere()
        vertex_count = len(split.vertices)

        assert len(export.decimate(split, 0).vertices) == vertex_count

    def test_a_mesh_that_cannot_be_welded_is_still_reduced(self, monkeypatch):
        # A weld failure must not stop the reduction: the face budget bounds the exported file size.
        _welded, split = _seam_split_sphere()
        original = trimesh.Trimesh.merge_vertices
        calls = []

        def _explode_once(self, *a, **k):
            # Only the pre-decimation weld fails. The decimator welds internally too, and breaking
            # that instead would test trimesh rather than this fallback.
            if not calls:
                calls.append(1)
                raise RuntimeError("no weld for you")
            return original(self, *a, **k)

        monkeypatch.setattr(trimesh.Trimesh, "merge_vertices", _explode_once)
        target = int(len(split.faces) * 0.5)

        reduced = export.decimate(split, target)

        assert calls, "the pre-decimation weld was never attempted"
        assert len(reduced.faces) <= target
