#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Which root prims beside a USD stage's default prim are left out of the preview.

A root prim is left out only when it looks like a rig by both tests: it is at least 50 times larger than
the default prim, and it holds both a camera and a light. A placeholder
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


def _root(name, *children, lights=False, camera=False):
    light = '    def DistantLight "Sun"\n    {\n    }\n' if lights else ""
    cam = '    def Camera "Cam"\n    {\n    }\n' if camera else ""
    return f'def Xform "{name}"\n{{\n' + "".join(children) + light + cam + "}\n"


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

    def test_a_tiny_locator_a_car_and_a_10_km_environment_without_lights_draws_both(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("locator", 0.01)) + _root("Car", _cube("body", 4))
                      + _root("Rig", _sphere("environment", 5000)))
        extent, report = _load(path)
        assert extent > 9000 and report == {}

    def test_a_1_mm_placeholder_with_a_4_m_car_and_no_lights_draws_the_car(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("locator", 0.001)) + _root("Car", _cube("body", 4)))
        extent, report = _load(path)
        assert extent >= 4 and report == {}

    def test_a_10_cm_placeholder_with_a_300_m_site_plan_draws_both(self, tmp_path):
        site = _cube("plan", 300, extra='        double3 xformOp:scale = (1, 0.01, 1)\n').replace(
            'xformOpOrder = ["xformOp:translate"]', 'xformOpOrder = ["xformOp:translate", "xformOp:scale"]')
        path = _write(tmp_path, _root("World", _cube("locator", 0.1)) + _root("Site", site))
        extent, report = _load(path)
        assert extent > 250 and report == {}

    def test_a_car_root_with_a_light_and_no_camera_beside_a_placeholder_is_drawn(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("locator", 0.05))
                      + _root("Car", _cube("body", 4), lights=True))
        extent, report = _load(path)
        assert extent >= 4 and report == {}

    def test_a_root_with_a_camera_and_no_light_is_drawn(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("a", 4)) + _root("Dome", _sphere("sky", 500), camera=True))
        extent, report = _load(path)
        assert extent > 900 and report == {}

    def test_a_rig_with_a_camera_and_a_light_is_left_out(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("locator", 0.2)) + _root(
            "Rig", _sphere("environment", 5000), lights=True, camera=True))
        extent, report = _load(path)
        assert extent < 1 and report["leftOut"] == ["Rig"]

    def test_any_light_type_counts(self, tmp_path):
        for light_type in ("DomeLight", "RectLight", "SphereLight", "DiskLight", "CylinderLight"):
            rig = (f'def Xform "Rig"\n{{\n{_sphere("sky", 500)}    def {light_type} "L"\n    {{\n    }}\n'
                   f'    def Camera "Cam"\n    {{\n    }}\n}}\n')
            path = _write(tmp_path, _root("World", _cube("a", 4)) + rig, name=f"{light_type}.usda")
            assert _load(path)[1].get("leftOut") == ["Rig"], light_type

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

    def test_a_root_60_times_the_default_prim_needs_both_a_camera_and_a_light(self, tmp_path):
        both = _write(tmp_path, _root("World", _cube("a", 4)) + _root(
            "Dome", _sphere("sky", 125), lights=True, camera=True), name="both.usda")
        light_only = _write(tmp_path, _root("World", _cube("a", 4)) + _root(
            "Dome", _sphere("sky", 125), lights=True), name="light.usda")
        assert _load(both)[1]["leftOut"] == ["Dome"]
        extent, report = _load(light_only)
        assert extent > 200 and report == {}

    def test_a_root_with_only_lights_changes_nothing(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("a", 4)) + _root("Lights", lights=True))
        extent, report = _load(path)
        assert 3.9 < extent < 4.5 and report == {}

    def test_a_default_prim_larger_than_the_other_roots_leaves_nothing_out(self, tmp_path):
        path = _write(tmp_path, _root("World", _cube("a", 4000)) + _root("Small", _cube("b", 1),
                                                                         lights=True, camera=True))
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
