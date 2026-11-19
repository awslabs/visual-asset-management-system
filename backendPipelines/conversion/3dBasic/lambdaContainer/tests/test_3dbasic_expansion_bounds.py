#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A COLLADA or USD scene is sized before its loader expands it (backendPipelines/CLAUDE.md "Sizing a
Container's Work to Its Task").

Nested instances multiply, so the size of a scene differs from the size of its file. ``trimesh`` builds
an entry for every COLLADA placement of every primitive element, and ``Usd.Stage.Open`` composes every
USD prim before the walk's own bound can apply, so ``expansion_bounds`` counts both from the file first
and ``model_formats``/``usd_io`` refuse a scene over the conversion bound. Real libraries throughout; a
refusal is checked by the loader never being reached. The tests give the bound to the job, so a small
scene stands for a large one."""

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import expansion_bounds  # noqa: E402
import model_formats  # noqa: E402
import scene_limits  # noqa: E402

pxr = pytest.importorskip("pxr")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("collada")

import usd_io  # noqa: E402
from pxr import Usd  # noqa: E402


# --------------------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------------------

_TRIANGLE = "0 0 0 1 0 0 0 1 0"


def dae(depth, triangles=1, cycle=False, instances=1, primitives=1):
    """Library node n_k holds an instance of n_(k-1) and a child node holding a second one; n_0 holds
    ``instances`` placements of one geometry of ``primitives`` primitive elements of ``triangles``
    triangles each."""
    library = ['<node id="n0">' + '<instance_geometry url="#g"/>' * instances + '</node>']
    for k in range(1, depth + 1):
        library.append(f'<node id="n{k}"><instance_node url="#n{k - 1}"/>'
                       f'<node><translate>1 0 0</translate><instance_node url="#n{k - 1}"/></node></node>')
    if cycle:
        library.append('<node id="a"><instance_node url="#b"/></node><node id="b"><instance_node url="#a"/></node>')
        top = "a"
    else:
        top = f"n{depth}"
    positions = " ".join(str(float(i % 7)) for i in range(3 * (triangles + 2)))
    indices = " ".join(f"{i} {i + 1} {i + 2}" for i in range(triangles))
    return f'''<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit name="meter" meter="1"/><up_axis>Y_UP</up_axis></asset>
<library_geometries><geometry id="g"><mesh>
<source id="p"><float_array id="pa" count="{3 * (triangles + 2)}">{positions}</float_array>
<technique_common><accessor source="#pa" count="{triangles + 2}" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>
<vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
{"".join(f'<triangles count="{triangles}" material="m{i}"><input semantic="VERTEX" source="#v" offset="0"/><p>{indices}</p></triangles>' for i in range(primitives))}
</mesh></geometry></library_geometries>
<library_nodes>{"".join(library)}</library_nodes>
<library_visual_scenes><visual_scene id="s"><node id="root"><instance_node url="#{top}"/></node></visual_scene></library_visual_scenes>
<scene><instance_visual_scene url="#s"/></scene></COLLADA>'''


def usda(depth, instanceable=False):
    """Prim L_k has two children that each reference L_(k-1); L_0 holds a cube."""
    marker = "\n        instanceable = true" if instanceable else ""
    out = ['#usda 1.0', '(', '    defaultPrim = "Root"', ')', '',
           'def Xform "L0"', '{', '    def Cube "c"', '    {', '    }', '}', '']
    for k in range(1, depth + 1):
        out += [f'def Xform "L{k}"', '{',
                f'    def Xform "a" (\n        references = </L{k - 1}>{marker}\n    )', '    {', '    }',
                f'    def Xform "b" (\n        references = </L{k - 1}>{marker}\n    )', '    {',
                '        double3 xformOp:translate = (1, 0, 0)',
                '        uniform token[] xformOpOrder = ["xformOp:translate"]', '    }', '}', '']
    out += ['def Xform "Root" (\n    references = </L%d>\n)' % depth, '{', '}', '']
    return "\n".join(out)


def instanced_usda(prototype_prims, instances):
    """A prototype of ``prototype_prims`` cubes and ``instances`` instanceable prims that reference it."""
    out = ['#usda 1.0', '(', '    defaultPrim = "World"', ')', '', 'def Xform "Proto"', '{']
    out += [f'    def Cube "c{i}"\n    {{\n    }}' for i in range(prototype_prims)]
    out += ['}', '', 'def Xform "World"', '{']
    out += [f'    def Xform "i{i}" (\n        instanceable = true\n        references = </Proto>\n    )\n    {{\n    }}'
            for i in range(instances)]
    out += ['}', '']
    return "\n".join(out)


def _job(directory, name, text, max_scene_nodes=None):
    path = directory / name
    path.write_text(text)
    job = {"input_file": str(path), "input_dir": str(directory), "input_relative_path": name,
           "by_reference": {}, "input_name": name}
    if max_scene_nodes is not None:
        job["max_scene_nodes"] = max_scene_nodes
    return job


def _all_prims(path):
    stage = Usd.Stage.Open(str(path))
    return len(list(Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate)))


@pytest.fixture
def stage_never_opened(monkeypatch):
    """Fail the test if the scene gets as far as composing a stage."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("Usd.Stage.Open was called")
    monkeypatch.setattr(usd_io, "_import_pxr", _import_without_open(refuse))


def _import_without_open(refuse):
    real = usd_io._import_pxr

    class _Usd:
        def __getattr__(self, name):
            if name == "Stage":
                return type("Stage", (), {"Open": staticmethod(refuse)})
            return getattr(pxr.Usd, name)

    def patched():
        modules = list(real())
        modules[4] = _Usd()
        return tuple(modules)
    return patched


# --------------------------------------------------------------------------------------------------
# COLLADA
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestColladaInstancing:
    def test_a_scene_whose_placements_exceed_the_bound_is_refused_before_trimesh_reads_it(self, tmp_path):
        job = _job(tmp_path, "nested.dae", dae(12), max_scene_nodes=1000)
        trimesh_stub = MagicMock()
        started = time.monotonic()
        with pytest.raises(ValueError, match=r"nested\.dae: the COLLADA scene expands to more than 1,000 "
                                             r"nodes, more than this function converts"):
            model_formats.load_model(job, trimesh_stub)
        assert time.monotonic() - started < 5
        trimesh_stub.load.assert_not_called()

    def test_the_message_names_nodes_not_memory(self, tmp_path):
        job = _job(tmp_path, "nested.dae", dae(12), max_scene_nodes=1000)
        with pytest.raises(ValueError) as refused:
            model_formats.load_model(job, MagicMock())
        assert "memory" not in str(refused.value)

    def test_a_scene_below_the_bound_still_converts(self, tmp_path):
        job = _job(tmp_path, "nested.dae", dae(8))
        loaded, meters, up = model_formats.load_model(job, trimesh)
        assert meters == 1.0 and up == 'y'
        assert sum(len(g.faces) for g in loaded.dump()) == 2 ** 8

    @pytest.mark.parametrize("depth", [1, 3, 5, 9])
    @pytest.mark.parametrize("primitives", [1, 3])
    def test_the_count_is_never_below_what_trimesh_places(self, tmp_path, depth, primitives):
        job = _job(tmp_path, "nested.dae", dae(depth, triangles=3, instances=2, primitives=primitives))
        size = expansion_bounds.collada_scene_size(job["input_file"], 10 ** 6)
        loaded = trimesh.load(job["input_file"])
        placed_triangles = sum(len(g.faces) for g in loaded.dump())
        assert placed_triangles == 3 * 2 * primitives * 2 ** depth
        assert size.nodes >= len(loaded.graph.nodes_geometry) >= 2 * primitives * 2 ** depth
        assert size.triangles >= placed_triangles
        assert size.vertices >= 3 * placed_triangles
        assert size.stored_triangles == 3 * primitives

    def test_each_primitive_element_of_a_placed_geometry_counts_as_a_node(self, tmp_path):
        # 64 placements of a geometry of 20 primitive elements place 1,280 scene nodes in a file whose
        # placements alone count about 250.
        many = _job(tmp_path, "many.dae", dae(6, primitives=20))
        one = _job(tmp_path, "one.dae", dae(6, primitives=1))
        with pytest.raises(ValueError, match="expands to more than 1,000 nodes"):
            model_formats.check_collada_scene(many["input_file"], "many.dae", max_nodes=1000)
        model_formats.check_collada_scene(one["input_file"], "one.dae", max_nodes=1000)
        assert len(trimesh.load(many["input_file"]).graph.nodes_geometry) == 64 * 20

    def test_a_small_file_with_many_primitive_elements_is_refused_before_it_loads(self, tmp_path):
        job = _job(tmp_path, "prims.dae", dae(8, primitives=100), max_scene_nodes=10_000)
        assert os.path.getsize(job["input_file"]) < 20_000
        trimesh_stub = MagicMock()
        with pytest.raises(ValueError, match=r"prims\.dae: the COLLADA scene expands to more than 10,000 nodes"):
            model_formats.load_model(job, trimesh_stub)
        trimesh_stub.load.assert_not_called()

    def test_the_bound_is_the_conversion_bound_unless_given(self, tmp_path):
        job = _job(tmp_path, "nested.dae", dae(10))
        model_formats.check_collada_scene(job["input_file"], "nested.dae")
        with pytest.raises(ValueError, match="expands to more than 100 nodes"):
            model_formats.check_collada_scene(job["input_file"], "nested.dae", max_nodes=100)

    def test_a_node_that_places_itself_is_refused(self, tmp_path):
        job = _job(tmp_path, "loop.dae", dae(2, cycle=True))
        trimesh_stub = MagicMock()
        with pytest.raises(ValueError, match=r"loop\.dae: a node in the COLLADA scene places itself"):
            model_formats.load_model(job, trimesh_stub)
        trimesh_stub.load.assert_not_called()

    def test_instanced_triangles_past_the_budget_are_refused(self, tmp_path):
        # Placements of a 100-triangle geometry against a 1,000-triangle budget.
        job = _job(tmp_path, "many.dae", dae(6, triangles=100))
        with pytest.raises(ValueError, match="places more than 1,000 triangles through instancing"):
            model_formats.check_collada_scene(job["input_file"], "many.dae", max_triangles=1000)

    def test_one_large_mesh_placed_once_is_not_refused(self, tmp_path):
        job = _job(tmp_path, "big.dae", dae(0, triangles=500))
        size = model_formats.check_collada_scene(job["input_file"], "big.dae", max_triangles=100)
        assert size.triangles == size.stored_triangles == 500

    def test_a_document_type_declaration_is_still_refused(self, tmp_path):
        text = dae(1).replace("<COLLADA", '<!DOCTYPE COLLADA [<!ENTITY x "y">]>\n<COLLADA', 1)
        job = _job(tmp_path, "entity.dae", text)
        with pytest.raises(ValueError, match="is not a COLLADA file this pipeline reads"):
            model_formats.load_model(job, MagicMock())

    def test_index_tokens_split_between_pieces_are_counted_once(self):
        scan = expansion_bounds._ColladaScan()
        scan._stack = [("library_geometries", None)]
        scan.start("geometry", {"id": "g"})
        scan.start("triangles", {"count": "1"})
        scan.start("input", {"offset": "0"})
        scan.end("input")
        scan.start("p", {})
        for piece in ("0 1", " 2 3", "4 5"):  # '2 34 5' is the same tokens however the file is cut
            scan.data(piece)
        scan.end("p")
        scan.end("triangles")
        assert scan.geometries["g"] == [5, 1, 1]


# --------------------------------------------------------------------------------------------------
# USD
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestUsdComposition:
    def test_a_scene_whose_prims_exceed_the_bound_is_refused_before_a_stage_is_opened(
            self, tmp_path, stage_never_opened):
        path = tmp_path / "nested.usda"
        path.write_text(usda(12))
        limit = scene_limits.SceneLimit("nested.usda", max_nodes=1000)
        started = time.monotonic()
        with pytest.raises(ValueError, match=r"nested\.usda: the USD scene expands to more than 1,000 prims "
                                             r"through its references, more than this function converts"):
            usd_io.read_usd(str(path), str(tmp_path), limit=limit)
        assert time.monotonic() - started < 5

    def test_the_default_bound_is_the_shared_one(self, tmp_path, stage_never_opened):
        assert expansion_bounds.CONVERSION_MAX_SCENE_NODES == scene_limits.MAX_SCENE_NODES
        path = tmp_path / "nested.usda"
        path.write_text(usda(24))
        with pytest.raises(ValueError, match="expands to more than 1,000,000 prims"):
            usd_io.read_usd(str(path), str(tmp_path))

    def test_the_message_names_nodes_not_memory(self, tmp_path):
        path = tmp_path / "nested.usda"
        path.write_text(usda(12))
        with pytest.raises(ValueError) as refused:
            usd_io.read_usd(str(path), str(tmp_path), limit=scene_limits.SceneLimit("n", max_nodes=1000))
        assert "memory" not in str(refused.value)

    def test_a_scene_below_the_bound_still_converts(self, tmp_path):
        path = tmp_path / "nested.usda"
        path.write_text(usda(6))
        scene, meters, up = usd_io.read_usd(str(path), str(tmp_path))
        # The file's other prims (L0 to L6) are scene content too; the cubes under Root are the placed ones.
        assert len([name for name in scene.geometry if name.startswith("Root_")]) == 2 ** 6

    @pytest.mark.parametrize("depth", [1, 4, 8])
    def test_the_count_is_never_below_what_the_stage_composes(self, tmp_path, depth):
        path = tmp_path / "nested.usda"
        path.write_text(usda(depth))
        count = expansion_bounds.usd_composed_prims(str(path), 10 ** 6)
        assert count >= _all_prims(path)
        assert count <= 4 * _all_prims(path)

    def test_sublayers_variants_inherits_and_payloads_are_counted(self, tmp_path):
        (tmp_path / "sub.usda").write_text(
            '#usda 1.0\ndef Xform "A"\n{\n    def Xform "x"\n    {\n    }\n    def Xform "y"\n    {\n    }\n}\n')
        (tmp_path / "pay.usda").write_text('#usda 1.0\ndef Xform "P"\n{\n    def Xform "q"\n    {\n    }\n}\n')
        (tmp_path / "root.usda").write_text('''#usda 1.0
(
    defaultPrim = "Root"
    subLayers = [@./sub.usda@]
)

def Xform "Root" (
    inherits = </_class>
    payload = @./pay.usda@</P>
    variants = {
        string v = "x"
    }
    prepend variantSets = "v"
)
{
    variantSet "v" = {
        "x" {
            def Xform "inx"
            {
            }
        }
        "y" {
            def Xform "iny1"
            {
            }
            def Xform "iny2"
            {
            }
        }
    }
}

class Xform "_class"
{
    def Xform "fromClass"
    {
    }
}

over "A"
{
}
''')
        path = tmp_path / "root.usda"
        assert expansion_bounds.usd_composed_prims(str(path), 10 ** 6) >= _all_prims(path)

    def test_instanceable_content_counts_once_for_each_prototype(self, tmp_path):
        path = tmp_path / "instanced.usda"
        path.write_text(instanced_usda(prototype_prims=100, instances=3000))
        count = expansion_bounds.usd_composed_prims(str(path), 1_000_000)
        assert count < 5000  # 3,000 instances and 100 prims, not 303,000
        assert count >= 3100

    def test_distinct_prototypes_each_count_their_own_content(self, tmp_path):
        path = tmp_path / "two.usda"
        text = instanced_usda(prototype_prims=100, instances=10)
        text += ('def Xform "Other"\n{\n' + "".join(f'    def Cube "o{i}"\n    {{\n    }}\n' for i in range(50))
                 + '}\n\ndef Xform "World2" (\n    instanceable = true\n    references = </Other>\n)\n{\n}\n')
        path.write_text(text)
        assert expansion_bounds.usd_composed_prims(str(path), 1_000_000) >= 100 + 50

    def test_an_instanced_scene_converts_under_the_default_bound(self, tmp_path):
        path = tmp_path / "instanced.usda"
        path.write_text(instanced_usda(prototype_prims=20, instances=100))
        scene, _meters, _up = usd_io.read_usd(str(path), str(tmp_path))
        assert len([name for name in scene.geometry if name.startswith("World_")]) == 20 * 100

    def test_instanceable_prims_count_once_and_the_walk_bound_still_applies(self, tmp_path):
        path = tmp_path / "instanced.usda"
        path.write_text(usda(20, instanceable=True))
        count = expansion_bounds.usd_composed_prims(str(path), 200_000)
        assert count < 1000
        limit = scene_limits.SceneLimit("instanced.usda", max_nodes=2000)
        with pytest.raises(ValueError, match="expands to more nodes or geometry than this function converts"):
            usd_io.read_usd(str(path), str(tmp_path), limit=limit)

    def test_a_prim_that_composes_itself_is_refused(self, tmp_path, stage_never_opened):
        path = tmp_path / "loop.usda"
        path.write_text('#usda 1.0\n(\n    defaultPrim = "A"\n)\n'
                        'def Xform "A" (\n    references = </B>\n)\n{\n}\n'
                        'def Xform "B" (\n    references = </A>\n)\n{\n}\n')
        with pytest.raises(ValueError, match=r"loop\.usda: a prim in the USD scene composes itself"):
            usd_io.read_usd(str(path), str(tmp_path))

    def test_the_walk_stops_at_the_deadline(self, tmp_path):
        path = tmp_path / "nested.usda"
        path.write_text(usda(6))
        with pytest.raises(ValueError, match=r"expands to too many nodes to read in the time this function has"):
            usd_io.read_usd(str(path), str(tmp_path), deadline=time.monotonic() - 1)

    def test_the_node_bound_follows_the_memory_of_the_task(self):
        mb = 1024 ** 2
        assert expansion_bounds.max_scene_nodes(None) == 1_000_000
        assert expansion_bounds.max_scene_nodes(10_240 * mb) == 1_000_000
        assert expansion_bounds.max_scene_nodes(3_008 * mb) == 1_000_000
        assert expansion_bounds.max_scene_nodes(30_720 * mb) == 3_000_000

    def test_a_job_carries_the_bound_of_its_task(self, tmp_path):
        nested = usda(12)
        refused = _job(tmp_path, "nested.usda", nested, max_scene_nodes=1000)
        with pytest.raises(ValueError, match="expands to more than 1,000 prims"):
            model_formats.load_model(refused, trimesh)
        larger = _job(tmp_path, "nested.usda", nested, max_scene_nodes=1_000_000)
        scene, _meters, _up = model_formats.load_model(larger, trimesh)
        assert scene.geometry

    def test_load_model_passes_a_deadline_from_the_workers_time(self, tmp_path, monkeypatch):
        job = _job(tmp_path, "nested.usda", usda(2))
        job["tool_timeout_seconds"] = 100
        seen = {}

        def fake_read(path, input_dir, limit=None, deadline=None):
            seen["remaining"] = deadline - time.monotonic()
            return "scene", 1.0, 'y'
        monkeypatch.setattr(usd_io, "read_usd", fake_read)
        assert model_formats.load_model(job, MagicMock()) == ("scene", 1.0, 'y')
        assert 70 < seen["remaining"] <= 75
