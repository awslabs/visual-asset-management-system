#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""USD stages render as a renderer composes them (#465).

The handler used to walk ``stage.Traverse()`` for ``UsdGeom.Mesh`` only, so instanced geometry,
point instancers and implicit gprims were missing; guides, proxies and invisible prims were drawn;
time-sampled-only points had no value; indexed, uniform and faceVarying ``displayColor`` was
dropped; one uncoloured prim dropped every prim's colour; and any texture of the bound material
(a normal map, say) could colour the mesh. Each case is a small synthesised stage.

The stage tests need the real pxr/pyvista and run inside the pipeline image; the triangulation
tests are numpy only.
"""

import os

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline.format_handlers import usd_handler

_TRI = """
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]"""
_QUAD = """
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]"""


def _stage(tmp_path, body, name="scene.usda", header='upAxis = "Y"'):
    path = tmp_path / name
    path.write_text(f"#usda 1.0\n(\n    {header}\n)\n{body}\n")
    return str(path)


def _rgb(data):
    return np.asarray(data.point_data["RGB"]) if "RGB" in data.point_data else None


@pytest.mark.unit
class TestFanTriangulate:
    def test_triangles_quads_and_ngons(self):
        counts = [3, 4, 5]
        indices = [0, 1, 2, 0, 2, 3, 4, 0, 4, 5, 6, 7]
        triangles, faces, corners = usd_handler.fan_triangulate(counts, indices, 8)
        assert triangles.tolist() == [[0, 1, 2], [0, 2, 3], [0, 3, 4], [0, 4, 5], [0, 5, 6], [0, 6, 7]]
        assert faces.tolist() == [0, 1, 1, 2, 2, 2]
        assert corners[1].tolist() == [3, 4, 5]

    def test_degenerate_truncated_and_out_of_range_faces_are_dropped(self):
        # a 2-vertex face, a valid triangle, a triangle naming vertex 99, then a face past the end
        counts = [2, 3, 3, 4]
        indices = [0, 1, 0, 1, 2, 0, 1, 99, 0, 1]
        triangles, faces, _ = usd_handler.fan_triangulate(counts, indices, 3)
        assert triangles.tolist() == [[0, 1, 2]]
        assert faces.tolist() == [1]

    def test_empty(self):
        triangles, _, _ = usd_handler.fan_triangulate([], [], 0)
        assert triangles.shape == (0, 3)


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestStageComposition:
    def test_instanceable_prims_are_drawn_at_every_instance(self, tmp_path):
        _stage(tmp_path, f'def Xform "Proto"\n{{\n    def Mesh "Tri"\n    {{{_TRI}\n    }}\n}}',
               name="proto.usda", header='defaultPrim = "Proto"')
        path = _stage(tmp_path, """
def Xform "A" (
    instanceable = true
    references = @./proto.usda@
)
{
    double3 xformOp:translate = (5, 0, 0)
    uniform token[] xformOpOrder = ["xformOp:translate"]
}
def Xform "B" (
    instanceable = true
    references = @./proto.usda@
)
{
    double3 xformOp:translate = (-5, 0, 0)
    uniform token[] xformOpOrder = ["xformOp:translate"]
}
""")
        data = usd_handler.load(path)
        xs = np.asarray(data.points)[:, 0]
        assert data.n_points == 6 and xs.max() >= 5 and xs.min() <= -5

    def test_a_point_instancer_places_every_instance(self, tmp_path):
        path = _stage(tmp_path, f"""
def PointInstancer "Field"
{{
    point3f[] positions = [(0, 0, 0), (10, 0, 0), (0, 0, 10)]
    int[] protoIndices = [0, 0, 1]
    rel prototypes = [</Field/Protos/Tri>, </Field/Protos/Quad>]
    def Scope "Protos"
    {{
        def Mesh "Tri" {{{_TRI}
        }}
        def Mesh "Quad" {{{_QUAD}
        }}
    }}
}}
""")
        data = usd_handler.load(path)
        points = np.asarray(data.points)
        assert data.n_points == 3 + 3 + 4
        assert points[:, 0].max() >= 10 and points[:, 2].max() >= 10

    def test_guides_proxies_and_invisible_prims_are_not_drawn(self, tmp_path):
        path = _stage(tmp_path, f"""
def Mesh "Render" {{{_TRI}
    color3f[] primvars:displayColor = [(0, 1, 0)]
}}
def Mesh "Guide" {{{_TRI}
    uniform token purpose = "guide"
    color3f[] primvars:displayColor = [(1, 0, 0)]
}}
def Mesh "Proxy" {{{_TRI}
    uniform token purpose = "proxy"
    color3f[] primvars:displayColor = [(1, 0, 0)]
}}
def Xform "Hidden" {{
    token visibility = "invisible"
    def Mesh "Child" {{{_TRI}
        color3f[] primvars:displayColor = [(1, 0, 0)]
    }}
}}
""")
        data = usd_handler.load(path)
        assert data.n_points == 3
        assert np.all(_rgb(data) == [255, 255, 255] * np.array([0, 1, 0]))

    def test_a_time_sampled_only_mesh_is_read(self, tmp_path):
        path = _stage(tmp_path, """
def Mesh "Animated"
{
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0, 1, 2]
    point3f[] points.timeSamples = { 1: [(0, 0, 0), (2, 0, 0), (0, 2, 0)], 10: [(0, 0, 0), (9, 0, 0), (0, 9, 0)] }
}
""")
        data = usd_handler.load(path)
        assert np.asarray(data.points)[:, 0].max() == pytest.approx(2.0)

    def test_the_start_time_code_is_used_when_the_stage_declares_one(self, tmp_path):
        path = _stage(tmp_path, """
def Mesh "Animated"
{
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0, 1, 2]
    point3f[] points.timeSamples = { 1: [(0, 0, 0), (2, 0, 0), (0, 2, 0)], 10: [(0, 0, 0), (9, 0, 0), (0, 9, 0)] }
}
""", header='upAxis = "Y"\n    startTimeCode = 10\n    endTimeCode = 20')
        data = usd_handler.load(path)
        assert np.asarray(data.points)[:, 0].max() == pytest.approx(9.0)

    @pytest.mark.parametrize("prim,attributes,extent_axis,expected", [
        ('Cube "C"', ["double size = 4"], 0, 4.0),
        ('Sphere "S"', ["double radius = 3"], 1, 6.0),
        ('Cylinder "Y"', ["double radius = 1", "double height = 6", 'uniform token axis = "Y"'], 1, 6.0),
        ('Cone "K"', ["double radius = 1", "double height = 5", 'uniform token axis = "Y"'], 1, 5.0),
        ('Capsule "P"', ["double radius = 1", "double height = 4", 'uniform token axis = "Y"'], 1, 6.0),
    ])
    def test_implicit_gprims_are_tessellated(self, tmp_path, prim, attributes, extent_axis, expected):
        body = f"def {prim}\n{{\n" + "".join(f"    {line}\n" for line in attributes) + "}"
        data = usd_handler.load(_stage(tmp_path, body))
        bounds = np.asarray(data.bounds).reshape(3, 2)
        assert data.n_cells > 0
        assert bounds[extent_axis, 1] - bounds[extent_axis, 0] == pytest.approx(expected, rel=0.05)


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestStageColour:
    def test_an_uncoloured_prim_gets_a_neutral_fill_and_keeps_the_others_colour(self, tmp_path):
        path = _stage(tmp_path, f"""
def Mesh "Red" {{{_TRI}
    color3f[] primvars:displayColor = [(1, 0, 0)]
}}
def Mesh "Plain" {{{_QUAD}
}}
""")
        rgb = _rgb(usd_handler.load(path))
        assert rgb is not None and len(rgb) == 7
        assert rgb[:3].tolist() == [[255, 0, 0]] * 3
        assert rgb[3:].tolist() == [list(usd_handler.NEUTRAL_RGB)] * 4

    def test_indexed_uniform_and_face_varying_display_colours(self, tmp_path):
        path = _stage(tmp_path, f"""
def Mesh "Indexed" {{{_QUAD}
    color3f[] primvars:displayColor = [(0, 0, 1), (1, 1, 0)] (interpolation = "vertex")
    int[] primvars:displayColor:indices = [1, 1, 1, 1]
}}
def Mesh "Uniform" {{
    int[] faceVertexCounts = [3, 3]
    int[] faceVertexIndices = [0, 1, 2, 3, 4, 5]
    point3f[] points = [(0, 0, 5), (1, 0, 5), (0, 1, 5), (0, 0, 6), (1, 0, 6), (0, 1, 6)]
    color3f[] primvars:displayColor = [(1, 0, 0), (0, 1, 0)] (interpolation = "uniform")
}}
def Mesh "FaceVarying" {{{_TRI}
    color3f[] primvars:displayColor = [(0, 1, 1), (0, 1, 1), (0, 1, 1)] (interpolation = "faceVarying")
}}
""")
        rgb = _rgb(usd_handler.load(path))
        assert rgb[:4].tolist() == [[255, 255, 0]] * 4
        assert rgb[4:7].tolist() == [[255, 0, 0]] * 3 and rgb[7:10].tolist() == [[0, 255, 0]] * 3
        assert rgb[10:].tolist() == [[0, 255, 255]] * 3

    def _textured(self, tmp_path, connect_to, texture="tex.png"):
        from PIL import Image

        Image.fromarray(np.full((4, 4, 3), (200, 10, 10), np.uint8)).save(tmp_path / "tex.png")
        return _stage(tmp_path, f"""
def Mesh "M" (prepend apiSchemas = ["MaterialBindingAPI"]) {{{_TRI}
    texCoord2f[] primvars:st = [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)] (interpolation = "vertex")
    color3f[] primvars:displayColor = [(0, 0, 1)]
    rel material:binding = </Mat>
}}
def Material "Mat" {{
    token outputs:surface.connect = </Mat/Surface.outputs:surface>
    def Shader "Surface" {{
        uniform token info:id = "UsdPreviewSurface"
        color3f inputs:{connect_to}.connect = </Mat/Tex.outputs:rgb>
        normal3f inputs:normal = (0, 0, 1)
        token outputs:surface
    }}
    def Shader "Tex" {{
        uniform token info:id = "UsdUVTexture"
        asset inputs:file = @{texture}@
        float3 outputs:rgb
    }}
}}
""")

    def test_the_diffuse_texture_colours_the_mesh(self, tmp_path):
        rgb = _rgb(usd_handler.load(self._textured(tmp_path, "diffuseColor")))
        assert rgb.tolist() == [[200, 10, 10]] * 3

    def test_a_texture_not_connected_to_diffuse_colour_is_not_used(self, tmp_path):
        rgb = _rgb(usd_handler.load(self._textured(tmp_path, "emissiveColor")))
        assert rgb.tolist() == [[0, 0, 255]] * 3

    def test_a_texture_outside_the_asset_root_is_not_read(self, tmp_path):
        asset = tmp_path / "asset"
        asset.mkdir()
        from PIL import Image

        Image.fromarray(np.full((4, 4, 3), (200, 10, 10), np.uint8)).save(tmp_path / "outside.png")
        path = self._textured(asset, "diffuseColor", texture="../outside.png")
        os.remove(asset / "tex.png")
        rgb = _rgb(usd_handler.load(path, asset_root=str(asset)))
        assert rgb.tolist() != [[200, 10, 10]] * 3
