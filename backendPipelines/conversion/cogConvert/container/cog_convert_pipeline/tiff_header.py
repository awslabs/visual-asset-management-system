# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Header-only reading of a TIFF: size, layout, georeferencing and the shared "is a COG" check.

This is the container's copy of the definition written in the raster contract
(planning/feature-cog-raster-map/specs/raster-contract.md section 1.2). The SYSTEM metadata extractor
(Python), this container and the web viewer (TypeScript) cannot share code, so each carries the same
definition and the same fixtures. Only `struct` is used: the file is never decoded and GDAL is not
opened until the file has passed this check.

`is_cog` is conservative: tiled, at least one overview when a side exceeds 512, every directory and tag
array before the first tile's data, and ascending sampled tile offsets. A false positive means a file is
skipped, a false negative means a harmless re-conversion.

The conversion pipeline additionally needs the CRS: a COG that is not in EPSG:3857 is still converted,
because the map viewer only draws Web Mercator COGs (`needs_conversion`).
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAX_IFDS = 64
MAX_IFD_ENTRIES = 4096
TAG_ARRAY_MAX_BYTES = 16 * 1024 * 1024
MAX_DIMENSION = 4_000_000
COG_OVERVIEW_MIN_DIMENSION = 512
COG_OFFSET_SAMPLES = 4096

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
_STRUCT_CODES = {1: "B", 2: "B", 3: "H", 4: "I", 6: "b", 7: "B", 8: "h", 9: "i", 11: "f", 12: "d", 13: "I",
                 16: "Q", 17: "q", 18: "Q"}

TAG_NEW_SUBFILE_TYPE = 254
TAG_WIDTH, TAG_LENGTH, TAG_BITS = 256, 257, 258
TAG_COMPRESSION = 259
TAG_STRIP_OFFSETS = 273
TAG_SAMPLES_PER_PIXEL = 277
TAG_TILE_WIDTH, TAG_TILE_LENGTH, TAG_TILE_OFFSETS = 322, 323, 324
TAG_MODEL_PIXEL_SCALE, TAG_MODEL_TIEPOINT, TAG_MODEL_TRANSFORMATION = 33550, 33922, 34264
TAG_GEO_KEY_DIRECTORY = 34735

_COMPRESSION_NAMES = {1: "none", 5: "lzw", 6: "jpeg-old", 7: "jpeg", 8: "deflate", 32946: "deflate",
                      32773: "packbits", 34712: "jpeg2000", 34887: "lerc", 50000: "zstd", 50001: "webp",
                      34925: "lzma"}

_GT_MODEL_TYPE, _GEOGRAPHIC_TYPE, _PROJECTED_CS_TYPE = 1024, 2048, 3072
_USER_DEFINED = 32767
_MODEL_PROJECTED, _MODEL_GEOGRAPHIC, _MODEL_GEOCENTRIC = 1, 2, 3
# The EPSG codes GDAL and the GeoTIFF files in circulation use for Web Mercator.
WEB_MERCATOR_CODES = (3857, 900913, 3785)


class HeaderRejected(ValueError):
    """The bytes are not a TIFF this check reads, or they break one of its limits (the message says which)."""


@dataclass
class _Ifd:
    offset: int
    end: int
    # tag -> (type, count, inline scalar value or None, file position of the value bytes)
    entries: Dict[int, Tuple[int, int, Optional[int], int]] = field(default_factory=dict)

    def scalar(self, tag: int) -> Optional[int]:
        entry = self.entries.get(tag)
        return entry[2] if entry and entry[1] == 1 else None


@dataclass
class RasterHeader:
    width: int
    height: int
    band_count: int
    bits_per_sample: int
    big_tiff: bool
    tiled: bool
    overview_count: int
    compression: str
    georeferenced: bool
    crs: str  # "EPSG:n", "user-defined", "geocentric" or "none"
    epsg: Optional[int]
    is_cog: bool
    raw_bytes: int  # width * height * bands * bytes per sample, uncompressed

    @property
    def is_web_mercator(self) -> bool:
        return self.epsg in WEB_MERCATOR_CODES


def _read_at(handle, offset: int, length: int) -> bytes:
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise HeaderRejected("the TIFF directory runs past the end of the file")
    return data


def _open_walk(handle, size: int):
    handle.seek(0)
    head = handle.read(16)
    if len(head) < 8 or head[:2] not in (b"II", b"MM"):
        raise HeaderRejected("not a TIFF file (no byte-order mark)")
    order = "<" if head[:2] == b"II" else ">"
    magic = struct.unpack(order + "H", head[2:4])[0]
    if magic == 42:
        return order, False, struct.unpack(order + "I", head[4:8])[0]
    if magic == 43:
        if len(head) < 16 or struct.unpack(order + "HH", head[4:8]) != (8, 0):
            raise HeaderRejected("not a BigTIFF file (bad offset size)")
        return order, True, struct.unpack(order + "Q", head[8:16])[0]
    raise HeaderRejected("not a TIFF file (bad magic number)")


def _walk_ifds(handle, size: int, order: str, big: bool, first: int) -> Tuple[List[_Ifd], bool]:
    ifds: List[_Ifd] = []
    seen = set()
    offset = first
    truncated = False
    count_fmt, count_size, entry_size, next_size = ("Q", 8, 20, 8) if big else ("H", 2, 12, 4)
    head_size = 12 if big else 8
    inline = 8 if big else 4
    offset_fmt = "Q" if big else "I"
    while offset:
        if offset in seen:
            raise HeaderRejected("the TIFF directory chain loops")
        if len(ifds) >= MAX_IFDS:
            truncated = True
            break
        if offset < 8 or offset + count_size > size:
            raise HeaderRejected("a TIFF directory offset is outside the file")
        seen.add(offset)
        count = struct.unpack(order + count_fmt, _read_at(handle, offset, count_size))[0]
        if count == 0 or count > MAX_IFD_ENTRIES:
            raise HeaderRejected(f"a TIFF directory declares {count} entries (limit {MAX_IFD_ENTRIES})")
        table_end = offset + count_size + count * entry_size + next_size
        if table_end > size:
            raise HeaderRejected("a TIFF directory runs past the end of the file")
        table = _read_at(handle, offset + count_size, count * entry_size + next_size)
        ifd = _Ifd(offset=offset, end=table_end)
        for index in range(count):
            raw = table[index * entry_size:(index + 1) * entry_size]
            tag, dtype, n = struct.unpack(order + ("HHQ" if big else "HHI"), raw[:head_size])
            width = _TYPE_SIZES.get(dtype)
            if width is None:
                continue
            value_raw = raw[head_size:head_size + inline]
            nbytes = n * width
            if nbytes > inline:
                position = struct.unpack(order + offset_fmt, value_raw)[0]
                if position + nbytes > size:
                    raise HeaderRejected(f"tag {tag} stores {nbytes} bytes outside the file")
                ifd.end = max(ifd.end, position + nbytes)
                ifd.entries[tag] = (dtype, n, None, position)
            else:
                inline_at = offset + count_size + index * entry_size + head_size
                value = None
                if n == 1 and dtype in _STRUCT_CODES:
                    value = struct.unpack(order + _STRUCT_CODES[dtype], value_raw[:width])[0]
                ifd.entries[tag] = (dtype, n, value, inline_at)
        ifds.append(ifd)
        offset = struct.unpack(order + offset_fmt, table[count * entry_size:count * entry_size + next_size])[0]
    if not ifds:
        raise HeaderRejected("the TIFF has no directory")
    return ifds, truncated


def _array(handle, order: str, ifd: _Ifd, tag: int, limit: int = TAG_ARRAY_MAX_BYTES) -> List[float]:
    """The values of an array tag; [] when the tag is absent. Refuses an array over `limit` bytes."""
    entry = ifd.entries.get(tag)
    if entry is None:
        return []
    dtype, n, _value, position = entry
    width = _TYPE_SIZES[dtype]
    if n * width > limit:
        raise HeaderRejected(f"tag {tag} stores {n * width} bytes (limit {limit})")
    data = _read_at(handle, position, n * width)
    if dtype == 2:
        return []
    if dtype in (5, 10):
        code = "I" if dtype == 5 else "i"
        pairs = struct.unpack(order + code * (2 * n), data)
        return [pairs[i] / pairs[i + 1] if pairs[i + 1] else 0.0 for i in range(0, len(pairs), 2)]
    return list(struct.unpack(order + _STRUCT_CODES[dtype] * n, data))


def _geo_keys(handle, order: str, ifd: _Ifd) -> Dict[int, int]:
    values = _array(handle, order, ifd, TAG_GEO_KEY_DIRECTORY, 256 * 1024)
    keys: Dict[int, int] = {}
    # Header of four shorts, then four shorts per key: (id, tiff tag location, count, value or offset).
    for index in range(4, len(values) - 3, 4):
        key_id, location, _count, value = (int(v) for v in values[index:index + 4])
        if location == 0:
            keys[key_id] = value
    return keys


def _crs_of(keys: Dict[int, int]) -> Tuple[str, Optional[int]]:
    model = keys.get(_GT_MODEL_TYPE)
    projected = keys.get(_PROJECTED_CS_TYPE)
    geographic = keys.get(_GEOGRAPHIC_TYPE)
    if model == _MODEL_GEOCENTRIC:
        return "geocentric", None
    if model == _MODEL_PROJECTED or (projected is not None and model != _MODEL_GEOGRAPHIC):
        if projected is not None and projected != _USER_DEFINED and projected > 0:
            return f"EPSG:{projected}", projected
        return ("user-defined", None) if keys else ("none", None)
    if geographic is not None and geographic != _USER_DEFINED and geographic > 0:
        return f"EPSG:{geographic}", geographic
    if model is not None:
        return "user-defined", None
    return "none", None


def _sampled(values: List[int]) -> List[int]:
    if len(values) <= COG_OFFSET_SAMPLES:
        return values
    step = len(values) / COG_OFFSET_SAMPLES
    return [values[int(i * step)] for i in range(COG_OFFSET_SAMPLES)] + [values[-1]]


def _is_cog(handle, order: str, ifds: List[_Ifd], truncated: bool, width: int, height: int,
            overview_count: int) -> bool:
    first = ifds[0]
    if truncated or TAG_TILE_WIDTH not in first.entries:
        return False
    if max(width, height) > COG_OVERVIEW_MIN_DIMENSION and overview_count < 1:
        return False
    first_data = None
    for ifd in ifds:
        tag = TAG_TILE_OFFSETS if TAG_TILE_OFFSETS in ifd.entries else TAG_STRIP_OFFSETS
        offsets = [int(v) for v in _array(handle, order, ifd, tag) if v]
        if not offsets:
            return False
        sample = _sampled(offsets)
        if any(later < earlier for earlier, later in zip(sample, sample[1:])):
            return False
        low = min(offsets)
        first_data = low if first_data is None else min(first_data, low)
    return first_data is not None and max(ifd.end for ifd in ifds) <= first_data


def read_header(path: str) -> RasterHeader:
    """The header facts of the TIFF at `path`. Raises HeaderRejected for bytes that are not a usable TIFF."""
    import os

    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        order, big, first = _open_walk(handle, size)
        ifds, truncated = _walk_ifds(handle, size, order, big, first)
        main = ifds[0]
        width, height = main.scalar(TAG_WIDTH), main.scalar(TAG_LENGTH)
        if not width or not height or width > MAX_DIMENSION or height > MAX_DIMENSION:
            raise HeaderRejected(f"the image size is not usable (width {width}, height {height}; "
                                 f"each side must be 1 to {MAX_DIMENSION:,} pixels)")
        bands = main.scalar(TAG_SAMPLES_PER_PIXEL) or 1
        bits = [int(b) for b in _array(handle, order, main, TAG_BITS, 4096)] or [8]
        bits_per_sample = max(bits)
        overviews = 0
        for ifd in ifds[1:]:
            subfile = ifd.scalar(TAG_NEW_SUBFILE_TYPE)
            if subfile is not None and subfile & 1:
                overviews += 1
        keys = _geo_keys(handle, order, main)
        crs, epsg = _crs_of(keys)
        has_transform = (TAG_MODEL_TRANSFORMATION in main.entries
                         or (TAG_MODEL_TIEPOINT in main.entries and TAG_MODEL_PIXEL_SCALE in main.entries))
        # A transform alone does not georeference a file: with no CRS named there is nothing to place on Earth.
        georeferenced = has_transform and crs != "none"
        compression = _COMPRESSION_NAMES.get(int(main.scalar(TAG_COMPRESSION) or 1), "unknown")
        cog = _is_cog(handle, order, ifds, truncated, width, height, overviews)
    return RasterHeader(
        width=width, height=height, band_count=int(bands), bits_per_sample=bits_per_sample, big_tiff=big,
        tiled=TAG_TILE_WIDTH in main.entries, overview_count=overviews, compression=compression,
        georeferenced=georeferenced, crs=crs, epsg=epsg, is_cog=cog,
        raw_bytes=width * height * int(bands) * max(1, bits_per_sample // 8))


def needs_conversion(header: RasterHeader) -> Tuple[bool, str]:
    """(convert, reason). A file is left alone when it is not georeferenced (nothing to draw on a map) or when it
    is already a COG in EPSG:3857, the one CRS the map viewer draws. A COG in any other CRS is converted."""
    if not header.georeferenced:
        return False, "the file is not georeferenced, so there is nothing to convert for a map"
    if header.is_cog and header.is_web_mercator:
        return False, "the file is already a Cloud Optimized GeoTIFF in EPSG:3857"
    if header.is_cog:
        return True, f"the file is a COG in {header.crs}; the map viewer draws EPSG:3857 only"
    return True, "the file is a georeferenced TIFF that is not a COG"
