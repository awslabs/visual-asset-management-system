# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An unwrap that leaves the model at the uv origin is caught, retried, and never painted into.

xatlas reports a chart it could not parameterise by leaving every one of its vertices at (0, 0) and saying
nothing. The first Blender-agent model to be re-baked in the container came back that way -- 99,980 of
100,028 atlas vertices at the origin, 48 seam splits placed -- while the identical geometry charted fully on
a workstation with the same xatlas version. The rasteriser then painted a few hundred huge triangles fanning
out from the corner, the log read normally, the export carried an image, and the model rendered one flat
colour; the blind judge scored its colour 1 of 10. The texture pipeline now measures every layout it gets.
"""

import sys
import types

import numpy as np
import pytest
import trimesh
from PIL import Image

import texturing


def _textured_box():
    mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    uv = (mesh.vertices[:, :2] - mesh.vertices[:, :2].min(axis=0))
    uv = uv / max(uv.max(), 1e-9) * 0.9 + 0.05
    image = Image.new("RGB", (32, 32), (200, 40, 40))
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.SimpleMaterial(image=image))
    return mesh


@pytest.mark.unit
class TestTheCollapseMeasure:
    def test_a_real_layout_measures_near_zero_and_a_collapsed_one_near_one(self):
        good = np.random.default_rng(0).random((1000, 2)) * 0.9 + 0.05
        assert texturing._collapsed_fraction(good) == 0.0
        collapsed = np.zeros((1000, 2))
        collapsed[:10] = 0.5
        assert texturing._collapsed_fraction(collapsed) == pytest.approx(0.99)
        assert texturing._collapsed_fraction(np.zeros((0, 2))) == 0.0


@pytest.mark.unit
class TestTheUnwrapIsRetriedAndTheRebakeRefusesACollapsedLayout:
    @pytest.fixture
    def fake_xatlas(self, monkeypatch):
        """A stand-in xatlas whose default call collapses and whose bounded-chart call succeeds."""
        calls = []

        def parametrize(vertices, faces):
            calls.append("parametrize")
            n = len(vertices)
            return np.arange(n, dtype=np.uint32), np.asarray(faces, dtype=np.uint32), np.zeros((n, 2))

        class Atlas:
            chart_count = 7

            def add_mesh(self, vertices, faces):
                self._n, self._faces = len(vertices), np.asarray(faces, dtype=np.uint32)

            def generate(self, chart_options=None, pack_options=None):
                calls.append("generate")

            def __getitem__(self, index):
                good = np.random.default_rng(1).random((self._n, 2)) * 0.9 + 0.05
                return np.arange(self._n, dtype=np.uint32), self._faces, good

        module = types.SimpleNamespace(parametrize=parametrize, Atlas=Atlas,
                                       ChartOptions=lambda: types.SimpleNamespace(),
                                       PackOptions=lambda: types.SimpleNamespace())
        monkeypatch.setitem(sys.modules, "xatlas", module)
        return calls

    def test_a_small_mesh_is_unwrapped_at_unit_scale(self, monkeypatch):
        """The cause, found by shrinking the delivered geometry a thousandfold on a workstation: at the
        agent's 0.06-unit height xatlas gave 99.95% of the vertices the origin in under a second, at 60
        units it charted every one. A uv layout does not depend on scale, so the unwrap never sees a small
        mesh."""
        seen = []

        def parametrize(vertices, faces):
            seen.append(np.asarray(vertices))
            n = len(vertices)
            return np.arange(n, dtype=np.uint32), np.asarray(faces, dtype=np.uint32), np.full((n, 2), 0.5)

        monkeypatch.setitem(sys.modules, "xatlas", types.SimpleNamespace(parametrize=parametrize))
        mesh = _textured_box()
        tiny = np.asarray(mesh.vertices, dtype=np.float32) * 0.06
        texturing._unwrap(tiny, np.asarray(mesh.faces, dtype=np.uint32))
        assert np.ptp(seen[0], axis=0).max() == pytest.approx(1.0, abs=1e-5)
        assert seen[0].min() >= 0.0
        # A mesh already at a sane size is passed through as it is.
        seen.clear()
        texturing._unwrap(np.asarray(mesh.vertices, dtype=np.float32) * 12.0, np.asarray(mesh.faces, dtype=np.uint32))
        assert np.ptp(seen[0], axis=0).max() == pytest.approx(12.0, abs=1e-4)

    def test_a_collapsed_default_unwrap_is_retried_with_bounded_charts(self, fake_xatlas):
        mesh = _textured_box()
        vmapping, indices, uv = texturing._unwrap(np.asarray(mesh.vertices, dtype=np.float32),
                                                  np.asarray(mesh.faces, dtype=np.uint32))
        assert fake_xatlas == ["parametrize", "generate"]
        assert texturing._collapsed_fraction(uv) == 0.0

    def test_a_layout_that_stays_collapsed_makes_the_rebake_decline_rather_than_paint(self, monkeypatch):
        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        n = len(mesh.vertices)
        monkeypatch.setattr(texturing, "_unwrap", lambda vertices, faces: (
            np.arange(n, dtype=np.uint32), np.asarray(faces, dtype=np.uint32), np.zeros((n, 2))))
        assert texturing.rebake_atlas(mesh, snapshot) is None

    def test_a_good_layout_still_rebakes(self, monkeypatch):
        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        result = texturing.rebake_atlas(mesh, snapshot)
        assert result is not None
        rebaked, status = result
        assert status == texturing.TEXTURE_REBAKED
        assert texturing._collapsed_fraction(rebaked.visual.uv) <= texturing.UNWRAP_COLLAPSE_TOLERANCE
