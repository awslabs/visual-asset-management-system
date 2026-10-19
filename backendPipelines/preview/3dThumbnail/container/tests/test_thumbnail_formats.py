#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""More sub-formats and up-axis from metadata (#472).

* ``.pts`` (Leica text, streamed), ``.off`` (trimesh), ``.3mf`` and ``.dae`` (COLLADA); ``.drc`` keeps
  its vertex colours.
* 3MF and COLLADA are XML: they are parsed with DTDs, entity declarations and external entities
  refused (ARCC BSC12), so a crafted file can neither expand entities nor read local files or URLs.
* A zip-based input (3MF, SOG, members read out of USDZ) is checked against the free disk and for zip
  bombs from its central directory before anything is decompressed.
* The up axis comes from metadata (glTF Y-up, FBX ``UpAxis``, COLLADA ``<up_axis>``, 3MF Z-up, Leica
  and survey formats Z-up); STL is no longer rotated unconditionally and the ``dz > 1.2 * dy``
  guess, which put Y-up models that are long in depth on their side, is gone.

Tests that build meshes need the real pyvista (pipeline image).
"""

import io
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import core
from preview_pipeline.format_handlers import mesh_handler, point_streams, xml_mesh_handler
from preview_pipeline.utils import archive


# --------------------------------------------------------------------------------------------------
# Secure XML (BSC12)
# --------------------------------------------------------------------------------------------------

_XXE = b"""<?xml version="1.0"?>
<!DOCTYPE COLLADA [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<COLLADA><asset><up_axis>&secret;</up_axis></asset></COLLADA>"""

_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
<COLLADA>&lol3;</COLLADA>"""

_EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE COLLADA SYSTEM "http://example.invalid/collada.dtd"><COLLADA/>"""


@pytest.mark.unit
class TestSecureXml:
    @pytest.mark.parametrize("document", [_XXE, _LAUGHS, _EXTERNAL_DTD], ids=["xxe", "billion-laughs", "external-dtd"])
    def test_dtds_and_entities_are_refused(self, document):
        with pytest.raises(xml_mesh_handler.UnsafeXmlError):
            xml_mesh_handler.parse_xml(io.BytesIO(document))

    def test_a_dae_with_an_external_entity_fails_without_reading_it(self, tmp_path):
        path = tmp_path / "evil.dae"
        path.write_bytes(_XXE)
        with patch("builtins.open", wraps=open) as opened:
            with pytest.raises(xml_mesh_handler.UnsafeXmlError):
                xml_mesh_handler.load_dae(str(path))
        assert all("/etc/passwd" not in str(call.args[0]) for call in opened.call_args_list)

    def test_a_3mf_model_part_with_a_dtd_is_refused(self, tmp_path):
        path = tmp_path / "evil.3mf"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("3D/3dmodel.model", _LAUGHS.replace(b"COLLADA", b"model"))
        with pytest.raises(xml_mesh_handler.UnsafeXmlError):
            xml_mesh_handler.load_3mf(str(path))

    def test_plain_xml_parses(self):
        root = xml_mesh_handler.parse_xml(io.BytesIO(b"<a><b>1</b></a>"))
        assert root.tag == "a" and root[0].text == "1"


# --------------------------------------------------------------------------------------------------
# Archives
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestArchiveLimits:
    def test_an_archive_larger_than_the_free_disk_is_refused(self, tmp_path):
        path = tmp_path / "big.3mf"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("3D/3dmodel.model", b"0" * (3 * 1024 ** 2))
        with patch.object(archive, "free_disk_bytes", return_value=archive.OUTPUT_DISK_HEADROOM_BYTES + 1024 ** 2):
            with pytest.raises(archive.ArchiveLimitError, match="uncompressed"):
                archive.check_archive(str(path), str(tmp_path))
        assert archive.check_archive(str(path), str(tmp_path)) == 3 * 1024 ** 2

    def test_an_implausible_compression_ratio_is_refused(self):
        info = zipfile.ZipInfo("m")
        info.file_size, info.compress_size = 10 * 1024 ** 3, 1024
        with pytest.raises(archive.ArchiveLimitError, match="compression ratio"):
            archive.check_member(info, 100 * 1024 ** 3)


# --------------------------------------------------------------------------------------------------
# 3MF and COLLADA geometry
# --------------------------------------------------------------------------------------------------

_3MF_MODEL = b"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <resources>
  <basematerials id="1"><base name="red" displaycolor="#FF0000"/><base name="blue" displaycolor="#0000FFFF"/></basematerials>
  <object id="2" type="model" pid="1" pindex="0"><mesh>
   <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/></vertices>
   <triangles><triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="1" v3="3" pid="1" p1="1"/></triangles>
  </mesh></object>
  <object id="3" type="model"><components><component objectid="2" transform="1 0 0 0 1 0 0 0 1 10 0 0"/></components></object>
 </resources>
 <build><item objectid="3"/></build>
</model>"""

_DAE = b"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
 <asset><up_axis>Z_UP</up_axis></asset>
 <library_effects><effect id="green-fx"><profile_COMMON><technique sid="c"><phong>
   <diffuse><color>0 1 0 1</color></diffuse></phong></technique></profile_COMMON></effect></library_effects>
 <library_materials><material id="green"><instance_effect url="#green-fx"/></material></library_materials>
 <library_geometries><geometry id="g"><mesh>
  <source id="g-pos"><float_array id="g-arr" count="12">0 0 0 1 0 0 1 1 0 0 0 1</float_array>
   <technique_common><accessor source="#g-arr" count="4" stride="3"/></technique_common></source>
  <vertices id="g-v"><input semantic="POSITION" source="#g-pos"/></vertices>
  <triangles count="1" material="m"><input semantic="VERTEX" source="#g-v" offset="0"/><p>0 1 2</p></triangles>
  <polylist count="1"><input semantic="VERTEX" source="#g-v" offset="0"/><vcount>4</vcount><p>0 1 2 3</p></polylist>
 </mesh></geometry></library_geometries>
 <library_visual_scenes><visual_scene id="s"><node id="n"><translate>0 0 5</translate>
   <instance_geometry url="#g"><bind_material><technique_common>
    <instance_material symbol="m" target="#green"/></technique_common></bind_material></instance_geometry>
 </node></visual_scene></library_visual_scenes>
 <scene><instance_visual_scene url="#s"/></scene>
</COLLADA>"""


@pytest.mark.unit
@requires_real("pyvista")
class TestXmlMeshes:
    def test_a_3mf_build_with_components_colours_and_z_up(self, tmp_path):
        path = tmp_path / "part.3mf"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("3D/3dmodel.model", _3MF_MODEL)
        mesh = core._load_file(str(path), ".3mf")
        assert mesh.n_cells == 2
        assert np.asarray(mesh.cell_data["RGB"]).tolist() == [[255, 0, 0], [0, 0, 255]]
        points = np.asarray(mesh.points)
        assert np.allclose(points[:, 0].min(), 10.0), "the component transform places the part"
        assert np.allclose(points[:, 1].max(), 1.0), "3MF z (up) becomes y"

    def test_a_dae_with_triangles_polylist_material_node_transform_and_z_up(self, tmp_path):
        path = tmp_path / "scene.dae"
        path.write_bytes(_DAE)
        mesh = core._load_file(str(path), ".dae")
        assert mesh.n_cells == 3, "one triangle plus a quad fanned into two"
        colours = np.asarray(mesh.cell_data["RGB"])
        assert colours[0].tolist() == [0, 255, 0], "the bound material's diffuse colour"
        assert np.allclose(np.asarray(mesh.points)[:, 1].min(), 5.0), "translate z=5, Z_UP turned to Y"


# --------------------------------------------------------------------------------------------------
# Scene expansion bounds (BSC1): each level references the same child twice
# --------------------------------------------------------------------------------------------------

_TRIANGLE_3MF_OBJECT = ('<object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/>'
                        '<vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/></vertices>'
                        '<triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>')


def _fan_out_3mf(path, depth):
    """A 3MF whose object at each level places the level below twice: 2^depth triangles."""
    objects = [_TRIANGLE_3MF_OBJECT] + [
        f'<object id="{level}" type="model"><components><component objectid="{level - 1}"/>'
        f'<component objectid="{level - 1}" transform="1 0 0 0 1 0 0 0 1 {level} 0 0"/></components></object>'
        for level in range(2, depth + 2)]
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
             + "".join(objects) + f'</resources><build><item objectid="{depth + 1}"/></build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("3D/3dmodel.model", model)
    return str(path)


def _fan_out_dae(path, depth, geometry=True):
    """A COLLADA scene whose node at each level instances the level below twice."""
    leaf = '<instance_geometry url="#g"/>' if geometry else ""
    nodes = [f'<node id="n0">{leaf}</node>'] + [
        f'<node id="n{level}"><instance_node url="#n{level - 1}"/><node><translate>{level} 0 0</translate>'
        f'<instance_node url="#n{level - 1}"/></node></node>' for level in range(1, depth + 1)]
    document = f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
 <library_geometries><geometry id="g"><mesh>
  <source id="g-pos"><float_array id="g-arr" count="9">0 0 0 1 0 0 0 1 0</float_array>
   <technique_common><accessor source="#g-arr" count="3" stride="3"/></technique_common></source>
  <vertices id="g-v"><input semantic="POSITION" source="#g-pos"/></vertices>
  <triangles count="1"><input semantic="VERTEX" source="#g-v" offset="0"/><p>0 1 2</p></triangles>
 </mesh></geometry></library_geometries>
 <library_nodes>{"".join(nodes)}</library_nodes>
 <library_visual_scenes><visual_scene id="s"><node><instance_node url="#n{depth}"/></node></visual_scene>
 </library_visual_scenes>
 <scene><instance_visual_scene url="#s"/></scene>
</COLLADA>"""
    path.write_text(document)
    return str(path)


class _Unbounded(BaseException):
    """Raised by a counting wrapper once a walk passes any plausible bound. A BaseException, so a
    loader's ``except Exception`` cannot turn it into a fallback that keeps expanding."""


def _counting(function, limit=50_000):
    calls = [0]

    def wrapper(*args, **kwargs):
        calls[0] += 1
        if calls[0] > limit:
            raise _Unbounded(f"{function.__name__} called more than {limit:,} times: the walk is unbounded")
        return function(*args, **kwargs)

    wrapper.calls = calls
    return wrapper


def _small_budgets(triangles=1_000):
    from preview_pipeline import resources

    return resources.Budgets(memory_bytes=16 * 1024 ** 3, cpus=4, points=1_000_000, triangles=triangles,
                             splats=250_000)


@pytest.mark.unit
@requires_real("pyvista")
class TestSceneExpansion:
    def test_a_3mf_doubling_30_levels_stops_at_the_triangle_budget(self, tmp_path, caplog):
        path = _fan_out_3mf(tmp_path / "fan.3mf", depth=30)
        transform = _counting(xml_mesh_handler._transform)
        with patch.object(xml_mesh_handler, "_transform", transform), caplog.at_level("WARNING"):
            mesh = core._load_file(path, ".3mf", budgets=_small_budgets())
        assert 1_000 < mesh.n_cells <= 1_001, "the placement that crosses the budget is the last one"
        assert transform.calls[0] < 5_000
        assert caplog.text.count("expands past") == 1

    def test_a_dae_doubling_30_levels_stops_at_the_triangle_budget_and_parses_the_geometry_once(
            self, tmp_path, caplog):
        path = _fan_out_dae(tmp_path / "fan.dae", depth=30)
        node_matrix = _counting(xml_mesh_handler._node_matrix)
        geometry = _counting(xml_mesh_handler._geometry_triangles)
        with patch.object(xml_mesh_handler, "_node_matrix", node_matrix), \
                patch.object(xml_mesh_handler, "_geometry_triangles", geometry), caplog.at_level("WARNING"):
            mesh = core._load_file(path, ".dae", budgets=_small_budgets())
        assert 1_000 < mesh.n_cells <= 1_001
        assert node_matrix.calls[0] < 5_000
        assert geometry.calls[0] == 1, "one geometry instanced 2^30 times is parsed once"
        assert caplog.text.count("expands past") == 1

    def test_a_dae_of_nodes_that_place_nothing_stops_at_the_node_bound(self, tmp_path, monkeypatch, caplog):
        from preview_pipeline.format_handlers import scene_limits

        monkeypatch.setattr(scene_limits, "MAX_SCENE_NODES", 2_000)
        path = _fan_out_dae(tmp_path / "empty.dae", depth=40, geometry=False)
        with caplog.at_level("WARNING"), pytest.raises(ValueError, match="no triangle geometry"):
            core._load_file(path, ".dae", budgets=_small_budgets())
        assert "2,000 nodes" in caplog.text

    def test_a_scene_within_the_budget_loads_every_placement(self, tmp_path):
        mesh = core._load_file(_fan_out_3mf(tmp_path / "small.3mf", depth=6), ".3mf",
                               budgets=_small_budgets())
        assert mesh.n_cells == 2 ** 6
        mesh = core._load_file(_fan_out_dae(tmp_path / "small.dae", depth=6), ".dae", budgets=_small_budgets())
        assert mesh.n_cells == 2 ** 6


@pytest.mark.unit
class TestSceneLimit:
    def test_a_placement_that_crosses_the_triangle_bound_is_the_last(self, caplog):
        from preview_pipeline.format_handlers.scene_limits import SceneLimit

        limit = SceneLimit("scene", max_triangles=10)
        with caplog.at_level("WARNING"):
            limit.add(3, 8)
            assert not limit.reached
            limit.add(3, 8)
            assert limit.reached and not limit.visit()
            limit.add(3, 8)
        assert caplog.text.count("expands past 10 triangles") == 1

    def test_vertices_are_bounded_at_three_per_triangle_of_the_limit(self):
        from preview_pipeline.format_handlers.scene_limits import SceneLimit

        limit = SceneLimit("scene", max_triangles=10)
        limit.add(30, 1)
        assert not limit.reached
        limit.add(1, 0)
        assert limit.reached

    def test_the_node_bound_and_a_count_known_in_advance(self, monkeypatch):
        from preview_pipeline.format_handlers import scene_limits

        monkeypatch.setattr(scene_limits, "MAX_SCENE_NODES", 3)
        limit = scene_limits.SceneLimit("scene", max_triangles=10)
        assert [limit.visit() for _ in range(4)] == [True, True, True, False]
        assert scene_limits.SceneLimit("scene").expect(3)
        assert not scene_limits.SceneLimit("scene").expect(4)

    def test_the_default_triangle_bound_is_the_triangle_ceiling(self):
        from preview_pipeline import resources
        from preview_pipeline.format_handlers.scene_limits import SceneLimit

        assert SceneLimit("scene").max_triangles == resources.MAX_TRIANGLES


# --------------------------------------------------------------------------------------------------
# PTS, OFF, DRC colours
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestPts:
    def test_a_counted_pts_block_with_colours(self):
        text = "3\n1 2 3 10 255 0 0\n4 5 6 10 255 0 0\n7 8 9 10 255 0 0\n2\n1 1 1 5 0 255 0\n2 2 2 5 0 255 0\n"
        points, colours = point_streams.read_pts([text.encode()[i:i + 5] for i in range(0, len(text), 5)], 100)
        assert sorted(points[:, 0].tolist()) == [1, 1, 2, 4, 7]
        assert {tuple(c) for c in colours} == {(255, 0, 0), (0, 255, 0)}

    def test_xyz_only_lines(self):
        points, colours = point_streams.read_pts([b"1 2 3\n4 5 6\n"], 100)
        assert points.tolist() == [[1, 2, 3], [4, 5, 6]] and colours is None


@pytest.mark.unit
@requires_real("trimesh", "pyvista")
class TestOff:
    def test_an_off_mesh_loads(self, tmp_path):
        path = tmp_path / "t.off"
        path.write_text("OFF\n4 2 0\n0 0 0\n1 0 0\n0 1 0\n0 0 1\n3 0 1 2\n3 0 1 3\n")
        mesh = core._load_file(str(path), ".off")
        assert mesh.n_cells == 2 and mesh.n_points == 4


@pytest.mark.unit
@requires_real("DracoPy", "pyvista")
class TestDracoColours:
    def test_vertex_colours_are_kept(self, tmp_path):
        import DracoPy

        points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        colours = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], dtype=np.uint8)
        path = tmp_path / "t.drc"
        path.write_bytes(DracoPy.encode(points, faces=np.array([[0, 1, 2]], dtype=np.uint32), colors=colours))
        mesh = core._load_file(str(path), ".drc")
        assert np.asarray(mesh.point_data["RGB"]).tolist() == colours.tolist()


# --------------------------------------------------------------------------------------------------
# Up axis
# --------------------------------------------------------------------------------------------------

class _Data(SimpleNamespace):
    pass


def _data(points, up_axis=None):
    data = _Data(points=np.asarray(points, dtype=float), bounds=None)
    if up_axis:
        data._preview_up_axis = up_axis
    return data


@pytest.mark.unit
class TestUpAxis:
    @pytest.mark.parametrize("ext", [".stl", ".obj", ".ply", ".drc", ".off", ".fbx", ".glb"])
    def test_formats_without_a_declared_z_up_are_rendered_as_authored(self, ext):
        # A car modelled Y-up and long in depth: the old guess turned it onto its side.
        car = _data([[0, 0, 0], [2, 1.5, 4.5]])
        assert core._normalize_up_axis(car, ext).points.tolist() == [[0, 0, 0], [2, 1.5, 4.5]]

    @pytest.mark.parametrize("ext", [".las", ".e57", ".ptx", ".pts", ".pcd"])
    def test_survey_formats_are_z_up(self, ext):
        assert core._normalize_up_axis(_data([[1, 2, 3]]), ext).points.tolist() == [[1, 3, -2]]

    def test_a_declared_z_up_is_honoured(self):
        assert core._normalize_up_axis(_data([[1, 2, 3]], "Z"), ".fbx").points.tolist() == [[1, 3, -2]]

    def test_fbx_up_axis_is_read_from_ascii_and_binary_files(self, tmp_path):
        ascii_fbx = tmp_path / "a.fbx"
        ascii_fbx.write_bytes(b'GlobalSettings:  {\n P: "UpAxis", "int", "Integer", "",2\n P: "UpAxisSign"')
        assert mesh_handler.fbx_up_axis(str(ascii_fbx)) == "Z"
        binary_fbx = tmp_path / "b.fbx"
        binary_fbx.write_bytes(b"Kaydara FBX Binary\x00" + b"\x00" * 20 + b"S\x06\x00\x00\x00UpAxisS\x03\x00\x00\x00int"
                               b"S\x07\x00\x00\x00IntegerS\x00\x00\x00\x00I\x01\x00\x00\x00")
        assert mesh_handler.fbx_up_axis(str(binary_fbx)) == "Y"
        (tmp_path / "c.fbx").write_bytes(b"no settings")
        assert mesh_handler.fbx_up_axis(str(tmp_path / "c.fbx")) is None

    def test_every_new_extension_is_offered(self):
        assert {".pts", ".3mf", ".off", ".dae"} <= core._all_extensions()
