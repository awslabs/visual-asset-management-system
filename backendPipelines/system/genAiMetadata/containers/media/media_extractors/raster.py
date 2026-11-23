# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GeoTIFF and Cloud Optimized GeoTIFF (`.tif`, `.tiff`): the raster's facts, its CRS and its WGS84 footprint ->
`sys_raster` and `sys_geo`. Only the TIFF header is read, by ranged GETs of the object (`s3_ranged.RangedS3Reader`):
no pixel is read or decoded, so a multi-gigabyte raster costs a few requests and no download.

Reads are bounded. A walk over the file's IFD chain (the header's directory entries, struct only) runs first and
enforces four limits before `tifffile` decodes anything: `MAX_IFDS` directories, `MAX_IFD_ENTRIES` entries in one,
`TAG_ARRAY_MAX_BYTES` for any single array `tifffile` would load whole (the strip or tile offset and byte-count
arrays, strings, the palette-free sample tags), and `HEADER_READ_BUDGET_BYTES` for all of them together. A file
whose arrays exceed a limit (one row per strip over a huge image) is described from the walk alone: `layout` and
`offsetsTruncated: true`, no CRS and no footprint. A file the walk or `tifffile` cannot read yields `other` with a
warning. Neither result is georeferenced, so the handler gives such a `.tif` or `.tiff` the `image` path and keeps no
`sys_raster` (`handler.py`, `is_georeferenced`).

`isCog` is a header-only structural check, written down once for the three runtimes that need it (this extractor,
the TIFF-to-COG pipeline's container and the web viewer):

1. the first image is tiled;
2. when either dimension is over `COG_OVERVIEW_MIN_DIMENSION` pixels there is at least one reduced-resolution image;
3. every IFD and every out-of-line tag value lies before the first byte of image data (the lowest tile offset of
   any image);
4. the tile offsets of each image are ascending.

It is conservative: a false positive means conversion is skipped, a false negative means a harmless conversion.

A file named `<source>.cog.tif` whose `<source>` object exists beside it is a derived COG (the TIFF-to-COG pipeline's
output). It keeps `sys_raster` and `sys_geo`, and is marked `derivedCog` so the metadata catalog writes no
`location` for it: the source file's footprint is the one search shows.
"""

import logging
import math
import os
import struct
from typing import Dict, List, Optional, Tuple

from .common import CLASS_RASTER, BranchResult, ExtractContext, human_count, other_fallback
from .geo_reproject import extent_to_wgs84_polygon

MAX_IFDS = 64
MAX_IFD_ENTRIES = 4096
TAG_ARRAY_MAX_BYTES = 4 * 1024 * 1024
HEADER_READ_BUDGET_BYTES = 8 * 1024 * 1024
# The ranged reader's own bound for one raster: the header budget plus the directory tables and the read-ahead.
RANGED_READ_MAX_BYTES = 2 * HEADER_READ_BUDGET_BYTES
RANGED_READ_MAX_GETS = 64
MAX_DIMENSION = 4_000_000
COG_OVERVIEW_MIN_DIMENSION = 512
# Tile offsets inspected per image for the ascending check; an image with more tiles is checked on this many,
# evenly spread.
COG_OFFSET_SAMPLES = 4096
DERIVED_COG_SUFFIX = ".cog.tif"

# The tags tifffile loads whole when it reads a directory (its TIFF.TAG_LOAD), plus the tags it reads eagerly
# for a page's geometry. Their value sizes are what the walk bounds.
_EAGER_TAGS = frozenset({258, 270, 273, 277, 279, 282, 283, 305, 324, 325, 330, 338, 339, 347, 513, 514, 530,
                         33628, 42113, 50838, 50839})
# The georeferencing tags tifffile loads on first access (ModelPixelScale, ModelTiepoint, ModelTransformation, the three
# GeoKey tags). A real file holds a few hundred bytes in each; one is refused above GEO_TAG_MAX_BYTES, and every one
# counts against the header read budget like an eager tag.
_GEO_TAGS = frozenset({33550, 33922, 34264, 34735, 34736, 34737})
GEO_TAG_MAX_BYTES = 256 * 1024
_TAG_WIDTH, _TAG_LENGTH, _TAG_COMPRESSION = 256, 257, 259
_TAG_STRIP_OFFSETS, _TAG_TILE_OFFSETS, _TAG_GEOKEYS = 273, 324, 34735
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
_COMPRESSION_NAMES = {1: "none", 5: "lzw", 6: "jpeg-old", 7: "jpeg", 8: "deflate", 32946: "deflate", 32773: "packbits",
                      34712: "jpeg2000", 34887: "lerc", 50000: "zstd", 50001: "webp", 34925: "lzma"}

# GeoKey ids and values (GeoTIFF 1.1).
_GT_MODEL_TYPE, _GT_RASTER_TYPE = 1024, 1025
_GEOGRAPHIC_TYPE, _PROJECTED_CS_TYPE = 2048, 3072
_USER_DEFINED = 32767
_MODEL_GEOCENTRIC = 3
_RASTER_PIXEL_IS_POINT = 2


TIFF_EXTENSIONS = (".tif", ".tiff")


class RasterRejected(ValueError):
    """The bytes are not a TIFF this extractor reads, or break one of its read limits (the message says which)."""


def is_raster_name(file_name: str) -> bool:
    return str(file_name).lower().endswith(TIFF_EXTENSIONS)


def derived_cog_source_name(file_name: str) -> Optional[str]:
    """`<source>` for a name `<source>.cog.tif` where `<source>` itself ends `.tif` or `.tiff`, else None."""
    name = str(file_name)
    if not name.lower().endswith(DERIVED_COG_SUFFIX):
        return None
    source = name[:-len(DERIVED_COG_SUFFIX)]
    return source if source.lower().endswith(TIFF_EXTENSIONS) else None


# --- the directory walk (struct only) ----------------------------------------------------------------------------

class _Ifd:
    def __init__(self, offset: int):
        self.offset = offset
        self.end = offset
        self.entries: Dict[int, Tuple[int, int, int]] = {}  # tag -> (type, count, value or offset)
        self.eager_bytes = 0
        self.oversize_tag: Optional[int] = None

    def value_int(self, tag: int) -> Optional[int]:
        entry = self.entries.get(tag)
        return entry[2] if entry and entry[1] == 1 else None


class _Walk:
    def __init__(self, big: bool, little: bool, size: int):
        self.big, self.little, self.size = big, little, size
        self.ifds: List[_Ifd] = []
        self.chain_truncated = False

    @property
    def order(self) -> str:
        return "<" if self.little else ">"


def _read_at(reader, offset: int, length: int) -> bytes:
    reader.seek(offset)
    data = reader.read(length)
    if len(data) != length:
        raise RasterRejected("the TIFF directory runs past the end of the file")
    return data


def walk_ifds(reader, size: int) -> _Walk:
    """The header and the IFD chain, at most MAX_IFDS directories, each read once. RasterRejected for bytes that are
    not a TIFF, a directory with more than MAX_IFD_ENTRIES entries, or a chain that points back at itself."""
    reader.seek(0)
    head = reader.read(16)
    if len(head) < 8 or head[:2] not in (b"II", b"MM"):
        raise RasterRejected("not a TIFF file (no byte-order mark)")
    little = head[:2] == b"II"
    order = "<" if little else ">"
    magic = struct.unpack(order + "H", head[2:4])[0]
    if magic == 42:
        big, first = False, struct.unpack(order + "I", head[4:8])[0]
    elif magic == 43:
        if len(head) < 16 or struct.unpack(order + "HH", head[4:8]) != (8, 0):
            raise RasterRejected("not a BigTIFF file (bad offset size)")
        big, first = True, struct.unpack(order + "Q", head[8:16])[0]
    else:
        raise RasterRejected("not a TIFF file (bad magic number)")
    walk = _Walk(big, little, size)
    seen = set()
    offset = first
    budget = 0
    while offset:
        if offset in seen:
            raise RasterRejected("the TIFF directory chain loops")
        if len(walk.ifds) >= MAX_IFDS:
            walk.chain_truncated = True
            break
        if offset < 8 or offset + 2 > size:
            raise RasterRejected("a TIFF directory offset is outside the file")
        seen.add(offset)
        ifd = _Ifd(offset)
        count_size, entry_size, next_size = (8, 20, 8) if big else (2, 12, 4)
        count = struct.unpack(order + ("Q" if big else "H"), _read_at(reader, offset, count_size))[0]
        if count == 0 or count > MAX_IFD_ENTRIES:
            raise RasterRejected(f"a TIFF directory declares {count} entries (limit {MAX_IFD_ENTRIES})")
        table_end = offset + count_size + count * entry_size + next_size
        if table_end > size:
            raise RasterRejected("a TIFF directory runs past the end of the file")
        table = _read_at(reader, offset + count_size, count * entry_size + next_size)
        inline = 8 if big else 4
        value_width = 8 if big else 4
        head_size = 12 if big else 8
        ifd.end = table_end
        for index in range(count):
            raw = table[index * entry_size:(index + 1) * entry_size]
            tag, dtype, n = struct.unpack(order + ("HHQ" if big else "HHI"), raw[:head_size])
            value_raw = raw[head_size:head_size + value_width]
            width = _TYPE_SIZES.get(dtype)
            if width is None:
                continue
            nbytes = n * width
            if nbytes > inline:
                position = struct.unpack(order + ("Q" if big else "I"), value_raw)[0]
                if position + nbytes > size:
                    raise RasterRejected(f"tag {tag} stores {nbytes} bytes outside the file")
                ifd.end = max(ifd.end, position + nbytes)
                value = position
                if tag in _EAGER_TAGS or tag in _GEO_TAGS:
                    ifd.eager_bytes += nbytes
                    limit = GEO_TAG_MAX_BYTES if tag in _GEO_TAGS else TAG_ARRAY_MAX_BYTES
                    if nbytes > limit and ifd.oversize_tag is None:
                        ifd.oversize_tag = tag
            elif n == 1 and dtype in (3, 4, 16, 8, 9, 1, 6):
                code = {3: "H", 4: "I", 16: "Q", 8: "h", 9: "i", 1: "B", 6: "b"}[dtype]
                value = struct.unpack(order + code, value_raw[:width])[0]
            else:
                value = 0
            ifd.entries[tag] = (dtype, n, value)
        walk.ifds.append(ifd)
        budget += ifd.eager_bytes
        if budget > HEADER_READ_BUDGET_BYTES and ifd.oversize_tag is None:
            ifd.oversize_tag = -1
        next_raw = table[count * entry_size:count * entry_size + next_size]
        offset = struct.unpack(order + ("Q" if big else "I"), next_raw)[0]
    if not walk.ifds:
        raise RasterRejected("the TIFF has no directory")
    return walk


# --- the extractor ---------------------------------------------------------------------------------------------

def _compression_name(code) -> str:
    return _COMPRESSION_NAMES.get(int(code), f"unknown-{int(code)}")


def _geo_keys(page) -> Dict[str, int]:
    """The integer-valued GeoKeys of a page (an unreadable directory gives none)."""
    keys = getattr(page, "geotiff_tags", None)
    out: Dict[str, int] = {}
    if not isinstance(keys, dict):
        return out
    for name, value in keys.items():
        try:
            out[name] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _crs_of(keys: Dict[str, int]) -> Tuple[str, Optional[int]]:
    """(label, EPSG code): `EPSG:n`, `user-defined`, or `none` when the GeoKeys name no CRS."""
    model = keys.get("GTModelTypeGeoKey")
    projected = keys.get("ProjectedCSTypeGeoKey")
    geographic = keys.get("GeographicTypeGeoKey")
    if model == _MODEL_GEOCENTRIC:
        return "geocentric", None
    if model == 1 or (projected is not None and model != 2):
        if projected is not None and projected != _USER_DEFINED and projected > 0:
            return f"EPSG:{projected}", projected
        return ("user-defined", None) if keys else ("none", None)
    if geographic is not None and geographic != _USER_DEFINED and geographic > 0:
        return f"EPSG:{geographic}", geographic
    if keys.get("GTModelTypeGeoKey") is not None:
        return "user-defined", None
    return "none", None


def _affine_of(page, pixel_is_point: bool) -> Optional[Tuple[float, float, float, float, float, float]]:
    """(a, b, c, d, e, f) with x = a*col + b*row + c, y = d*col + e*row + f, from the tie point and pixel scale or
    from the model transformation; None when the file carries neither."""
    tags = page.tags
    transformation = tags.get(34264)
    affine = None
    if transformation is not None:
        matrix = [float(v) for v in transformation.value]
        if len(matrix) >= 16:
            affine = (matrix[0], matrix[1], matrix[3], matrix[4], matrix[5], matrix[7])
    if affine is None:
        tiepoint = tags.get(33922)
        scale = tags.get(33550)
        if tiepoint is not None and scale is not None:
            tie = [float(v) for v in tiepoint.value]
            pixel = [float(v) for v in scale.value]
            if len(tie) >= 6 and len(pixel) >= 2:
                col, row, x, y = tie[0], tie[1], tie[3], tie[4]
                affine = (pixel[0], 0.0, x - col * pixel[0], 0.0, -pixel[1], y + row * pixel[1])
    if affine is None:
        return None
    if pixel_is_point:
        # PixelIsPoint: the transform locates pixel centres, so the outer edge of the grid is half a pixel further
        # out (GDAL's reading of the same file).
        a, b, c, d, e, f = affine
        affine = (a, b, c - 0.5 * a - 0.5 * b, d, e, f - 0.5 * d - 0.5 * e)
    return affine


def _native_bounds(affine, width: int, height: int) -> List[float]:
    a, b, c, d, e, f = affine
    xs, ys = [], []
    for col, row in ((0, 0), (width, 0), (width, height), (0, height)):
        xs.append(a * col + b * row + c)
        ys.append(d * col + e * row + f)
    return [min(xs), min(ys), max(xs), max(ys)]


def _sampled(values: List[int]) -> List[int]:
    if len(values) <= COG_OFFSET_SAMPLES:
        return values
    step = len(values) / COG_OFFSET_SAMPLES
    return [values[int(i * step)] for i in range(COG_OFFSET_SAMPLES)] + [values[-1]]


def _is_cog(walk: _Walk, pages: list, first, overview_count: int) -> bool:
    if walk.chain_truncated or not first.is_tiled:
        return False
    if max(first.imagewidth, first.imagelength) > COG_OVERVIEW_MIN_DIMENSION and overview_count < 1:
        return False
    first_data = None
    for page in pages:
        offsets = [int(v) for v in page.dataoffsets if v]
        if not offsets:
            return False
        sample = _sampled(offsets)
        if any(later < earlier for earlier, later in zip(sample, sample[1:])):
            return False
        first_data = min(first_data, min(offsets)) if first_data is not None else min(offsets)
    return first_data is not None and max(ifd.end for ifd in walk.ifds) <= first_data


def _nodata(page) -> Optional[float]:
    """The GDAL_NODATA tag's value (an ASCII number, or `nan`); None when the tag is absent or not a number. The page's
    own `nodata` property answers 0 for a file with no tag, which would record a nodata value the file never had."""
    tag = page.tags.get(42113)
    if tag is None:
        return None
    try:
        return float(str(tag.value).strip("\x00 "))
    except (TypeError, ValueError):
        return None


def _number(value) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    return value


def extract_raster(reader, size: int, ctx: ExtractContext) -> BranchResult:
    """The `raster` result for a TIFF read through `reader` (a seekable file object of `size` bytes)."""
    result = BranchResult(file_class=CLASS_RASTER, render_skipped="unsupported")
    try:
        walk = walk_ifds(reader, size)
    except RasterRejected as exc:
        return _unreadable(ctx, str(exc))
    except OSError as exc:
        return _unreadable(ctx, f"the TIFF header could not be read ({type(exc).__name__})")
    first_ifd = walk.ifds[0]
    width = first_ifd.value_int(_TAG_WIDTH)
    height = first_ifd.value_int(_TAG_LENGTH)
    if not width or not height or width > MAX_DIMENSION or height > MAX_DIMENSION:
        return _unreadable(ctx, f"the image size is not usable (width {width}, height {height}; "
                                        f"each side must be 1 to {MAX_DIMENSION:,} pixels)")
    sys_raster: Dict[str, object] = {"width": width, "height": height, "bigTiff": walk.big}
    sys_geo: Dict[str, object] = {"format": "GeoTIFF", "footprint": None}
    if any(ifd.oversize_tag is not None for ifd in walk.ifds):
        return _header_too_large(result, ctx, walk, first_ifd, sys_raster)
    try:
        import tifffile  # noqa: PLC0415 - deferred: the cold-start cost is paid only by a raster
        # tifffile logs several unstructured warning lines per directory of a malformed header (up to 64
        # directories); the extractor reports the outcome itself, so they are not wanted in the function log.
        logging.getLogger("tifffile").setLevel(logging.CRITICAL)
        reader.seek(0)  # tifffile takes the current position of a file object as the start of the file
        with tifffile.TiffFile(reader) as tiff:
            pages = [tiff.pages[index] for index in range(len(walk.ifds))]
            _describe(result, ctx, walk, pages, sys_raster, sys_geo)
    except RasterRejected as exc:
        return _unreadable(ctx, str(exc))
    except Exception as exc:  # noqa: BLE001 - tifffile and the ranged reader raise their own families
        return _unreadable(ctx, f"the TIFF header could not be decoded ({type(exc).__name__})")
    if derived_cog_source_name(ctx.file_name) and ctx.derived_source_exists is not None:
        try:
            if ctx.derived_source_exists():
                sys_raster["derivedCog"] = True
        except Exception as exc:  # noqa: BLE001 - the check is a single HEAD; a failure leaves the file un-flagged
            result.warnings.append(f"{ctx.file_name}: the source file beside this COG could not be checked "
                                   f"({type(exc).__name__}); it is treated as an original")
    result.attributes["sys_raster"] = sys_raster
    result.attributes["sys_geo"] = sys_geo
    _facts(result, sys_raster, sys_geo)
    return result


def is_georeferenced(result: BranchResult) -> bool:
    """Whether an `extract_raster` result is a georeferenced raster, the only TIFF the raster class keeps."""
    return result.file_class == CLASS_RASTER and (result.attributes.get("sys_raster") or {}).get(
        "georeferenced") is True


def _unreadable(ctx: ExtractContext, reason: str) -> BranchResult:
    """The file keeps its stored `sys_file` attributes (the `other` class); the reason is the only addition."""
    return other_fallback(ctx, reason)


def _header_too_large(result: BranchResult, ctx: ExtractContext, walk: _Walk, ifd: _Ifd,
                      sys_raster: Dict[str, object]) -> BranchResult:
    """A header whose arrays are over the read limits: the facts the walk read, and nothing that needs the arrays."""
    tiled = _TAG_TILE_OFFSETS in ifd.entries
    sys_raster.update({"layout": "tiled" if tiled else "striped", "offsetsTruncated": True, "isCog": False})
    compression = ifd.value_int(_TAG_COMPRESSION)
    if compression is not None:
        sys_raster["compression"] = _compression_name(compression)
    sys_raster["footprintNote"] = "header-too-large"
    result.attributes["sys_raster"] = sys_raster
    result.attributes["sys_geo"] = {"format": "GeoTIFF", "footprint": None}
    result.warnings.append(f"{ctx.file_name}: the TIFF header carries arrays over the read limits; only the layout "
                           "facts are recorded and no footprint is written")
    _facts(result, sys_raster, result.attributes["sys_geo"])
    return result


def _describe(result: BranchResult, ctx: ExtractContext, walk: _Walk, pages: list, sys_raster: Dict[str, object],
              sys_geo: Dict[str, object]) -> None:
    first = pages[0]
    overviews = sum(1 for page in pages[1:] if page.tags.get(254) is not None and int(page.tags[254].value) & 1)
    dtype = first.dtype
    keys = _geo_keys(first)
    crs_label, epsg = _crs_of(keys)
    pixel_is_point = keys.get("GTRasterTypeGeoKey") == _RASTER_PIXEL_IS_POINT
    affine = _affine_of(first, pixel_is_point)
    nodata = _nodata(first)
    # A transform alone does not georeference a file: with no CRS named (an identity ModelTransformation GDAL
    # itself writes for an unreferenced raster, or a tie point with no GeoKeys) there is nothing to place on Earth.
    if crs_label == "none":
        affine = None
    sys_raster.update({
        "bandCount": int(first.samplesperpixel),
        "dataType": str(dtype) if dtype is not None else "unknown",
        "compression": _compression_name(first.compression),
        "layout": "tiled" if first.is_tiled else "striped",
        "blockWidth": int(first.tilewidth if first.is_tiled else first.imagewidth),
        "blockHeight": int(first.tilelength if first.is_tiled else first.rowsperstrip),
        "overviewCount": overviews,
        "isCog": _is_cog(walk, pages, first, overviews),
        "georeferenced": affine is not None,
        "crs": crs_label,
    })
    if nodata is not None:
        sys_raster["nodata"] = _number(nodata)
    if walk.chain_truncated:
        sys_raster["ifdsTruncated"] = True
        result.warnings.append(f"{ctx.file_name}: more than {MAX_IFDS} images in the file; the first {MAX_IFDS} "
                               "were read")
    if affine is None:
        sys_raster["footprintNote"] = "not-georeferenced"
        return
    width, height = int(first.imagewidth), int(first.imagelength)
    scale_x, scale_y = math.hypot(affine[0], affine[3]), math.hypot(affine[1], affine[4])
    sys_raster["pixelSizeX"], sys_raster["pixelSizeY"] = scale_x, scale_y
    if pixel_is_point:
        sys_raster["pixelIsPoint"] = True
    rotated = abs(affine[1]) > 1e-12 or abs(affine[3]) > 1e-12
    if rotated:
        sys_raster["rotated"] = True
    native = _native_bounds(affine, width, height)
    sys_raster["nativeBounds"] = native
    if epsg is None:
        sys_raster["footprintNote"] = "user-defined-crs" if crs_label == "user-defined" else (
            "geocentric-crs" if crs_label == "geocentric" else "no-crs")
        return
    footprint = extent_to_wgs84_polygon(epsg, affine=affine, columns=width, rows=height)
    if footprint.geometry is None:
        sys_raster["footprintNote"] = footprint.note
        return
    sys_raster["wgs84Bounds"] = footprint.bounds
    sys_geo["footprint"] = footprint.geometry
    sys_geo["crs"] = crs_label
    if footprint.geometry["type"] == "MultiPolygon":
        sys_raster["crossesAntimeridian"] = True


def _facts(result: BranchResult, sys_raster: Dict[str, object], sys_geo: Dict[str, object]) -> None:
    result.facts["size"] = f"{sys_raster['width']:,} x {sys_raster['height']:,} pixels"
    if "bandCount" in sys_raster:
        result.facts["bands"] = human_count(int(sys_raster["bandCount"]), "band") + f", {sys_raster['dataType']}"
    if sys_raster.get("isCog"):
        result.facts["layout"] = "Cloud Optimized GeoTIFF"
    elif "layout" in sys_raster:
        result.facts["layout"] = f"{sys_raster['layout']} GeoTIFF" if sys_raster.get("georeferenced") else str(
            sys_raster["layout"])
    if sys_raster.get("crs") and sys_raster["crs"] != "none":
        result.facts["crs"] = str(sys_raster["crs"])
    if sys_geo.get("footprint"):
        result.facts["footprint"] = str(sys_geo["footprint"]["type"])
    elif sys_raster.get("footprintNote"):
        result.facts["footprint"] = f"none ({sys_raster['footprintNote']})"


def extract_raster_file(path: str, ctx: ExtractContext) -> BranchResult:
    """The same result for a TIFF already on local disk (the downloaded-copy path every other class takes)."""
    with open(path, "rb") as handle:
        return extract_raster(handle, os.path.getsize(path), ctx)
