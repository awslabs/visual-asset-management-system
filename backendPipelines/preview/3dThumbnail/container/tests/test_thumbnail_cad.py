#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""STEP tessellation, colours, placement and free surfaces (#468).

* Tessellation used an absolute tolerance of 0.1 model units, so the same 20 mm cylinder became 500
  triangles in millimetres and 24 in metres. The linear deflection is now relative to the bounding-box
  diagonal, with an angular deflection.
* Colours set on parts, components or faces were dropped. The file is read through XCAF and each
  face's colour travels on its triangles.
* The fallback triangulation ignored each face's ``TopLoc_Location``, and only solids were tessellated
  when a file had any, so free surfaces beside solids were lost.

The tests build STEP files with cadquery and need the real cadquery/OCP and pyvista (pipeline image).
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline.format_handlers import cad_handler


@pytest.mark.unit
class TestDeflection:
    def test_the_linear_deflection_is_a_share_of_the_diagonal(self):
        assert cad_handler.linear_deflection(1000.0) == pytest.approx(2.0)
        assert cad_handler.linear_deflection(0.05) == pytest.approx(0.0001)
        assert cad_handler.linear_deflection(0.0) > 0


@pytest.mark.unit
@requires_real("cadquery", "OCP", "pyvista")
class TestStep:
    def _export(self, tmp_path, name, shape_or_wp):
        import cadquery as cq

        path = str(tmp_path / f"{name}.step")
        cq.exporters.export(shape_or_wp, path)
        return path

    def test_detail_does_not_depend_on_the_model_units(self, tmp_path):
        import cadquery as cq

        metres = cad_handler.load(self._export(tmp_path, "m", cq.Workplane("XY").circle(0.01).extrude(0.05)))
        millimetres = cad_handler.load(self._export(tmp_path, "mm", cq.Workplane("XY").circle(10).extrude(50)))
        assert metres.n_cells == millimetres.n_cells
        assert metres.n_cells >= 60, "a cylinder must not be a handful of facets"

    def test_part_component_and_nested_assembly_colours_and_placements_are_kept(self, tmp_path):
        import cadquery as cq

        assembly = cq.Assembly()
        assembly.add(cq.Workplane("XY").box(10, 10, 10), name="red", color=cq.Color("red"))
        assembly.add(cq.Workplane("XY").box(10, 10, 10), name="blue", color=cq.Color("blue"),
                     loc=cq.Location((20, 0, 0)))
        nested = cq.Assembly(loc=cq.Location((0, 30, 0)))
        nested.add(cq.Workplane("XY").sphere(5), name="green", color=cq.Color(0, 1, 0))
        assembly.add(nested, name="nested")
        path = str(tmp_path / "assembly.step")
        assembly.save(path)

        mesh = cad_handler.load(path)
        rgb = np.asarray(mesh.cell_data["RGB"])
        centres = mesh.cell_centers().points

        def centre_of(colour):
            selected = (rgb == colour).all(axis=1)
            assert selected.any(), f"no triangles carry {colour}"
            return centres[selected].mean(axis=0)

        assert np.allclose(centre_of([255, 0, 0]), (0, 0, 0), atol=0.5)
        assert np.allclose(centre_of([0, 0, 255]), (20, 0, 0), atol=0.5)
        assert np.allclose(centre_of([0, 255, 0]), (0, 30, 0), atol=0.5)

    def test_free_surfaces_beside_a_solid_are_kept(self, tmp_path):
        import cadquery as cq

        solid = cq.Workplane("XY").box(10, 10, 10).val()
        sheet = cq.Face.makePlane(30, 30, basePnt=(0, 0, -8))
        mesh = cad_handler.load(self._export(tmp_path, "mixed", cq.Compound.makeCompound([solid, sheet])))
        xmin, xmax, _, _, zmin, _ = mesh.bounds
        assert xmin == pytest.approx(-15) and xmax == pytest.approx(15) and zmin == pytest.approx(-8)

    def test_an_uncoloured_file_has_no_colour_array(self, tmp_path):
        import cadquery as cq

        mesh = cad_handler.load(self._export(tmp_path, "plain", cq.Workplane("XY").box(1, 2, 3)))
        assert "RGB" not in mesh.cell_data and mesh.n_cells == 12

    def test_a_face_location_is_applied_to_its_triangles(self):
        import cadquery as cq
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopoDS import TopoDS

        face = cq.Face.makePlane(2, 2).moved(cq.Location((5, 0, 0))).wrapped
        BRepMesh_IncrementalMesh(face, 0.01, False, 0.5, False)
        nodes, triangles = cad_handler.face_triangles(TopoDS.Face_s(face))
        assert np.allclose(nodes[:, 0].mean(), 5.0) and len(triangles) >= 2
