#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Which root prims beside a USD stage's default prim are left out of the preview.

A root prim is left out only when it clearly is a rig: the largest root, at least 1,000 times larger
than the next largest, or at least 50 times larger and holding lights or cameras. A placeholder
default prim, peer roots and a site plan beside a building are all drawn, as they are for a stage
without a default prim. A left-out root is also kept in the incomplete-preview report when anything
that is drawn depends on it.
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline.format_handlers import usd_handler


def _write(tmp_path, body, default='"World"', name="scene.usda"):
    header = f"defaultPrim = {default}\n    " if default else ""
    path = tmp_path / name
    path.write_text(f'#usda 1.0\n(\n    {header}upAxis = "Y"\n)\n{body}\n')
    return str(path)


def _cube(name, size, at=(0, 0, 0), extra=""):
    return (f'    def Cube "{name}"\n    {{\n        double size = {size}\n'
            f'        double3 xformOp:translate = {at}\n        uniform token[] xformOpOrder = '
            f'["xformOp:translate"]\n{extra}    }}\n')


def _root(name, *children, lights=False):
    light = '    def DistantLight "Sun"\n    {\n    }\n' if lights else ""
    return f'def Xform "{name}"\n{{\n' + "".join(children) + light + "}\n"


def _sphere(name, radius):
    return f'    def Sphere "{name}"\n    {{\n        double radius = {radius}\n    }}\n'


def _load(path):
    report = {}
    data = usd_handler.load(path, report=report)
    bounds = np.asarray(data.bounds).reshape(3, 2)
    return float((bounds[:, 1] - bounds[:, 0]).max()), report


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestRigRule:
    def test_a_placeholder_default_prim_beside_the_model_draws_the_model(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("locator", 0.2)) + _root("Car", _cube("body", 4.5)))
        extent, report = _load(path)
        assert extent > 4 and report == {}

    def test_peer_roots_with_the_default_prim_set_to_the_first_are_both_drawn(self, tmp_path):
        path = _write(tmp_path, _root("group1", _cube("a", 4)) + _root("group2", _cube("b", 4, (0, 0, 20))),
                      default='"group1"')
        extent, report = _load(path)
        assert extent > 20 and report == {}

    def test_a_tiny_locator_a_car_and_a_10_km_rig_draw_the_car_and_leave_out_the_rig(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("locator", 0.01)) + _root("Car", _cube("body", 4))
                      + _root("Rig", _sphere("environment", 5000)))
        extent, report = _load(path)
        assert 3.9 < extent < 4.5 and report["leftOut"] == ["Rig"]

    def test_a_site_plan_beside_a_building_is_drawn(self, tmp_path):
        site = _cube("plan", 300, extra='        double3 xformOp:scale = (1, 0.01, 1)\n').replace(
            'xformOpOrder = ["xformOp:translate"]', 'xformOpOrder = ["xformOp:translate", "xformOp:scale"]')
        path = _write(tmp_path, _root("World", _cube("building", 5)) + _root("Site", site))
        extent, report = _load(path)
        assert extent > 250 and report == {}

    def test_two_ordinary_roots_one_with_lights_are_both_drawn(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("a", 4)) + _root("Other", _cube("b", 4, (0, 0, 12)),
                                                                      lights=True))
        extent, report = _load(path)
        assert extent > 12 and report == {}

    def test_a_root_60_times_the_default_prim_is_left_out_with_lights_and_drawn_without(self, tmp_path):
        lit = _write(tmp_path, _root("World", _cube("a", 4)) + _root("Dome", _sphere("sky", 125), lights=True),
                     name="lit.usda")
        bare = _write(tmp_path, _root("World", _cube("a", 4)) + _root("Dome", _sphere("sky", 125)),
                      name="bare.usda")
        assert _load(lit)[1]["leftOut"] == ["Dome"]
        extent, report = _load(bare)
        assert extent > 200 and report == {}

    def test_a_root_with_only_lights_changes_nothing(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("a", 4)) + _root("Lights", lights=True))
        extent, report = _load(path)
        assert 3.9 < extent < 4.5 and report == {}

    def test_a_default_prim_larger_than_the_other_roots_leaves_nothing_out(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("a", 4000)) + _root("Small", _cube("b", 1)))
        assert _load(path)[1] == {}


@pytest.mark.unit
@requires_real("pxr")
class TestLeftOutRootsUnused:
    def _stage(self, tmp_path, default_body, name="scene.usda"):
        return _write(tmp_path, default_body + _root("Rig", _sphere("environment", 5000)), name=name)

    def test_a_rig_nothing_refers_to_is_unused(self, tmp_path):
        path = self._stage(tmp_path, _root("World", _cube("a", 4)))
        assert usd_handler.left_out_roots_unused(path, ["Rig"]) == ["Rig"]

    def test_an_internal_reference_into_the_rig_makes_it_used(self, tmp_path):
        path = self._stage(tmp_path, 'def Xform "World" (\n    prepend references = </Rig/environment>\n)\n{\n}\n')
        assert usd_handler.left_out_roots_unused(path, ["Rig"]) == []

    def test_an_instancer_prototype_in_the_rig_makes_it_used(self, tmp_path):
        body = ('def Xform "World"\n{\n    def PointInstancer "Inst"\n    {\n'
                '        rel prototypes = [</Rig/environment>]\n        int[] protoIndices = [0]\n'
                '        point3f[] positions = [(0, 0, 0)]\n    }\n}\n')
        assert usd_handler.left_out_roots_unused(self._stage(tmp_path, body), ["Rig"]) == []

    def test_a_relationship_target_in_the_rig_makes_it_used(self, tmp_path):
        body = 'def Xform "World"\n{\n    rel look:target = </Rig/environment>\n}\n'
        assert usd_handler.left_out_roots_unused(self._stage(tmp_path, body), ["Rig"]) == []

    def test_an_attribute_connection_into_the_rig_makes_it_used(self, tmp_path):
        body = ('def Xform "World"\n{\n    color3f inputs:tint.connect = '
                '</Rig/environment.outputs:out>\n}\n')
        assert usd_handler.left_out_roots_unused(self._stage(tmp_path, body), ["Rig"]) == []

    def test_a_root_that_is_not_a_rig_is_never_in_the_report(self, tmp_path):
        body = ('def Xform "Prototypes"\n{\n    def Xform "Car" (\n        payload = @./missing.usda@\n'
                '    )\n    {\n    }\n}\n'
                'def Xform "World" (\n    prepend references = </Prototypes/Car>\n)\n{\n'
                + _cube("a", 4) + '}\n')
        extent, report = _load(_write(tmp_path, body))
        assert extent > 3 and report == {}
