#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The print gate rejects a subject that came apart.

Measured on a live TripoSR run: a mug whose handle had separated from its body reported watertight,
0 non-manifold edges, 0 non-manifold vertices, 0 broken faces and a positive volume, and would have
printed as a loose crescent beside a mug. Every other criterion describes the surface of one shell, so
none of them can see it -- only the connected-component count can.
"""

import numpy as np
import pytest
import trimesh

import meshcleanup
import printfeatures
import texturing


def _box(offset=(0.0, 0.0, 0.0), size=1.0):
    mesh = trimesh.creation.box(extents=(size, size, size))
    mesh.apply_translation(offset)
    return mesh


def _detached_pair():
    """One solid plus a smaller solid nowhere near it: a subject in two pieces."""
    return trimesh.util.concatenate([_box(), _box(offset=(5.0, 0.0, 0.0), size=0.4)])


@pytest.mark.unit
class TestOneSolidPasses:

    def test_a_single_closed_solid_reports_one_body_and_passes(self):
        report = meshcleanup.evaluate_gate(_box())

        assert report.bodies == 1
        assert report.expected_bodies == 1
        assert report.passed

    def test_the_count_appears_in_the_reported_dict(self):
        # The report is what an operator reads; a criterion that is enforced but not reported leaves a
        # rejection unexplained.
        as_dict = meshcleanup.evaluate_gate(_box()).as_dict()

        assert as_dict["bodies"] == 1
        assert as_dict["expectedBodies"] == 1


@pytest.mark.unit
class TestADetachedPieceFails:

    def test_two_pieces_fail_even_though_every_other_criterion_passes(self):
        detached = _detached_pair()

        report = meshcleanup.evaluate_gate(detached)

        # The point of the test: the mesh is otherwise perfect.
        assert report.is_watertight
        assert report.non_manifold_edges == 0
        assert report.non_manifold_vertices == 0
        assert not report.broken_faces
        assert report.volume > 0
        # And still rejected.
        assert report.bodies == 2
        assert not report.passed

    def test_the_failure_says_what_is_wrong_in_print_terms(self):
        report = meshcleanup.evaluate_gate(_detached_pair())

        message = " ".join(report.failures)
        assert "2 separate pieces" in message
        assert "loose piece" in message

    def test_a_subject_in_three_pieces_is_still_rejected(self):
        three = trimesh.util.concatenate(
            [_box(), _box(offset=(5.0, 0, 0), size=0.4), _box(offset=(-5.0, 0, 0), size=0.4)])

        assert meshcleanup.evaluate_gate(three).bodies == 3
        assert not meshcleanup.evaluate_gate(three).passed


@pytest.mark.unit
class TestDeliberatelyAddedShellsAreAllowed:

    def test_an_allowance_lets_a_model_plus_its_features_pass(self):
        # concat leaves a loop and a base as their own shells by design, so three is correct here.
        three = trimesh.util.concatenate(
            [_box(), _box(offset=(5.0, 0, 0), size=0.4), _box(offset=(-5.0, 0, 0), size=0.4)])

        report = meshcleanup.evaluate_gate(three, expected_bodies=3)

        assert report.bodies == 3
        assert report.passed

    def test_a_model_already_in_two_pieces_still_exceeds_its_allowance(self):
        # The live case: a detached handle plus a loop and a base is four shells where the features
        # account for only three. The allowance must not hide the split.
        four = trimesh.util.concatenate(
            [_box(), _box(offset=(3.0, 0, 0), size=0.4),
             _box(offset=(5.0, 0, 0), size=0.4), _box(offset=(-5.0, 0, 0), size=0.4)])

        report = meshcleanup.evaluate_gate(four, expected_bodies=3)

        assert report.bodies == 4
        assert not report.passed

    def test_regate_carries_the_allowance_through(self):
        three = trimesh.util.concatenate(
            [_box(), _box(offset=(5.0, 0, 0), size=0.4), _box(offset=(-5.0, 0, 0), size=0.4)])
        previous = meshcleanup.evaluate_gate(_box())

        assert meshcleanup.regate(previous, three, expected_bodies=3).passed
        assert not meshcleanup.regate(previous, three, expected_bodies=1).passed


@pytest.mark.unit
class TestATextureIsNotMistakenForManyPieces:

    def test_a_seam_split_textured_mesh_still_counts_as_one_body(self):
        # Counted on the raw indexing a textured mesh reports one body per face -- a live run reported
        # 6663 where the geometry had 4. The weld is what makes the criterion measure geometry.
        from PIL import Image

        sphere = trimesh.creation.icosphere(subdivisions=3)
        vmapping, indices, uvs = texturing._unwrap(
            np.asarray(sphere.vertices), np.asarray(sphere.faces))
        split = trimesh.Trimesh(vertices=np.asarray(sphere.vertices)[np.asarray(vmapping)],
                                faces=np.asarray(indices), process=False)
        split.visual = trimesh.visual.TextureVisuals(
            uv=uvs, material=trimesh.visual.material.SimpleMaterial(
                image=Image.new("RGB", (32, 32), (200, 120, 60))))

        report = meshcleanup.evaluate_gate(split)

        assert report.bodies == 1
        assert report.passed


@pytest.mark.unit
class TestTheFeatureReportDeclaresItsAllowance:

    def test_concat_expects_one_shell_per_feature(self):
        mesh, report = printfeatures.apply_features(
            _box(), {"PRINT_FEATURES": "loop+base", "PRINT_FEATURE_JOIN": "concat",
                     "TARGET_SIZE_MM": 0})

        assert report["printFeatureExpectedBodies"] == "3"

    def test_union_expects_the_model_alone(self):
        mesh, report = printfeatures.apply_features(
            _box(), {"PRINT_FEATURES": "base", "PRINT_FEATURE_JOIN": "union",
                     "TARGET_SIZE_MM": 0})

        assert report["printFeatureExpectedBodies"] == "1"
