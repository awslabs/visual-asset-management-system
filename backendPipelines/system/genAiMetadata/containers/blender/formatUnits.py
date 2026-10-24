# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The linear unit a 3D file declares, read from its header without a format library.

`sys_geometry.units` labels the numbers this image writes, and on the Blender path those numbers are scene
metres for every format whose declaration the importer converts (glTF is metres by specification; the FBX
importer applies `GlobalSettings.UnitScaleFactor`, the Collada importer matches `<asset><unit meter>` to the
scene, the USD importer multiplies by the stage's `metersPerUnit`). The declaration itself is still what the
author wrote, so `declared_units` reads it cheaply and it lands under `sys_format`: the COLLADA
`<asset><unit meter="…">` element, the FBX `UnitScaleFactor` property (binary or ASCII), the `metersPerUnit`
layer metadata of a `.usda` or of the `.usda` root layer inside a `.usdz`. A binary `.usdc` is not parsed
here; the Blender script reads the composed stage through Blender's bundled `pxr` module and hands the value
over in the scene facts (`stage_meters_per_unit`).
"""

import math
import re
import struct
import zipfile

# The only unit strings the promotion catalogue accepts; anything else is omitted.
UNIT_LABELS = ("m", "mm", "cm", "in", "ft")
METERS_PER_UNIT_LABELS = ((1.0, "m"), (0.001, "mm"), (0.01, "cm"), (0.0254, "in"), (0.3048, "ft"))

GLTF_EXTENSIONS = (".glb", ".gltf")
USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
HEADER_SCAN_BYTES = 1024 * 1024

# Where a declaration came from, recorded beside it.
SOURCE_SPECIFICATION = "specification"  # the format fixes the unit (glTF: metres)
SOURCE_HEADER = "header"                # authored in the file's header
SOURCE_DEFAULT = "default"              # not authored; the format's specified fallback applies
SOURCE_STAGE = "stage"                  # read from the composed USD stage inside Blender (pxr)

# The formats' specified fallbacks when the header authors nothing.
USD_DEFAULT_METERS_PER_UNIT = 0.01      # USD: the metersPerUnit fallback is centimetres
COLLADA_DEFAULT_METERS_PER_UNIT = 1.0   # COLLADA: <unit> defaults to meter="1.0"
FBX_DEFAULT_UNIT_SCALE_FACTOR = 1.0     # FBX: UnitScaleFactor defaults to 1 (centimetres)
FBX_UNIT_SCALE_TO_METERS = 0.01         # FBX's base linear unit is the centimetre

FBX_BINARY_MAGIC = b"Kaydara FBX Binary  \x00"
FBX_ASCII_MARKER = b"FBXHeaderExtension"
_FBX_PROPERTY_NAME = b"UnitScaleFactor"
_FBX_NAME_PREFIX = b"S" + struct.pack("<I", len(_FBX_PROPERTY_NAME))

_USDA_METERS_PER_UNIT = re.compile(rb"\bmetersPerUnit\s*=\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)")
_DAE_UNIT = re.compile(rb"<unit\b[^>]*?\bmeter\s*=\s*\"([^\"]+)\"")
_FBX_ASCII_UNIT_SCALE = re.compile(
    rb"P:\s*\"UnitScaleFactor\"\s*,\s*\"double\"\s*,\s*\"Number\"\s*,\s*\"\"\s*,\s*([-+0-9.eE]+)")


def units_from_meters_per_unit(meters_per_unit):
    """The unit label for a metres-per-unit value, or None when it is not one of the five."""
    if isinstance(meters_per_unit, bool):
        return None
    try:
        value = float(meters_per_unit)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0.0:
        return None
    for reference, label in METERS_PER_UNIT_LABELS:
        if math.isclose(value, reference, rel_tol=1e-6):
            return label
    return None


def _unknown():
    return {"metersPerUnit": None, "units": None, "source": None}


def _declaration(meters_per_unit, source):
    return {
        "metersPerUnit": float(meters_per_unit),
        "units": units_from_meters_per_unit(meters_per_unit),
        "source": source,
    }


def _head(path, limit=HEADER_SCAN_BYTES):
    with open(path, "rb") as handle:
        return handle.read(limit)


def usda_meters_per_unit(data):
    """(metersPerUnit, source) from usda text, or None when the bytes are not a usda layer."""
    if not data.lstrip().startswith(b"#usda"):
        return None
    match = _USDA_METERS_PER_UNIT.search(data)
    if match:
        return float(match.group(1)), SOURCE_HEADER
    return USD_DEFAULT_METERS_PER_UNIT, SOURCE_DEFAULT


def dae_meters_per_unit(data):
    """(metersPerUnit, source) from a COLLADA document's <asset><unit meter> element, or None when the
    bytes are not COLLADA."""
    if b"<COLLADA" not in data:
        return None
    match = _DAE_UNIT.search(data)
    if match is None:
        return COLLADA_DEFAULT_METERS_PER_UNIT, SOURCE_DEFAULT
    try:
        return float(match.group(1)), SOURCE_HEADER
    except ValueError:
        return None


def fbx_unit_scale_factor(data):
    """(UnitScaleFactor, source) from an FBX header, binary or ASCII, or None when the bytes are not FBX."""
    if data.startswith(FBX_BINARY_MAGIC):
        idx = data.find(_FBX_PROPERTY_NAME)
        while idx != -1:
            # The GlobalSettings record is S"UnitScaleFactor" S"double" S"Number" S"" D<value>; the length
            # prefix check keeps the same name inside "OriginalUnitScaleFactor" from matching.
            if data[idx - len(_FBX_NAME_PREFIX):idx] == _FBX_NAME_PREFIX:
                pos = idx + len(_FBX_PROPERTY_NAME)
                try:
                    for expected in (b"double", b"Number", b""):
                        if data[pos:pos + 1] != b"S":
                            raise ValueError
                        length = struct.unpack("<I", data[pos + 1:pos + 5])[0]
                        if data[pos + 5:pos + 5 + length] != expected:
                            raise ValueError
                        pos += 5 + length
                    if data[pos:pos + 1] != b"D":
                        raise ValueError
                    return struct.unpack("<d", data[pos + 1:pos + 9])[0], SOURCE_HEADER
                except (ValueError, struct.error):
                    pass
            idx = data.find(_FBX_PROPERTY_NAME, idx + 1)
        return FBX_DEFAULT_UNIT_SCALE_FACTOR, SOURCE_DEFAULT
    if FBX_ASCII_MARKER in data:
        match = _FBX_ASCII_UNIT_SCALE.search(data)
        if match is None:
            return FBX_DEFAULT_UNIT_SCALE_FACTOR, SOURCE_DEFAULT
        try:
            return float(match.group(1)), SOURCE_HEADER
        except ValueError:
            return None
    return None


def _usd_declaration(path, extension):
    if extension == ".usdz":
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if not names:
                    return _unknown()
                # The usdz root layer is the first member of the archive.
                with archive.open(names[0]) as member:
                    data = member.read(HEADER_SCAN_BYTES)
        except (zipfile.BadZipFile, OSError, KeyError):
            return _unknown()
    else:
        data = _head(path)
    parsed = usda_meters_per_unit(data)
    if parsed is None:
        return _unknown()  # binary crate (.usdc): only the Blender-side stage read covers it
    return _declaration(*parsed)


def declared_units(path, extension, stage_meters_per_unit=None):
    """{metersPerUnit, units, source} for the unit `path` declares; every value None when the format
    carries none or the header cannot be read. `stage_meters_per_unit` is the value the Blender script read
    from the composed USD stage; it wins for USD extensions (and is the only source for a binary .usdc)."""
    extension = (extension or "").lower()
    if extension in GLTF_EXTENSIONS:
        return _declaration(1.0, SOURCE_SPECIFICATION)
    if extension in USD_EXTENSIONS and isinstance(stage_meters_per_unit, (int, float)) \
            and not isinstance(stage_meters_per_unit, bool):
        return _declaration(stage_meters_per_unit, SOURCE_STAGE)
    try:
        if extension in USD_EXTENSIONS:
            return _usd_declaration(path, extension)
        if extension == ".dae":
            parsed = dae_meters_per_unit(_head(path))
            return _declaration(*parsed) if parsed else _unknown()
        if extension == ".fbx":
            parsed = fbx_unit_scale_factor(_head(path))
            if parsed is None:
                return _unknown()
            return _declaration(parsed[0] * FBX_UNIT_SCALE_TO_METERS, parsed[1])
    except OSError:
        return _unknown()
    return _unknown()
