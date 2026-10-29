#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Routing of one local file through load -> attributes -> still frames.

Attributes-only classes (DXF, IFC, splat containers, a splat PLY, and mesh containers no library here
reads) never touch the display or the renderer and report renderSkipped "unsupported"; renderable
classes load through the thumbnail pipeline's handlers, and a render fault after a successful load
degrades to attributes-only with renderSkipped "error" rather than losing the attributes."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

np = pytest.importorskip("numpy", reason="the dispatcher moves numpy point arrays")

from preview_pipeline.analysis import analysis, attributes  # noqa: E402

PLY_MESH = (b"ply\nformat ascii 1.0\nelement vertex 8\nproperty float x\nproperty float y\n"
            b"property float z\nelement face 12\nproperty list uchar int vertex_indices\nend_header\n")
PLY_SPLAT = (b"ply\nformat binary_little_endian 1.0\nelement vertex 4\nproperty float x\n"
             b"property float y\nproperty float z\nproperty float f_dc_0\nend_header\n")
PLY_CLOUD = (b"ply\nformat binary_little_endian 1.0\nelement vertex 50\nproperty float x\n"
             b"property float y\nproperty float z\nend_header\n")

BOX = np.array([[x, y, z] for x in (0.0, 2.0) for y in (0.0, 1.0) for z in (0.0, 4.0)])


def _polydata(points=BOX, n_cells=12, rgb=None):
    point_data = {} if rgb is None else {"RGB": rgb}
    return SimpleNamespace(points=np.asarray(points, dtype=np.float64), n_points=len(points),
                           n_cells=n_cells, point_data=point_data)


def _frames(n):
    return [np.zeros((4, 4, 3), dtype=np.uint8) for _ in range(n)]


def _write(tmp_path, name, data=b"\0" * 64):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def render_stack():
    """The display, up-axis and renderer seams, patched so no X server or VTK is needed."""
    with patch.object(analysis.display, "ensure_display") as ensure, \
            patch.object(analysis.core, "normalize_up_axis", side_effect=lambda data, ext: data), \
            patch.object(analysis.renderer, "generate_still_frames", return_value=_frames(4)) as stills, \
            patch.object(analysis.pointcloud_handler, "to_polydata",
                         side_effect=lambda points, colors: _polydata(points, 0, colors)):
        yield SimpleNamespace(ensure=ensure, stills=stills)


@pytest.mark.unit
class TestExtensionTables:
    def test_tables_are_disjoint_and_cover_the_render3d_rows(self):
        assert analysis.RENDERABLE_EXTENSIONS.isdisjoint(analysis.ATTRIBUTES_ONLY_EXTENSIONS)
        assert analysis.RENDERABLE_EXTENSIONS.isdisjoint(analysis.UNLOADABLE_MESH_EXTENSIONS)
        for ext in (".ply", ".stl", ".obj", ".glb", ".gltf", ".drc", ".off", ".3mf",
                    ".las", ".laz", ".e57", ".ptx", ".pcd", ".xyz",
                    ".stp", ".step", ".iges", ".igs", ".brep", ".usd", ".usda", ".usdc", ".usdz"):
            assert ext in analysis.RENDERABLE_EXTENSIONS, ext
        assert analysis.ATTRIBUTES_ONLY_EXTENSIONS == {".dxf", ".ifc", ".ifczip", ".spz", ".sog", ".splat", ".lcc"}
        assert analysis.UNLOADABLE_MESH_EXTENSIONS == {".3ds", ".wrl", ".amf", ".3dm", ".bim"}
        assert ".fbx" not in analysis.RENDERABLE_EXTENSIONS, "FBX is the Blender image's"
        assert analysis.FULL_BOUNDS_EXTENSIONS == analysis.USD_EXTENSIONS | analysis.CAD_EXTENSIONS


@pytest.mark.unit
class TestAttributesOnlyRoutes:
    def test_dxf(self, tmp_path, render_stack):
        payload = {attributes.SYS_CAD: {"solidCount": 0, "faceCount": 0, "edgeCount": 3, "assemblyCount": 0,
                                        "declaredUnits": "mm", "estimatedUnits": None},
                   attributes.SYS_FORMAT: {"extension": ".dxf", "loader": "ezdxf", "details": {
                       "is2d": True, "dxfVersion": "AC1027", "entityTotal": 3, "layers": 1}}}
        with patch.object(analysis.attributes, "dxf_attributes", return_value=payload) as dxf:
            result = analysis.analyze_local_file(_write(tmp_path, "plan.dxf"), ".dxf")
        dxf.assert_called_once()
        assert result.attributes == payload
        assert result.render_skipped == analysis.RENDER_SKIPPED_UNSUPPORTED
        assert result.frames == [] and result.loader == "ezdxf"
        assert "cad" in result.facts
        render_stack.ensure.assert_not_called()
        render_stack.stills.assert_not_called()

    def test_ifc_receives_the_work_dir(self, tmp_path, render_stack):
        with patch.object(analysis.attributes, "ifc_attributes",
                          return_value={attributes.SYS_IFC: {"schema": "IFC4", "projectName": None, "storeyCount": 0,
                                                             "elementCount": 1, "site": None}}) as ifc:
            result = analysis.analyze_local_file(_write(tmp_path, "m.ifczip"), ".ifczip", work_dir=str(tmp_path))
        ifc.assert_called_once_with(str(tmp_path / "m.ifczip"), str(tmp_path))
        assert result.render_skipped == "unsupported" and result.loader == "ifcopenshell"

    def test_splat_container_from_its_header(self, tmp_path, render_stack):
        result = analysis.analyze_local_file(_write(tmp_path, "a.splat", b"\0" * 96), ".splat")
        assert result.attributes[attributes.SYS_POINTCLOUD]["pointCount"] == 3
        assert result.attributes[attributes.SYS_FORMAT]["details"]["isGaussianSplat"] is True
        assert result.render_skipped == "unsupported" and result.loader == "header"
        assert result.facts["pointcloud"].startswith("Gaussian splat with 3 splats")

    def test_lcc_is_a_warning_only(self, tmp_path, render_stack):
        result = analysis.analyze_local_file(_write(tmp_path, "a.lcc"), ".lcc")
        assert result.attributes == {} and result.facts == {}
        assert result.render_skipped == "unsupported" and result.loader is None
        assert any(".lcc" in w for w in result.warnings)

    def test_splat_ply_never_reaches_the_mesh_loader(self, tmp_path, render_stack):
        with patch.object(analysis.mesh_handler, "load") as load:
            result = analysis.analyze_local_file(_write(tmp_path, "s.ply", PLY_SPLAT), ".ply")
        load.assert_not_called()
        assert result.attributes[attributes.SYS_POINTCLOUD] == {
            "pointCount": 4, "hasColor": True, "boundsMin": None, "boundsMax": None, "crs": None}
        assert result.attributes[attributes.SYS_FORMAT] == {"extension": ".ply", "loader": "header", "details": {
            "container": "ply", "plyFormat": "binary_little_endian", "properties": 4, "shDegree": 0,
            "isGaussianSplat": True}}
        assert result.render_skipped == "unsupported"

    @pytest.mark.parametrize("name", ["a.3ds", "a.wrl", "a.amf", "a.3dm", "a.bim", "a.foo"])
    def test_unloadable_extensions_are_unsupported_with_a_warning(self, tmp_path, render_stack, name):
        result = analysis.analyze_local_file(_write(tmp_path, name), "." + name.split(".")[-1])
        assert result.attributes == {}
        assert result.render_skipped == "unsupported"
        assert result.warnings and "No loader" in result.warnings[0]
        render_stack.stills.assert_not_called()


@pytest.mark.unit
class TestRenderableRoutes:
    def test_ply_mesh_loads_extracts_and_renders(self, tmp_path, render_stack):
        with patch.object(analysis.mesh_handler, "load", return_value=_polydata()) as load:
            result = analysis.analyze_local_file(_write(tmp_path, "m.ply", PLY_MESH), ".ply", n_views=4)
        load.assert_called_once()
        assert result.loader == "trimesh"
        assert result.attributes[attributes.SYS_STATISTICS]["vertices"] == 8
        assert result.attributes[attributes.SYS_STATISTICS]["faces"] == 12
        assert result.attributes[attributes.SYS_GEOMETRY]["dimensions"] == {"width": 2.0, "height": 1.0, "depth": 4.0}
        assert attributes.SYS_POINTCLOUD not in result.attributes
        assert len(result.frames) == 4
        assert result.render_skipped is None and result.warnings == []
        render_stack.ensure.assert_called_once_with()
        kwargs = render_stack.stills.call_args.kwargs
        assert kwargs["n_views"] == 4 and kwargs["use_full_bounds"] is False
        assert kwargs["resolution"] == analysis.renderer.DEFAULT_STILL_RESOLUTION

    def test_ply_pointcloud_adds_sys_pointcloud_and_caps(self, tmp_path, render_stack):
        points = np.arange(150, dtype=np.float64).reshape(50, 3)
        with patch.object(analysis.mesh_handler, "load", return_value=_polydata(points, 0)):
            result = analysis.analyze_local_file(_write(tmp_path, "c.ply", PLY_CLOUD), ".ply", max_points=10)
        cloud = result.attributes[attributes.SYS_POINTCLOUD]
        assert cloud["pointCount"] == 50 and cloud["crs"] is None
        assert result.attributes[attributes.SYS_FORMAT] == {
            "extension": ".ply", "loader": "trimesh",
            "details": {"plyFormat": "binary_little_endian", "pointsLoaded": 10}}
        rendered = render_stack.stills.call_args.args[0]
        assert rendered.n_points == 10

    def test_off_and_3mf_use_trimesh_and_drc_uses_dracopy(self, tmp_path, render_stack):
        with patch.object(analysis.mesh_handler, "load", return_value=_polydata()):
            assert analysis.analyze_local_file(_write(tmp_path, "a.off"), ".off").loader == "trimesh"
            assert analysis.analyze_local_file(_write(tmp_path, "a.3mf"), ".3mf").loader == "trimesh"
            assert analysis.analyze_local_file(_write(tmp_path, "a.drc"), ".drc").loader == "DracoPy"

    def test_cad_route_frames_on_full_bounds(self, tmp_path, render_stack):
        cad_attrs = {attributes.SYS_CAD: {"solidCount": 1, "faceCount": 6, "edgeCount": 12, "assemblyCount": 1,
                                          "declaredUnits": None, "estimatedUnits": None},
                     attributes.SYS_STATISTICS: {"meshCount": 1, "vertices": 8, "faces": 6, "triangles": None,
                                                 "watertight": None}}
        with patch.object(analysis.cad_handler, "load_shape", return_value="shape") as load_shape, \
                patch.object(analysis.cad_handler, "tessellate_shape", return_value=_polydata()) as tess, \
                patch.object(analysis.attributes, "cad_shape_attributes", return_value=cad_attrs) as extract:
            result = analysis.analyze_local_file(_write(tmp_path, "part.stp"), ".stp")
        load_shape.assert_called_once_with(str(tmp_path / "part.stp"))
        tess.assert_called_once_with("shape")
        extract.assert_called_once()
        assert extract.call_args.args[0] == "shape" and extract.call_args.args[1] == ".stp"
        assert result.loader == "OCP"
        assert result.attributes == cad_attrs
        assert render_stack.stills.call_args.kwargs["use_full_bounds"] is True

    def test_pointcloud_route_threads_the_cap_and_the_header(self, tmp_path, render_stack):
        points = np.arange(30, dtype=np.float64).reshape(10, 3)
        with patch.object(analysis.attributes, "pointcloud_header",
                          return_value={"points": 5000, "details": {"version": "1.4"}}) as header, \
                patch.object(analysis.pointcloud_handler, "load_points", return_value=(points, None)) as load:
            result = analysis.analyze_local_file(_write(tmp_path, "c.laz"), ".laz", max_points=123)
        header.assert_called_once_with(".laz", str(tmp_path / "c.laz"))
        load.assert_called_once_with(str(tmp_path / "c.laz"), max_points=123)
        cloud = result.attributes[attributes.SYS_POINTCLOUD]
        assert cloud["pointCount"] == 5000 and cloud["hasColor"] is False and cloud["crs"] is None
        assert result.attributes[attributes.SYS_FORMAT] == {
            "extension": ".laz", "loader": "laspy", "details": {"version": "1.4", "pointsLoaded": 10}}
        assert result.loader == "laspy"
        assert render_stack.stills.call_args.kwargs["use_full_bounds"] is False

    def test_usd_route_adds_the_scene(self, tmp_path, render_stack):
        scene = {attributes.SYS_SCENE: {"nodeCount": 3, "hasAnimation": False, "hasArmature": False},
                 attributes.SYS_STATISTICS: {"meshCount": 1},
                 attributes.SYS_GEOMETRY: {"units": "m"},
                 attributes.SYS_FORMAT: {"details": {"upAxis": "Y", "metersPerUnit": 1.0}}}
        with patch.object(analysis.usd_handler, "load", return_value=_polydata()), \
                patch.object(analysis.attributes, "usd_scene_attributes", return_value=scene):
            result = analysis.analyze_local_file(_write(tmp_path, "s.usdz"), ".usdz")
        assert result.loader == "usd-core"
        assert result.attributes[attributes.SYS_SCENE] == {"nodeCount": 3, "hasAnimation": False, "hasArmature": False}
        assert result.attributes[attributes.SYS_STATISTICS] == {"meshCount": 1, "vertices": 8, "faces": 12,
                                                                "triangles": 12, "watertight": None}, \
            "the stage's mesh count merges over the tessellated PolyData's statistics"
        assert result.attributes[attributes.SYS_GEOMETRY]["units"] == "m"
        assert result.attributes[attributes.SYS_FORMAT] == {"extension": ".usdz", "loader": "usd-core",
                                                            "details": {"upAxis": "Y", "metersPerUnit": 1.0}}
        assert "scene" in result.facts
        assert render_stack.stills.call_args.kwargs["use_full_bounds"] is True

    def test_a_render_fault_degrades_to_attributes_only(self, tmp_path, render_stack):
        render_stack.stills.side_effect = RuntimeError("GL context lost")
        with patch.object(analysis.mesh_handler, "load", return_value=_polydata()):
            result = analysis.analyze_local_file(_write(tmp_path, "a.obj"), ".obj")
        assert result.attributes[attributes.SYS_STATISTICS]["vertices"] == 8
        assert result.frames == []
        assert result.render_skipped == analysis.RENDER_SKIPPED_ERROR
        assert result.warnings == ["Render failed: GL context lost"]

    def test_a_loader_fault_propagates(self, tmp_path, render_stack):
        with patch.object(analysis.mesh_handler, "load", side_effect=ValueError("No valid mesh geometry found")):
            with pytest.raises(ValueError, match="No valid mesh geometry"):
                analysis.analyze_local_file(_write(tmp_path, "a.obj"), ".obj")

    def test_render_false_skips_display_and_renderer(self, tmp_path, render_stack):
        with patch.object(analysis.mesh_handler, "load", return_value=_polydata()):
            result = analysis.analyze_local_file(_write(tmp_path, "a.obj"), ".obj", render=False)
        render_stack.ensure.assert_not_called()
        render_stack.stills.assert_not_called()
        assert result.frames == [] and result.render_skipped is None
        assert result.attributes[attributes.SYS_STATISTICS]["vertices"] == 8

    def test_the_result_is_json_serialisable(self, tmp_path, render_stack):
        with patch.object(analysis.mesh_handler, "load", return_value=_polydata()):
            result = analysis.analyze_local_file(_write(tmp_path, "a.obj"), ".obj")
        json.dumps({"attributes": result.attributes, "facts": result.facts, "warnings": result.warnings})

    def test_extension_falls_back_to_the_file_name(self, tmp_path, render_stack):
        with patch.object(analysis.mesh_handler, "load", return_value=_polydata()):
            result = analysis.analyze_local_file(_write(tmp_path, "a.OBJ"), "")
        assert result.attributes[attributes.SYS_FORMAT]["extension"] == ".obj"
