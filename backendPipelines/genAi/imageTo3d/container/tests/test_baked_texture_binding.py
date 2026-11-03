# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A model's baked atlas has to survive the gap between how it writes it and how the mesh loads.

TripoSR exports its baked run through xatlas, which writes uv coordinates but no material file naming
the image beside them, and which writes OBJ bytes whatever extension the filename carries. Both
details are silent: the mesh loads with a uv field addressing nothing and exports untextured, and a
mesh named .glb holding OBJ bytes fails only when something tries to read it.
"""

import os
import sys

import numpy as np
import pytest
import trimesh
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import texturing  # noqa: E402
from models.triposr import TripoSRAdapter  # noqa: E402


def _uv_mesh(with_image=False):
    """A unit box carrying a uv field, optionally with a material image already bound."""
    mesh = trimesh.creation.box()
    uv = np.tile([[0.0, 0.0]], (len(mesh.vertices), 1))
    material = None
    if with_image:
        material = trimesh.visual.material.SimpleMaterial(
            image=Image.new("RGB", (4, 4), (7, 8, 9)))
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    return mesh


def _png(tmp_path, name="texture.png", colour=(255, 0, 0)):
    path = tmp_path / name
    Image.new("RGB", (8, 8), colour).save(path)
    return str(path)


class TestAttachGeneratedTexture:
    def test_a_loose_image_is_bound_to_the_existing_uv_field(self, tmp_path):
        mesh = _uv_mesh()
        assert texturing.real_material_image(mesh.visual.material) is None
        bound = texturing.attach_generated_texture(mesh, [_png(tmp_path)])
        image = texturing.material_image(bound.visual.material)
        assert image is not None, "the baked atlas was not bound"
        assert bound.visual.uv is not None, "binding the image must not drop the uv field"

    def test_the_bound_texture_survives_a_glb_round_trip(self, tmp_path):
        """The point of binding: the export has to carry the atlas into the glTF container."""
        bound = texturing.attach_generated_texture(_uv_mesh(), [_png(tmp_path)])
        reloaded = trimesh.load(
            trimesh.util.wrap_as_stream(bound.export(file_type="glb")), file_type="glb",
            force="mesh")
        assert texturing.material_image(reloaded.visual.material) is not None, (
            "the texture did not survive export to GLB")

    def test_an_already_textured_mesh_is_left_alone(self, tmp_path):
        """A model whose own container carries its texture must not be overwritten."""
        mesh = _uv_mesh(with_image=True)
        before = texturing.material_image(mesh.visual.material).getpixel((0, 0))
        texturing.attach_generated_texture(mesh, [_png(tmp_path, colour=(255, 0, 0))])
        assert texturing.material_image(mesh.visual.material).getpixel((0, 0)) == before

    def test_a_mesh_without_uvs_is_left_alone(self, tmp_path):
        """With no uv field an image has no coordinates to address it by."""
        mesh = trimesh.creation.box()
        result = texturing.attach_generated_texture(mesh, [_png(tmp_path)])
        assert texturing.real_material_image(getattr(result.visual, "material", None)) is None

    def test_sidecars_that_are_not_bindable_images_are_ignored(self, tmp_path):
        mesh = _uv_mesh()
        for name in ("material.mtl", "compressed.ktx2"):
            (tmp_path / name).write_bytes(b"x")
        texturing.attach_generated_texture(
            mesh, [str(tmp_path / "material.mtl"), str(tmp_path / "compressed.ktx2")])
        assert texturing.real_material_image(mesh.visual.material) is None

    def test_no_sidecars_at_all_leaves_the_mesh_as_geometry(self):
        mesh = _uv_mesh()
        texturing.attach_generated_texture(mesh, [])
        assert texturing.real_material_image(mesh.visual.material) is None

    def test_an_unreadable_image_raises_rather_than_shipping_a_blank_atlas(self, tmp_path):
        """A uv field addressing an unreadable file is a real fault, not a texture-less mesh."""
        broken = tmp_path / "texture.png"
        broken.write_bytes(b"not a png")
        with pytest.raises(texturing.TextureTransferError, match="could not be read"):
            texturing.attach_generated_texture(_uv_mesh(), [str(broken)])


class TestTriposrExportFormat:
    def test_the_baking_path_never_asks_upstream_for_a_glb(self, monkeypatch, tmp_path):
        """xatlas writes OBJ bytes whatever the extension, so requesting glb yields an unreadable
        file named .glb. The failure surfaces only when something opens it, after the GPU work."""
        captured = {}

        def fake_run_upstream(cmd, cwd, hf_home, extra_env=None, timeout=None):
            captured["cmd"] = [str(c) for c in cmd]
            out = tmp_path / "generate" / "triposr" / "0"
            out.mkdir(parents=True, exist_ok=True)
            trimesh.creation.box().export(str(out / "mesh.obj"))

        monkeypatch.setattr("models.triposr.run_upstream", fake_run_upstream)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
        monkeypatch.setattr(TripoSRAdapter, "ensure_ready", lambda self, hf_home: None)

        TripoSRAdapter().generate(
            image_path=str(tmp_path / "in.png"), work_dir=str(tmp_path),
            params={"TRIPOSR_BAKE_TEXTURE": "true"})

        cmd = captured["cmd"]
        assert "--bake-texture" in cmd, "the bake was not requested"
        fmt = cmd[cmd.index("--model-save-format") + 1]
        assert fmt == "obj", f"the baking path asked upstream for '{fmt}'"

    def test_the_dead_foreground_ratio_flag_is_not_passed(self, monkeypatch, tmp_path):
        """Upstream reads --foreground-ratio only in the branch --no-remove-bg skips, so passing it
        configures nothing and misleads anyone reading the command in the job log."""
        captured = {}

        def fake_run_upstream(cmd, cwd, hf_home, extra_env=None, timeout=None):
            captured["cmd"] = [str(c) for c in cmd]
            out = tmp_path / "generate" / "triposr" / "0"
            out.mkdir(parents=True, exist_ok=True)
            trimesh.creation.box().export(str(out / "mesh.obj"))

        monkeypatch.setattr("models.triposr.run_upstream", fake_run_upstream)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
        monkeypatch.setattr(TripoSRAdapter, "ensure_ready", lambda self, hf_home: None)

        TripoSRAdapter().generate(image_path=str(tmp_path / "in.png"), work_dir=str(tmp_path),
                                  params={})
        assert "--foreground-ratio" not in captured["cmd"]
        assert "--no-remove-bg" in captured["cmd"], (
            "the pipeline supplies the mask, so upstream's own removal stays off")
