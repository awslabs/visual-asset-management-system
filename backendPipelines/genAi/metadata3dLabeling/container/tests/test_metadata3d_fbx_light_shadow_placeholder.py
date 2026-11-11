# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`add_fbx_light_shadow_placeholder` in `renderScene.py` (#1181).

Cycles dropped the light setting `cast_shadow` in Blender 4.2, but the image's bundled FBX importer (io_scene_fbx
5.15.0) still runs `lamp.cycles.cast_shadow = lamp.use_shadow` for every light, so an FBX with a light
fails to import with AttributeError. The helper registers a placeholder property under that name on the
Cycles light settings class, only when the class lacks it.

`renderScene.py` runs inside Blender and its module body renders, so the function definition is extracted
with `ast` and executed against a stand-in `bpy`. A real Blender run of the fix is part of the PR's
evidence and of the pipeline run in the release deploy matrix.
"""

import ast
import io
import os
from types import SimpleNamespace

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
RENDER_SCENE = os.path.join(os.path.dirname(_HERE), "main", "blenderAppScripts", "renderScene.py")
FUNCTION = "add_fbx_light_shadow_placeholder"


class _BoolProperty:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Lights:
    """`bpy.data.lights`: records what was created and removed."""

    def __init__(self, make_light):
        self._make_light = make_light
        self.created = []
        self.removed = []

    def new(self, name, type):
        light = self._make_light()
        self.created.append(light)
        return light

    def remove(self, light):
        self.removed.append(light)


def _fake_bpy(make_light):
    return SimpleNamespace(
        data=SimpleNamespace(lights=_Lights(make_light)),
        props=SimpleNamespace(BoolProperty=_BoolProperty),
    )


def _load_function(bpy):
    for node in ast.parse(io.open(RENDER_SCENE, encoding="utf-8").read()).body:
        if isinstance(node, ast.FunctionDef) and node.name == FUNCTION:
            module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
            namespace = {"bpy": bpy}
            exec(compile(module, "<renderScene-placeholder>", "exec"), namespace)  # nosemgrep: python.lang.security.audit.exec-detected.exec-detected -- runs one function parsed from this repository's renderScene.py against a stand-in bpy; no outside input
            return namespace[FUNCTION]
    raise AssertionError(f"renderScene.py defines no {FUNCTION}()")


@pytest.mark.unit
def test_adds_cast_shadow_when_cycles_lacks_it():
    """Blender 5.1: the Cycles light settings class has no `cast_shadow`; the helper adds a boolean."""

    class CyclesLightSettings:
        pass

    bpy = _fake_bpy(lambda: SimpleNamespace(cycles=CyclesLightSettings()))
    _load_function(bpy)()

    placeholder = CyclesLightSettings.__dict__.get("cast_shadow")
    assert isinstance(placeholder, _BoolProperty)
    assert placeholder.kwargs == {"default": True}


@pytest.mark.unit
def test_leaves_an_existing_cast_shadow_alone():
    """A Blender that still has the setting keeps its own property."""
    original = object()

    class CyclesLightSettings:
        cast_shadow = original

    bpy = _fake_bpy(lambda: SimpleNamespace(cycles=CyclesLightSettings()))
    _load_function(bpy)()

    assert CyclesLightSettings.__dict__["cast_shadow"] is original


@pytest.mark.unit
def test_does_nothing_without_cycles():
    """Without Cycles a light has no `cycles` settings, and the importer skips the line."""
    bpy = _fake_bpy(lambda: SimpleNamespace())
    bool_property_calls = []
    bpy.props.BoolProperty = lambda **kwargs: bool_property_calls.append(kwargs)
    _load_function(bpy)()
    assert bool_property_calls == []
    assert len(bpy.data.lights.created) == 1


@pytest.mark.unit
@pytest.mark.parametrize("has_cycles", [True, False])
def test_removes_the_probe_light(has_cycles):
    """The light it creates to reach the settings class is removed, so the render scene is unchanged."""

    class CyclesLightSettings:
        pass

    bpy = _fake_bpy(lambda: SimpleNamespace(cycles=CyclesLightSettings()) if has_cycles else SimpleNamespace())
    _load_function(bpy)()
    assert bpy.data.lights.removed == bpy.data.lights.created
    assert len(bpy.data.lights.created) == 1
