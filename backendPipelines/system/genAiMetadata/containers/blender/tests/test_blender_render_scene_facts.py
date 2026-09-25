# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The measurement and unit facts `renderScene.py` adds to scene_facts.json, executed through `ast` against
stand-ins for `bmesh` and `pxr`: world-space surface area and volume through a bmesh transformed by the
object's matrix_world, closedness as every-edge-manifold (volume only then), the UV / colour-attribute
flags, and the USD stage metersPerUnit read through Blender's bundled pxr module. These are the Blender-path
sources of the promotion source keys `surfaceArea`, `volume`, `watertight`, `hasUv`, `hasVertexColors`,
so the aggregation rules -- not Blender -- are what is pinned here.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from test_blender_render_scene_dispatch import exec_named_nodes


class _FakeBMesh:
    """`bmesh.new()` stand-in: from_mesh copies the mesh's face areas, edge manifold flags and volume;
    transform() takes the object's matrix_world -- modelled as a uniform scale factor -- so a parser that
    forgets to apply it measures local space and fails the scaled-cube assertion."""

    def __init__(self, log):
        self.log = log
        self.faces = []
        self.edges = []
        self._volume = 0.0

    def from_mesh(self, mesh):
        self.log.append(("from_mesh", mesh.name))
        self.faces = [SimpleNamespace(calc_area=lambda area=area: area) for area in mesh.face_areas]
        self.edges = [SimpleNamespace(is_manifold=flag) for flag in mesh.edge_manifold]
        self._volume = mesh.volume

    def transform(self, matrix):
        self.log.append(("transform", matrix))
        scale = float(matrix)
        self.faces = [SimpleNamespace(calc_area=lambda area=face.calc_area() * scale * scale: area)
                      for face in self.faces]
        self._volume *= scale ** 3

    def calc_volume(self, signed=False):
        assert signed is False, "unsigned volume: a flipped normal must not go negative"
        return self._volume

    def free(self):
        self.log.append(("free",))


def _object(name, face_areas, edge_manifold, volume, scale=1.0, uv=(), colors=()):
    mesh = SimpleNamespace(name=name, face_areas=list(face_areas), edge_manifold=list(edge_manifold),
                           volume=volume, uv_layers=list(uv), color_attributes=list(colors))
    return SimpleNamespace(data=mesh, matrix_world=scale)


def _closed_cube(name, scale=1.0, **kwargs):
    return _object(name, face_areas=[1.0] * 6, edge_manifold=[True] * 12, volume=1.0, scale=scale, **kwargs)


def _measurements(log=None):
    log = [] if log is None else log
    namespace = exec_named_nodes(["mesh_measurements"], {"bmesh": SimpleNamespace(new=lambda: _FakeBMesh(log))})
    return namespace["mesh_measurements"], log


@pytest.mark.unit
def test_measurements_sum_world_space_area_and_volume_over_all_objects():
    measure, log = _measurements()
    result = measure([_closed_cube("unit"), _closed_cube("double", scale=2.0)])
    # 6 + 6 * 2^2 for area; 1 + 2^3 for volume: the second cube is measured after its world transform.
    assert result == {"surfaceArea": 30.0, "volume": 9.0, "watertight": True, "hasUv": False, "hasVertexColors": False}
    assert [entry for entry in log if entry[0] == "transform"] == [("transform", 1.0), ("transform", 2.0)]
    assert log.count(("free",)) == 2, "every bmesh is freed"


@pytest.mark.unit
def test_open_mesh_has_no_volume():
    measure, _log = _measurements()
    open_box = _object("open", face_areas=[1.0] * 5, edge_manifold=[True] * 8 + [False] * 4, volume=0.75)
    result = measure([_closed_cube("unit"), open_box])
    assert result["watertight"] is False and "volume" not in result
    assert result["surfaceArea"] == 11.0, "area is still summed for an open set"


@pytest.mark.unit
def test_flags_uv_and_vertex_colors_when_any_mesh_carries_them():
    measure, _log = _measurements()
    result = measure([_closed_cube("plain"), _closed_cube("textured", uv=["UVMap"], colors=["Col"])])
    assert result["hasUv"] is True and result["hasVertexColors"] is True


@pytest.mark.unit
def test_no_objects_measure_nothing_and_are_not_watertight():
    measure, log = _measurements()
    assert measure([]) == {"surfaceArea": 0.0, "watertight": False, "hasUv": False, "hasVertexColors": False}
    assert log == []


def _stage_namespace(monkeypatch, pxr_module):
    if pxr_module is None:
        monkeypatch.setitem(sys.modules, "pxr", None)  # `from pxr import ...` raises ImportError
    else:
        monkeypatch.setitem(sys.modules, "pxr", pxr_module)
    return exec_named_nodes(["usd_stage_meters_per_unit"], {})["usd_stage_meters_per_unit"]


def _fake_pxr(open_result, meters_per_unit=0.01, authored=True, opens=None):
    module = types.ModuleType("pxr")
    opens = [] if opens is None else opens

    def _open(path, load):
        opens.append((path, load))
        if isinstance(open_result, Exception):
            raise open_result
        return open_result

    module.Usd = SimpleNamespace(Stage=SimpleNamespace(Open=_open, LoadNone="LoadNone"))
    module.UsdGeom = SimpleNamespace(
        GetStageMetersPerUnit=lambda stage: meters_per_unit,
        StageHasAuthoredMetersPerUnit=lambda stage: authored,
    )
    return module


@pytest.mark.unit
def test_usd_stage_meters_per_unit_reads_the_composed_stage(monkeypatch):
    opens = []
    read = _stage_namespace(
        monkeypatch, _fake_pxr(open_result=object(), meters_per_unit=0.0254, authored=True, opens=opens))
    assert read("/work/input/model.usdc") == (0.0254, True)
    assert opens == [("/work/input/model.usdc", "LoadNone")], "payloads stay unloaded"


@pytest.mark.unit
def test_usd_stage_unauthored_value_is_still_reported(monkeypatch):
    read = _stage_namespace(monkeypatch, _fake_pxr(open_result=object(), meters_per_unit=0.01, authored=False))
    assert read("/work/input/model.usda") == (0.01, False)


@pytest.mark.unit
def test_usd_stage_meters_per_unit_without_pxr_is_none(monkeypatch):
    read = _stage_namespace(monkeypatch, None)
    assert read("/work/input/model.usdc") == (None, None)


@pytest.mark.unit
@pytest.mark.parametrize("open_result", [None, RuntimeError("Tf error: unresolvable asset")])
def test_usd_stage_that_does_not_open_is_none(monkeypatch, open_result):
    read = _stage_namespace(monkeypatch, _fake_pxr(open_result=open_result))
    assert read("/work/input/broken.usdz") == (None, None)
