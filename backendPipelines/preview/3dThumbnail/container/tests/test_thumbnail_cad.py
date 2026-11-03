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
* An assembly placing the same component twice per level expands to 2^depth placements; the
  placements are counted from the file's text and bounded before OCCT transfers anything, and the
  tessellation stops at the triangle budget.

The tests build STEP files with cadquery and need the real cadquery/OCP and pyvista (pipeline image).
"""

from unittest.mock import patch

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


# --------------------------------------------------------------------------------------------------
# Assembly expansion (BSC1): every level places the level below twice
# --------------------------------------------------------------------------------------------------

def _fan_out_step(leaf_text: str, depth: int) -> str:
    """A STEP file of ``depth`` assembly levels over the single part in ``leaf_text``, each level
    placing the one below twice: 2^depth part placements in a few KB. Written as text, since OCCT's
    writer expands every placement and cannot write a deep one."""
    import re

    header, rest = leaf_text.split("DATA;", 1)
    entities = rest.rsplit("ENDSEC;", 1)[0]
    next_id = max(int(n) for n in re.findall(r"#(\d+)\s*=", entities)) + 1
    child_definition = int(re.search(r"#(\d+)\s*=\s*PRODUCT_DEFINITION\('design'", entities).group(1))
    part = re.search(r"#(\d+)\s*=\s*ADVANCED_BREP_SHAPE_REPRESENTATION\('[^']*',\(#(\d+),[^)]*\),#(\d+)\)",
                     entities)
    child_representation, child_axis, context = (int(part.group(i)) for i in (1, 2, 3))
    lines = []

    def add(text):
        nonlocal next_id
        lines.append(f"#{next_id} = {text};")
        next_id += 1
        return next_id - 1

    z_axis = add("DIRECTION('',(0.,0.,1.))")
    x_axis = add("DIRECTION('',(1.,0.,0.))")
    for level in range(depth):
        product_context = add("PRODUCT_CONTEXT('',#2,'mechanical')")
        product = add(f"PRODUCT('A{level}','A{level}','',(#{product_context}))")
        formation = add(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
        definition_context = add("PRODUCT_DEFINITION_CONTEXT('part definition',#2,'design')")
        definition = add(f"PRODUCT_DEFINITION('design','',#{formation},#{definition_context})")
        definition_shape = add(f"PRODUCT_DEFINITION_SHAPE('','',#{definition})")
        origin_point = add("CARTESIAN_POINT('',(0.,0.,0.))")
        origin = add(f"AXIS2_PLACEMENT_3D('',#{origin_point},#{z_axis},#{x_axis})")
        shifted_point = add(f"CARTESIAN_POINT('',({2.0 ** level:.1f},0.,0.))")
        shifted = add(f"AXIS2_PLACEMENT_3D('',#{shifted_point},#{z_axis},#{x_axis})")
        representation = add(f"SHAPE_REPRESENTATION('',(#{origin},#{shifted}),#{context})")
        add(f"SHAPE_DEFINITION_REPRESENTATION(#{definition_shape},#{representation})")
        add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{product}))")
        for k, placement in enumerate((origin, shifted), start=1):
            transformation = add(f"ITEM_DEFINED_TRANSFORMATION('','',#{child_axis},#{placement})")
            relationship = add(f"( REPRESENTATION_RELATIONSHIP('','',#{child_representation},#{representation}) "
                               f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{transformation}) "
                               f"SHAPE_REPRESENTATION_RELATIONSHIP() )")
            usage = add(f"NEXT_ASSEMBLY_USAGE_OCCURRENCE('{level}.{k}','','',#{definition},#{child_definition},$)")
            placement_shape = add(f"PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#{usage})")
            add(f"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{relationship},#{placement_shape})")
        child_definition, child_representation, child_axis = definition, representation, origin
    return header + "DATA;" + entities + "\n".join(lines) + "\nENDSEC;\nEND-ISO-10303-21;\n"


def _nauo_file(tmp_path, edges, name="nauo.step"):
    """A STEP data section of NEXT_ASSEMBLY_USAGE_OCCURRENCE (parent, child) entities only."""
    body = "\n".join(f"#{1000 + i} = NEXT_ASSEMBLY_USAGE_OCCURRENCE('{i}','','',#{parent},#{child},$);"
                     for i, (parent, child) in enumerate(edges))
    path = tmp_path / name
    path.write_text(f"ISO-10303-21;\nDATA;\n{body}\nENDSEC;\nEND-ISO-10303-21;\n")
    return str(path)


def _doubling(depth):
    return [(level, level - 1) for level in range(1, depth + 1) for _ in range(2)]


@pytest.mark.unit
class TestAssemblyOccurrences:
    def test_a_doubling_assembly_is_counted_without_expanding_it(self, tmp_path):
        assert cad_handler.assembly_occurrences(_nauo_file(tmp_path, _doubling(3)), 1_000) == 2 + 4 + 8
        deep = _nauo_file(tmp_path, _doubling(60), "deep.step")
        assert cad_handler.assembly_occurrences(deep, 1_000_000) == 1_000_001

    def test_a_deep_chain_is_counted_without_recursion(self, tmp_path):
        chain = [(level, level - 1) for level in range(1, 5_001)]
        assert cad_handler.assembly_occurrences(_nauo_file(tmp_path, chain), 1_000_000) == 5_000

    def test_a_cycle_counts_as_over_the_bound(self, tmp_path):
        assert cad_handler.assembly_occurrences(_nauo_file(tmp_path, [(1, 2), (2, 1)]), 100) == 101
        assert cad_handler.assembly_occurrences(_nauo_file(tmp_path, [(9, 1), (1, 2), (2, 1)], "c.step"), 100) == 101

    def test_escaped_quotes_a_missing_description_and_line_breaks_are_read(self, tmp_path):
        path = tmp_path / "forms.step"
        path.write_text("DATA;\n#7=NEXT_ASSEMBLY_USAGE_OCCURRENCE('it''s','a;b',$,\n  #5 ,\n#6,$);\n"
                        "#8 = next_assembly_usage_occurrence('2','','x',#5,#6,'d');\nENDSEC;\n")
        assert cad_handler.assembly_occurrences(str(path), 100) == 2

    def test_a_file_without_an_assembly_has_no_occurrences(self, tmp_path):
        path = tmp_path / "part.step"
        path.write_text("DATA;\n#1=PRODUCT('p','p','',(#2));\nENDSEC;\n")
        assert cad_handler.assembly_occurrences(str(path), 100) == 0
        empty = tmp_path / "empty.step"
        empty.write_bytes(b"")
        assert cad_handler.assembly_occurrences(str(empty), 100) == 0


class _Unbounded(BaseException):
    """Raised by a counting wrapper once a walk passes any plausible bound. A BaseException, so the
    loader's ``except Exception`` cannot turn it into a fallback that keeps expanding."""


@pytest.mark.unit
@requires_real("cadquery", "OCP", "pyvista")
class TestStepExpansion:
    def _fan_out(self, tmp_path, depth):
        import cadquery as cq

        leaf = tmp_path / "leaf.step"
        cq.exporters.export(cq.Workplane("XY").box(1, 1, 1), str(leaf))
        path = tmp_path / f"fan{depth}.step"
        path.write_text(_fan_out_step(leaf.read_text(), depth))
        return str(path)

    def _budgets(self, triangles):
        from preview_pipeline import resources

        return resources.Budgets(memory_bytes=16 * 1024 ** 3, cpus=4, points=1_000_000, triangles=triangles,
                                 splats=250_000)

    def _counting_colour_of(self):
        calls = [0]
        colour_of = cad_handler._colour_of

        def wrapper(*args):
            calls[0] += 1
            if calls[0] > 50_000:
                raise _Unbounded("the assembly walk passed 50,000 labels")
            return colour_of(*args)

        return wrapper, calls

    def test_an_assembly_placing_more_than_the_node_bound_is_refused_before_it_is_read(self, tmp_path):
        from preview_pipeline import core

        path = self._fan_out(tmp_path, depth=21)
        colour_of, calls = self._counting_colour_of()
        with patch.object(cad_handler, "_colour_of", colour_of), \
                pytest.raises(ValueError, match="more than 1,000,000 components"):
            core._load_file(path, ".step", budgets=self._budgets(1_000))
        assert calls[0] == 0, "refused before the walk"

    def test_an_assembly_within_the_node_bound_stops_at_the_triangle_budget(self, tmp_path, caplog):
        from preview_pipeline import core

        path = self._fan_out(tmp_path, depth=8)
        with caplog.at_level("WARNING"):
            mesh = core._load_file(path, ".step", budgets=self._budgets(1_000))
        assert 1_000 < mesh.n_cells <= 1_012, "boxes of 12 triangles until one crosses the budget"
        assert caplog.text.count("expands past 1,000 triangles") == 1

    def test_an_assembly_within_both_bounds_places_every_part(self, tmp_path):
        mesh = cad_handler.load(self._fan_out(tmp_path, depth=4))
        assert mesh.n_cells == 12 * 2 ** 4
        assert mesh.bounds[1] - mesh.bounds[0] == pytest.approx(2 ** 4, abs=1e-6)
