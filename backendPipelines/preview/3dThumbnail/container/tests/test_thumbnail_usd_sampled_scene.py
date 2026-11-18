#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A USD stage past the vertex cap previews the whole scene.

The loader used to stop walking at ``MAX_POINTS`` vertices, so the prims it read first were drawn and
the camera framed that part of the scene alone. Past the cap it now counts the stage without
tessellating it and reads an evenly spaced sample of its gprims and point-instancer instances, plus
the ones holding the scene's outermost bounds. A stage under the cap loads exactly as before.

The cap is a module constant, so each test lowers it and uses a small stage.
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline.format_handlers import usd_handler

GRID = 40            # a mesh of GRID x GRID vertices
MESH_VERTICES = GRID * GRID


def _stage(path):
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, "Y")
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    return stage


def _dense_mesh(stage, path):
    from pxr import Gf, UsdGeom

    UsdGeom.Xform.Define(stage, path.rsplit("/", 1)[0])
    mesh = UsdGeom.Mesh.Define(stage, path)
    points = [Gf.Vec3f(i * 0.05, np.sin(i * 0.3) * 0.1, j * 0.05) for i in range(GRID) for j in range(GRID)]
    indices = []
    for i in range(GRID - 1):
        for j in range(GRID - 1):
            a = i * GRID + j
            indices += [a, a + 1, a + GRID + 1, a + GRID]
    mesh.GetPointsAttr().Set(points)
    mesh.GetFaceVertexCountsAttr().Set([4] * ((GRID - 1) ** 2))
    mesh.GetFaceVertexIndicesAttr().Set(indices)
    return mesh


def _native_grid(tmp_path, columns, rows, name="grid.usda"):
    from pxr import Gf, UsdGeom

    stage = _stage(tmp_path / name)
    _dense_mesh(stage, "/Lib/Dense/Mesh")
    for i in range(columns):
        for j in range(rows):
            holder = UsdGeom.Xform.Define(stage, f"/World/T{i}_{j}")
            holder.AddTranslateOp().Set(Gf.Vec3d(i * 5.0, 0, j * 5.0))
            inst = UsdGeom.Xform.Define(stage, f"/World/T{i}_{j}/Obj")
            inst.GetPrim().GetReferences().AddInternalReference("/Lib/Dense")
            inst.GetPrim().SetInstanceable(True)
    stage.GetRootLayer().Save()
    return str(tmp_path / name)


def _instancer(tmp_path, count, name="scatter.usda", with_cube=False):
    from pxr import Gf, UsdGeom

    stage = _stage(tmp_path / name)
    _dense_mesh(stage, "/World/Protos/Dense")
    scatter = UsdGeom.PointInstancer.Define(stage, "/World/Scatter")
    scatter.GetPrototypesRel().AddTarget("/World/Protos/Dense")
    side = int(np.ceil(np.sqrt(count)))
    scatter.GetPositionsAttr().Set([Gf.Vec3f((n % side) * 5.0, 0, (n // side) * 5.0) for n in range(count)])
    scatter.GetProtoIndicesAttr().Set([0] * count)
    if with_cube:
        cube = UsdGeom.Cube.Define(stage, "/World/Marker")
        cube.GetSizeAttr().Set(1.0)
        UsdGeom.Xformable(cube).AddTranslateOp().Set(Gf.Vec3d(-300, 0, 400))
    stage.GetRootLayer().Save()
    return str(tmp_path / name)


def _bounds(data):
    return np.asarray(data.bounds).reshape(3, 2)


def _load(path, cap):
    saved = usd_handler.MAX_POINTS
    usd_handler.MAX_POINTS = cap
    try:
        return usd_handler.load(path)
    finally:
        usd_handler.MAX_POINTS = saved


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestSampledScene:
    def test_native_instances_past_the_cap_cover_the_whole_grid(self, tmp_path):
        path = _native_grid(tmp_path, 10, 10)             # 100 instances, 160,000 vertices
        whole = _load(path, 10 ** 9)
        sampled = _load(path, 40_000)
        assert 0 < sampled.n_points <= 40_000 + MESH_VERTICES
        assert sampled.n_points < whole.n_points / 3
        np.testing.assert_allclose(_bounds(sampled), _bounds(whole), atol=1e-6)

    def test_the_sample_is_deterministic(self, tmp_path):
        path = _native_grid(tmp_path, 10, 10)
        first, second = _load(path, 40_000), _load(path, 40_000)
        np.testing.assert_array_equal(np.asarray(first.points), np.asarray(second.points))

    def test_the_old_loop_would_have_framed_one_corner(self, tmp_path):
        path = _native_grid(tmp_path, 10, 10)
        whole = _bounds(_load(path, 10 ** 9))
        sampled = _bounds(_load(path, 40_000))
        assert (sampled[0, 1] - sampled[0, 0]) >= 0.99 * (whole[0, 1] - whole[0, 0])
        assert (sampled[2, 1] - sampled[2, 0]) >= 0.99 * (whole[2, 1] - whole[2, 0])

    def test_a_point_instancer_past_the_cap_covers_the_whole_scatter(self, tmp_path):
        path = _instancer(tmp_path, 100)
        whole = _load(path, 10 ** 9)
        sampled = _load(path, 40_000)
        assert sampled.n_points < whole.n_points / 2
        np.testing.assert_allclose(_bounds(sampled), _bounds(whole), atol=1e-6)

    def test_a_mixed_stage_keeps_a_distant_lone_prim_and_the_instancer_extent(self, tmp_path):
        path = _instancer(tmp_path, 100, with_cube=True)
        whole = _load(path, 10 ** 9)
        sampled = _load(path, 40_000)
        np.testing.assert_allclose(_bounds(sampled), _bounds(whole), atol=1e-6)

    def test_a_stage_under_the_cap_loads_exactly_as_before(self, tmp_path):
        path = _native_grid(tmp_path, 10, 10)
        total = 100 * MESH_VERTICES
        under = _load(path, total + 1)
        reference = _load(path, 10 ** 9)
        np.testing.assert_array_equal(np.asarray(under.points), np.asarray(reference.points))
        assert under.n_cells == reference.n_cells

    def test_the_log_says_how_much_of_the_scene_is_shown(self, tmp_path, caplog):
        path = _native_grid(tmp_path, 10, 10)
        import logging

        with caplog.at_level(logging.WARNING):
            _load(path, 40_000)
        assert any("the preview shows" in record.getMessage() for record in caplog.records)
