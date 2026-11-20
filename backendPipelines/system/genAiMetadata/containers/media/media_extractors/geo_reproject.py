# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The WGS84 footprint of a rectangle that is known in another coordinate reference system.

`extent_to_wgs84_polygon` takes a raster's pixel grid (an affine transform and the pixel size of the grid) or a plain
bounding box, and a CRS, and returns a GeoJSON footprint in longitude/latitude, or no footprint and the reason. The
GeoTIFF extractor (`raster.py`) and the GeoPackage extractor (`gis.py`) share it, so both obey one set of rules:

- Axis order is always (x=east/longitude, y=north/latitude) (`always_xy`), whatever the CRS authority says.
- Each edge of the rectangle is densified (`DEFAULT_DENSIFY` points per edge), so a projection that bends a straight
  edge is followed rather than cut by the corners alone.
- A ring that jumps more than 180 degrees of longitude between two neighbours crosses the antimeridian. It is cut
  at +/-180 into a MultiPolygon, one polygon per side.
- An extent that contains a pole gets no footprint in this version (a polygon around a pole is not a polygon in
  longitude/latitude); the reason is `contains-pole`.
- A PixelIsPoint raster's transform refers to pixel centres; the grid is shifted by half a pixel to its edges.
- Any invalid or non-finite result gives no footprint. WGS84 is never assumed for a CRS that cannot be resolved.

The module imports `pyproj` on first use, so an image that never reads a raster does not pay for the import.
"""

import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# Points per edge (the corner excluded) when an edge is densified.
DEFAULT_DENSIFY = 20
# Footprint coordinates are rounded to this many decimals (about a centimetre).
COORDINATE_DECIMALS = 7

NOTE_CONTAINS_POLE = "contains-pole"
NOTE_UNKNOWN_CRS = "unknown-crs"
NOTE_INVALID = "invalid-extent"
NOTE_OUT_OF_RANGE = "out-of-range"

# (a, b, c, d, e, f): x = a*col + b*row + c, y = d*col + e*row + f
Affine = Tuple[float, float, float, float, float, float]
Position = Tuple[float, float]


@dataclass
class Footprint:
    """`geometry` is a GeoJSON Polygon or MultiPolygon in longitude/latitude (None when there is none),
    `bounds` the (west, south, east, north) box of it (an antimeridian footprint reports its two extreme
    longitudes as west > east), `note` why there is no geometry."""

    geometry: Optional[dict] = None
    bounds: Optional[List[float]] = None
    note: Optional[str] = None


# Transformers built for recent CRSs, least recently used first out: one costs about 0.2 MB, and a run meets a handful.
TRANSFORMER_CACHE_SIZE = 16
_transformers: "OrderedDict[object, object]" = OrderedDict()


def _pyproj():
    import pyproj  # noqa: PLC0415 - deferred: the import is the dominant cold-start cost of this module
    return pyproj


def resolve_crs(crs):
    """A `pyproj.CRS` for an EPSG integer, an `EPSG:n` label or a pyproj-readable definition; None when it cannot
    be resolved or is not a 2D coordinate system a footprint can be drawn in."""
    pyproj = _pyproj()
    try:
        if isinstance(crs, int) and not isinstance(crs, bool):
            resolved = pyproj.CRS.from_epsg(crs)
        else:
            resolved = pyproj.CRS.from_user_input(crs)
    except Exception:  # noqa: BLE001 - pyproj raises its own CRSError and ValueError for anything unreadable
        return None
    if resolved.is_geocentric or resolved.axis_info is None or len(resolved.axis_info) < 2:
        return None
    return resolved


def _cached_transformer(key, build):
    transformer = _transformers.get(key)
    if transformer is not None:
        _transformers.move_to_end(key)
        return transformer
    transformer = build()
    _transformers[key] = transformer
    while len(_transformers) > TRANSFORMER_CACHE_SIZE:
        _transformers.popitem(last=False)
    return transformer


def _to_wgs84(crs):
    """The CRS -> WGS84 transformer. Raises when pyproj has no operation between them (about 20 EPSG codes, such as
    3145, need grids or a network this image does not have); the caller turns that into `unknown-crs`."""
    pyproj = _pyproj()
    return _cached_transformer(
        crs.to_wkt(), lambda: pyproj.Transformer.from_crs(crs, pyproj.CRS.from_epsg(4326), always_xy=True))


def _to_native(crs):
    pyproj = _pyproj()
    return _cached_transformer(
        ("native", crs.to_wkt()), lambda: pyproj.Transformer.from_crs(pyproj.CRS.from_epsg(4326), crs, always_xy=True))


def affine_for_bounds(bounds: Sequence[float]) -> Tuple[Affine, int, int]:
    """(affine, columns, rows) of a one-cell grid over (min_x, min_y, max_x, max_y), so a bounding box is one grid
    whose edges are densified like a raster's."""
    min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    return (max_x - min_x, 0.0, min_x, 0.0, min_y - max_y, max_y), 1, 1


def _edge_ring(affine: Affine, columns: int, rows: int, densify: int) -> List[Position]:
    """The grid's outline, upper-left corner first and clockwise in pixel space, as native coordinates with
    `densify` extra points on each edge. Closed (the first position is repeated last)."""
    a, b, c, d, e, f = affine
    steps = max(1, int(densify) + 1)
    corners = [(0.0, 0.0), (float(columns), 0.0), (float(columns), float(rows)), (0.0, float(rows))]
    ring: List[Position] = []
    for index, (col0, row0) in enumerate(corners):
        col1, row1 = corners[(index + 1) % 4]
        for step in range(steps):
            t = step / steps
            col = col0 + (col1 - col0) * t
            row = row0 + (row1 - row0) * t
            ring.append((a * col + b * row + c, d * col + e * row + f))
    ring.append(ring[0])
    return ring


def _point_in_ring(x: float, y: float, ring: Sequence[Position]) -> bool:
    inside = False
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _contains_pole(crs, ring: Sequence[Position]) -> bool:
    """Whether a pole of the Earth lies inside the (native) ring. A geographic CRS is checked on its latitude."""
    if crs.is_geographic:
        return False
    transform = _to_native(crs)  # raises when pyproj has no operation: extent_to_wgs84_polygon guards the call
    for latitude in (90.0, -90.0):
        try:
            x, y = transform.transform(0.0, latitude)
        except Exception:  # noqa: BLE001 - a projection with no pole (Mercator) raises or returns infinity
            continue
        if math.isfinite(x) and math.isfinite(y) and _point_in_ring(x, y, ring):
            return True
    return False


def _unwrap(ring: Sequence[Position]) -> List[Position]:
    """Longitudes made continuous: each position within 180 degrees of its predecessor."""
    out: List[Position] = [ring[0]]
    for lon, lat in ring[1:]:
        previous = out[-1][0]
        while lon - previous > 180.0:
            lon -= 360.0
        while lon - previous < -180.0:
            lon += 360.0
        out.append((lon, lat))
    return out


def _clip(ring: Sequence[Position], boundary: float, keep_below: bool) -> List[Position]:
    """Sutherland-Hodgman clip of an open ring (no repeated closing position) against the vertical line
    x = boundary, keeping the side x <= boundary (`keep_below`) or x >= boundary."""

    def inside(position: Position) -> bool:
        return position[0] <= boundary if keep_below else position[0] >= boundary

    out: List[Position] = []
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if inside(current):
            if not inside(previous):
                out.append(_cross(previous, current, boundary))
            out.append(current)
        elif inside(previous):
            out.append(_cross(previous, current, boundary))
    return out


def _cross(p: Position, q: Position, boundary: float) -> Position:
    t = (boundary - p[0]) / (q[0] - p[0])
    return boundary, p[1] + (q[1] - p[1]) * t


def _closed(ring: Sequence[Position]) -> Optional[List[List[float]]]:
    """A GeoJSON ring: coordinates rounded, consecutive duplicates removed, closed, at least three distinct
    vertices; None when the ring is degenerate."""
    rounded: List[Tuple[float, float]] = []
    for lon, lat in ring:
        point = (round(lon, COORDINATE_DECIMALS), round(lat, COORDINATE_DECIMALS))
        if not rounded or rounded[-1] != point:
            rounded.append(point)
    if len(rounded) > 1 and rounded[0] == rounded[-1]:
        rounded.pop()
    if len(set(rounded)) < 3:
        return None
    rounded.append(rounded[0])
    return [[lon, lat] for lon, lat in rounded]


def _signed_area(ring: Sequence[Sequence[float]]) -> float:
    return sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1)) / 2.0


def _counter_clockwise(ring: List[List[float]]) -> List[List[float]]:
    """The ring wound counter-clockwise, the winding RFC 7946 gives an exterior ring."""
    return ring if _signed_area(ring) >= 0 else list(reversed(ring))


def extent_to_wgs84_polygon(crs, *, affine: Optional[Affine] = None, columns: int = 1, rows: int = 1,
                            bounds: Optional[Sequence[float]] = None, pixel_is_point: bool = False,
                            densify: int = DEFAULT_DENSIFY) -> Footprint:
    """The WGS84 footprint of a grid (`affine`, `columns`, `rows`) or of `bounds` (min_x, min_y, max_x, max_y),
    both in `crs`. See the module notes for the rules; a `Footprint` with `geometry` None carries the `note`."""
    if affine is None:
        if bounds is None or len(bounds) != 4:
            return Footprint(note=NOTE_INVALID)
        affine, columns, rows = affine_for_bounds(bounds)
    values = list(affine) + [columns, rows]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values) or columns < 1 \
            or rows < 1:
        return Footprint(note=NOTE_INVALID)
    resolved = resolve_crs(crs)
    if resolved is None:
        return Footprint(note=NOTE_UNKNOWN_CRS)
    a, b, c, d, e, f = (float(value) for value in affine)
    if pixel_is_point:
        # The transform locates pixel centres; the footprint is the grid's outer edge, half a pixel further out.
        c -= 0.5 * a + 0.5 * b
        f -= 0.5 * d + 0.5 * e
    native = _edge_ring((a, b, c, d, e, f), int(columns), int(rows), densify)
    # Every pyproj call that can fail for a CRS it cannot transform sits in this guard (the helper never raises).
    try:
        transformer = _to_wgs84(resolved)
        if _contains_pole(resolved, native):
            return Footprint(note=NOTE_CONTAINS_POLE)
        xs, ys = transformer.transform([p[0] for p in native], [p[1] for p in native])
    except Exception:  # noqa: BLE001 - pyproj's ProjError and friends: no operation, no footprint, never a guess
        return Footprint(note=NOTE_UNKNOWN_CRS)
    ring = list(zip(xs, ys))
    if not all(math.isfinite(lon) and math.isfinite(lat) for lon, lat in ring):
        return Footprint(note=NOTE_INVALID)
    if any(abs(lat) > 90.0 + 1e-9 for _, lat in ring):
        return Footprint(note=NOTE_OUT_OF_RANGE)
    ring = [(lon, max(-90.0, min(90.0, lat))) for lon, lat in ring]
    unwrapped = _unwrap(ring)
    lons = [lon for lon, _ in unwrapped]
    if max(lons) > 180.0 + 1e-9 or min(lons) < -180.0 - 1e-9:
        return _split_at_antimeridian(unwrapped[:-1])
    unwrapped = [(max(-180.0, min(180.0, lon)), lat) for lon, lat in unwrapped]
    closed = _closed(unwrapped)
    if closed is None:
        return Footprint(note=NOTE_INVALID)
    closed = _counter_clockwise(closed)
    return Footprint(geometry={"type": "Polygon", "coordinates": [closed]}, bounds=_bounds(closed))


def _split_at_antimeridian(ring: List[Position]) -> Footprint:
    """The unwrapped (continuous) open ring as a MultiPolygon cut at the antimeridian it crosses."""
    lons = [lon for lon, _ in ring]
    if max(lons) > 180.0:
        west_part = _clip(ring, 180.0, keep_below=True)
        east_part = [(lon - 360.0, lat) for lon, lat in _clip(ring, 180.0, keep_below=False)]
    else:
        west_part = [(lon + 360.0, lat) for lon, lat in _clip(ring, -180.0, keep_below=True)]
        east_part = _clip(ring, -180.0, keep_below=False)
    polygons = []
    for part in (west_part, east_part):
        closed = _closed([(max(-180.0, min(180.0, lon)), lat) for lon, lat in part]) if part else None
        if closed is not None:
            polygons.append([_counter_clockwise(closed)])
    if not polygons:
        return Footprint(note=NOTE_INVALID)
    if len(polygons) == 1:
        return Footprint(geometry={"type": "Polygon", "coordinates": polygons[0]}, bounds=_bounds(polygons[0][0]))
    all_lats = [position[1] for polygon in polygons for position in polygon[0]]
    # The box runs eastwards from the western piece's west edge across +/-180 to the eastern piece's east edge
    # (west > east). When the two pieces together cover 180 degrees or more (a 0..360 global raster) no such box is
    # narrower than the globe, so it is the whole longitude range.
    west_edge = min(position[0] for position in polygons[0][0])
    east_edge = max(position[0] for position in polygons[-1][0])
    if (180.0 - west_edge) + (east_edge + 180.0) >= 180.0:
        bounds = [-180.0, min(all_lats), 180.0, max(all_lats)]
    else:
        bounds = [west_edge, min(all_lats), east_edge, max(all_lats)]
    return Footprint(geometry={"type": "MultiPolygon", "coordinates": polygons}, bounds=bounds)


def _bounds(ring: Sequence[Sequence[float]]) -> List[float]:
    lons = [position[0] for position in ring]
    lats = [position[1] for position in ring]
    return [min(lons), min(lats), max(lons), max(lats)]
