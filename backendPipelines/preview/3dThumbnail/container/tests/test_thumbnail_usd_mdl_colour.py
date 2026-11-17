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

    @pytest.mark.parametrize("uv_sets", [
        ["st_0"],              # an exporter's set typed as texture coordinates
        ["st_1", "st_0"],      # several sets: the first by name is sampled
        ["map1"],
    ])
    def test_a_texture_is_sampled_through_a_uv_set_of_another_name(self, tmp_path, uv_sets):
        _texture(tmp_path / "tex.png")
        uvs = "".join(f'\n        texCoord2f[] primvars:{name} = [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)] '
                      f'(interpolation = "vertex")' for name in uv_sets)
        tri = _TRI.split("\n        texCoord2f[]")[0] + uvs
        body = f"""
def Mesh "M" (prepend apiSchemas = ["MaterialBindingAPI"]) {{{tri}
    rel material:binding = </Mat>
}}""" + _mdl_material("        asset inputs:diffuse_texture = @tex.png@")
        assert _rgb(usd_handler.load(_stage(tmp_path, body))) == [list(_RED)] * 3

    @pytest.mark.parametrize("decl", [
        "float2[] primvars:st_0",      # the warehouse forklifts' layout: no texture-coordinate role
        "float2[] primvars:st_1",
        "float2[] primvars:UV2",
        "double2[] primvars:map1",
    ])
    def test_a_role_less_uv_set_named_as_one_is_sampled(self, tmp_path, decl):
        _texture(tmp_path / "tex.png")
        tri = _TRI.split("\n        texCoord2f[]")[0] + (
            f'\n        {decl} = [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)] (interpolation = "vertex")')
        body = f"""
def Mesh "M" (prepend apiSchemas = ["MaterialBindingAPI"]) {{{tri}
    rel material:binding = </Mat>
}}""" + _mdl_material("        asset inputs:diffuse_texture = @tex.png@")
        assert _rgb(usd_handler.load(_stage(tmp_path, body))) == [list(_RED)] * 3

    @pytest.mark.parametrize("decl", [
        "float2[] primvars:wind",       # data, not a UV set name
        "float2[] primvars:stretch_0",
        "float3[] primvars:st_0",       # three components
        "int2[] primvars:uv_0",
    ])
    def test_a_primvar_that_is_not_a_uv_set_is_not_sampled(self, tmp_path, decl):
        _texture(tmp_path / "tex.png")
        values = "[(0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, 0.5)]" if "3" in decl.split("[")[0] else (
            "[(1, 1), (1, 1), (1, 1)]" if decl.startswith("int") else "[(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)]")
        tri = _TRI.split("\n        texCoord2f[]")[0] + f'\n        {decl} = {values} (interpolation = "vertex")'
        body = f"""
def Mesh "M" (prepend apiSchemas = ["MaterialBindingAPI"]) {{{tri}
    rel material:binding = </Mat>
}}""" + _mdl_material("        asset inputs:diffuse_texture = @tex.png@")
        assert _rgb(usd_handler.load(_stage(tmp_path, body))) is None

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
        assert _rgb(usd_handler.load(path, asset_root=str(asset))) is None

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
        why = [line for line in caplog.text.splitlines()
               if "which authors no colour input the preview reads" in line]
        assert len(why) == 2, why  # the first material's line, and the stage summary
        assert "/Custom:" in why[0] and "'MI_WallB_01.mdl'" in why[0]
        assert "2 prims are not coloured by their MDL material: 2 with 'MI_WallB_01.mdl'" in why[1]

    def test_an_omni_pbr_without_colour_inputs_keeps_the_default_colour(self, tmp_path, caplog):
        path = _stage(tmp_path, _mesh() + _mdl_material("        float inputs:metallic_constant = 1"))
        with caplog.at_level("INFO"):
            data = usd_handler.load(path)
        assert _rgb(data) is None
        assert "'OmniPBR.mdl', with neither diffuse_texture nor diffuse_color_constant read" in caplog.text


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestUnrealExportMdl:
    """Other MDL modules, read by the input names an Unreal Engine export authors (#1559). The Isaac Sim
    warehouse's MI_*.mdl materials set AlbedoTexture with ColorAlbedo or BaseColor_Tint on the shader."""

    def test_the_albedo_texture_colours_the_mesh(self, tmp_path):
        _texture(tmp_path / "Textures" / "T_Floor_D.png")
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:AlbedoTexture = @./Textures/T_Floor_D.png@\n"
            "        float4 inputs:ColorAlbedo = (0, 0, 1, 0)", module="MI_Floor_01.mdl"))
        assert _rgb(usd_handler.load(path)) == [list(_RED)] * 3

    def test_the_albedo_colour_is_used_without_a_texture(self, tmp_path):
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        float4 inputs:ColorAlbedo = (0, 0, 1, 0)", module="MI_CeilingA_06b.mdl"))
        assert _rgb(usd_handler.load(path)) == [[0, 0, 255]] * 3

    def test_the_base_colour_tint_multiplies_the_texture(self, tmp_path):
        _texture(tmp_path / "tex.png", rgb=(200, 200, 200))
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:AlbedoTexture = @tex.png@\n"
            "        float4 inputs:BaseColor_Tint = (1, 0.5, 0, 1)", module="M_WallBoard_01.mdl"))
        _assert_close(_rgb(usd_handler.load(path)), [[200, 100, 0]] * 3)

    def test_a_base_colour_texture_is_read_too(self, tmp_path):
        _texture(tmp_path / "0001.png")
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:BaseColor_Texture = @0001.png@", module="MI_Barcode_0001.mdl"))
        assert _rgb(usd_handler.load(path)) == [list(_RED)] * 3

    def test_a_texture_outside_the_asset_root_falls_back_to_the_albedo_colour(self, tmp_path):
        asset = tmp_path / "asset"
        asset.mkdir()
        _texture(tmp_path / "outside.png")
        path = _stage(asset, _mesh() + _mdl_material(
            "        asset inputs:AlbedoTexture = @../outside.png@\n"
            "        float4 inputs:ColorAlbedo = (0, 1, 0, 0)", module="MI_WallB_01.mdl"))
        assert _rgb(usd_handler.load(path, asset_root=str(asset))) == [[0, 255, 0]] * 3

    def test_the_approximation_is_logged_once_per_module_with_a_summary(self, tmp_path, caplog):
        body = (_mesh("A", "/One") + _mesh("B", "/Two")
                + _mdl_material("        float4 inputs:ColorAlbedo = (1, 0, 0, 0)", module="MI_FrameA_01.mdl",
                                name="One")
                + _mdl_material("        float4 inputs:ColorAlbedo = (0, 0, 1, 0)", module="MI_FrameA_01.mdl",
                                name="Two"))
        path = _stage(tmp_path, body)
        with caplog.at_level("INFO"):
            rgb = _rgb(usd_handler.load(path))
        assert rgb == [[255, 0, 0]] * 3 + [[0, 0, 255]] * 3
        lines = [line for line in caplog.text.splitlines() if "coloured by an approximation" in line
                 or "an approximation, since" in line]
        assert len(lines) == 2, lines  # the first material's line, and the stage summary
        assert "/One:" in lines[0] and "'MI_FrameA_01.mdl' coloured from ColorAlbedo" in lines[0]
        assert "2 prims are coloured by an approximation of their MDL module's colour: " \
               "2 with 'MI_FrameA_01.mdl'" in lines[1]

    def test_omni_pbr_inputs_are_not_read_by_name(self, tmp_path):
        # An OmniPBR shader that happens to author an AlbedoTexture keeps its own family's rule.
        _texture(tmp_path / "tex.png")
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:AlbedoTexture = @tex.png@\n"
            "        color3f inputs:diffuse_color_constant = (0, 0, 1)"))
        assert _rgb(usd_handler.load(path)) == [[0, 0, 255]] * 3


def _chain(hops, value="(1, 0, 0)"):
    """Material interface inputs a0 .. a<hops-1>, each connected to the next, the last holding
    ``value``: a shader input connected to ``a0`` reaches the value in ``hops`` connections."""
    links = "".join(f"\n    color3f inputs:a{i}.connect = </Mat.inputs:a{i + 1}>" for i in range(hops - 1))
    return links + f"\n    color3f inputs:a{hops - 1} = {value}"


_GREEN_DISPLAY = "\n    color3f[] primvars:displayColor = [(0, 1, 0)]"
_CHAINED_CONSTANT = "        color3f inputs:diffuse_color_constant.connect = </Mat.inputs:a0>"


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestConnectionWalk:
    """A shader input's connections are followed one at a time, at most MAX_CONNECTION_HOPS of them;
    pxr's own recursive walk overflowed the C stack on a chain of tens of thousands of links."""

    def test_a_chain_at_the_bound_is_read(self, tmp_path):
        path = _stage(tmp_path, _mesh(extra=_GREEN_DISPLAY) + _mdl_material(
            _CHAINED_CONSTANT, interface=_chain(usd_handler.MAX_CONNECTION_HOPS)))
        assert _rgb(usd_handler.load(path)) == [[255, 0, 0]] * 3

    def test_a_chain_past_the_bound_is_not_read_and_the_log_says_why(self, tmp_path, caplog):
        path = _stage(tmp_path, _mesh(extra=_GREEN_DISPLAY) + _mdl_material(
            _CHAINED_CONSTANT, interface=_chain(usd_handler.MAX_CONNECTION_HOPS + 1)))
        with caplog.at_level("INFO"):
            rgb = _rgb(usd_handler.load(path))
        assert rgb == [[0, 255, 0]] * 3
        assert (f"Shader input /Mat/Shader.inputs:diffuse_color_constant: its connections run past "
                f"{usd_handler.MAX_CONNECTION_HOPS} hops, so it is not read") in caplog.text
        # The summary does not claim the constant is unset: it was set, through a chain not followed.
        assert "with neither diffuse_texture nor diffuse_color_constant read" in caplog.text

    def test_a_connection_loop_is_not_read_and_the_log_says_why(self, tmp_path, caplog):
        path = _stage(tmp_path, _mesh(extra=_GREEN_DISPLAY) + _mdl_material(
            _CHAINED_CONSTANT, interface="\n    color3f inputs:a0.connect = </Mat.inputs:a1>"
                                         "\n    color3f inputs:a1.connect = </Mat.inputs:a0>"))
        with caplog.at_level("WARNING"):
            rgb = _rgb(usd_handler.load(path))
        assert rgb == [[0, 255, 0]] * 3
        assert "inputs:diffuse_color_constant: its connections loop, so it is not read" in caplog.text

    def test_each_reason_is_logged_once_per_stage(self, tmp_path, caplog):
        body = _mesh("A", "/Mat") + _mesh("B", "/Two") + _mdl_material(
            _CHAINED_CONSTANT, interface=_chain(usd_handler.MAX_CONNECTION_HOPS + 1))
        body += _mdl_material(_CHAINED_CONSTANT.replace("/Mat.", "/Two."), name="Two",
                              interface=_chain(usd_handler.MAX_CONNECTION_HOPS + 1).replace("/Mat.", "/Two."))
        with caplog.at_level("WARNING"):
            usd_handler.load(_stage(tmp_path, body))
        assert caplog.text.count("its connections run past") == 1

    def test_a_value_behind_a_node_graph_output_is_read(self, tmp_path):
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        color3f inputs:diffuse_color_constant.connect = </Mat/Graph.outputs:colour>",
            interface="""
    def NodeGraph "Graph" {
        color3f inputs:base = (0, 0, 1)
        color3f outputs:colour.connect = </Mat/Graph.inputs:base>
    }"""))
        assert _rgb(usd_handler.load(path)) == [[0, 0, 255]] * 3

    def test_an_input_keeps_its_own_value_when_its_connection_has_none(self, tmp_path):
        # As UsdShadeInput.GetValueProducingAttributes: a connection to an input with no value, or to a
        # prim that does not exist, leaves the input's own authored value.
        no_value = _stage(tmp_path, _mesh() + _mdl_material(
            "        color3f inputs:diffuse_color_constant = (0, 0, 1)\n" + _CHAINED_CONSTANT,
            interface="\n    color3f inputs:a0"), name="no_value.usda")
        missing = _stage(tmp_path, _mesh() + _mdl_material(
            "        color3f inputs:diffuse_color_constant = (0, 0, 1)\n"
            "        color3f inputs:diffuse_color_constant.connect = </Nowhere.inputs:x>"), name="missing.usda")
        assert _rgb(usd_handler.load(no_value)) == [[0, 0, 255]] * 3
        assert _rgb(usd_handler.load(missing)) == [[0, 0, 255]] * 3

    def test_an_input_connected_to_another_shaders_output_is_not_read(self, tmp_path):
        # The output is computed when the material renders, so it has no value to read here; the input's
        # own authored value does not apply while it is connected.
        path = _stage(tmp_path, _mesh(extra=_GREEN_DISPLAY) + _mdl_material(
            "        color3f inputs:diffuse_color_constant = (1, 0, 0)\n"
            "        color3f inputs:diffuse_color_constant.connect = </Mat/Noise.outputs:out>",
            interface="""
    def Shader "Noise" {
        color3f outputs:out
    }"""))
        assert _rgb(usd_handler.load(path)) == [[0, 255, 0]] * 3

    def test_a_chain_of_fifty_thousand_links_loads_uncoloured(self, tmp_path):
        """The load runs in a child interpreter, so a crash (the C stack overflowing in a recursive walk)
        fails this test on the child's exit status instead of ending pytest."""
        import os
        import subprocess  # nosec B404 - fixed interpreter, no shell
        import sys

        import conftest

        path = _stage(tmp_path, _mesh() + _mdl_material(_CHAINED_CONSTANT, interface=_chain(50_000)))
        child = (
            "import sys\n"
            "from preview_pipeline.format_handlers import usd_handler\n"
            "data = usd_handler.load(sys.argv[1])\n"
            "print('LOADED', 'RGB' in data.point_data)\n"
        )
        env = dict(os.environ,
                   PYTHONPATH=conftest._CONTAINER_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""))
        result = subprocess.run([sys.executable, "-c", child, path],  # nosec B603 - fixed args
                                capture_output=True, text=True, timeout=600, env=env)
        assert result.returncode == 0, (
            f"the load did not return cleanly (returncode {result.returncode}). "
            f"stdout={result.stdout!r} stderr={result.stderr[-500:]!r}")
        assert "LOADED False" in result.stdout, f"stdout={result.stdout!r}"
        assert "its connections run past" in result.stderr


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestColourLog:
    """The run log says how each MDL material was drawn, after its prims were sampled, and the stage
    summaries stay one short line however many modules a scene has."""

    def test_an_unsampled_texture_with_no_constant_is_logged_as_not_coloured(self, tmp_path, caplog):
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:AlbedoTexture = @./Textures/missing.png@", module="MI_Floor_01.mdl"))
        with caplog.at_level("INFO"):
            rgb = _rgb(usd_handler.load(path))
        assert rgb is None
        assert "coloured from" not in caplog.text
        assert ("1 prims are not coloured by their MDL material: 1 with 'MI_Floor_01.mdl', whose "
                "AlbedoTexture could not be sampled") in caplog.text

    def test_an_omni_pbr_texture_not_read_with_no_constant_is_counted(self, tmp_path, caplog):
        path = _stage(tmp_path, _mesh() + _mdl_material("        asset inputs:diffuse_texture = @missing.png@"))
        with caplog.at_level("INFO"):
            usd_handler.load(path)
        assert "1 with 'OmniPBR.mdl', whose diffuse_texture could not be sampled" in caplog.text

    def test_the_approximation_names_the_inputs_that_coloured_the_prim(self, tmp_path, caplog):
        # The texture is named but cannot be read, so the albedo colour, not the texture, colours it.
        path = _stage(tmp_path, _mesh() + _mdl_material(
            "        asset inputs:AlbedoTexture = @missing.png@\n"
            "        float4 inputs:ColorAlbedo = (0, 0, 1, 0)\n"
            "        float4 inputs:BaseColor_Tint = (1, 1, 1, 1)", module="MI_Wall_01.mdl"))
        with caplog.at_level("INFO"):
            rgb = _rgb(usd_handler.load(path))
        assert rgb == [[0, 0, 255]] * 3
        assert "'MI_Wall_01.mdl' coloured from ColorAlbedo, BaseColor_Tint; an approximation" in caplog.text

    def test_the_summary_names_the_modules_with_most_prims_and_counts_the_rest(self, tmp_path, caplog):
        limit = usd_handler.MAX_SUMMARY_ENTRIES
        modules = limit + 3
        body = "".join(_mesh(f"Extra{i}", "/Mat0") for i in range(2))
        for i in range(modules):
            body += _mesh(f"M{i}", f"/Mat{i}") + _mdl_material(
                "        float inputs:Roughness = 0.5", module=f"MI_Part_{i:02d}.mdl", name=f"Mat{i}")
        with caplog.at_level("INFO"):
            usd_handler.load(_stage(tmp_path, body))
        summary = next(line for line in caplog.text.splitlines() if "prims are not coloured" in line)
        assert summary.count(" with 'MI_Part_") == limit
        assert f"{modules + 2} prims are not coloured by their MDL material: 3 with 'MI_Part_00.mdl'" in summary
        assert summary.endswith("; and 3 more (3 prims)")


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestMdlStageLayouts:
    def test_a_material_inside_an_instance_is_read_once_for_every_instance(self, tmp_path, monkeypatch):
        reads = []
        read = usd_handler._read_mdl_diffuse
        monkeypatch.setattr(usd_handler, "_read_mdl_diffuse",
                            lambda material, context: reads.append(material.GetPath()) or read(material, context))
        path = _stage(tmp_path, """
class Xform "Proto"
{""" + _mesh(material="/Proto/Mat") + _mdl_material(
            "        color3f inputs:diffuse_color_constant = (1, 0, 0)").replace("/Mat/", "/Proto/Mat/") + """
}
def Xform "A" (
    instanceable = true
    references = </Proto>
)
{
}
def Xform "B" (
    instanceable = true
    references = </Proto>
)
{
}""")
        assert _rgb(usd_handler.load(path)) == [[255, 0, 0]] * 6
        assert len(reads) == 1, reads

    def test_an_mdl_texture_inside_a_usdz_colours_the_mesh(self, tmp_path):
        from pxr import Sdf, UsdUtils

        source = tmp_path / "source"
        _texture(source / "Textures" / "wood.png")
        (source / "OmniPBR.mdl").write_text("mdl 1.4;\n")  # packaged beside the layer; never opened
        layer = _stage(source, _mesh() + _mdl_material(
            "        asset inputs:diffuse_texture = @./Textures/wood.png@"))
        package = tmp_path / "pallet.usdz"
        assert UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(layer), str(package))
        assert _rgb(usd_handler.load(str(package))) == [list(_RED)] * 3
