#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""An FBX file is sized before Assimp reads it (backendPipelines/CLAUDE.md "Sizing a Container's
Work"). Assimp builds a node for every placement and copies instanced geometry before it returns, so
a file of a few kilobytes whose levels each sit under both models of the level above describes
2^depth placements. ``fbx_scene.py`` is vendored from the 3D thumbnail container (drift test in
test_3dbasic_vendored_guards.py); ``model_formats.check_fbx_scene`` applies it with the SceneLimit
bounds and runs in the worker before the Assimp tool is started."""

import os
import sys
from unittest.mock import MagicMock

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import fbx_fixture  # noqa: E402
import fbx_scene  # noqa: E402
import model_formats  # noqa: E402

_CUBE_VERTICES = ",".join(str(v) for p in [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                                           (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)] for v in p)
_CUBE_INDICES = "0,3,2,-2,4,5,6,-8,0,1,5,-5,1,2,6,-6,2,3,7,-7,3,0,4,-8"


def doubling_fbx(depth):
    """Level k holds two models, each a child of both models of level k - 1; the last level's two models
    carry one cube: 2^(depth + 1) cube placements from a few kilobytes."""
    models, connections = [], []
    for k in range(depth + 1):
        a, b = 1000 + 2 * k, 1001 + 2 * k
        models += [a, b]
        parents = [0] if k == 0 else [998 + 2 * k, 999 + 2 * k]
        connections += [(child, parent) for child in (a, b) for parent in parents]
    connections += [(10, 1000 + 2 * depth), (10, 1001 + 2 * depth)]
    out = ["; FBX 7.4.0 project file\nFBXHeaderExtension:  {\n\tFBXVersion: 7400\n}\nObjects:  {\n",
           f'\tGeometry: 10, "Geometry::g", "Mesh" {{\n\t\tVertices: *24 {{\n\t\t\ta: {_CUBE_VERTICES}\n\t\t}}\n'
           f'\t\tPolygonVertexIndex: *24 {{\n\t\t\ta: {_CUBE_INDICES}\n\t\t}}\n\t}}\n']
    out += [f'\tModel: {m}, "Model::m{m}", "Mesh" {{\n\t\tVersion: 232\n\t}}\n' for m in models]
    out.append("}\nConnections:  {\n")
    out += [f'\tC: "OO",{child},{parent}\n' for child, parent in connections]
    out.append("}\n")
    return "".join(out).encode()


def nested_fbx(depth):
    """An ASCII FBX whose scopes nest ``depth`` levels deep, and nothing else."""
    return ("; FBX 7.4.0 project file\n" + "".join(f"N{i}: {{\n" for i in range(depth)) + "}\n" * depth).encode()


def _job(tmp_path, name, data):
    directory = tmp_path / "input"
    directory.mkdir(exist_ok=True)
    (directory / name).write_bytes(data)
    return {"input_file": str(directory / name), "input_dir": str(directory), "input_relative_path": name,
            "by_reference": {}, "input_name": name}


@pytest.mark.unit
class TestTheCount:
    def test_a_doubling_scene_counts_every_placement(self, tmp_path):
        path = tmp_path / "d.fbx"
        path.write_bytes(doubling_fbx(4))
        size = fbx_scene.scene_size(str(path), 10_000)
        assert size.triangles == 2 ** 5 * 12 and size.stored_triangles == 12

    def test_the_box_fixture_is_one_model(self, tmp_path):
        path = tmp_path / "b.fbx"
        path.write_bytes(fbx_fixture.box_fbx())
        size = fbx_scene.scene_size(str(path), 10)
        assert (size.models, size.triangles) == (1, 12)


@pytest.mark.unit
class TestTheWorkerRefusesBeforeAssimp:
    @pytest.fixture()
    def assimp(self, monkeypatch, tmp_path):
        """A stand-in for the Assimp tool that records whether it was started."""
        tool = tmp_path / "assimp"
        tool.write_text("#!/bin/sh\nexit 1\n")
        tool.chmod(0o755)
        run = MagicMock(side_effect=AssertionError("Assimp was started"))
        monkeypatch.setattr(model_formats, "ASSIMP_BIN", str(tool))
        monkeypatch.setattr(model_formats.subprocess, "run", run)
        return run

    def test_an_instancing_bomb_is_refused(self, tmp_path, assimp):
        job = _job(tmp_path, "bomb.fbx", doubling_fbx(40))
        with pytest.raises(ValueError, match=r"bomb\.fbx: the FBX scene places more than 1,000,000 models"):
            model_formats.load_model(job, MagicMock())
        assimp.assert_not_called()

    def test_instanced_triangles_past_the_budget_are_refused(self, tmp_path, assimp):
        # 2^13 placements of a 12-triangle cube against a 1,000-triangle budget.
        job = _job(tmp_path, "many.fbx", doubling_fbx(12))
        with pytest.raises(ValueError, match="places more than 1,000 triangles through instancing"):
            model_formats.check_fbx_scene(job["input_file"], "many.fbx", max_triangles=1000)

    @pytest.mark.parametrize("depth", [300, 50_000])
    def test_deep_nesting_is_refused(self, tmp_path, assimp, depth):
        job = _job(tmp_path, "deep.fbx", nested_fbx(depth))
        with pytest.raises(ValueError, match=r"deep\.fbx could not be read as FBX: .*256"):
            model_formats.load_model(job, MagicMock())
        assimp.assert_not_called()

    def test_an_ordinary_scene_is_handed_to_assimp(self, tmp_path, assimp):
        job = _job(tmp_path, "box.fbx", fbx_fixture.box_fbx())
        assimp.side_effect = None
        assimp.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"Error: stand-in")
        with pytest.raises(ValueError, match="could not be read as FBX"):
            model_formats.load_model(job, MagicMock())
        assimp.assert_called_once()
