#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Each repair rung has to actually execute against a real mesh, not merely be named.

The ladder guards every rung so that one unusable backend cannot cost the generation work already
done. That resilience has a cost: a rung whose upstream API has drifted is downgraded to a warning
and a note in the report, and the run still succeeds. The pymeshfix rung sat broken that way -- its
library moved ``verbose`` onto the constructor and renamed its output arrays from ``v``/``f`` to
``points``/``faces`` -- so the strongest hole-filling rung silently never ran while every mesh went
out with unclosed holes.

The gate tests elsewhere use hand-built face lists and no real libraries, which is what makes them
fast; they cannot catch a signature drift. These tests call the real backends instead.
"""

import pytest

import meshcleanup

trimesh = pytest.importorskip("trimesh", reason="the real repair backends need trimesh")


def _holed_sphere(drop_every=17):
    """A sphere with faces removed, so there is genuine boundary for a rung to close."""
    import numpy as np

    sphere = trimesh.creation.icosphere(subdivisions=3)
    keep = np.ones(len(sphere.faces), dtype=bool)
    keep[::drop_every] = False
    holed = trimesh.Trimesh(vertices=sphere.vertices, faces=sphere.faces[keep], process=True)
    assert not holed.is_watertight, "the fixture must not already be watertight"
    return holed


@pytest.mark.parametrize("backend", sorted(meshcleanup._REPAIR_FUNCTIONS))
def test_every_rung_runs_against_a_real_mesh(backend):
    """A rung that raises here is a rung the ladder would silently skip in production."""
    pytest.importorskip(
        {"trimesh": "trimesh", "manifold3d": "manifold3d",
         "pymeshfix": "pymeshfix", "pymeshlab": "pymeshlab"}[backend],
        reason=f"the {backend} rung's library is not installed in this environment")

    result = meshcleanup._REPAIR_FUNCTIONS[backend](_holed_sphere())
    assert result is not None, f"the {backend} rung returned nothing"
    assert len(getattr(result, "faces", ())) > 0, f"the {backend} rung returned no geometry"


def test_the_pymeshfix_rung_closes_holes():
    """The rung exists for exactly this: boundary a slicer cannot thicken."""
    pytest.importorskip("pymeshfix", reason="pymeshfix is not installed in this environment")

    holed = _holed_sphere()
    repaired = meshcleanup._repair_pymeshfix(holed)
    assert repaired.is_watertight, (
        "pymeshfix ran but left the mesh open, so the rung is not doing its job")
    assert repaired.volume > 0


def test_the_ladder_records_pymeshfix_as_applied_rather_than_skipped():
    """What the live failure looked like: the rung named, the report saying it was skipped.

    A skip note in ``failures`` and an absence from ``backendsApplied`` is how a drifted signature
    presents, and the run still exits zero, so only an assertion on the report catches it.
    """
    pytest.importorskip("pymeshfix", reason="pymeshfix is not installed in this environment")

    _, report = meshcleanup.run_cleanup(_holed_sphere(), {"REPAIR_BACKENDS": "pymeshfix"})
    skips = [f for f in report.failures if "repair backend skipped" in f]
    assert not skips, f"the pymeshfix rung was skipped rather than run: {skips}"
    assert "pymeshfix" in report.backends_applied, (
        f"the report does not record the rung as applied: {report.as_dict()}")
