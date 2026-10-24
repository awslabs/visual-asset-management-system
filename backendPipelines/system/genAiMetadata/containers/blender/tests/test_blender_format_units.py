# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`formatUnits.declared_units`: the unit a file declares, read from its header, one synthetic input per
format branch. The label is one of the five accepted unit strings or None -- never a guess -- a format whose
specification fixes a fallback reports that fallback with source "default", and bytes that are not what the
extension claims are unknown rather than defaulted.
"""

import struct
import zipfile

import pytest

import formatUnits

USDA_CM = (b'#usda 1.0\n(\n    defaultPrim = "World"\n    metersPerUnit = 0.01\n    upAxis = "Z"\n)\n\n'
           b'def Xform "World"\n{\n}\n')
USDA_UNAUTHORED = b'#usda 1.0\n(\n    upAxis = "Y"\n)\n'
USDC_HEAD = b"PXR-USDC" + b"\x00" * 56

DAE_HEAD = (b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n<asset>\n')
DAE_TAIL = b'<up_axis>Z_UP</up_axis>\n</asset>\n<library_geometries/>\n</COLLADA>\n'


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def _s(value):
    return b"S" + struct.pack("<I", len(value)) + value


def _d(value):
    return b"D" + struct.pack("<d", value)


def _fbx_binary(unit_scale=None, original=None):
    """A binary FBX head: magic, version, then the GlobalSettings Properties70 records the importer reads.
    `original` is written FIRST so a parser that matches the name inside OriginalUnitScaleFactor is caught."""
    body = formatUnits.FBX_BINARY_MAGIC + struct.pack("<I", 7400) + b"\x00" * 13 + b"GlobalSettings" + b"Properties70"
    if original is not None:
        body += b"P" + _s(b"OriginalUnitScaleFactor") + _s(b"double") + _s(b"Number") + _s(b"") + _d(original)
    if unit_scale is not None:
        body += b"P" + _s(b"UnitScaleFactor") + _s(b"double") + _s(b"Number") + _s(b"") + _d(unit_scale)
    return body + b"\x00" * 32


FBX_ASCII = (b"; FBX 7.4.0 project file\nFBXHeaderExtension:  {\n\tFBXHeaderVersion: 1003\n}\n"
             b"GlobalSettings:  {\n\tVersion: 1000\n\tProperties70:  {\n"
             b'\t\tP: "UpAxis", "int", "Integer", "",1\n'
             b'\t\tP: "OriginalUnitScaleFactor", "double", "Number", "",100\n'
             b'\t\tP: "UnitScaleFactor", "double", "Number", "",2.54\n'
             b"\t}\n}\n")


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    (1.0, "m"), (0.001, "mm"), (0.01, "cm"), (0.0254, "in"), (0.3048, "ft"),
    (1.0000001, "m"),                       # within the relative tolerance
    (0.5, None), (0.0, None), (-1.0, None), (float("inf"), None), ("many", None), (None, None), (True, None),
])
def test_labels_for_meters_per_unit(value, expected):
    assert formatUnits.units_from_meters_per_unit(value) == expected
    assert expected is None or expected in formatUnits.UNIT_LABELS


@pytest.mark.unit
@pytest.mark.parametrize("extension", [".glb", ".gltf"])
def test_gltf_is_metres_by_specification(tmp_path, extension):
    # No header is read: glTF defines its linear unit, so the path need not even exist.
    declared = formatUnits.declared_units(str(tmp_path / f"absent{extension}"), extension)
    assert declared == {"metersPerUnit": 1.0, "units": "m", "source": "specification"}


@pytest.mark.unit
def test_usda_authored_meters_per_unit(tmp_path):
    path = _write(tmp_path, "model.usda", USDA_CM)
    assert formatUnits.declared_units(path, ".usda") == {"metersPerUnit": 0.01, "units": "cm", "source": "header"}


@pytest.mark.unit
def test_usda_without_meters_per_unit_is_centimetres_by_the_usd_default(tmp_path):
    path = _write(tmp_path, "model.usda", USDA_UNAUTHORED)
    assert formatUnits.declared_units(path, ".usda") == {"metersPerUnit": 0.01, "units": "cm", "source": "default"}


@pytest.mark.unit
def test_usd_extension_is_sniffed_for_text(tmp_path):
    path = _write(tmp_path, "model.usd", USDA_CM.replace(b"0.01", b"1"))
    assert formatUnits.declared_units(path, ".usd") == {"metersPerUnit": 1.0, "units": "m", "source": "header"}


@pytest.mark.unit
@pytest.mark.parametrize("extension", [".usdc", ".usd"])
def test_binary_crate_is_unknown_without_the_stage_value(tmp_path, extension):
    path = _write(tmp_path, f"model{extension}", USDC_HEAD)
    assert formatUnits.declared_units(path, extension) == {"metersPerUnit": None, "units": None, "source": None}


@pytest.mark.unit
def test_usdz_reads_the_root_layer(tmp_path):
    path = str(tmp_path / "model.usdz")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model.usda", USDA_CM)
        archive.writestr("textures/wood.png", b"\x89PNG")
    assert formatUnits.declared_units(path, ".usdz") == {"metersPerUnit": 0.01, "units": "cm", "source": "header"}


@pytest.mark.unit
def test_usdz_with_a_crate_root_layer_is_unknown(tmp_path):
    path = str(tmp_path / "model.usdz")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model.usdc", USDC_HEAD)
        archive.writestr("sub.usda", USDA_CM)  # not the root layer; must not be consulted
    assert formatUnits.declared_units(path, ".usdz") == {"metersPerUnit": None, "units": None, "source": None}


@pytest.mark.unit
def test_usdz_that_is_not_a_zip_is_unknown(tmp_path):
    path = _write(tmp_path, "model.usdz", b"not a zip archive")
    assert formatUnits.declared_units(path, ".usdz") == {"metersPerUnit": None, "units": None, "source": None}


@pytest.mark.unit
def test_stage_value_wins_for_usd(tmp_path):
    path = _write(tmp_path, "model.usdc", USDC_HEAD)
    declared = formatUnits.declared_units(path, ".usdc", stage_meters_per_unit=0.0254)
    assert declared == {"metersPerUnit": 0.0254, "units": "in", "source": "stage"}
    text = _write(tmp_path, "model.usda", USDA_CM)
    assert formatUnits.declared_units(text, ".usda", stage_meters_per_unit=1)["source"] == "stage"


@pytest.mark.unit
def test_stage_value_is_ignored_for_non_usd(tmp_path):
    path = _write(tmp_path, "room.dae", DAE_HEAD + b'<unit meter="0.01" name="centimeter"/>\n' + DAE_TAIL)
    assert formatUnits.declared_units(path, ".dae", stage_meters_per_unit=1.0)["source"] == "header"


@pytest.mark.unit
@pytest.mark.parametrize("stage", [True, "0.01", None])
def test_stage_value_must_be_a_number(tmp_path, stage):
    path = _write(tmp_path, "model.usdc", USDC_HEAD)
    assert formatUnits.declared_units(path, ".usdc", stage_meters_per_unit=stage)["source"] is None


@pytest.mark.unit
@pytest.mark.parametrize("meter,label,value", [
    (b"0.01", "cm", 0.01), (b"1", "m", 1.0), (b"1.0", "m", 1.0), (b"0.0254", "in", 0.0254),
    (b"0.3048", "ft", 0.3048), (b"0.001", "mm", 0.001), (b"0.5", None, 0.5),
])
def test_dae_unit_meter(tmp_path, meter, label, value):
    path = _write(tmp_path, "room.dae", DAE_HEAD + b'<unit meter="' + meter + b'" name="unit"/>\n' + DAE_TAIL)
    assert formatUnits.declared_units(path, ".dae") == {"metersPerUnit": value, "units": label, "source": "header"}


@pytest.mark.unit
def test_dae_without_unit_element_is_metres_by_the_collada_default(tmp_path):
    path = _write(tmp_path, "room.dae", DAE_HEAD + DAE_TAIL)
    assert formatUnits.declared_units(path, ".dae") == {"metersPerUnit": 1.0, "units": "m", "source": "default"}


@pytest.mark.unit
def test_dae_attribute_order_does_not_matter(tmp_path):
    path = _write(tmp_path, "room.dae", DAE_HEAD + b'<unit name="inch" meter="0.0254" />\n' + DAE_TAIL)
    assert formatUnits.declared_units(path, ".dae")["units"] == "in"


@pytest.mark.unit
def test_dae_that_is_not_collada_is_unknown(tmp_path):
    path = _write(tmp_path, "room.dae", b'<?xml version="1.0"?><scene><unit meter="0.01"/></scene>')
    assert formatUnits.declared_units(path, ".dae") == {"metersPerUnit": None, "units": None, "source": None}


@pytest.mark.unit
@pytest.mark.parametrize("unit_scale,label,value", [
    (1.0, "cm", 0.01), (100.0, "m", 1.0), (2.54, "in", 0.0254), (30.48, "ft", 0.3048), (0.1, "mm", 0.001),
])
def test_fbx_binary_unit_scale_factor(tmp_path, unit_scale, label, value):
    path = _write(tmp_path, "rig.fbx", _fbx_binary(unit_scale=unit_scale))
    declared = formatUnits.declared_units(path, ".fbx")
    assert declared["units"] == label and declared["source"] == "header"
    assert declared["metersPerUnit"] == pytest.approx(value)


@pytest.mark.unit
def test_fbx_binary_skips_the_original_unit_scale_factor(tmp_path):
    path = _write(tmp_path, "rig.fbx", _fbx_binary(unit_scale=1.0, original=100.0))
    assert formatUnits.declared_units(path, ".fbx")["units"] == "cm", "OriginalUnitScaleFactor (100 = m) must not win"


@pytest.mark.unit
def test_fbx_ascii_unit_scale_factor(tmp_path):
    path = _write(tmp_path, "rig.fbx", FBX_ASCII)
    declared = formatUnits.declared_units(path, ".fbx")
    assert declared["units"] == "in" and declared["source"] == "header"
    assert declared["metersPerUnit"] == pytest.approx(0.0254)


@pytest.mark.unit
@pytest.mark.parametrize("body", [
    formatUnits.FBX_BINARY_MAGIC + struct.pack("<I", 7400) + b"\x00" * 64,
    b"; FBX 7.4.0 project file\nFBXHeaderExtension:  {\n}\nGlobalSettings:  {\n}\n",
])
def test_fbx_header_without_the_property_is_centimetres_by_the_fbx_default(tmp_path, body):
    path = _write(tmp_path, "rig.fbx", body)
    assert formatUnits.declared_units(path, ".fbx") == {"metersPerUnit": 0.01, "units": "cm", "source": "default"}


@pytest.mark.unit
def test_fbx_without_magic_or_marker_is_unknown(tmp_path):
    path = _write(tmp_path, "rig.fbx", b"UnitScaleFactor 100 -- not an FBX file")
    assert formatUnits.declared_units(path, ".fbx") == {"metersPerUnit": None, "units": None, "source": None}


@pytest.mark.unit
@pytest.mark.parametrize("extension", [".obj", ".stl", ".ply", ".abc", ".blend"])
def test_unitless_formats_are_unknown(tmp_path, extension):
    # These formats define no linear unit; the header is not even opened, so an absent file is fine too.
    assert formatUnits.declared_units(str(tmp_path / f"model{extension}"), extension) == \
        {"metersPerUnit": None, "units": None, "source": None}


@pytest.mark.unit
def test_missing_file_is_unknown_not_a_raise(tmp_path):
    assert formatUnits.declared_units(str(tmp_path / "absent.dae"), ".dae") == \
        {"metersPerUnit": None, "units": None, "source": None}


@pytest.mark.unit
def test_every_unknown_is_a_fresh_dict(tmp_path):
    first = formatUnits.declared_units(str(tmp_path / "a.obj"), ".obj")
    first["units"] = "mutated"
    assert formatUnits.declared_units(str(tmp_path / "b.obj"), ".obj")["units"] is None
