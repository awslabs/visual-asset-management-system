#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""COLLADA (``.dae``) on the analysis path of the 3D render image.

The file loads through the thumbnail pipeline's hardened COLLADA loader (``xml_mesh_handler``), with the
scene walk bounded by the task's triangle budget, and its declared ``<asset><unit meter>`` is recorded the
way the Blender image records it. The thumbnail pipeline's own ``.dae`` path (``core._load_file``) is the
reference: the analysis path must produce the same geometry from the same file."""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy", reason="the dispatcher moves numpy point arrays")

from conftest import requires_real  # noqa: E402
from preview_pipeline import core, resources  # noqa: E402
from preview_pipeline.analysis import analysis, attributes  # noqa: E402
from preview_pipeline.format_handlers import xml_mesh_handler  # noqa: E402

_HEAD = b'<?xml version="1.0" encoding="utf-8"?>\n<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n'
_GEOMETRY = b"""<library_geometries><geometry id="g"><mesh>
 <source id="g-pos"><float_array id="g-arr" count="9">0 0 0 200 0 0 0 100 0</float_array>
  <technique_common><accessor source="#g-arr" count="3" stride="3"/></technique_common></source>
 <vertices id="g-v"><input semantic="POSITION" source="#g-pos"/></vertices>
 <triangles count="1"><input semantic="VERTEX" source="#g-v" offset="0"/><p>0 1 2</p></triangles>
</mesh></geometry></library_geometries>
<library_visual_scenes><visual_scene id="s"><node id="n"><instance_geometry url="#g"/></node></visual_scene>
</library_visual_scenes><scene><instance_visual_scene url="#s"/></scene>
</COLLADA>
"""


def _dae(tmp_path, asset=b"<asset><up_axis>Y_UP</up_axis></asset>", name="model.dae", body=_GEOMETRY):
    path = tmp_path / name
    path.write_bytes(_HEAD + asset + b"\n" + body)
    return str(path)


def _centimetre_asset():
    return b'<asset><unit name="centimeter" meter="0.01"/><up_axis>Y_UP</up_axis></asset>'


def _polydata():
    points = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    return SimpleNamespace(points=points, n_points=3, n_cells=1, point_data={})


@pytest.fixture
def render_stack():
    """The display, up-axis and renderer seams, patched so no X server or VTK is needed."""
    with patch.object(analysis.display, "ensure_display") as ensure, \
            patch.object(analysis.core, "normalize_up_axis", side_effect=lambda data, ext: data) as up, \
            patch.object(analysis.renderer, "generate_still_frames",
                         return_value=[np.zeros((4, 4, 3), dtype=np.uint8)] * 2) as stills:
        yield SimpleNamespace(ensure=ensure, up=up, stills=stills)


@pytest.mark.unit
class TestColladaRoute:
    def test_dae_is_renderable_and_not_an_unloadable_container(self):
        assert ".dae" in analysis.RENDERABLE_EXTENSIONS
        assert ".dae" not in analysis.UNLOADABLE_MESH_EXTENSIONS | analysis.ATTRIBUTES_ONLY_EXTENSIONS
        assert analysis.COLLADA_EXTENSIONS <= xml_mesh_handler.SUPPORTED_EXTENSIONS

    def test_dae_loads_through_the_hardened_loader_within_the_task_budget(self, tmp_path, render_stack):
        path = _dae(tmp_path, _centimetre_asset())
        budgets = resources.Budgets(memory_bytes=10 * 1024 ** 3, cpus=6, points=1, triangles=1234, splats=1)
        with patch.object(analysis.resources, "compute_budgets", return_value=budgets), \
                patch.object(analysis.xml_mesh_handler, "load", return_value=_polydata()) as load, \
                patch.object(analysis.mesh_handler, "load") as trimesh_load:
            result = analysis.analyze_local_file(path, ".dae", n_views=2)
        load.assert_called_once_with(path, ".dae", max_triangles=1234)
        trimesh_load.assert_not_called()
        assert result.loader == analysis.COLLADA_LOADER == "expat"
        assert result.warnings == [] and result.render_skipped is None
        assert len(result.frames) == 2
        render_stack.up.assert_called_once()
        assert render_stack.up.call_args.args[1] == ".dae"
        assert render_stack.stills.call_args.kwargs["use_full_bounds"] is False
        assert result.attributes[attributes.SYS_STATISTICS]["vertices"] == 3
        assert result.attributes[attributes.SYS_GEOMETRY]["units"] == "cm"
        assert result.attributes[attributes.SYS_FORMAT] == {
            "extension": ".dae", "loader": "expat", "details": {},
            "declaredMetersPerUnit": 0.01, "declaredUnits": "cm", "declaredUnitsSource": "header"}
        assert result.facts["geometry"].startswith("bounding box 2.000 x 1.000 x 0.000 cm")

    def test_an_undecidable_unit_leaves_the_geometry_unlabelled(self, tmp_path, render_stack):
        path = _dae(tmp_path, b'<asset><unit meter="not-a-number"/></asset>')
        with patch.object(analysis.xml_mesh_handler, "load", return_value=_polydata()):
            result = analysis.analyze_local_file(path, ".dae", render=False)
        assert result.attributes[attributes.SYS_GEOMETRY]["units"] is None
        assert result.attributes[attributes.SYS_FORMAT] == {"extension": ".dae", "loader": "expat", "details": {}}

    def test_a_dae_with_an_external_entity_fails_without_reading_it(self, tmp_path, render_stack):
        path = tmp_path / "evil.dae"
        path.write_bytes(b'<?xml version="1.0"?>\n<!DOCTYPE COLLADA [<!ENTITY secret SYSTEM "file:///etc/passwd">]>\n'
                         b"<COLLADA><asset><up_axis>&secret;</up_axis></asset></COLLADA>")
        with patch("builtins.open", wraps=open) as opened:
            with pytest.raises(xml_mesh_handler.UnsafeXmlError):
                analysis.analyze_local_file(str(path), ".dae")
        assert all("/etc/passwd" not in str(call.args[0]) for call in opened.call_args_list)
        render_stack.stills.assert_not_called()


@pytest.mark.unit
@requires_real("pyvista")
class TestColladaGeometry:
    def test_the_analysis_path_loads_the_same_geometry_as_the_thumbnail(self, tmp_path):
        path = _dae(tmp_path, b'<asset><unit meter="0.01"/><up_axis>Z_UP</up_axis></asset>')
        reference = core._load_file(path, ".dae")
        loaded = []
        original = xml_mesh_handler.load

        def load(*args, **kwargs):
            loaded.append(original(*args, **kwargs))
            return loaded[-1]

        with patch.object(analysis.xml_mesh_handler, "load", side_effect=load):
            result = analysis.analyze_local_file(path, ".dae", render=False)
        assert np.array_equal(np.asarray(loaded[0].points), np.asarray(reference.points))
        assert np.array_equal(np.asarray(loaded[0].faces), np.asarray(reference.faces))
        statistics = result.attributes[attributes.SYS_STATISTICS]
        assert (statistics["vertices"], statistics["faces"]) == (reference.n_points, reference.n_cells)
        # The loader keeps the authored numbers (centimetres here) and turns Z-up into Y-up.
        assert result.attributes[attributes.SYS_GEOMETRY]["dimensions"] == {"width": 200.0, "height": 0.0,
                                                                            "depth": 100.0}
        assert result.attributes[attributes.SYS_GEOMETRY]["units"] == "cm"

    def test_a_dae_doubling_30_levels_stops_at_the_task_triangle_budget(self, tmp_path, caplog):
        levels = 30
        nodes = ['<node id="n0"><instance_geometry url="#g"/></node>'] + [
            f'<node id="n{level}"><instance_node url="#n{level - 1}"/><node><translate>{level} 0 0</translate>'
            f'<instance_node url="#n{level - 1}"/></node></node>' for level in range(1, levels + 1)]
        body = (_GEOMETRY.split(b"<library_visual_scenes>")[0]
                + b"<library_nodes>" + "".join(nodes).encode() + b"</library_nodes>"
                + b'<library_visual_scenes><visual_scene id="s"><node><instance_node url="#n'
                + str(levels).encode() + b'"/></node></visual_scene></library_visual_scenes>'
                + b'<scene><instance_visual_scene url="#s"/></scene></COLLADA>')
        path = _dae(tmp_path, b"<asset/>", name="fan.dae", body=body)
        budgets = resources.Budgets(memory_bytes=16 * 1024 ** 3, cpus=4, points=1_000_000, triangles=1_000,
                                    splats=250_000)
        with patch.object(analysis.resources, "compute_budgets", return_value=budgets), caplog.at_level("WARNING"):
            result = analysis.analyze_local_file(path, ".dae", render=False)
        assert 1_000 < result.attributes[attributes.SYS_STATISTICS]["faces"] <= 1_001
        assert caplog.text.count("expands past") == 1


@pytest.mark.unit
class TestColladaDeclaredUnit:
    @pytest.mark.parametrize("unit,expected", [
        (b'<unit name="centimeter" meter="0.01"/>', (0.01, "header")),
        (b'<unit name="inch" meter="0.0254"/>', (0.0254, "header")),
        (b'<unit meter="1"/>', (1.0, "header")),
        (b'<unit meter="0.5"/>', (0.5, "header")),
        (b'<unit name="unit"/>', (1.0, "default")),
        (b"", (1.0, "default")),
    ])
    def test_the_root_asset_unit(self, tmp_path, unit, expected):
        path = _dae(tmp_path, b"<asset><contributor><author>a</author></contributor>" + unit
                    + b"<up_axis>Z_UP</up_axis></asset>")
        assert attributes.collada_declared_unit(path) == expected

    @pytest.mark.parametrize("unit,groups", [
        (b'<unit meter="0.001"/>', {"sys_geometry": {"units": "mm"}, "sys_format": {
            "declaredMetersPerUnit": 0.001, "declaredUnits": "mm", "declaredUnitsSource": "header"}}),
        (b'<unit meter="0.3048"/>', {"sys_geometry": {"units": "ft"}, "sys_format": {
            "declaredMetersPerUnit": 0.3048, "declaredUnits": "ft", "declaredUnitsSource": "header"}}),
        (b'<unit meter="0.5"/>', {"sys_geometry": {"units": None}, "sys_format": {
            "declaredMetersPerUnit": 0.5, "declaredUnitsSource": "header"}}),
        (b"", {"sys_geometry": {"units": "m"}, "sys_format": {
            "declaredMetersPerUnit": 1.0, "declaredUnits": "m", "declaredUnitsSource": "default"}}),
    ])
    def test_unit_groups_match_the_blender_images_keys(self, tmp_path, unit, groups):
        assert attributes.collada_unit_attributes(_dae(tmp_path, b"<asset>" + unit + b"</asset>")) == groups

    def test_a_namespace_prefix_is_read(self, tmp_path):
        path = tmp_path / "prefixed.dae"
        path.write_bytes(b'<?xml version="1.0"?><c:COLLADA xmlns:c="http://www.collada.org/2005/11/COLLADASchema">'
                         b'<c:asset><c:unit meter="0.0254"/></c:asset></c:COLLADA>')
        assert attributes.collada_declared_unit(str(path)) == (0.0254, "header")

    def test_a_unit_on_a_nested_asset_is_not_the_documents(self, tmp_path):
        body = _GEOMETRY.replace(b'<node id="n">', b'<node id="n"><asset><unit meter="0.01"/></asset>')
        assert attributes.collada_declared_unit(_dae(tmp_path, b"<asset/>", body=body)) == (1.0, "default")

    def test_a_root_without_a_leading_asset_takes_the_default(self, tmp_path):
        assert attributes.collada_declared_unit(_dae(tmp_path, b"")) == (1.0, "default")

    @pytest.mark.parametrize("meter", [b"abc", b"-1", b"0", b"inf", b"nan", b""])
    def test_an_unusable_value_is_unknown(self, tmp_path, meter):
        path = _dae(tmp_path, b'<asset><unit meter="' + meter + b'"/></asset>')
        assert attributes.collada_declared_unit(path) is None
        assert attributes.collada_unit_attributes(path) == {}

    def test_a_document_that_is_not_collada_is_unknown(self, tmp_path):
        path = tmp_path / "scene.dae"
        path.write_bytes(b'<?xml version="1.0"?><scene><asset><unit meter="0.01"/></asset></scene>')
        assert attributes.collada_declared_unit(str(path)) is None

    def test_a_dtd_is_refused_before_any_entity_is_read(self, tmp_path):
        path = tmp_path / "evil.dae"
        path.write_bytes(b'<?xml version="1.0"?>\n<!DOCTYPE COLLADA [<!ENTITY secret SYSTEM "file:///etc/passwd">]>\n'
                         b'<COLLADA><asset><unit meter="&secret;"/></asset></COLLADA>')
        with patch("builtins.open", wraps=open) as opened:
            assert attributes.collada_declared_unit(str(path)) is None
        assert all("/etc/passwd" not in str(call.args[0]) for call in opened.call_args_list)

    def test_the_scan_stops_at_the_unit(self, tmp_path):
        # Everything after the unit is unparsable; the answer is decided before it is read.
        path = _dae(tmp_path, b'<asset><unit meter="0.01"/>', body=b"<<<not xml" * 1000)
        assert attributes.collada_declared_unit(path) == (0.01, "header")

    def test_malformed_xml_before_the_unit_is_unknown(self, tmp_path):
        path = tmp_path / "broken.dae"
        path.write_bytes(b"<COLLADA><asset><<unit meter='0.01'/></asset></COLLADA>")
        assert attributes.collada_declared_unit(str(path)) is None

    def test_a_unit_past_the_scan_window_is_unknown(self, tmp_path):
        padding = b"<contributor><comments>" + b"x" * 4096 + b"</comments></contributor>"
        path = _dae(tmp_path, b"<asset>" + padding + b'<unit meter="0.01"/></asset>')
        with patch.object(attributes, "COLLADA_UNIT_SCAN_BYTES", 1024):
            assert attributes.collada_declared_unit(path) is None
        assert attributes.collada_declared_unit(path) == (0.01, "header")

    def test_an_unreadable_file_is_unknown(self, tmp_path):
        assert attributes.collada_declared_unit(os.path.join(str(tmp_path), "absent.dae")) is None
