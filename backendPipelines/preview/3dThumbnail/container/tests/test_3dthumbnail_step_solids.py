#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""STEP models through the SYSTEM GenAI metadata pipeline's CAD path: ``analysis.analyze_local_file`` with the real
OCP reader, on committed STEP files and on models generated here.

The cases are the ones the retired CAD/Mesh Metadata Extraction pipeline's v2.6.7 fixes added there, run against
the code that now reads CAD files for the SYSTEM pipeline (the Render3D and Fargate branches):

- A model with no solid has no volume, and surfaces beside solids add nothing to it (#1190). OCCT's volume
  integral over a shell or a face that bounds no solid is not a volume: a closed surface model read as the volume
  it encloses, and a loose face beside a solid added a sliver that depended on where the face lay.

The fixtures were written by cadquery 2.6.0:

- ``fixtures/cylinder-r4-h5.step``, a cylinder of radius 4 and height 5:
  ``cq.exporters.export(cq.Workplane("XY").cylinder(5, 4), "cylinder-r4-h5.step")``.
- ``fixtures/plane-10x20-surface.step``, one 10 x 20 plane face and no solid (a ``SHELL_BASED_SURFACE_MODEL``):
  ``cq.exporters.export(cq.Face.makePlane(10, 20), "plane-10x20-surface.step")``.

They need the real cadquery/OCP and pyvista (the pipeline image).
"""

import os

import pytest

from conftest import requires_real

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_SURFACE_FIXTURE = os.path.join(_FIXTURES, "plane-10x20-surface.step")


def _analyse(path):
    from preview_pipeline.analysis import analysis

    result = analysis.analyze_local_file(path, os.path.splitext(path)[1], render=False)
    return result.attributes


def _write(tmp_path, name, *shapes):
    """A STEP file with each shape as a root of its own."""
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    path = str(tmp_path / name)
    writer = STEPControl_Writer()
    for shape in shapes:
        writer.Transfer(shape.wrapped, STEPControl_AsIs)
    writer.Write(path)
    return path


@pytest.mark.unit
@requires_real("cadquery", "OCP", "pyvista")
class TestVolumeIsTheVolumeOfTheSolids:
    def _surface_models(self, tmp_path):
        import cadquery as cq

        box = cq.Workplane("XY").box(10, 10, 10).val()
        return {
            "surface": (_SURFACE_FIXTURE, 200),
            "closed_surface": (_write(tmp_path, "closed-surface.step", cq.Shell.makeShell(box.Faces())), 600),
            "two_surfaces": (_write(tmp_path, "two-surfaces.step",
                                    *(cq.Face.makePlane(10, 20).translate((offset, 0, 0)) for offset in (0, 30))), 400),
        }

    @pytest.mark.parametrize("name", ["surface", "closed_surface", "two_surfaces"])
    def test_a_model_with_no_solid_has_no_volume(self, tmp_path, name):
        path, area = self._surface_models(tmp_path)[name]
        attributes = _analyse(path)
        # The premise: the model really has no solid.
        assert attributes["sys_cad"]["solidCount"] == 0, attributes
        geometry = attributes["sys_geometry"]
        # The key stays, as it does for a mesh that is not watertight; only its value is empty.
        assert "volume" in geometry and geometry["volume"] is None, geometry
        assert geometry["surfaceArea"] == pytest.approx(area, rel=1e-6), geometry

    def test_a_surface_beside_a_solid_adds_nothing_to_the_volume(self, tmp_path):
        import cadquery as cq

        box = cq.Workplane("XY").box(10, 10, 10).val()
        # The face is off the z = 0 plane: there, OCCT's integral counts it as no volume and the case would pass
        # without the fix.
        path = str(tmp_path / "solid-and-surface.step")
        cq.exporters.export(cq.Compound.makeCompound([box, cq.Face.makePlane(10, 20).translate((50, 0, 3))]), path)
        attributes = _analyse(path)
        assert attributes["sys_cad"]["solidCount"] == 1, attributes
        geometry = attributes["sys_geometry"]
        assert geometry["volume"] == pytest.approx(1000, rel=1e-6), geometry
        assert geometry["surfaceArea"] == pytest.approx(600 + 200, rel=1e-6), geometry
