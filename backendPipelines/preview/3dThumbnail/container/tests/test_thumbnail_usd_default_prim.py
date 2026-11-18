#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A USD stage with a default prim draws what the default prim composes.

A stage's default prim is what another file gets when it references this one. Content packs often
add root prims beside it: a thumbnail rig with a very large environment mesh, a lights or camera
set, a navigation mesh. Drawing every root prim framed the preview on those, and the model shrank to
a dot or vanished. The handler now draws the default prim's subtree only, and the whole stage when
the stage has no usable default prim or the default prim draws nothing.

The stage tests need the real pxr/pyvista and run inside the pipeline image; the report tests need
pxr for the layer they read.
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import dependencies
from preview_pipeline.format_handlers import usd_handler

_MODEL = """
    def Xform "Model"
    {
        def Cube "Body"
        {
            double size = 2
            double3 xformOp:translate = (0, 1, 0)
            uniform token[] xformOpOrder = ["xformOp:translate"]
        }
        def Sphere "Head"
        {
            double radius = 0.6
            double3 xformOp:translate = (0, 2.6, 0)
            uniform token[] xformOpOrder = ["xformOp:translate"]
        }
    }"""


def _rig(radius=5000, lights=True):
    light = ('    def DistantLight "Sun"\n    {\n    }\n    def Camera "Cam"\n    {\n    }\n'
             if lights else "")
    return f"""
def Xform "Rig"
{{
    def Sphere "environment"
    {{
        double radius = {radius}
    }}
{light}}}"""


_RIG = _rig()


def _stage(tmp_path, body, default='"World"', name="scene.usda"):
    header = f"defaultPrim = {default}\n    " if default else ""
    path = tmp_path / name
    path.write_text(f'#usda 1.0\n(\n    {header}upAxis = "Y"\n)\n{body}\n')
    return str(path)


def _extent(data):
    bounds = np.asarray(data.bounds).reshape(3, 2)
    return float((bounds[:, 1] - bounds[:, 0]).max())


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestDefaultPrim:
    def test_root_prims_beside_the_default_prim_are_not_drawn(self, tmp_path):
        path = _stage(tmp_path, f'def Xform "World"\n{{{_MODEL}\n}}\n{_RIG}')
        report = {}
        data = usd_handler.load(path, report=report)
        assert _extent(data) < 5, "the rig's environment mesh was drawn"
        assert report["leftOut"] == ["Rig"]

    def test_a_stage_without_a_default_prim_draws_every_root_prim(self, tmp_path):
        path = _stage(tmp_path, f'def Xform "World"\n{{{_MODEL}\n}}\n{_RIG}', default=None)
        report = {}
        assert _extent(usd_handler.load(path, report=report)) > 5000
        assert report == {}

    def test_a_default_prim_that_names_no_prim_draws_every_root_prim(self, tmp_path):
        path = _stage(tmp_path, f'def Xform "World"\n{{{_MODEL}\n}}\n{_RIG}', default='"Missing"')
        assert _extent(usd_handler.load(path)) > 5000

    def test_an_inactive_default_prim_draws_every_root_prim(self, tmp_path):
        body = f'def Xform "World" (\n    active = false\n)\n{{{_MODEL}\n}}\n{_RIG}'
        assert _extent(usd_handler.load(_stage(tmp_path, body))) > 5000

    def test_a_default_prim_that_draws_nothing_falls_back_to_the_whole_stage(self, tmp_path):
        body = 'def Xform "World"\n{\n    def Xform "Lights"\n    {\n    }\n}\n' + _MODEL.replace(
            'def Xform "Model"', 'def Xform "Model"', 1)
        report = {}
        data = usd_handler.load(_stage(tmp_path, body), report=report)
        assert data.n_points > 0 and _extent(data) < 5
        assert report == {}

    def test_a_default_prim_beside_geometry_only_in_other_roots_keeps_that_geometry(self, tmp_path):
        data = usd_handler.load(_stage(tmp_path, f'def Xform "World"\n{{\n}}\n{_RIG}'))
        assert _extent(data) > 5000

    def test_the_default_prims_ground_plane_is_still_dropped_as_a_backdrop(self, tmp_path):
        ground = ('def Mesh "Ground"\n{\n    int[] faceVertexCounts = [4]\n'
                  '    int[] faceVertexIndices = [0, 1, 2, 3]\n'
                  '    point3f[] points = [(-500, 0, -500), (500, 0, -500), (500, 0, 500), (-500, 0, 500)]\n}')
        path = _stage(tmp_path, f'def Xform "World"\n{{{_MODEL}\n{ground}\n}}\n{_rig(500000)}')
        assert _extent(usd_handler.load(path)) < 5

    def test_two_distant_objects_in_the_default_prim_are_both_kept(self, tmp_path):
        far = _MODEL.replace('def Xform "Model"', 'def Xform "Far"\n    (\n    )\n    ', 1)
        far = far.replace('{\n        def Cube', '{\n        double3 xformOp:translate = (300, 0, 0)\n'
                          '        uniform token[] xformOpOrder = ["xformOp:translate"]\n        def Cube', 1)
        data = usd_handler.load(_stage(tmp_path, f'def Xform "World"\n{{{_MODEL}\n{far}\n}}\n{_rig(500000)}'))
        assert 250 < _extent(data) < 400

    def test_point_instancer_prototypes_in_another_root_draw_as_they_do_for_a_stage_without_a_default_prim(self, tmp_path):
        body = f"""
def Xform "Protos"
{{
    def Cube "p0"
    {{
        double size = 1
    }}
}}
def Xform "World"
{{
    def PointInstancer "Inst"
    {{
        rel prototypes = [</Protos/p0>]
        int[] protoIndices = [0, 0]
        point3f[] positions = [(0, 0, 0), (10, 0, 0)]
    }}
}}"""
        data = usd_handler.load(_stage(tmp_path, body))
        # two instances, plus the prototype root itself, which is a root prim that draws like any other
        assert data.n_points == 24

    def test_native_instances_in_the_default_prim_are_drawn_at_every_instance(self, tmp_path):
        body = f"""
class Xform "Lib"
{{{_MODEL}
}}
def Xform "World"
{{
    def Xform "A" (
        instanceable = true
        references = </Lib/Model>
    )
    {{
    }}
    def Xform "B" (
        instanceable = true
        references = </Lib/Model>
    )
    {{
        double3 xformOp:translate = (10, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }}
}}"""
        data = usd_handler.load(_stage(tmp_path, body))
        assert 10 < _extent(data) < 20

    def test_materials_in_a_root_prim_beside_the_default_prim_still_colour_the_model(self, tmp_path):
        body = """
def Xform "Looks"
{
    def Material "Red"
    {
        token outputs:surface.connect = </Looks/Red/Surface.outputs:surface>
        def Shader "Surface"
        {
            uniform token info:id = "UsdPreviewSurface"
            color3f inputs:diffuseColor = (1, 0, 0)
            token outputs:surface
        }
    }
}
def Xform "World"
{
    def Cube "Box" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        rel material:binding = </Looks/Red>
    }
}"""
        data = usd_handler.load(_stage(tmp_path, body))
        rgb = np.asarray(data.point_data["RGB"])
        assert (rgb[:, 0] > 200).all() and (rgb[:, 1] < 50).all()


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestRenderedAsTheModelAlone:
    def test_orbit_frames_match_the_same_model_rendered_alone(self, tmp_path):
        from preview_pipeline import core, renderer

        with_rig = _stage(tmp_path, f'def Xform "World"\n{{{_MODEL}\n}}\n{_RIG}', name="rig.usda")
        alone = _stage(tmp_path, f'def Xform "World"\n{{{_MODEL}\n}}', name="alone.usda")
        frames = []
        for path in (with_rig, alone):
            data = core._normalize_up_axis(usd_handler.load(path), ".usda")
            frames.append(renderer.generate_rotating_frames(data, n_frames=6, resolution=(160, 120)))
        for rigged, bare in zip(*frames):
            a, b = rigged[..., 3] > 0, bare[..., 3] > 0
            assert (a & b).sum() / max((a | b).sum(), 1) >= 0.99
            assert (rigged[..., 3] == 0).mean() > 0.3, "environment pixels fill the frame"


def _layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f'#usda 1.0\n(\n    defaultPrim = "World"\n)\n{body}\n')
    return str(path)


@pytest.mark.unit
@requires_real("pxr")
class TestReportLeavesOutUnusedRoots:
    def _report(self, input_rel, edges, skipped):
        report = dependencies.FetchReport()
        report.edges = list(edges)
        report.skipped = [dependencies.SkippedReference(ref, "missing from the asset", referrer,
                                                        kind=dependencies.KIND_GEOMETRY_LAYER)
                          for ref, referrer in skipped]
        return report

    def _input(self, tmp_path):
        return _layer(tmp_path, "scene.usda", """
def Xform "World" ( references = @./model.usda@ )
{
}
def Xform "Tagging" ( payload = @./rig.usda@ )
{
}""")

    def test_a_file_only_a_left_out_root_uses_is_not_reported(self, tmp_path):
        report = self._report("scene.usda", [("scene.usda", "model.usda"), ("scene.usda", "rig.usda"),
                                             ("rig.usda", "lights.usda")],
                              [("rig.usda", "scene.usda"), ("lights.usda", "rig.usda")])
        removed = dependencies.drop_left_out_references(report, "scene.usda", self._input(tmp_path),
                                                        ["Tagging"])
        assert removed == 2 and dependencies.incomplete_preview(report) is None

    def test_a_file_the_default_prim_uses_is_still_reported(self, tmp_path):
        report = self._report("scene.usda", [("scene.usda", "model.usda"), ("scene.usda", "rig.usda")],
                              [("model.usda", "scene.usda")])
        assert dependencies.drop_left_out_references(report, "scene.usda", self._input(tmp_path),
                                                     ["Tagging"]) == 0
        assert dependencies.incomplete_preview(report) is not None

    def test_a_file_both_a_left_out_root_and_another_file_use_is_still_reported(self, tmp_path):
        report = self._report("scene.usda", [("scene.usda", "model.usda"), ("scene.usda", "rig.usda"),
                                             ("model.usda", "rig.usda")],
                              [("rig.usda", "scene.usda")])
        assert dependencies.drop_left_out_references(report, "scene.usda", self._input(tmp_path),
                                                     ["Tagging"]) == 0

    def test_nothing_is_removed_when_no_root_prim_was_left_out(self, tmp_path):
        report = self._report("scene.usda", [("scene.usda", "rig.usda")], [("rig.usda", "scene.usda")])
        assert dependencies.drop_left_out_references(report, "scene.usda", self._input(tmp_path), []) == 0
