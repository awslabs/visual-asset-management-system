# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The gate tool's colour reading, on the files the agent actually hands it. The rest of the tool is the
pipeline's own gate and silhouette code, tested with the pipeline; the live Blender test runs it end to end."""

import os
import sys

import numpy as np
import pytest

pytest.importorskip("trimesh")
import trimesh  # noqa: E402

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blenderagent_tools")
sys.path.insert(0, _TOOLS_DIR)
import agent_gate  # noqa: E402

pytestmark = pytest.mark.unit


def _coloured_sphere(colours):
    mesh = trimesh.creation.icosphere(subdivisions=3)  # 642 vertices
    assert len(colours) == len(mesh.vertices)
    mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colours)
    return mesh


def test_the_gate_reads_the_colour_of_a_mesh_that_carries_a_textureless_material(tmp_path):
    """Blender's export of an imported reconstruction carries the vertex colour beside a default material
    with no image. Read as a texture visual with no uv, that colour was invisible to the gate, which told
    every refine run its starting mesh had none."""
    mesh = trimesh.creation.icosphere(subdivisions=2)
    brown = np.tile([120, 70, 30, 255], (len(mesh.vertices), 1)).astype(np.uint8)
    mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial())
    mesh.visual.vertex_attributes["color"] = brown
    path = tmp_path / "start.glb"
    path.write_bytes(mesh.export(file_type="glb"))

    loaded = agent_gate.load_mesh(str(path))
    colours = agent_gate.model_colours(loaded)
    assert colours is not None, "the vertex colour under a textureless material is the model's colour"
    assert colours.shape == (len(mesh.vertices), 3)
    assert np.allclose(colours.mean(axis=0) * 255, [120, 70, 30], atol=1.0)


def test_colour_noise_is_flat_for_one_colour_and_speckled_for_scattered_colours():
    """The blind judge marked every refine whose neighbouring vertices differed by ten or more down as
    speckled, and every one under seven up; a reconstruction's own colour reads four to five. The gate
    prints that number so the agent sees a texture step making the surface worse."""
    flat = _coloured_sphere(np.tile([120, 70, 30, 255], (642, 1)).astype(np.uint8))
    noise = agent_gate.colour_noise(flat, agent_gate.model_colours(flat))
    assert noise["meanStep"] == 0.0 and noise["speckledShare"] == 0.0 and noise["nearBlackShare"] == 0.0
    assert noise["word"] == "smooth"

    rng = np.random.default_rng(1)
    scattered = rng.integers(0, 256, size=(642, 3))
    speckled = _coloured_sphere(np.column_stack([scattered, np.full(642, 255)]).astype(np.uint8))
    noise = agent_gate.colour_noise(speckled, agent_gate.model_colours(speckled))
    assert noise["meanStep"] > 40 and noise["speckledShare"] > 0.5
    assert noise["word"] == "speckled"

    dark = np.tile([120, 70, 30, 255], (642, 1)).astype(np.uint8)
    dark[:64, :3] = 0
    holed = _coloured_sphere(dark)
    noise = agent_gate.colour_noise(holed, agent_gate.model_colours(holed))
    assert noise["nearBlackShare"] == pytest.approx(64 / 642, abs=1e-6)


def test_colour_noise_reports_the_share_of_isolated_speckles_the_mean_step_misses():
    """A refined horse stepped 6.1 on average -- smooth by the mean -- and a blind judge still called it
    pitted: 0.9% of its vertices were isolated dots against 0.1% of the reconstruction's own. The share of
    vertices that differ from their neighbours' mean by more than 40 is the number that sees them."""
    colours = np.tile([120, 70, 30, 255], (642, 1)).astype(np.uint8)
    dotted = np.arange(0, 642, 50)
    colours[dotted, :3] = 0
    mesh = _coloured_sphere(colours)
    noise = agent_gate.colour_noise(mesh, agent_gate.model_colours(mesh))
    assert noise["word"] == "smooth", noise
    assert noise["speckleVertexShare"] == pytest.approx(len(dotted) / 642, abs=1e-6)

    flat = _coloured_sphere(np.tile([120, 70, 30, 255], (642, 1)).astype(np.uint8))
    assert agent_gate.colour_noise(flat, agent_gate.model_colours(flat))["speckleVertexShare"] == 0.0


def test_the_gate_reads_an_atlas_the_way_the_delivery_does(tmp_path):
    """The delivery reads a chart-packed atlas a texel and a half inside each face, so a vertex on an
    island's border takes the island's colour. The gate must read it the same way, or it reports speckle
    that never ships and the agent spends its budget chasing it -- one did."""
    from PIL import Image

    size = 64
    uv = np.array([[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]])
    pixels = np.tile(np.array([120, 70, 30], dtype=np.uint8), (size, size, 1))
    for corner in uv:
        x = int(np.clip((corner[0] % 1.0) * (size - 1), 0, size - 1))
        y = int(np.clip((1.0 - corner[1] % 1.0) * (size - 1), 0, size - 1))
        pixels[y, x] = 0
    mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]], process=False)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(pixels))

    colours = agent_gate.model_colours(mesh)

    assert colours is not None
    assert np.allclose(colours * 255, [120, 70, 30], atol=1.0), colours * 255


def test_colour_noise_measures_the_welded_surface_not_the_split_islands():
    """An atlas export splits every vertex per island, so on the file the agent measures each vertex's only
    neighbours are its own triangle's corners, which sampled the same island, and speckle reads near zero
    whatever ships. One agent read 0.08% where the delivered, welded mesh carried 0.38%. The gate welds the
    positions first, so a vertex meets its neighbours across islands as the viewer will."""
    box = trimesh.creation.box()
    vertices = box.vertices[box.faces].reshape(-1, 3)
    faces = np.arange(len(vertices)).reshape(-1, 3)
    split = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    rng = np.random.default_rng(3)
    per_face = rng.integers(0, 256, size=(len(faces), 3))
    colours = np.column_stack([np.repeat(per_face, 3, axis=0), np.full(len(vertices), 255)]).astype(np.uint8)
    split.visual = trimesh.visual.ColorVisuals(mesh=split, vertex_colors=colours)

    noise = agent_gate.colour_noise(split, agent_gate.model_colours(split))

    assert noise["meanStep"] > 20, f"islands of unrelated colour must register as steps, got {noise['meanStep']}"


def test_colour_noise_is_not_reported_for_a_mesh_without_colour():
    grey = trimesh.creation.icosphere(subdivisions=1)
    assert agent_gate.colour_noise(grey, None) is None
