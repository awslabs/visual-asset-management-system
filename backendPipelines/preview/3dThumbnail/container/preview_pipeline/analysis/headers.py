# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Header-only readers for formats whose attributes come from a header rather than from loading the
geometry: PLY (and its mesh / Gaussian-splat / point-cloud disambiguation), the splat containers
(.splat, .spz, .sog) and PTX. Pure Python with no numpy, so they run in either image and in tests."""

import gzip
import json
import os
import struct
import zipfile

# A PLY vertex element carrying spherical-harmonic colour is a Gaussian splat, not a point cloud.
PLY_SPLAT_PROPERTY = "f_dc_0"
# f_rest_* coefficient counts per spherical-harmonic degree (3 colour channels x (degree+1)^2 - 3).
_PLY_SH_REST_COUNTS = {0: 0, 9: 1, 24: 2, 45: 3}

# .splat: position 3 x f32, scale 3 x f32, colour 4 x u8, rotation 4 x u8 per splat.
SPLAT_RECORD_BYTES = 32

# .spz, two layouts, told apart by the first bytes (nianticlabs/spz README, "Format detection"):
#   versions 1-3: a gzip stream (1f 8b) whose first 16 bytes are magic u32, version u32, numPoints u32,
#     shDegree u8, fractionalBits u8, flags u8, reserved u8;
#   version 4: a 32-byte plaintext NgspFileHeader (the same first 15 fields, then numStreams u8,
#     tocByteOffset u32 and 12 reserved bytes) before independently ZSTD-compressed attribute streams.
# All fields little-endian; the magic spells "NGSP" in file order.
SPZ_MAGIC = 0x5053474E
SPZ_HEADER_BYTES = 16
SPZ_V4_HEADER_BYTES = 32
SPZ_V4_MIN_VERSION = 4
_GZIP_MAGIC = b"\x1f\x8b"
_SPZ_MAGIC_BYTES = struct.pack("<I", SPZ_MAGIC)

# A SOG meta.json is a few kilobytes of JSON read whole into memory; one larger than this, as a bundle member
# (by its central-directory size, before decompressing) or as a bare file, is refused.
SOG_META_MAX_BYTES = 16 * 1024 ** 2


class HeaderError(ValueError):
    """The file does not carry the header the reader expects."""


def read_ply_header(path: str) -> dict:
    """Parse the ASCII header of a PLY file (either encoding of the body).

    Returns ``{format, version, elements: {name: {count, properties: [name, ...]}}, headerBytes}``. A
    list property contributes its own name (``vertex_indices``), not its count/index types."""
    with open(path, "rb") as handle:
        first = handle.readline()
        if first.strip() != b"ply":
            raise HeaderError("not a PLY file (missing 'ply' magic line)")
        header_bytes = len(first)
        fmt, version, elements, current = "", "", {}, None
        while True:
            line = handle.readline()
            if not line:
                raise HeaderError("PLY header has no end_header line")
            header_bytes += len(line)
            parts = line.decode("ascii", "replace").strip().split()
            if not parts or parts[0] in ("comment", "obj_info"):
                continue
            if parts[0] == "format" and len(parts) >= 3:
                fmt, version = parts[1], parts[2]
            elif parts[0] == "element" and len(parts) >= 3:
                current = {"count": int(parts[2]), "properties": []}
                elements[parts[1]] = current
            elif parts[0] == "property" and current is not None and len(parts) >= 3:
                current["properties"].append(parts[-1])
            elif parts[0] == "end_header":
                break
    return {"format": fmt, "version": version, "elements": elements, "headerBytes": header_bytes}


def ply_kind(header: dict) -> str:
    """``mesh`` when faces are declared, ``splat`` when the vertex element carries spherical-harmonic
    colour, ``pointcloud`` otherwise."""
    elements = header.get("elements") or {}
    if (elements.get("face") or {}).get("count", 0) > 0:
        return "mesh"
    if PLY_SPLAT_PROPERTY in (elements.get("vertex") or {}).get("properties", []):
        return "splat"
    return "pointcloud"


def sniff_ply(path: str) -> str:
    return ply_kind(read_ply_header(path))


def ply_sh_degree(properties):
    """The spherical-harmonic degree a splat PLY stores, from its f_rest_* count; None when the count
    matches no degree."""
    rest = sum(1 for name in properties if str(name).startswith("f_rest_"))
    return _PLY_SH_REST_COUNTS.get(rest)


def read_splat_header(path: str) -> dict:
    size = os.path.getsize(path)
    if size == 0 or size % SPLAT_RECORD_BYTES:
        raise HeaderError(f".splat size {size} is not a multiple of the {SPLAT_RECORD_BYTES}-byte record")
    return {"points": size // SPLAT_RECORD_BYTES, "recordBytes": SPLAT_RECORD_BYTES}


def read_spz_header(path: str) -> dict:
    """The header of an .spz in either layout: the gzip-wrapped stream of versions 1-3, or the plaintext
    ``NGSP`` header of version 4 (ZSTD streams, which are never decompressed here). Anything else is a
    HeaderError rather than a gzip error."""
    with open(path, "rb") as handle:
        lead = handle.read(SPZ_V4_HEADER_BYTES)
    if lead[:4] == _SPZ_MAGIC_BYTES:
        if len(lead) < SPZ_V4_HEADER_BYTES:
            raise HeaderError(".spz file is shorter than its version 4 header")
        _magic, version, points, sh_degree, fractional_bits, flags, _streams, _toc = struct.unpack(
            "<IIIBBBBI", lead[:20])
        if version < SPZ_V4_MIN_VERSION:
            raise HeaderError(f".spz plaintext header names version {version}; versions 1-3 are gzip streams")
        return {"version": version, "points": points, "shDegree": sh_degree,
                "fractionalBits": fractional_bits, "antialiased": bool(flags & 1)}
    if lead[:2] != _GZIP_MAGIC:
        raise HeaderError(".spz file is neither a gzip stream (versions 1-3) nor an NGSP header (version 4)")
    try:
        with gzip.open(path, "rb") as handle:
            raw = handle.read(SPZ_HEADER_BYTES)
    except (OSError, EOFError) as exc:
        raise HeaderError(f".spz gzip stream could not be read: {exc}") from exc
    if len(raw) < SPZ_HEADER_BYTES:
        raise HeaderError(".spz stream is shorter than its header")
    magic, version, points, sh_degree, fractional_bits, flags, _reserved = struct.unpack("<IIIBBBB", raw)
    if magic != SPZ_MAGIC:
        raise HeaderError(f".spz magic 0x{magic:08x} does not match 0x{SPZ_MAGIC:08x}")
    return {"version": version, "points": points, "shDegree": sh_degree,
            "fractionalBits": fractional_bits, "antialiased": bool(flags & 1)}


def read_sog_meta(path: str) -> dict:
    """The ``meta.json`` of a SOG bundle (a zip) or of a bare SOG ``meta.json`` written as the file. The
    meta.json is checked against SOG_META_MAX_BYTES, and a bundle member also against the zip-bomb ratio
    (``utils/archive.check_member``), before it is read."""
    from ..utils.archive import ArchiveLimitError, check_member

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            meta_name = next((n for n in names if n.rsplit("/", 1)[-1] == "meta.json"), None)
            if meta_name is None:
                raise HeaderError("SOG bundle carries no meta.json")
            info = archive.getinfo(meta_name)
            try:
                check_member(info, SOG_META_MAX_BYTES)
            except ArchiveLimitError as exc:
                raise HeaderError(f"SOG meta.json was not read: {exc}") from exc
            meta = json.loads(archive.read(info).decode("utf-8"))
            members = len(names)
    else:
        size = os.path.getsize(path)
        if size > SOG_META_MAX_BYTES:
            raise HeaderError(f"SOG meta.json was not read: it is {size:,} bytes, over the "
                              f"{SOG_META_MAX_BYTES:,}-byte limit")
        with open(path, "rb") as handle:
            try:
                meta = json.loads(handle.read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise HeaderError(f"SOG file is neither a zip bundle nor a meta.json: {exc}") from exc
        members = 1
    if not isinstance(meta, dict):
        raise HeaderError("SOG meta.json is not an object")
    return {"points": int(meta.get("count", 0) or 0), "version": meta.get("version"),
            "hasHigherOrderSh": "shN" in meta, "members": members}


def read_ptx_header(path: str) -> dict:
    """The column and row counts a PTX scan declares in its first two lines."""
    with open(path, "rb") as handle:
        try:
            columns = int(handle.readline().strip())
            rows = int(handle.readline().strip())
        except ValueError as exc:
            raise HeaderError(
                "Invalid PTX header: the first two lines must be the column and row counts") from exc
    return {"columns": columns, "rows": rows, "points": columns * rows}
