#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""USD prims bound to an NVIDIA MDL material take its base colour (#1547).

NVIDIA Omniverse and Isaac Sim assets bind ``OmniPBR.mdl`` materials, which carry no
``UsdPreviewSurface``. The handler read colour only from a ``UsdPreviewSurface``, so those prims were
drawn in the default colour although their diffuse textures were in the asset. Each case here is a
small synthesised stage; no NVIDIA file is used.

The stage tests need the real pxr/pyvista/Pillow and run inside the pipeline image.
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline.format_handlers import usd_handler

_TRI = """
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        texCoord2f[] primvars:st = [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)] (interpolation = "vertex")"""
_RED = (200, 10, 10)


def _stage(directory, body, name="scene.usda"):
    path = directory / name
    path.write_text(f'#usda 1.0\n(\n    upAxis = "Y"\n)\n{body}\n')
    return str(path)


def _texture(path, rgb=_RED):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((4, 4, 3), rgb, np.uint8)).save(path)


def _mesh(name="M", material="/Mat", extra=""):
    return f"""
def Mesh "{name}" (prepend apiSchemas = ["MaterialBindingAPI"]) {{{_TRI}
    rel material:binding = <{material}>{extra}
}}"""


def _mdl_material(inputs, module="OmniPBR.mdl", name="Mat", interface="", preview=False):
    """An MDL material whose ``outputs:mdl:surface`` connects to a shader of ``module``."""
    preview_shader = """
    token outputs:surface.connect = </Mat/Preview.outputs:surface>
    def Shader "Preview" {
        uniform token info:id = "UsdPreviewSurface"
        color3f inputs:diffuseColor = (0, 1, 0)
        token outputs:surface
    }""" if preview else ""
    sub = module.rsplit(".", 1)[0]
    return f"""
def Material "{name}" {{{interface}
    token outputs:mdl:surface.connect = </{name}/Shader.outputs:out>{preview_shader}
    def Shader "Shader" {{
        uniform token info:implementationSource = "sourceAsset"
        uniform asset info:mdl:sourceAsset = @{module}@
        uniform token info:mdl:sourceAsset:subIdentifier = "{sub}"
{inputs}
        token outputs:out
    }}
}}"""


def _rgb(data):
    return np.asarray(data.point_data["RGB"]).tolist() if "RGB" in data.point_data else None


def _assert_close(rgb, expected):
    """8-bit colours equal to within one step (float rounding of a product)."""
    assert rgb is not None and np.abs(np.asarray(rgb, int) - np.asarray(expected, int)).max() <= 1, rgb


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestOmniPbr:
    def test_the_diffuse_texture_colours_the_mesh(self, tmp_path):
        _texture(tmp_path / "Textures" / "wood.png")
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:diffuse_texture = @./Textures/wood.png@"))
        assert _rgb(usd_handler.load(path)) == [list(_RED)] * 3

    def test_a_texture_on_the_material_interface_is_followed(self, tmp_path):
        # The Isaac Sim pallet's layout: the shader input connects to the material's own input.
        _texture(tmp_path / "Materials" / "Textures" / "pallet_D.tga")
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:diffuse_texture.connect = </Mat.inputs:diffuse_texture>",
            interface="\n    asset inputs:diffuse_texture = @Materials/Textures/pallet_D.tga@"))
        assert _rgb(usd_handler.load(path)) == [list(_RED)] * 3

    def test_a_texture_resolves_against_the_layer_that_authored_it(self, tmp_path):
        # Nova Carter's layout: materials live in Materials/Materials.usd, textures beside it.
        _texture(tmp_path / "Materials" / "Textures" / "tire.png")
        _stage(tmp_path / "Materials", _mdl_material(
            "        asset inputs:diffuse_texture = @./Textures/tire.png@", name="Lib"),
            name="Materials.usda")
        path = _stage(tmp_path, _mesh(material="/Looks/Tire") + """
def Scope "Looks"
{
    def "Tire" (
        references = @./Materials/Materials.usda@</Lib>
    )
    {
    }
}""")
        assert _rgb(usd_handler.load(path)) == [list(_RED)] * 3

    def test_the_tint_multiplies_the_texture(self, tmp_path):
        _texture(tmp_path / "tex.png", rgb=(200, 200, 200))
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:diffuse_texture = @tex.png@\n"
            "        color3f inputs:diffuse_tint = (1, 0.5, 0)"))
        _assert_close(_rgb(usd_handler.load(path)), [[200, 100, 0]] * 3)

    def test_the_constant_colours_the_mesh_when_there_is_no_texture(self, tmp_path):
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        color3f inputs:diffuse_color_constant = (0, 0, 1)"))
        assert _rgb(usd_handler.load(path)) == [[0, 0, 255]] * 3

    def test_the_tint_multiplies_the_constant(self, tmp_path):
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        color3f inputs:diffuse_color_constant = (1, 1, 1)\n"
            "        color3f inputs:diffuse_tint = (0.4, 0.6, 0.8)"))
        _assert_close(_rgb(usd_handler.load(path)), [[102, 153, 204]] * 3)

    def test_an_unreadable_texture_falls_back_to_the_constant(self, tmp_path):
        # OmniPBR draws the constant when its texture is not valid.
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:diffuse_texture = @missing.png@\n"
            "        color3f inputs:diffuse_color_constant = (0, 1, 1)"))
        assert _rgb(usd_handler.load(path)) == [[0, 255, 255]] * 3

    def test_a_texture_outside_the_asset_root_is_not_read(self, tmp_path):
        asset = tmp_path / "asset"
        asset.mkdir()
        _texture(tmp_path / "outside.png")
        path = _stage(asset, _mesh() + _mdl_material(
            "        asset inputs:diffuse_texture = @../outside.png@\n"
            "        color3f inputs:diffuse_color_constant = (0, 0, 1)"))
        assert _rgb(usd_handler.load(path, asset_root=str(asset))) == [[0, 0, 255]] * 3

    def test_a_texture_outside_the_asset_root_with_no_constant_leaves_the_default(self, tmp_path):
        asset = tmp_path / "asset"
        asset.mkdir()
        _texture(tmp_path / "outside.png")
        path = _stage(asset, _mesh() + _mdl_material(
            "        asset inputs:diffuse_texture = @../outside.png@"))
        rgb = _rgb(usd_handler.load(path, asset_root=str(asset)))
        assert rgb is None or rgb != [list(_RED)] * 3

    def test_an_implicit_gprim_takes_the_constant(self, tmp_path):
        path = _stage(tmp_path, """
def Cube "C" (prepend apiSchemas = ["MaterialBindingAPI"]) {
    rel material:binding = </Mat>
}""" + _mdl_material("        color3f inputs:diffuse_color_constant = (1, 0, 0)"))
        rgb = np.asarray(usd_handler.load(path).point_data["RGB"])
        assert len(rgb) > 0 and np.all(rgb == [255, 0, 0])

    def test_the_material_beats_display_color(self, tmp_path):
        path = _stage(tmp_path, _mesh(extra="\n    color3f[] primvars:displayColor = [(0, 1, 0)]")
                      + _mdl_material("        color3f inputs:diffuse_color_constant = (1, 0, 0)"))
        assert _rgb(usd_handler.load(path)) == [[255, 0, 0]] * 3


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestOtherMaterials:
    def test_a_usd_preview_surface_is_still_preferred(self, tmp_path):
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        color3f inputs:diffuse_color_constant = (1, 0, 0)", preview=True))
        assert _rgb(usd_handler.load(path)) == [[0, 255, 0]] * 3

    def test_omni_surface_reads_its_colour_image_then_its_colour(self, tmp_path):
        _texture(tmp_path / "tex.png")
        textured = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:diffuse_reflection_color_image = @tex.png@\n"
            "        color3f inputs:diffuse_reflection_color = (0, 0, 1)", module="OmniSurface.mdl"),
            name="textured.usda")
        plain = _stage(tmp_path, _mesh() + _mdl_material(
            "        color3f inputs:diffuse_reflection_color = (0, 0, 1)", module="OmniSurfaceLite.mdl"),
            name="plain.usda")
        assert _rgb(usd_handler.load(textured)) == [list(_RED)] * 3
        assert _rgb(usd_handler.load(plain)) == [[0, 0, 255]] * 3

    def test_a_custom_mdl_module_keeps_the_default_colour_and_says_why_once(self, tmp_path, caplog):
        # The Isaac Sim warehouse: hundreds of materials, each of one of a few custom modules.
        body = (_mesh("A", "/Custom") + _mesh("B", "/Custom2")
                + _mesh("Red", "/Mat")
                + _mdl_material("        float inputs:Roughness = 0.5", module="MI_WallB_01.mdl",
                                name="Custom")
                + _mdl_material("        float inputs:Roughness = 0.7", module="MI_WallB_01.mdl",
                                name="Custom2")
                + _mdl_material("        color3f inputs:diffuse_color_constant = (1, 0, 0)"))
        path = _stage(tmp_path, body)
        with caplog.at_level("INFO"):
            rgb = _rgb(usd_handler.load(path))
        assert rgb[:6] == [list(usd_handler.NEUTRAL_RGB)] * 6
        assert rgb[6:] == [[255, 0, 0]] * 3
        why = [line for line in caplog.text.splitlines() if "colour defined inside the module" in line]
        assert len(why) == 2, why  # the first material's line, and the stage summary
        assert "/Custom:" in why[0] and "'MI_WallB_01.mdl'" in why[0]
        assert "2 prims are not coloured by their MDL material: 2 with 'MI_WallB_01.mdl'" in why[1]

    def test_an_omni_pbr_without_colour_inputs_keeps_the_default_colour(self, tmp_path, caplog):
        path = _stage(tmp_path, _mesh() + _mdl_material("        float inputs:metallic_constant = 1"))
        with caplog.at_level("INFO"):
            data = usd_handler.load(path)
        assert _rgb(data) is None
        assert "'OmniPBR.mdl', neither diffuse_texture nor diffuse_color_constant set" in caplog.text
