# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GeoJSON (a `.json` whose root is a Geometry, Feature or FeatureCollection): feature count, geometry types
and a footprint -> sys_geo. The file keeps the `data` class; the attribute text is the analysis input and
nothing renders. Pure Python over the parsed document, no geometry library."""

import json
import math
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .common import CLASS_DATA, BranchResult, ExtractContext, human_count, truncate_text

GEOMETRY_TYPES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon",
                  "GeometryCollection")
GEOJSON_TYPES = GEOMETRY_TYPES + ("Feature", "FeatureCollection")
# The member that makes an object of each type GeoJSON rather than a document that merely says `"type": "Point"`.
_MEMBER_KEY = {"GeometryCollection": "geometries", "Feature": "geometry", "FeatureCollection": "features"}
_MAX_PROPERTY_KEYS = 50


def is_geojson(obj) -> bool:
    """Root is an object whose `type` is a GeoJSON type and which carries that type's member key."""
    if not isinstance(obj, dict):
        return False
    kind = obj.get("type")
    return kind in GEOJSON_TYPES and _MEMBER_KEY.get(kind, "coordinates") in obj


def geometries(document: dict) -> List[Optional[dict]]:
    """One entry per feature: the geometry object, or None for a feature whose geometry is null or not an
    object. A bare Geometry and a Feature are one entry each."""
    kind = document.get("type")
    if kind == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list):
            return []
        return [feature.get("geometry") if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
                else None for feature in features]
    if kind == "Feature":
        geometry = document.get("geometry")
        return [geometry if isinstance(geometry, dict) else None]
    return [document]


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def iter_positions(coordinates) -> Iterator[Tuple[float, float]]:
    """Every (lon, lat) in a coordinates array of any nesting depth. ValueError for a position that is not
    two-plus numbers, is not finite, or lies outside longitude [-180, 180] / latitude [-90, 90] -- the ranges
    the metadata model's `_validate_lon_lat` enforces on the `location` value."""
    if not isinstance(coordinates, (list, tuple)):
        raise ValueError(f"coordinates member {coordinates!r} is not an array")
    if coordinates and _is_number(coordinates[0]):
        if len(coordinates) < 2 or not _is_number(coordinates[1]):
            raise ValueError(f"position {coordinates!r} is not a [lon, lat] pair")
        lon, lat = float(coordinates[0]), float(coordinates[1])
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise ValueError(f"position {coordinates!r} is not finite")
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise ValueError(f"position {coordinates!r} is outside longitude [-180, 180] / latitude [-90, 90]")
        yield lon, lat
        return
    for item in coordinates:
        yield from iter_positions(item)


def geometry_positions(geometry) -> Iterator[Tuple[float, float]]:
    """The positions of one geometry, walking a GeometryCollection's members."""
    if not isinstance(geometry, dict):
        raise ValueError("geometry is not an object")
    if geometry.get("type") == "GeometryCollection":
        members = geometry.get("geometries")
        if not isinstance(members, list):
            raise ValueError("GeometryCollection without a geometries array")
        for member in members:
            yield from geometry_positions(member)
        return
    if "coordinates" not in geometry:
        raise ValueError(f"{geometry.get('type')} geometry without coordinates")
    yield from iter_positions(geometry["coordinates"])


def bbox_footprint(positions: Iterable[Tuple[float, float]]) -> Tuple[Optional[dict], int]:
    """(footprint, position count). The footprint is the bounding-box Polygon -- one closed ring in lon/lat
    order, counter-clockwise from the south-west corner, the shape `_validate_linear_ring` accepts; a Point
    when every position coincides; a two-corner LineString when the box has no width or no height (a ring
    with repeated vertices would fail the ring checks); None when there are no positions."""
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf
    count = 0
    for lon, lat in positions:
        count += 1
        min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
        min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
    if not count:
        return None, 0
    if min_lon == max_lon and min_lat == max_lat:
        return {"type": "Point", "coordinates": [min_lon, min_lat]}, count
    if min_lon == max_lon or min_lat == max_lat:
        return {"type": "LineString", "coordinates": [[min_lon, min_lat], [max_lon, max_lat]]}, count
    ring = [[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]
    return {"type": "Polygon", "coordinates": [ring]}, count


def _closed_rings(polygon: dict) -> bool:
    """Every ring has at least four positions and ends where it starts."""
    rings = polygon.get("coordinates")
    if not isinstance(rings, list) or not rings:
        return False
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            return False
        first, last = ring[0], ring[-1]
        if not isinstance(first, (list, tuple)) or not isinstance(last, (list, tuple)):
            return False
        if list(first[:2]) != list(last[:2]):
            return False
    return True


def _property_keys(document: dict) -> List[str]:
    keys = set()
    features = document.get("features") if document.get("type") == "FeatureCollection" else [document]
    for feature in features if isinstance(features, list) else []:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if isinstance(properties, dict):
            keys.update(str(key) for key in properties)
    return sorted(keys)[:_MAX_PROPERTY_KEYS]


def extract_geo(document: dict, ctx: ExtractContext) -> BranchResult:
    result = BranchResult(file_class=CLASS_DATA)
    shapes = geometries(document)
    present = [shape for shape in shapes if isinstance(shape, dict)]
    sys_geo: Dict[str, object] = {
        "format": "GeoJSON",
        "type": document.get("type"),
        "featureCount": len(shapes),
        "geometryTypes": sorted({str(shape.get("type")) for shape in present}),
        "footprint": None,
    }
    property_keys = _property_keys(document)
    if property_keys:
        sys_geo["propertyKeys"] = property_keys
    own_geometry = False
    try:
        single = present[0] if len(present) == 1 else None
        own_geometry = single is not None and (
            single.get("type") == "Point" or (single.get("type") == "Polygon" and _closed_rings(single)))
        if own_geometry:
            # The file's own geometry is the footprint; walking it validates every position.
            count = sum(1 for _ in geometry_positions(single))
            footprint = {"type": single["type"], "coordinates": single["coordinates"]}
        else:
            if single is not None and single.get("type") == "Polygon":
                result.warnings.append("GeoJSON Polygon ring is not closed; the bounding box is the footprint")
            footprint, count = bbox_footprint(
                position for shape in present for position in geometry_positions(shape))
            if footprint is None:
                result.warnings.append("GeoJSON carries no positions; footprint omitted")
        sys_geo["footprint"] = footprint
        sys_geo["positionCount"] = count
    except ValueError as exc:
        own_geometry = False
        result.warnings.append(f"GeoJSON coordinates rejected ({exc}); footprint omitted")
    result.attributes["sys_geo"] = sys_geo
    result.facts["features"] = human_count(int(sys_geo["featureCount"]), "feature")
    if sys_geo["geometryTypes"]:
        result.facts["geometryTypes"] = ", ".join(sys_geo["geometryTypes"])
    footprint = sys_geo.get("footprint")
    if isinstance(footprint, dict):
        result.facts["footprint"] = str(footprint["type"]) + ("" if own_geometry else " bounding box")
    summary = {key: sys_geo[key] for key in ("type", "featureCount", "geometryTypes", "footprint", "propertyKeys")
               if key in sys_geo}
    result.text_excerpt = truncate_text(json.dumps(summary, default=str), ctx.max_text_chars)
    return result
