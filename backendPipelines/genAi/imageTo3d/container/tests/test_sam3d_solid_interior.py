#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The SAM 3D Objects driver's interior fill, run over hand-built vertex grids.

The FlexiCubes extractor seeds +1 at every vertex the sparse structure does not carry. The structure
is a band around the surface rather than a filled volume, so the field reads outside, inside and
outside again across the band and then outside through the whole interior, and the extractor emits a
second, inward-facing wall inside the outer one. ``solid_interior_sdf`` floods the positive field from
the grid boundary, reads what the flood does not reach against the outer skin the outside can see, and
rewrites what the skin encloses as inside. It is Python held in a string inside the adapter and runs
only on a GPU instance, so it is compiled out of the driver here by name and exercised over grids
whose sealed volume is known exactly.
"""

import ast
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

OPENINGS = [0, 1, 2]


# The fill and the functions it calls.
FILL_FUNCTIONS = ("solid_interior_sdf", "skin_winding", "ray_reach", "interior_components")


def _driver_functions(names):
    """The named functions of the DRIVER_TEMPLATE string, compiled out of it together and on their own."""
    with open(os.path.join(_MODELS_DIR, "sam3d.py"), encoding="utf-8") as handle:
        source = handle.read()
    driver = source.split("DRIVER_TEMPLATE = '''", 1)[1].split("'''", 1)[0]
    functions = [node for node in ast.parse(driver).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert sorted(node.name for node in functions) == sorted(names), "the driver defines each exactly once"
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "<sam3d driver>", "exec"),  # nosec B102
         namespace)
    return namespace


@pytest.fixture(scope="module")
def solid_interior_sdf():
    """The driver's ``solid_interior_sdf``, with the functions it calls."""
    return _driver_functions(FILL_FUNCTIONS)["solid_interior_sdf"]


@pytest.fixture(scope="module")
def driver_functions():
    """The fill's own parts, the colour sources and the grey count, by name."""
    return _driver_functions(FILL_FUNCTIONS + ("fill_colour_sources", "grey_vertices"))


def _block(res, x, y, z):
    """A grid marking the closed block spanning ``x``, ``y`` and ``z``, each an inclusive pair."""
    grid = np.zeros((res, res, res), dtype=bool)
    grid[x[0]:x[1] + 1, y[0]:y[1] + 1, z[0]:z[1] + 1] = True
    return grid


def _box(res, low, high):
    """A grid marking the closed cube ``low..high`` on every axis."""
    return _block(res, (low, high), (low, high), (low, high))


def _shell(res, low, high):
    """A one-voxel-thick closed box: the cube minus the cube one voxel inside it."""
    return _box(res, low, high) & ~_box(res, low + 1, high - 1)


def _field(res, negative):
    """A field of +1 with ``negative`` set to -1."""
    field = np.ones((res, res, res), dtype=np.float32)
    field[negative] = -1.0
    return field


@pytest.mark.unit
class TestAClosedSurfaceSealsItsInterior:
    """A one-voxel negative shell around a positive core, the rest of the grid positive."""

    RES = 12

    def _fill(self, solid_interior_sdf, opening):
        shell = _shell(self.RES, 3, 8)
        sdf, stats = solid_interior_sdf(shell, _field(self.RES, shell), opening)
        return shell, sdf, stats

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_the_core_is_rewritten_negative(self, solid_interior_sdf, opening):
        _, sdf, stats = self._fill(solid_interior_sdf, opening)
        assert stats["flippedVertices"] == 4 ** 3
        assert (sdf[_box(self.RES, 4, 7)] == -1).all()
        assert stats["flippedComponents"] == 1
        assert stats["largestFlippedComponent"] == 4 ** 3

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_the_exterior_and_the_shell_keep_their_field(self, solid_interior_sdf, opening):
        shell, sdf, stats = self._fill(solid_interior_sdf, opening)
        assert (sdf[~_box(self.RES, 3, 8)] == 1).all()
        assert (sdf[shell] == -1).all()
        assert stats["exteriorVertices"] == self.RES ** 3 - 6 ** 3
        assert stats["bridgedVertices"] == 0

    def test_a_single_vertex_pocket_inside_a_solid_is_interior(self, solid_interior_sdf):
        res = 14
        solid = _box(res, 3, 10)
        solid[6, 6, 6] = False
        for opening in OPENINGS:
            sdf, stats = solid_interior_sdf(solid, _field(res, solid), opening)
            assert stats["flippedVertices"] == 1
            assert sdf[6, 6, 6] == -1

    def test_the_counts_describe_the_grid_and_the_setting(self, solid_interior_sdf):
        shell, _, stats = self._fill(solid_interior_sdf, 0)
        assert set(stats) == {
            "gridRes", "opening", "openingRequested", "flippedByRadius", "activeVertices",
            "positiveVertices", "erodedVertices", "exteriorVertices", "openingFlippedVertices",
            "outsideSkinVertices", "exteriorPieceVertices", "exemptPieces", "closureVertices", "flippedVertices",
            "flippedActive", "flippedComponents", "largestFlippedComponent", "bridgedVertices", "elapsedSeconds"}
        assert stats["gridRes"] == self.RES
        assert stats["opening"] == 0
        assert stats["openingRequested"] == 0
        assert stats["flippedByRadius"] == [stats["flippedVertices"]]
        assert stats["openingFlippedVertices"] == stats["flippedVertices"]
        assert stats["outsideSkinVertices"] == stats["exteriorPieceVertices"] == 0
        assert stats["exemptPieces"] == stats["closureVertices"] == 0
        assert stats["activeVertices"] == int(shell.sum())
        assert stats["positiveVertices"] == self.RES ** 3 - int(shell.sum())
        assert stats["erodedVertices"] == stats["positiveVertices"]
        assert stats["flippedActive"] == 0
        assert stats["elapsedSeconds"] >= 0
        json.dumps(stats)

    def test_the_opening_is_echoed_and_erodes_the_positive_region(self, solid_interior_sdf):
        _, _, stats = self._fill(solid_interior_sdf, 1)
        assert stats["opening"] == 1
        assert stats["erodedVertices"] < stats["positiveVertices"]

    def test_the_field_handed_in_is_left_alone(self, solid_interior_sdf):
        shell = _shell(self.RES, 3, 8)
        field = _field(self.RES, shell)
        before = field.copy()
        solid_interior_sdf(shell, field, 1)
        assert (field == before).all()


@pytest.mark.unit
class TestTheBandsInnerPositiveLayerIsInterior:
    """A band three vertices thick whose middle layer is negative and whose inner layer is positive,
    around a positive interior the structure does not carry: the field the extractor sees."""

    RES = 16

    def _band(self):
        active = _box(self.RES, 2, 13) & ~_box(self.RES, 5, 10)
        return active, _field(self.RES, _shell(self.RES, 3, 12))

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_the_inner_layer_and_the_interior_are_rewritten_together(self, solid_interior_sdf, opening):
        active, field = self._band()
        sdf, stats = solid_interior_sdf(active, field, opening)
        assert stats["flippedVertices"] == 8 ** 3
        assert stats["flippedActive"] == 8 ** 3 - 6 ** 3
        assert (sdf[_box(self.RES, 4, 11)] == -1).all()

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_the_outer_layer_stays_positive(self, solid_interior_sdf, opening):
        active, field = self._band()
        sdf, stats = solid_interior_sdf(active, field, opening)
        assert (sdf[_shell(self.RES, 2, 13)] == 1).all()
        assert (sdf[~_box(self.RES, 3, 12)] == 1).all()
        assert stats["bridgedVertices"] == 0


@pytest.mark.unit
class TestAThinSubjectIsCarriedWhollyByTheBand:
    """A slab one vertex thick inside its negative layer: every interior vertex is in the structure,
    so there is no uncarried interior to seed from and the field alone decides."""

    RES = 14

    def _slab(self):
        active = _block(self.RES, (4, 6), (3, 10), (3, 10))
        core = _block(self.RES, (5, 5), (4, 9), (4, 9))
        return active, core, _field(self.RES, active & ~core)

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_the_core_is_rewritten_negative(self, solid_interior_sdf, opening):
        active, core, field = self._slab()
        sdf, stats = solid_interior_sdf(active, field, opening)
        assert stats["flippedVertices"] == 36
        assert stats["flippedActive"] == 36
        assert (sdf[core] == -1).all()
        assert (sdf[~active] == 1).all()


@pytest.mark.unit
class TestAPunctureInTheSurface:
    """The closed shell with positive vertices knocked out of one face. The opening seals the puncture,
    and the puncture's own vertices sit in a dent in the outer skin, looking through it at the core: a
    single-vertex puncture's vertex sees the unit face between it and the core, a sixth of the sphere,
    so its winding is a sixth. They are handed back to the outside and the seal settles one layer in."""

    RES = 12

    def _punctured(self, width):
        shell = _shell(self.RES, 3, 8)
        low, high = 5 - width // 2, 5 + width // 2
        shell[3, low:high + 1, low:high + 1] = False
        return shell, _field(self.RES, shell)

    def test_the_flood_pours_through_a_puncture_at_opening_zero(self, solid_interior_sdf):
        shell, field = self._punctured(1)
        sdf, stats = solid_interior_sdf(shell, field, 0)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()

    def test_an_opening_of_one_seals_a_single_vertex_puncture_one_layer_in(self, solid_interior_sdf):
        shell, field = self._punctured(1)
        sdf, stats = solid_interior_sdf(shell, field, 1)
        assert stats["openingFlippedVertices"] == 4 ** 3 + 1
        assert stats["outsideSkinVertices"] == 1
        assert stats["flippedVertices"] == 4 ** 3
        assert sdf[3, 5, 5] == 1
        assert (sdf[_box(self.RES, 4, 7)] == -1).all()
        assert (sdf[~_box(self.RES, 3, 8)] == 1).all()
        assert stats["bridgedVertices"] == 1

    def test_a_three_wide_puncture_is_wider_than_an_opening_of_one(self, solid_interior_sdf):
        shell, field = self._punctured(3)
        sdf, stats = solid_interior_sdf(shell, field, 1)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()

    def test_an_opening_of_two_seals_it_one_layer_in(self, solid_interior_sdf):
        shell, field = self._punctured(3)
        sdf, stats = solid_interior_sdf(shell, field, 2)
        assert stats["openingFlippedVertices"] == 4 ** 3 + 9
        assert stats["outsideSkinVertices"] == 9
        assert stats["flippedVertices"] == 4 ** 3
        assert (sdf[3, 4:7, 4:7] == 1).all()
        assert (sdf[_box(self.RES, 4, 7)] == -1).all()
        assert (sdf[~_box(self.RES, 3, 8)] == 1).all()
        assert stats["bridgedVertices"] == 9


@pytest.mark.unit
class TestAGapBetweenTwoSurfaces:
    """Two solid negative slabs with an exterior gap between them, open to the boundary on four sides.
    An opening that bridges the gap shuts the flood out of it, but rays along the gap see both its
    walls, so the skin does not enclose it and it is handed back whole."""

    RES = 16

    def _slabs(self, gap):
        near = _block(self.RES, (3, 5), (3, 12), (3, 12))
        far = _block(self.RES, (6 + gap, 8 + gap), (3, 12), (3, 12))
        between = _block(self.RES, (6, 5 + gap), (3, 12), (3, 12))
        return near | far, between, _field(self.RES, near | far)

    def test_the_gap_is_exterior_at_opening_zero(self, solid_interior_sdf):
        active, _, field = self._slabs(2)
        sdf, stats = solid_interior_sdf(active, field, 0)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()

    def test_a_gap_two_wide_that_an_opening_of_one_bridges_is_handed_back(self, solid_interior_sdf):
        active, between, field = self._slabs(2)
        sdf, stats = solid_interior_sdf(active, field, 1)
        assert stats["openingFlippedVertices"] == int(between.sum()) == 2 * 10 * 10
        assert stats["outsideSkinVertices"] == 2 * 10 * 10
        assert stats["flippedVertices"] == 0
        assert stats["bridgedVertices"] == 0
        assert (sdf == field).all()

    def test_a_gap_three_wide_is_kept(self, solid_interior_sdf):
        active, _, field = self._slabs(3)
        sdf, stats = solid_interior_sdf(active, field, 1)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()


@pytest.mark.unit
class TestAnInwardCornerIsNotFilledIn:
    """Exterior vertices touching two faces of the surface at once: a crease, and a cavity open at
    the top of the grid. Neither has anything sealed off, so the field is returned as it came."""

    RES = 14

    def _crease(self):
        solid = _block(self.RES, (3, 10), (3, 6), (3, 10)) | _block(self.RES, (3, 6), (7, 10), (3, 10))
        return solid, _field(self.RES, solid)

    def _cup(self):
        walls = _block(self.RES, (3, 10), (3, 10), (3, 13)) & ~_block(self.RES, (4, 9), (4, 9), (4, 13))
        return walls, _field(self.RES, walls)

    @pytest.mark.parametrize("opening", [1, 2])
    def test_the_crease_keeps_its_field(self, solid_interior_sdf, opening):
        solid, field = self._crease()
        sdf, stats = solid_interior_sdf(solid, field, opening)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_the_cavity_keeps_its_field(self, solid_interior_sdf, opening):
        walls, field = self._cup()
        sdf, stats = solid_interior_sdf(walls, field, opening)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()


@pytest.mark.unit
class TestAnObjectStandingOnAFaceOfTheGrid:
    """A closed shell resting on the grid's low z face with zero, one or two rows of positive floor
    under it. The space beyond the grid is outside, so the floor keeps its field at every radius, as
    does the gap between the shell and the face, and the search settles where it does for a shell in
    mid-air rather than reading the floor as a sealed volume. The padding reaches deep enough that a
    hollow open toward the face stays open below half its mouth width and from there on is sealed
    where the outside does not see into it, and a puncture in the wall lying on the face is sealed
    from beyond the grid, one layer in, the way a puncture in mid-air is."""

    RES = 16
    CORE = 6 ** 3

    def _standing(self, floor):
        shell = (_block(self.RES, (4, 11), (4, 11), (floor, floor + 7))
                 & ~_block(self.RES, (5, 10), (5, 10), (floor + 1, floor + 6)))
        return shell, _field(self.RES, shell)

    def _punctured_on_the_face(self):
        shell, _ = self._standing(0)
        shell[7, 7, 0] = False
        return shell, _field(self.RES, shell)

    def _open_toward_the_face(self):
        hollow = _block(self.RES, (5, 10), (5, 10), (0, 6))
        walls = _block(self.RES, (4, 11), (4, 11), (0, 7)) & ~hollow
        return walls, hollow, _field(self.RES, walls)

    @pytest.mark.parametrize("floor", [0, 1, 2])
    @pytest.mark.parametrize("opening", [1, 2, 3])
    def test_the_floor_under_the_shell_keeps_its_field(self, solid_interior_sdf, floor, opening):
        shell, field = self._standing(floor)
        sdf, stats = solid_interior_sdf(shell, field, opening)
        assert stats["flippedVertices"] == self.CORE
        assert stats["bridgedVertices"] == 0
        assert (sdf[:, :, :floor] == 1).all()
        assert (sdf[_block(self.RES, (5, 10), (5, 10), (floor + 1, floor + 6))] == -1).all()

    @pytest.mark.parametrize("floor", [0, 1, 2])
    def test_the_search_settles_where_it_does_for_a_shell_in_mid_air(self, solid_interior_sdf, floor):
        shell, field = self._standing(floor)
        sdf, stats = solid_interior_sdf(shell, field, "auto", max_radius=3)
        assert stats["opening"] == 0
        assert stats["flippedByRadius"] == [self.CORE] * 4
        assert (sdf[:, :, :floor] == 1).all()

    @pytest.mark.parametrize("opening", [1, 2, 3])
    def test_a_puncture_on_the_face_is_sealed_from_beyond_the_grid(self, solid_interior_sdf, opening):
        shell, field = self._punctured_on_the_face()
        sdf, stats = solid_interior_sdf(shell, field, opening)
        assert stats["openingFlippedVertices"] == self.CORE + 1
        assert stats["flippedVertices"] == self.CORE
        assert stats["bridgedVertices"] == 1
        assert sdf[7, 7, 0] == 1
        assert sdf[7, 7, 1] == -1

    def test_the_search_seals_a_puncture_on_the_face(self, solid_interior_sdf):
        shell, field = self._punctured_on_the_face()
        sdf, stats = solid_interior_sdf(shell, field, "auto", max_radius=3)
        assert stats["opening"] == 1
        assert stats["flippedByRadius"] == [0, self.CORE + 1, self.CORE + 1]
        assert stats["flippedVertices"] == self.CORE
        assert stats["bridgedVertices"] == 1
        assert sdf[7, 7, 1] == -1

    @pytest.mark.parametrize("opening", [1, 2])
    def test_a_hollow_open_toward_the_face_keeps_its_field(self, solid_interior_sdf, opening):
        walls, _, field = self._open_toward_the_face()
        sdf, stats = solid_interior_sdf(walls, field, opening)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()

    def test_an_opening_of_half_the_mouth_seals_the_hollow_where_the_outside_does_not_see_in(
            self, solid_interior_sdf):
        walls, hollow, field = self._open_toward_the_face()
        sdf, stats = solid_interior_sdf(walls, field, 3)
        assert stats["openingFlippedVertices"] == int(hollow.sum())
        # The mouth looks out beyond the grid and is handed back; the layers under the ceiling look at
        # walls no ray from outside reaches along more than a few directions, and stay sealed.
        assert (sdf[:, :, 0][hollow[:, :, 0]] == 1).all()
        assert (sdf[:, :, 5:7][hollow[:, :, 5:7]] == -1).all()
        assert 0 < stats["flippedVertices"] < int(hollow.sum())


@pytest.mark.unit
class TestAFieldWithNoOutside:
    """A positive region touching no face of the grid has nowhere to flood from."""

    RES = 12

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_it_is_returned_as_it_came(self, solid_interior_sdf, opening):
        core = _box(self.RES, 4, 7)
        field = _field(self.RES, ~core)
        sdf, stats = solid_interior_sdf(np.zeros_like(core), field, opening)
        assert stats["flippedVertices"] == 0
        assert (sdf == field).all()

    @pytest.mark.parametrize("opening", OPENINGS)
    def test_an_all_positive_field_flips_nothing(self, solid_interior_sdf, opening):
        field = np.ones((self.RES,) * 3, dtype=np.float32)
        sdf, stats = solid_interior_sdf(np.zeros((self.RES,) * 3, dtype=bool), field, opening)
        assert stats["flippedVertices"] == 0
        assert stats["exteriorVertices"] == self.RES ** 3
        assert (sdf == 1).all()


@pytest.mark.unit
class TestTheOpeningFindsItsOwnRadius:
    """Under ``"auto"`` the radius grows from zero and settles where a step flips a volume rather than
    a sliver. The shell here is 4..11 in a grid of 16, so its 6-cube core holds 216 vertices, the
    outside ring is four deep and survives every erosion the tests run, and the band carries 296 active
    vertices: at the default ``sigma`` a step counts as sealing from about 18 flipped vertices up."""

    RES = 16
    CORE = 6 ** 3

    def _punctured(self, low, high):
        shell = _shell(self.RES, 4, 11)
        shell[4, low:high + 1, low:high + 1] = False
        return shell

    def _fill(self, solid_interior_sdf, shell, **kwargs):
        return solid_interior_sdf(shell, _field(self.RES, shell), "auto", max_radius=3, **kwargs)

    def test_a_closed_surface_is_sealed_at_radius_zero(self, solid_interior_sdf):
        shell = _shell(self.RES, 4, 11)
        sdf, stats = self._fill(solid_interior_sdf, shell)
        assert stats["opening"] == 0
        assert stats["openingRequested"] == "auto"
        assert stats["flippedVertices"] == self.CORE
        assert stats["bridgedVertices"] == 0
        assert stats["flippedByRadius"] == [self.CORE] * 4
        assert (sdf[_box(self.RES, 5, 10)] == -1).all()

    def test_a_one_wide_puncture_is_sealed_by_the_first_radius(self, solid_interior_sdf):
        shell = self._punctured(7, 7)
        sdf, stats = self._fill(solid_interior_sdf, shell)
        assert stats["opening"] == 1
        assert stats["flippedVertices"] == self.CORE
        assert stats["bridgedVertices"] == 1
        assert stats["flippedByRadius"] == [0, self.CORE + 1, self.CORE + 1]
        assert sdf[4, 7, 7] == 1
        assert sdf[5, 7, 7] == -1

    def test_a_three_wide_puncture_is_sealed_by_the_second(self, solid_interior_sdf):
        shell = self._punctured(6, 8)
        sdf, stats = self._fill(solid_interior_sdf, shell)
        assert stats["opening"] == 2
        assert stats["flippedVertices"] == self.CORE
        assert stats["bridgedVertices"] == 9
        assert stats["flippedByRadius"] == [0, 0, self.CORE + 9, self.CORE + 9]
        assert (sdf[4, 6:9, 6:9] == 1).all()
        assert (sdf[5, 6:9, 6:9] == -1).all()

    def test_the_search_stops_at_the_first_step_after_the_seal_that_flips_only_a_sliver(
            self, solid_interior_sdf):
        shell = self._punctured(7, 7)
        _, stats = solid_interior_sdf(shell, _field(self.RES, shell), "auto", max_radius=6)
        assert stats["opening"] == 1
        assert len(stats["flippedByRadius"]) == 3

    def test_a_puncture_no_radius_closes_leaves_the_field_as_it_came(self, solid_interior_sdf):
        shell = self._punctured(5, 9)
        sdf, stats = solid_interior_sdf(shell, _field(self.RES, shell), "auto", max_radius=2)
        assert stats["opening"] == 0
        assert stats["flippedVertices"] == 0
        assert stats["flippedByRadius"] == [0, 0, 0]
        assert (sdf == _field(self.RES, shell)).all()

    def test_a_step_that_flips_less_than_sigma_of_the_active_count_does_not_seal(self, solid_interior_sdf):
        shell = self._punctured(7, 7)
        _, stats = self._fill(solid_interior_sdf, shell, sigma=1.0)
        assert stats["opening"] == 0
        assert stats["flippedVertices"] == 0
        assert stats["flippedByRadius"] == [0, self.CORE + 1, self.CORE + 1, self.CORE + 1]

    def _two_shells(self):
        """Two hollow boxes in a grid of 32, eight apart, one punctured one wide and one three wide."""
        res = 32
        shell = np.zeros((res, res, res), dtype=bool)
        for x in (4, 20):
            shell[x:x + 8, 12:20, 12:20] = True
            shell[x + 1:x + 7, 13:19, 13:19] = False
        shell[4, 15, 15] = False
        shell[20, 14:17, 14:17] = False
        return res, shell

    def test_two_punctures_sealing_at_different_radii_settle_on_the_later(self, solid_interior_sdf):
        res, shell = self._two_shells()
        sdf, stats = solid_interior_sdf(shell, _field(res, shell), "auto", max_radius=3)
        assert stats["opening"] == 2
        assert stats["flippedByRadius"] == [0, self.CORE + 1, 2 * self.CORE + 10, 2 * self.CORE + 10]
        assert stats["flippedVertices"] == 2 * self.CORE
        assert stats["bridgedVertices"] == 10
        # Both cores touch the outside through their punctures and are the same size: one is the
        # largest piece, and the other stays because it lies well inside the skin.
        assert stats["exemptPieces"] == 1
        assert (sdf[5:11, 13:19, 13:19] == -1).all()
        assert (sdf[21:27, 13:19, 13:19] == -1).all()

    def test_a_search_still_sealing_at_its_last_radius_keeps_that_radius(self, solid_interior_sdf):
        res, shell = self._two_shells()
        _, stats = solid_interior_sdf(shell, _field(res, shell), "auto", max_radius=2)
        assert stats["opening"] == 2
        assert stats["flippedByRadius"] == [0, self.CORE + 1, 2 * self.CORE + 10]

    @pytest.mark.parametrize("word", ["auto", "AUTO", " Auto "])
    def test_the_word_is_read_in_any_case(self, solid_interior_sdf, word):
        shell = _shell(self.RES, 4, 11)
        _, stats = solid_interior_sdf(shell, _field(self.RES, shell), word, max_radius=1)
        assert stats["openingRequested"] == "auto"
        assert stats["flippedByRadius"] == [self.CORE, self.CORE]

    def test_a_fixed_radius_measures_only_itself(self, solid_interior_sdf):
        shell = self._punctured(7, 7)
        _, stats = solid_interior_sdf(shell, _field(self.RES, shell), 2)
        assert stats["opening"] == 2
        assert stats["openingRequested"] == 2
        assert stats["flippedByRadius"] == [self.CORE + 1]

    def test_a_negative_radius_is_refused(self, solid_interior_sdf):
        shell = _shell(self.RES, 4, 11)
        with pytest.raises(ValueError, match="the opening is a radius"):
            solid_interior_sdf(shell, _field(self.RES, shell), -1)

    def test_a_negative_search_limit_is_refused(self, solid_interior_sdf):
        shell = _shell(self.RES, 4, 11)
        with pytest.raises(ValueError, match="max_radius is a radius"):
            solid_interior_sdf(shell, _field(self.RES, shell), "auto", max_radius=-1)


@pytest.mark.unit
class TestTheOuterSkinsWindingNumber:
    """``skin_winding`` counts only the walls the outside can see. Where every wall is seen it is the
    inside indicator itself; a wall no ray from outside reaches adds nothing, so what the seen walls
    enclose reads as inside; and a dent in the skin reads as the share of the sphere its far end fills."""

    RES = 12

    def test_a_solid_block_reads_as_its_own_indicator(self, driver_functions):
        solid = _box(self.RES, 3, 8)
        winding = driver_functions["skin_winding"](_field(self.RES, solid))
        assert np.abs(winding - solid).max() < 1e-3

    def test_the_unseen_core_of_a_closed_shell_reads_as_inside(self, driver_functions):
        shell = _shell(self.RES, 3, 8)
        winding = driver_functions["skin_winding"](_field(self.RES, shell))
        assert np.abs(winding - _box(self.RES, 3, 8)).max() < 1e-3

    def test_a_single_vertex_puncture_reads_as_the_face_it_looks_through(self, driver_functions):
        # From the puncture's vertex the unit face between it and the core fills a sixth of the sphere,
        # and from the core vertex behind it the same face is the sixth the skin leaves open.
        shell = _shell(self.RES, 3, 8)
        shell[3, 5, 5] = False
        winding = driver_functions["skin_winding"](_field(self.RES, shell))
        assert winding[3, 5, 5] == pytest.approx(1 / 6, abs=0.01)
        assert winding[4, 5, 5] == pytest.approx(5 / 6, abs=0.01)


@pytest.mark.unit
class TestTheRaysFromOutside:
    """``ray_reach`` counts the lattice directions along which a ray from beyond the grid reaches a
    vertex through positive vertices."""

    RES = 16

    def test_an_all_positive_grid_is_reached_along_every_direction(self, driver_functions):
        reach = driver_functions["ray_reach"](np.ones((6, 6, 6), dtype=bool))
        assert (reach == 26).all()

    def test_a_ray_does_not_slip_between_two_negatives_that_meet_at_an_edge(self, driver_functions):
        # A negative wall along x + y == 10 through the whole grid. Beyond it, at x + y == 15, a vertex
        # is reached only along the 17 directions with dx + dy <= 0, which never come from the wall's
        # side; a step along (1, 1, *) from x + y == 9 to 11 would pass between two wall vertices and
        # add three more.
        x, y, _ = np.indices((self.RES,) * 3)
        reach = driver_functions["ray_reach"]((x + y) != 10)
        assert reach[7, 8, 8] == 17


@pytest.mark.unit
class TestThePiecesOfTheInside:
    """``interior_components`` keeps the largest piece, every piece the outside does not touch, and a
    touching piece only when it lies well inside the skin: a mean winding of 0.9 or more with at most
    a quarter of its vertices touching."""

    RES = 14

    def _pieces(self):
        positive = np.ones((self.RES,) * 3, dtype=bool)
        large = _block(self.RES, (1, 5), (7, 11), (1, 5))
        # Sealed in a negative layer, and in one with a single positive vertex through it.
        sealed = _block(self.RES, (8, 10), (8, 10), (8, 10))
        positive[_block(self.RES, (7, 11), (7, 11), (7, 11)) & ~sealed] = False
        pocket = _block(self.RES, (8, 10), (1, 3), (1, 3))
        positive[_block(self.RES, (7, 11), (0, 4), (0, 4)) & ~pocket] = False
        positive[7, 2, 2] = True
        # One layer thick in the open, touching the outside with every vertex.
        ribbon = _block(self.RES, (1, 4), (1, 4), (12, 12))
        return positive, large, sealed, pocket, ribbon

    def test_outside_the_skin_only_the_largest_and_the_untouched_pieces_stay(self, driver_functions):
        positive, large, sealed, pocket, ribbon = self._pieces()
        kept, exempt = driver_functions["interior_components"](
            large | sealed | pocket | ribbon, positive, np.zeros((self.RES,) * 3, dtype=np.float32))
        assert (kept == (large | sealed)).all()
        assert exempt == 0

    def test_well_inside_the_skin_a_piece_touching_through_one_vertex_stays(self, driver_functions):
        positive, large, sealed, pocket, ribbon = self._pieces()
        kept, exempt = driver_functions["interior_components"](
            large | sealed | pocket | ribbon, positive, np.ones((self.RES,) * 3, dtype=np.float32))
        assert (kept == (large | sealed | pocket)).all()
        assert exempt == 1


@pytest.mark.unit
class TestTheFillsCrossingsTakeTheirColourFromTheStructure:
    """``fill_colour_sources`` pairs each seeded end of a crossing the fill created with the nearest
    vertex the structure carries."""

    RES = 8

    def test_each_seeded_end_of_a_new_crossing_takes_the_nearest_carried_vertex(self, driver_functions):
        res = self.RES
        before = np.ones((res,) * 3, dtype=np.float32)
        before[6] = -1.0
        active = np.zeros((res,) * 3, dtype=bool)
        active[1] = active[4] = True
        after = before.copy()
        after[:4] = -1.0
        targets, sources = driver_functions["fill_colour_sources"](active, before, after)
        # The fill's crossing lies between x = 3, seeded, and x = 4, carried; the model's own crossings
        # either side of x = 6 are not the fill's.
        layer = np.zeros((res,) * 3, dtype=bool)
        layer[3] = True
        assert (targets == np.flatnonzero(layer)).all()
        assert (sources == targets + res * res).all()

    def test_a_fill_that_flipped_nothing_seeds_nothing(self, driver_functions):
        field = _field(self.RES, _box(self.RES, 2, 5))
        targets, sources = driver_functions["fill_colour_sources"](
            np.ones((self.RES,) * 3, dtype=bool), field, field)
        assert len(targets) == len(sources) == 0


@pytest.mark.unit
class TestTheGreyVertexCount:
    """``grey_vertices`` counts the vertices carrying the mid grey the extractor decodes from zero."""

    def test_only_exact_mid_grey_counts(self, driver_functions):
        colours = np.array([[128, 128, 128, 255], [127, 128, 128, 255], [128, 128, 128, 90],
                            [128, 128, 129, 255]], dtype=np.uint8)
        mesh = SimpleNamespace(visual=SimpleNamespace(vertex_colors=colours))
        assert driver_functions["grey_vertices"](mesh) == 2

    def test_a_mesh_without_vertex_colours_has_no_count(self, driver_functions):
        assert driver_functions["grey_vertices"](SimpleNamespace(visual=SimpleNamespace())) is None
