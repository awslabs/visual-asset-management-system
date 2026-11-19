#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The image installs no SciPy, and trimesh has slow fallbacks for some work it does with SciPy: vertex
normals fall back to a scan of every face for every vertex, so a USD or COLLADA output of a mesh of
100,000+ vertices took minutes in the deployed image while the same job took 2 s in a development
environment that happened to have SciPy (in-development defect D2).

- requirements.txt has no SciPy, so this suite's own environment in CI (built from it) has none either.
- The timing cases run in a child interpreter with SciPy made unimportable, so they measure the image's
  code path whatever is installed here.
- ``vertex_normals.angle_weighted_vertex_normals`` matches trimesh's own weighting.
"""

import json
import os
import re
import subprocess
import sys
import textwrap

import numpy as np
import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _LAMBDA_DIR)

trimesh = pytest.importorskip("trimesh")

import vertex_normals  # noqa: E402

# Seconds a conversion of the 163,842-vertex fixture may take with SciPy absent. The slow fallback took
# minutes; the conversions take 1-4 s on a development machine.
TIME_BOUND_SECONDS = 30

_CHILD = textwrap.dedent(r"""
    import importlib.abc, importlib.machinery, json, os, sys, tempfile, time

    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] == "scipy":
                raise ModuleNotFoundError("No module named 'scipy' (simulated, as in the image)")
            return None

    sys.meta_path.insert(0, Blocker())
    sys.path.insert(0, sys.argv[1])
    sys.path.insert(0, os.path.join(sys.argv[1], "tests"))
    import trimesh
    import new_format_helpers as helpers
    import pathlib

    work = pathlib.Path(tempfile.mkdtemp())
    source = work / "src" / "scan.3mf"
    source.parent.mkdir()
    source.write_bytes(trimesh.creation.icosphere(subdivisions=7).export(file_type="3mf"))
    timings = {}
    for output in sys.argv[2].split(","):
        started = time.perf_counter()
        helpers.convert(work, str(source), output, run_dir="r" + output)
        timings[output] = time.perf_counter() - started
    try:
        import scipy  # noqa: F401
        timings["scipy"] = True
    except ModuleNotFoundError:
        timings["scipy"] = False
    print(json.dumps(timings))
""")


def _requirement_names(name):
    with open(os.path.join(_LAMBDA_DIR, name), encoding="utf-8") as handle:
        return {m.group(1).lower() for m in re.finditer(r"(?m)^([A-Za-z0-9][A-Za-z0-9._-]*)==", handle.read())}


@pytest.mark.unit
def test_the_image_installs_no_scipy():
    # The premise of this module: if SciPy is ever added to the image, the slow fallbacks no longer apply.
    assert "scipy" not in _requirement_names("requirements.txt")
    assert "scipy" not in _requirement_names("requirements-step.txt")


@pytest.mark.unit
@pytest.mark.parametrize("outputs", [".usdc,.usda,.usdz", ".dae", ".glb,.obj,.ply,.stl,.off,.3mf"])
def test_a_large_mesh_converts_quickly_without_scipy(outputs):
    pytest.importorskip("pxr")
    pytest.importorskip("collada")
    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            [sys.executable, "-c", _CHILD, _LAMBDA_DIR, outputs], capture_output=True, text=True,
            timeout=TIME_BOUND_SECONDS * len(outputs.split(",")) + 60)
    except subprocess.TimeoutExpired:
        pytest.fail(f"converting a 163,842-vertex mesh to {outputs} without SciPy did not finish")
    assert result.returncode == 0, result.stderr[-2000:]
    timings = json.loads(result.stdout.strip().splitlines()[-1])
    assert timings.pop("scipy") is False, "the child imported SciPy; the simulation did not apply"
    slow = {output: round(seconds, 1) for output, seconds in timings.items() if seconds > TIME_BOUND_SECONDS}
    assert slow == {}, slow


@pytest.mark.unit
class TestTheNormalsMatchTrimesh:
    @pytest.mark.parametrize("mesh", [
        trimesh.creation.icosphere(subdivisions=2),
        trimesh.creation.box(),
        trimesh.creation.cylinder(radius=1, height=3, sections=12),
        trimesh.creation.annulus(r_min=0.5, r_max=1, height=1),
    ], ids=["sphere", "box", "cylinder", "annulus"])
    def test_the_same_weighting_as_trimesh(self, mesh):
        expected = trimesh.geometry.weighted_vertex_normals(
            len(mesh.vertices), mesh.faces, mesh.face_normals, mesh.face_angles, use_loop=True)
        got = vertex_normals.angle_weighted_vertex_normals(mesh.vertices, mesh.faces, mesh.face_normals)
        np.testing.assert_allclose(got, expected, atol=1e-9)

    def test_an_unused_vertex_and_a_degenerate_face_give_zero_or_finite_normals(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5], [2, 0, 0]]
        faces = [[0, 1, 2], [0, 1, 4]]  # the second is degenerate (collinear)
        got = vertex_normals.angle_weighted_vertex_normals(vertices, faces)
        assert np.isfinite(got).all()
        np.testing.assert_allclose(got[3], [0, 0, 0])
        np.testing.assert_allclose(got[2], [0, 0, 1])

    def test_fill_puts_them_in_the_cache_without_recomputing(self):
        mesh = trimesh.creation.icosphere(subdivisions=1)
        assert vertex_normals.fill_vertex_normals(mesh) == 1
        assert "vertex_normals" in mesh._cache
        assert vertex_normals.fill_vertex_normals(mesh) == 0
        assert vertex_normals.fill_vertex_normals(trimesh.PointCloud(np.ones((3, 3)))) == 0
