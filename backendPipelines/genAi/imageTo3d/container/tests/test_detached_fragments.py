#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Debris is dropped; a detached piece of the subject is never fused into the body.

Both cases came off live runs. Stable3DGen arrived with 29 bodies -- 28 stray specks around one mug --
and the aggressive rung that resolved them was the right answer. TripoSR arrived with 2, the second
being the mug's handle, and the same rung bridged it into the wall and produced a featureless billet
that was watertight, manifold, one body, and no longer a mug.

The two are separated on SPAN rather than volume, and the margin is wide: measured on the real meshes,
the specks spanned 1.1% of the mesh's bounding-box diagonal and the handle 29%. By volume they look
alike -- the handle was 0.7% of the mesh -- so a volume threshold would have discarded it as debris.
"""

import numpy as np
import pytest
import trimesh

import meshcleanup


def _mug():
    """A hollow open cylinder: enough of a mug for these tests."""
    outer = trimesh.creation.cylinder(radius=20.0, height=40.0, sections=48)
    inner = trimesh.creation.cylinder(radius=17.0, height=40.0, sections=48)
    inner.apply_translation((0, 0, 4.0))
    return trimesh.boolean.difference([outer, inner])


def _speck(offset, size=0.8):
    return trimesh.creation.box(extents=(size, size, size)).apply_translation(offset)


def _handle_sized_piece(offset):
    """A piece spanning a large fraction of the subject, as a detached handle does."""
    piece = trimesh.creation.box(extents=(3.0, 10.0, 24.0))
    piece.apply_translation(offset)
    return piece


@pytest.mark.unit
class TestDebrisIsDropped:

    def test_specks_are_classified_as_negligible(self):
        speckled = trimesh.util.concatenate(
            [_mug()] + [_speck(o) for o in ((30, 0, 0), (-30, 4, 6), (0, 32, -5))])

        substantial, negligible = meshcleanup.classify_fragments(
            speckled, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION)

        assert len(substantial) == 1
        assert len(negligible) == 3

    def test_culling_removes_them_and_leaves_one_body(self):
        speckled = trimesh.util.concatenate([_mug()] + [_speck(o) for o in ((30, 0, 0), (-30, 4, 6))])

        culled, dropped = meshcleanup.cull_negligible_fragments(speckled, {})

        assert dropped == 2
        assert meshcleanup.evaluate_gate(culled).bodies == 1

    def test_culling_does_not_touch_the_subject(self):
        mug = _mug()
        speckled = trimesh.util.concatenate([mug, _speck((30, 0, 0))])

        culled, _dropped = meshcleanup.cull_negligible_fragments(speckled, {})

        assert culled.volume == pytest.approx(mug.volume, rel=0.01)

    def test_a_mesh_of_one_piece_is_left_alone(self):
        mug = _mug()

        culled, dropped = meshcleanup.cull_negligible_fragments(mug, {})

        assert dropped == 0
        assert len(culled.faces) == len(mug.faces)

    def test_the_largest_piece_is_never_dropped(self):
        # A mesh made entirely of small pieces must still keep one, or culling would empty it.
        specks = trimesh.util.concatenate([_speck((0, 0, 0)), _speck((30, 0, 0), size=0.4)])

        substantial, _negligible = meshcleanup.classify_fragments(
            specks, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION)

        assert len(substantial) >= 1

    def test_culling_can_be_turned_off(self):
        speckled = trimesh.util.concatenate([_mug(), _speck((30, 0, 0))])

        _culled, dropped = meshcleanup.cull_negligible_fragments(
            speckled, {"NEGLIGIBLE_FRAGMENT_FRACTION": "0"})

        assert dropped == 0


@pytest.mark.unit
class TestAPieceOfTheSubjectIsKept:

    def test_a_handle_sized_piece_is_substantial(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        substantial, negligible = meshcleanup.classify_fragments(
            detached, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION)

        assert len(substantial) == 2
        assert not negligible

    def test_it_survives_culling(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        culled, dropped = meshcleanup.cull_negligible_fragments(detached, {})

        assert dropped == 0
        assert meshcleanup.evaluate_gate(culled).bodies == 2

    def test_the_gate_still_reports_the_mesh_as_being_in_pieces(self):
        """Which is what makes the run fail under failClosed rather than ship a fused lump."""
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        report = meshcleanup.evaluate_gate(detached)

        assert report.bodies == 2
        assert not report.passed
        assert any("separate pieces" in f for f in report.failures)

    def test_a_thin_piece_is_judged_on_span_not_volume(self):
        # The real handle was 0.7% of the mesh's volume and 29% of its span. Volume would discard it.
        mug = _mug()
        piece = _handle_sized_piece((26, 0, 0))
        detached = trimesh.util.concatenate([mug, piece])

        assert piece.volume / detached.volume < 0.1, "the piece is small by volume"
        substantial, _ = meshcleanup.classify_fragments(
            detached, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION)
        assert len(substantial) == 2, "and must still be kept"


@pytest.mark.unit
class TestJoiningPiecesOfTheSubjectIsRecorded:
    """Joining is reported rather than refused, because no measurement separates the two things it does.

    Four candidates were tested against the raw output of two models on the same mug and all four failed:
    the pieces are the same size class, both are open surfaces, the absorbed handle sat CLOSER than the
    assembled shards (4.6 mm against 24 mm), surface area rose in both cases (123% and 125%), and the fill
    against the convex hull barely moved on either -- the model that produced the billet had already
    produced an 85%-filled mesh before any repair. Refusing the rung failed a good reconstruction on a
    live run, so the count reaches the report and the preview decides.
    """

    def test_the_join_is_counted(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        _mesh, report = meshcleanup.run_cleanup(
            detached, {"REPAIR_BACKENDS": "trimesh,manifold3d,pymeshfix,pymeshlab"})

        assert report.pieces_fused >= 1

    def test_the_join_is_not_treated_as_a_rejection(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        _mesh, report = meshcleanup.run_cleanup(
            detached, {"REPAIR_BACKENDS": "trimesh,manifold3d,pymeshfix,pymeshlab"})

        assert not any("fused" in note for note in report.repairs_rejected)

    def test_a_torn_reconstruction_can_still_reach_a_passing_mesh(self):
        """The behaviour refusing the rung took away: assembling pieces into one object."""
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        _mesh, report = meshcleanup.run_cleanup(
            detached, {"REPAIR_BACKENDS": "trimesh,manifold3d,pymeshfix,pymeshlab"})

        assert report.bodies == 1
        assert report.passed

    def test_the_count_reaches_the_reported_dict(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        _mesh, report = meshcleanup.run_cleanup(
            detached, {"REPAIR_BACKENDS": "trimesh,manifold3d,pymeshfix,pymeshlab"})

        assert report.as_dict().get("piecesFused", 0) >= 1

    def test_a_run_that_joined_nothing_reports_nothing(self):
        """The key stays out of the report entirely, so its presence means something happened."""
        mug = _mug()

        _mesh, report = meshcleanup.run_cleanup(mug, {"REPAIR_BACKENDS": "trimesh,manifold3d"})

        assert "piecesFused" not in report.as_dict()

    def test_debris_does_not_count_as_a_join(self):
        """Culling runs first, so the rungs never see the specks and can still do their job."""
        speckled = trimesh.util.concatenate([_mug(), _speck((30, 0, 0))])

        _mesh, report = meshcleanup.run_cleanup(
            speckled, {"REPAIR_BACKENDS": "trimesh,manifold3d,pymeshfix,pymeshlab"})

        assert report.pieces_fused == 0, "culled debris is not a piece of the subject"
        assert report.bodies == 1


@pytest.mark.unit
class TestAWispyStrandIsDebrisHoweverFarItReaches:
    """Span alone let these through. Measured on the raw output of two models on the same mug, a
    detached handle was 32% span / 68% solidity / 0.70% of the model's volume, while spurious strands
    were 15% span / 15-26% solidity / 0.14-0.24%. Solidity and volume share are what separate them."""

    def _strand(self, length=20.0):
        """A long thin sliver: reaches far, encloses almost nothing."""
        strand = trimesh.creation.box(extents=(0.35, 0.35, length))
        strand.apply_translation((30, 0, 0))
        return strand

    def test_a_long_thin_strand_is_debris(self):
        speckled = trimesh.util.concatenate([_mug(), self._strand()])

        substantial, negligible = meshcleanup.classify_fragments(
            speckled, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION, {})

        assert len(substantial) == 1
        assert len(negligible) == 1

    def test_a_solid_piece_of_the_same_reach_is_kept(self):
        # The contrast that matters: same reach, real substance.
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        substantial, negligible = meshcleanup.classify_fragments(
            detached, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION, {})

        assert len(substantial) == 2
        assert not negligible

    def test_the_thresholds_are_settable(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        substantial, negligible = meshcleanup.classify_fragments(
            detached, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION,
            {"DEBRIS_VOLUME_FRACTION": "0.9"})

        assert len(substantial) == 1, "a high volume floor should call the handle debris"
        assert len(negligible) == 1


@pytest.mark.unit
class TestTouchingSurfacesAreOnePiece:
    """A mug's inner wall arrives as its own component 0.068 mm from the outer wall -- 0.07% of the
    mesh -- and it is the same surface. Treating it as a separate piece had the guard refuse the only
    rung that could close the mesh, and the run failed."""

    def _nested_shell(self, gap=0.05):
        """A second surface sitting just inside another, as an inner wall does."""
        inner = trimesh.creation.cylinder(radius=17.0 - gap, height=39.0, sections=48)
        inner.invert()
        return inner

    def test_a_nested_wall_is_not_counted_as_a_separate_piece(self):
        nested = trimesh.util.concatenate([_mug(), self._nested_shell()])

        substantial, _negligible = meshcleanup.classify_fragments(
            nested, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION, {}, merge_touching=True)

        assert len(substantial) == 1

    def test_a_piece_held_away_from_the_body_stays_separate(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        substantial, _negligible = meshcleanup.classify_fragments(
            detached, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION, {}, merge_touching=True)

        assert len(substantial) == 2

    def test_the_tolerance_is_settable(self):
        detached = trimesh.util.concatenate([_mug(), _handle_sized_piece((26, 0, 0))])

        substantial, _negligible = meshcleanup.classify_fragments(
            detached, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION,
            {"TOUCHING_FRACTION": "0.9"}, merge_touching=True)

        assert len(substantial) == 1, "a large tolerance should read the pieces as one surface"

    def test_proximity_is_not_used_for_the_debris_decision(self):
        """A speck resting against the surface must still be dropped. Merging it into the main piece
        first left all 22 components of a live mesh in place and the gate failed on every one."""
        resting = trimesh.util.concatenate([_mug(), _speck((20.2, 0, 0))])

        _substantial, negligible = meshcleanup.classify_fragments(
            resting, meshcleanup.DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION, {})

        assert len(negligible) == 1
