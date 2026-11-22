#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A USD stage that expands to too many prims is refused before the walk runs on.

A file of nested instanceable prims that hold no geometry composes cheaply but expands to 2^depth
instance proxies; because the proxies hold no vertices, the ``MAX_POINTS`` vertex cap never stops the
walk, so without a node bound it runs until the job's timeout. The loader now counts every prim each
walk visits (instance proxies included) against ``scene_limits.MAX_SCENE_NODES`` and refuses once the
bound is passed, with a message naming prims.

The bound is a module constant, so this test lowers it and uses a small stage that still expands past
it. Needs the real USD bindings, so it runs inside the pipeline image.
"""

import pytest

from conftest import requires_real
from preview_pipeline.format_handlers import usd_handler


def _nested_instanceable_stage(path, depth):
    """A stage whose ``/World/inst`` instances a chain of ``depth`` prototypes, each holding two
    instanceable children that reference the next: traversing the instance proxies expands it to
    about 2**depth prims, none of which holds any geometry."""
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, "Y")
    world = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(world)
    stage.DefinePrim(f"/P{depth}", "Scope")
    for level in range(depth - 1, -1, -1):
        stage.DefinePrim(f"/P{level}", "Scope")
        for child in ("a", "b"):
            prim = stage.DefinePrim(f"/P{level}/{child}")
            prim.GetReferences().AddInternalReference(f"/P{level + 1}")
            prim.SetInstanceable(True)
    top = stage.DefinePrim("/World/inst")
    top.GetReferences().AddInternalReference("/P0")
    top.SetInstanceable(True)
    stage.GetRootLayer().Save()
    return str(path)


def _instancer_with_expanding_prototype(path, depth):
    """A stage whose PointInstancer has one prototype that is a doubling chain of prims nested under
    the instancer at ``/World/pi/C0``: each level holds two instanceable children that reference the
    next level, so expanding the prototype yields about 2**depth prims. The chain is reached only
    through the prototypes relationship -- ``_collect_parts`` prunes the instancer's children and hands
    the prototype to ``_expand_point_instancer``, so that walk is the only one that visits it. Without
    counting that walk the expansion runs unbounded; with it the load is refused."""
    from pxr import Gf, Sdf, Usd, UsdGeom

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, "Y")
    world = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(world)
    # A drawn triangle beside the instancer, so the stage holds real geometry.
    mesh = UsdGeom.Mesh.Define(stage, "/World/tri")
    mesh.GetPointsAttr().Set([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0)])
    mesh.GetFaceVertexCountsAttr().Set([3])
    mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2])
    instancer = UsdGeom.PointInstancer.Define(stage, "/World/pi")
    base = "/World/pi"
    # The doubling chain, nested under the instancer so the main walk prunes it and only the
    # prototype walk reaches it.
    stage.DefinePrim(f"{base}/C{depth}", "Scope")
    for level in range(depth - 1, -1, -1):
        stage.DefinePrim(f"{base}/C{level}", "Scope")
        for child in ("a", "b"):
            prim = stage.DefinePrim(f"{base}/C{level}/{child}")
            prim.GetReferences().AddInternalReference(f"{base}/C{level + 1}")
            prim.SetInstanceable(True)
    instancer.CreatePositionsAttr([Gf.Vec3f(0, 0, 0)])
    instancer.CreateProtoIndicesAttr([0])
    instancer.CreatePrototypesRel().SetTargets([Sdf.Path(f"{base}/C0")])
    stage.GetRootLayer().Save()
    return str(path)


@requires_real("pxr", "pyvista")
def test_a_nested_instancing_stage_is_refused_at_the_node_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(usd_handler, "MAX_SCENE_NODES", 2000)
    path = _nested_instanceable_stage(tmp_path / "bomb.usda", depth=14)
    with pytest.raises(ValueError, match=r"expands to more than 2,000 prims"):
        usd_handler.load(path)


@requires_real("pxr", "pyvista")
def test_a_point_instancer_prototype_that_expands_past_the_bound_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(usd_handler, "MAX_SCENE_NODES", 2000)
    path = _instancer_with_expanding_prototype(tmp_path / "instancer-prototype-expand.usda", depth=14)
    with pytest.raises(ValueError, match=r"expands to more than 2,000 prims"):
        usd_handler.load(path)


@requires_real("pxr", "pyvista")
def test_a_small_instanceable_stage_under_the_bound_is_not_refused_for_node_count(tmp_path, monkeypatch):
    from pxr import Gf, Usd, UsdGeom

    monkeypatch.setattr(usd_handler, "MAX_SCENE_NODES", 2000)
    stage_path = tmp_path / "ok.usda"
    stage = Usd.Stage.CreateNew(str(stage_path))
    UsdGeom.SetStageUpAxis(stage, "Y")
    world = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(world)
    mesh = UsdGeom.Mesh.Define(stage, "/World/tri")
    mesh.GetPointsAttr().Set([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0)])
    mesh.GetFaceVertexCountsAttr().Set([3])
    mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2])
    stage.GetRootLayer().Save()
    # A few prims is far under the bound, so the load is not refused for node count (it draws the one
    # triangle).
    result = usd_handler.load(str(stage_path))
    assert result is not None
