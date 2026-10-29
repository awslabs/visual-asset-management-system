# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`meshAttributes`: the trimesh-derived sys_* sections, the merge with Blender's scene facts, the declared
unit under sys_format, and the promotion source contract the metadata catalogue reads.

Real trimesh on generated meshes (a box, a two-box scene, an open box) rather than fakes: the assertions are
about what trimesh reports for a known shape, and the workstation carries trimesh (`pytest.importorskip`).
Failure paths are the load-bearing half -- an unloadable file must produce a WARNING, never a raise and never
an `extraction_error` value stored as attribute data. The contract tests are durable: the metadata
catalogue reads the promotion source keys by name, and a rename here stays writable forever.
"""

import json

import pytest

trimesh = pytest.importorskip("trimesh")

import meshAttributes  # noqa: E402  (after importorskip so a missing trimesh skips rather than errors)


@pytest.fixture
def box_stl(tmp_path):
    path = str(tmp_path / "box.stl")
    trimesh.creation.box(extents=[1.0, 2.0, 3.0]).export(path)
    return path


@pytest.fixture
def two_box_glb(tmp_path):
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(extents=[1.0, 1.0, 1.0]), node_name="a", geom_name="ga")
    second = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    second.apply_translation([5.0, 0.0, 0.0])
    scene.add_geometry(second, node_name="b", geom_name="gb")
    path = str(tmp_path / "two.glb")
    scene.export(path)
    return path


@pytest.fixture
def open_box_obj(tmp_path):
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    path = str(tmp_path / "open.obj")
    trimesh.Trimesh(vertices=box.vertices, faces=box.faces[:-1], process=False).export(path)
    return path


# The scene facts renderScene.py writes for a two-box scene, including the measurement, flag and unit keys
# the Blender path contributes to the promotion source contract.
FACTS = {
    "schemaVersion": 1,
    "blenderVersion": "4.5.13",
    "meshObjects": 2,
    "objectCounts": {"MESH": 2, "EMPTY": 1},
    "vertices": 16,
    "faces": 12,
    "triangles": 24,
    "boundsMin": [-0.5, -0.5, -0.5],
    "boundsMax": [5.5, 0.5, 0.5],
    "dimensions": {"width": 6.0, "height": 1.0, "depth": 1.0},
    "surfaceArea": 12.0,
    "volume": 2.0,
    "watertight": True,
    "materials": 1,
    "materialNames": ["Grey"],
    "images": 0,
    "hasUv": False,
    "hasVertexColors": True,
    "hasArmature": False,
    "hasAnimation": True,
    "upAxis": "Z",
    "units": "m",
}

# The promotion source contract -- the keys the metadata catalogue reads from the groups this image writes.
# Spelled out here rather than imported, so a rename in meshAttributes.py cannot hide behind its own constant.
REGISTRY_PROMOTION_SOURCE_KEYS = {
    "sys_geometry": {"boundsMin", "boundsMax", "dimensions", "extentMax", "volume", "surfaceArea", "units"},
    "sys_statistics": {"meshCount", "vertices", "faces", "triangles", "watertight"},
    "sys_visual": {"materialCount", "textureCount", "hasUv", "hasVertexColors"},
    "sys_scene": {"nodeCount", "hasAnimation", "hasArmature"},
}


def _fold(key):
    return key.replace("_", "").lower()


@pytest.mark.unit
def test_single_mesh_sections(box_stl):
    attributes, warnings = meshAttributes.compute_trimesh_attributes(box_stl, ".stl")
    assert warnings == []
    assert set(attributes) == {"sys_geometry", "sys_statistics", "sys_format", "sys_visual", "sys_scene"}
    geometry = attributes["sys_geometry"]
    assert geometry["boundsMin"] == [-0.5, -1.0, -1.5]
    assert geometry["boundsMax"] == [0.5, 1.0, 1.5]
    assert geometry["dimensions"] == {"width": 1.0, "height": 2.0, "depth": 3.0}
    assert geometry["extentMax"] == 3.0
    assert geometry["centroid"] == [0.0, 0.0, 0.0]
    assert geometry["volume"] == pytest.approx(6.0)
    assert geometry["surfaceArea"] == pytest.approx(22.0)
    assert "units" not in geometry, "no unit guesses for formats that do not define one"
    statistics = attributes["sys_statistics"]
    assert statistics == {
        "meshCount": 1, "vertices": 8, "faces": 12, "triangles": 12, "watertight": True, "isVolume": True}
    assert attributes["sys_format"] == {"extension": ".stl", "loader": "trimesh", "loadedType": "Trimesh"}
    visual = attributes["sys_visual"]
    assert visual["materialCount"] == 0 and visual["hasVertexColors"] is False and visual["hasUv"] is False
    assert attributes["sys_scene"] == {"nodeCount": 1, "geometryNodeCount": 1, "geometryNames": []}


@pytest.mark.unit
def test_scene_sections_aggregate_every_geometry(two_box_glb):
    attributes, warnings = meshAttributes.compute_trimesh_attributes(two_box_glb, ".glb")
    assert warnings == []
    geometry = attributes["sys_geometry"]
    assert geometry["boundsMin"] == [-0.5, -0.5, -0.5]
    assert geometry["boundsMax"] == [5.5, 0.5, 0.5]
    assert geometry["units"] == "m"
    assert attributes["sys_statistics"]["meshCount"] == 2
    assert attributes["sys_statistics"]["faces"] == 24
    assert attributes["sys_format"]["loadedType"] == "Scene"
    scene = attributes["sys_scene"]
    assert scene["geometryNodeCount"] == 2
    assert scene["geometryNames"] == ["ga", "gb"]
    assert scene["nodeCount"] >= 3


@pytest.mark.unit
def test_open_mesh_has_no_volume_and_is_not_watertight(open_box_obj):
    attributes, warnings = meshAttributes.compute_trimesh_attributes(open_box_obj, ".obj")
    assert warnings == []
    geometry, statistics = attributes["sys_geometry"], attributes["sys_statistics"]
    assert "volume" not in geometry, "an open mesh has no defined volume; the promotion omits ext_volume"
    assert geometry["surfaceArea"] == pytest.approx(5.5)
    assert statistics["watertight"] is False and statistics["faces"] == statistics["triangles"] == 11


@pytest.mark.unit
def test_ply_reports_its_vertex_properties(tmp_path):
    path = str(tmp_path / "box.ply")
    trimesh.creation.box(extents=[1.0, 1.0, 1.0]).export(path)
    attributes, _warnings = meshAttributes.compute_trimesh_attributes(path, ".ply")
    assert set(attributes["sys_format"]["vertexProperties"]) >= {"x", "y", "z"}


@pytest.mark.unit
def test_unloadable_file_yields_a_warning_not_a_raise(tmp_path):
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb at all")
    attributes, warnings = meshAttributes.compute_trimesh_attributes(str(bad), ".glb")
    assert attributes == {}
    assert len(warnings) == 1 and "could not load" in warnings[0] and "bad.glb" in warnings[0]


@pytest.mark.unit
@pytest.mark.parametrize("extension", [".fbx", ".usdz", ".blend", ".dae"])
def test_blender_only_extension_yields_no_warning(tmp_path, extension):
    # trimesh has no loader for these in this image (Collada would need pycollada), the Blender scene facts
    # describe them, and asking is by design -- so no warning reaches the manifest or analysis-summary.json.
    # `test_unloadable_file_yields_a_warning_not_a_raise` (a corrupt .glb) is the positive control.
    assert extension not in meshAttributes.TRIMESH_EXTENSIONS
    assert meshAttributes.compute_trimesh_attributes(str(tmp_path / f"model{extension}"), extension) == ({}, [])


@pytest.mark.unit
def test_missing_file_yields_a_warning_not_a_raise(tmp_path):
    attributes, warnings = meshAttributes.compute_trimesh_attributes(str(tmp_path / "absent.obj"), ".obj")
    assert attributes == {}
    assert len(warnings) == 1


@pytest.mark.unit
def test_no_error_text_is_ever_stored_as_attribute_data(box_stl):
    attributes, _warnings = meshAttributes.compute_trimesh_attributes(box_stl, ".stl")
    merged = meshAttributes.merge_scene_facts(attributes, FACTS, ".stl")
    assert "extraction_error" not in json.dumps(merged)


@pytest.mark.unit
def test_merge_fills_every_missing_section_from_blender_facts():
    merged = meshAttributes.merge_scene_facts({}, FACTS, ".fbx")
    assert set(merged) == {"sys_geometry", "sys_statistics", "sys_format", "sys_visual", "sys_scene"}
    assert merged["sys_geometry"] == {
        "boundsMin": [-0.5, -0.5, -0.5], "boundsMax": [5.5, 0.5, 0.5],
        "dimensions": {"width": 6.0, "height": 1.0, "depth": 1.0}, "extentMax": 6.0, "centroid": [2.5, 0.0, 0.0],
        "upAxis": "Z", "surfaceArea": 12.0, "volume": 2.0, "units": "m",
    }
    assert merged["sys_statistics"] == {
        "meshCount": 2, "vertices": 16, "faces": 12, "triangles": 24, "watertight": True}
    assert merged["sys_format"] == {"extension": ".fbx", "loader": "blender"}
    assert merged["sys_visual"] == {"materialCount": 1, "materialNames": ["Grey"], "textureCount": 0,
                                    "hasUv": False, "hasVertexColors": True}
    assert merged["sys_scene"]["objectCounts"] == {"MESH": 2, "EMPTY": 1}
    assert merged["sys_scene"]["nodeCount"] == 3, "every scene object counts as a node when trimesh saw none"
    assert merged["sys_scene"]["hasAnimation"] is True
    assert merged["sys_scene"]["blenderVersion"] == "4.5.13"
    assert "metersPerUnit" not in merged["sys_scene"], "only a USD stage carries metersPerUnit"


@pytest.mark.unit
def test_merge_keeps_trimesh_sections_and_adds_blender_scene_keys(box_stl):
    attributes, _warnings = meshAttributes.compute_trimesh_attributes(box_stl, ".stl")
    merged = meshAttributes.merge_scene_facts(attributes, FACTS, ".stl")
    # trimesh measured the real file; the facts describe a different (two-box) scene and must not override it.
    assert merged["sys_geometry"]["dimensions"] == {"width": 1.0, "height": 2.0, "depth": 3.0}
    assert "units" not in merged["sys_geometry"], "the facts' unit labels Blender's numbers, not trimesh's"
    assert merged["sys_statistics"]["meshCount"] == 1
    assert merged["sys_format"]["loader"] == "trimesh"
    assert merged["sys_scene"]["nodeCount"] == 1
    assert merged["sys_scene"]["objectCounts"] == {"MESH": 2, "EMPTY": 1}
    assert merged["sys_scene"]["hasArmature"] is False
    # The input is not mutated.
    assert "objectCounts" not in attributes["sys_scene"]


@pytest.mark.unit
def test_merge_carries_the_usd_stage_meters_per_unit():
    facts = dict(FACTS, metersPerUnit=0.01, metersPerUnitAuthored=True)
    scene = meshAttributes.merge_scene_facts({}, facts, ".usdc")["sys_scene"]
    assert scene["metersPerUnit"] == 0.01 and scene["metersPerUnitAuthored"] is True


@pytest.mark.unit
@pytest.mark.parametrize("facts", [None, {}, {"schemaVersion": 1, "error": "SceneImportError: no faces"}])
def test_merge_without_usable_facts_returns_an_equal_copy(facts):
    attributes = {"sys_geometry": {"extentMax": 1.0}}
    merged = meshAttributes.merge_scene_facts(attributes, facts, ".obj")
    assert merged == attributes
    assert merged is not attributes and merged["sys_geometry"] is not attributes["sys_geometry"]


@pytest.mark.unit
@pytest.mark.parametrize("attributes,declared,expected_format", [
    ({"sys_format": {"extension": ".dae", "loader": "blender"}},
     {"metersPerUnit": 0.01, "units": "cm", "source": "header"},
     {"extension": ".dae", "loader": "blender", "declaredMetersPerUnit": 0.01, "declaredUnits": "cm",
      "declaredUnitsSource": "header"}),
    ({"sys_format": {"extension": ".dae", "loader": "blender"}},
     {"metersPerUnit": 0.5, "units": None, "source": "header"},
     {"extension": ".dae", "loader": "blender", "declaredMetersPerUnit": 0.5, "declaredUnitsSource": "header"}),
    ({"sys_format": {"extension": ".obj", "loader": "trimesh"}},
     {"metersPerUnit": None, "units": None, "source": None},
     {"extension": ".obj", "loader": "trimesh"}),
    ({}, {"metersPerUnit": 1.0, "units": "m", "source": "default"},
     {"extension": ".dae", "declaredMetersPerUnit": 1.0, "declaredUnits": "m", "declaredUnitsSource": "default"}),
])
def test_apply_declared_units_records_the_declaration_under_sys_format(attributes, declared, expected_format):
    merged = meshAttributes.apply_declared_units(attributes, declared, ".dae")
    assert merged["sys_format"] == expected_format
    assert "sys_geometry" not in merged, "the declaration never invents a sys_geometry.units"
    assert merged is not attributes


@pytest.mark.unit
def test_module_constant_is_the_registry_contract():
    assert {k: set(v) for k, v in meshAttributes.PROMOTION_SOURCE_KEYS.items()} == REGISTRY_PROMOTION_SOURCE_KEYS


@pytest.mark.unit
@pytest.mark.parametrize("path", ["trimesh+blender", "blender"])
def test_promotion_source_keys_are_written_under_their_registry_names(two_box_glb, path):
    # Durable: the metadata catalogue reads exactly these names; a rename stays writable forever.
    if path == "blender":
        merged = meshAttributes.merge_scene_facts({}, FACTS, ".fbx")
    else:
        attributes, _warnings = meshAttributes.compute_trimesh_attributes(two_box_glb, ".glb")
        merged = meshAttributes.merge_scene_facts(attributes, FACTS, ".glb")
    for group, contract in REGISTRY_PROMOTION_SOURCE_KEYS.items():
        missing = contract - set(merged[group])
        assert not missing, f"{group} lacks contract keys {sorted(missing)} on the {path} path"
        for key in merged[group]:
            twins = [c for c in contract if _fold(c) == _fold(key) and c != key]
            assert not twins, f"{group}.{key} is a respelling of contract key {twins[0]}"
    geometry = merged["sys_geometry"]
    assert set(geometry["dimensions"]) == {"width", "height", "depth"}
    assert len(geometry["boundsMin"]) == len(geometry["boundsMax"]) == 3
    assert geometry["units"] in ("m", "mm", "cm", "in", "ft")
    assert all(isinstance(merged["sys_statistics"][k], int) for k in ("meshCount", "vertices", "faces", "triangles"))
    assert all(isinstance(merged["sys_visual"][k], bool) for k in ("hasUv", "hasVertexColors"))
    assert all(isinstance(merged["sys_scene"][k], bool) for k in ("hasAnimation", "hasArmature"))
