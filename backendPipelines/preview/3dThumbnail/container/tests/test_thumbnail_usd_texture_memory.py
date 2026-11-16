#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""USD textures are decoded at a bounded size and held within a share of the task's memory (#1584).

A thumbnail samples one texel per vertex. The USD handler decoded each distinct texture at full size
and held it until the whole stage had loaded, about 48 MiB per 4096x4096 texture, none of it charged
to the task's memory, so a scene with a few hundred large textures could exhaust its task while an
uncoloured preview of the same geometry fitted. Each texture is now decoded at most
``MAX_TEXTURE_SIDE`` pixels on its long side, once per stage, and the decoded textures a stage holds
are capped at ``texture_cache_limit`` of the task's memory, past which a texture is not read and its
prims take their material's constant colour.

The stage tests need the real pxr/pyvista/Pillow and run inside the pipeline image.
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import resources
from preview_pipeline.format_handlers import usd_handler

_TRI = """
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        texCoord2f[] primvars:st = [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)] (interpolation = "vertex")"""
_RED = (200, 10, 10)
_GREEN = (10, 200, 10)


def _stage(directory, body):
    path = directory / "scene.usda"
    path.write_text(f'#usda 1.0\n(\n    upAxis = "Y"\n)\n{body}\n')
    return str(path)


def _image(path, rgb, size=(4, 4), fmt=None):
    from PIL import Image

    Image.fromarray(np.full((size[1], size[0], 3), rgb, np.uint8)).save(path, fmt)
    return path


def _mesh(name, material, extra=""):
    return f"""
def Mesh "{name}" (prepend apiSchemas = ["MaterialBindingAPI"]) {{{_TRI}
    rel material:binding = </{material}>{extra}
}}"""


def _omni_pbr(name, texture, constant="(0, 0, 1)"):
    return f"""
def Material "{name}" {{
    token outputs:mdl:surface.connect = </{name}/Shader.outputs:out>
    def Shader "Shader" {{
        uniform token info:implementationSource = "sourceAsset"
        uniform asset info:mdl:sourceAsset = @OmniPBR.mdl@
        asset inputs:diffuse_texture = @{texture}@
        color3f inputs:diffuse_color_constant = {constant}
        token outputs:out
    }}
}}"""


def _preview_surface(name, texture):
    return f"""
def Material "{name}" {{
    token outputs:surface.connect = </{name}/Surface.outputs:surface>
    def Shader "Surface" {{
        uniform token info:id = "UsdPreviewSurface"
        color3f inputs:diffuseColor.connect = </{name}/Tex.outputs:rgb>
        token outputs:surface
    }}
    def Shader "Tex" {{
        uniform token info:id = "UsdUVTexture"
        asset inputs:file = @{texture}@
        float3 outputs:rgb
    }}
}}"""


def _rgb(data):
    return np.asarray(data.point_data["RGB"]).tolist() if "RGB" in data.point_data else None


def _record_textures(monkeypatch):
    """The arrays ``_load_texture`` returns during the test, one per prim that samples a texture."""
    returned = []
    load = usd_handler._load_texture

    def recording(asset_path, context):
        texels = load(asset_path, context)
        returned.append(texels)
        return texels

    monkeypatch.setattr(usd_handler, "_load_texture", recording)
    return returned


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestDecodeSize:
    def test_a_large_texture_is_held_at_the_bounded_long_side(self, tmp_path, monkeypatch):
        side = usd_handler.MAX_TEXTURE_SIDE
        _image(tmp_path / "wide.png", _RED, size=(2 * side, side // 2))
        held = _record_textures(monkeypatch)
        rgb = _rgb(usd_handler.load(_stage(tmp_path, _mesh("M", "Mat") + _omni_pbr("Mat", "wide.png"))))
        assert rgb == [list(_RED)] * 3
        assert [texels.shape for texels in held] == [(side // 4, side, 3)]

    def test_a_large_jpeg_is_decoded_at_a_reduced_scale(self, tmp_path, monkeypatch):
        from PIL import Image

        side = usd_handler.MAX_TEXTURE_SIDE
        path = _image(tmp_path / "big.jpg", _RED, size=(4 * side, 4 * side), fmt="JPEG")
        sizes_before_reduction = []
        thumbnail = Image.Image.thumbnail

        def recording(image, *args, **kwargs):
            sizes_before_reduction.append(image.size)
            return thumbnail(image, *args, **kwargs)

        monkeypatch.setattr(Image.Image, "thumbnail", recording)
        texels = usd_handler._decode_texture(str(path))
        assert texels.shape == (side, side, 3)
        assert sizes_before_reduction == [(side, side)]

    def test_a_small_texture_is_kept_at_its_size(self, tmp_path):
        texels = usd_handler._decode_texture(str(_image(tmp_path / "small.png", _RED, size=(6, 3))))
        assert texels.shape == (3, 6, 3) and texels.dtype == np.uint8
        assert texels.reshape(-1, 3).tolist() == [list(_RED)] * 18


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestTextureBudget:
    def _one_fits(self, tmp_path, monkeypatch):
        """Two textures in a task whose texture budget holds one of them, but not both."""
        monkeypatch.setenv(resources.TASK_MEMORY_ENV, "1")
        budget = usd_handler.texture_cache_limit(resources.memory_limit_bytes())
        side = int((budget / 3) ** 0.5)
        assert 3 * side * side <= budget < 6 * side * side
        _image(tmp_path / "red.png", _RED, size=(side, side))
        _image(tmp_path / "green.png", _GREEN, size=(side, side))

    def test_a_texture_past_the_budget_takes_the_constant_colour(self, tmp_path, monkeypatch, caplog):
        self._one_fits(tmp_path, monkeypatch)
        path = _stage(tmp_path, _mesh("A", "One") + _mesh("B", "Two")
                      + _omni_pbr("One", "red.png") + _omni_pbr("Two", "green.png"))
        with caplog.at_level("WARNING"):
            rgb = _rgb(usd_handler.load(path))
        assert rgb == [list(_RED)] * 3 + [[0, 0, 255]] * 3
        assert "Texture green.png not read: the stage's decoded textures would pass the" in caplog.text
        assert "MiB of decoded textures the stage may hold: 1" in caplog.text

    def test_a_usd_preview_surface_texture_past_the_budget_takes_display_color(self, tmp_path, monkeypatch):
        self._one_fits(tmp_path, monkeypatch)
        path = _stage(tmp_path, _mesh("A", "One") + _mesh(
            "B", "Two", extra="\n    color3f[] primvars:displayColor = [(1, 0, 1)]")
            + _preview_surface("One", "red.png") + _preview_surface("Two", "green.png"))
        assert _rgb(usd_handler.load(path)) == [list(_RED)] * 3 + [[255, 0, 255]] * 3

    def test_the_standard_tier_holds_hundreds_of_textures_at_the_bounded_size(self):
        bounded = usd_handler.MAX_TEXTURE_SIDE ** 2 * 3
        assert usd_handler.texture_cache_limit(16 * 1024 ** 3) // bounded >= 500
        assert usd_handler.texture_cache_limit(4 * 1024 ** 3) // bounded >= 100


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestDecodeOnce:
    def test_a_texture_shared_by_several_materials_is_decoded_once(self, tmp_path, monkeypatch):
        from PIL import Image

        _image(tmp_path / "shared.png", _RED)
        opened = []
        open_image = Image.open

        def recording(source, *args, **kwargs):
            opened.append(source)
            return open_image(source, *args, **kwargs)

        monkeypatch.setattr(Image, "open", recording)
        held = _record_textures(monkeypatch)
        path = _stage(tmp_path, _mesh("A", "One") + _mesh("B", "Two") + _mesh("C", "Three")
                      + _omni_pbr("One", "shared.png") + _omni_pbr("Two", "./shared.png")
                      + _preview_surface("Three", "shared.png"))
        assert _rgb(usd_handler.load(path)) == [list(_RED)] * 9
        assert len(opened) == 1, opened
        assert len(held) == 3 and all(texels is held[0] for texels in held)
        assert isinstance(held[0], np.ndarray) and held[0].shape == (4, 4, 3)
