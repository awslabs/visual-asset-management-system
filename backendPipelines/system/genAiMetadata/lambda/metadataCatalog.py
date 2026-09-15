#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Promotion of the raw ``sys_*`` attributes to typed ``ext_*`` file metadata, and the ``location``.

The attribute layer is the complete technical record (string-only). This module maps a curated subset
of it to typed metadata items through ``PROMOTED_FIELDS``, a data-driven catalogue: a field is written
only when its source key is present and non-null, and its value is rendered so the metadata service's
type validation accepts it (numbers -> ``number``, booleans -> ``boolean``, ISO-8601 -> ``date``,
triples -> ``xyz`` as ``{"x","y","z"}``, lists -> ``string`` joined with ", "). One prefix, ``ext_``,
marks every pipeline-extracted field.

``location`` is the one unprefixed key: the indexer reads the literal key ``location`` into the
geo-shape index and the map. It is derived from the class's positional attributes as a GeoJSON Point or
Polygon and validated with the structural and ring rules the backend metadata model applies to the
``geojson`` type (``backend/backend/models/metadata.py``), restated here because pipeline code cannot
import the backend package; the test suite pins the two equal.
"""

import datetime
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

import fileClassifier as fc

TYPE_STRING = "string"
TYPE_NUMBER = "number"
TYPE_BOOLEAN = "boolean"
TYPE_DATE = "date"
TYPE_XYZ = "xyz"
TYPE_GEOJSON = "geojson"

PROMOTED_PREFIX = "ext_"
LOCATION_KEY = "location"
PIPELINE_OWNED_PREFIXES = ("ext_", "genai_")
LIST_SEPARATOR = ", "
COORDINATE_DECIMALS = 7

UNIT_TO_METRES = {"m": 1, "mm": 0.001, "cm": 0.01, "in": 0.0254, "ft": 0.3048}
SIZE_CATEGORY_THRESHOLDS_M = ((0.3, "tiny"), (1.0, "small"), (2.0, "medium"), (5.0, "large"))
SIZE_CATEGORY_LARGEST = "huge"

# Where a model's units are declared, in the order a declared value beats an estimate.
UNITS_SOURCES = ("sys_geometry.units", "sys_cad.declaredUnits", "sys_cad.estimatedUnits")

GEOMETRY_CLASSES = frozenset({fc.CLASS_MESH, fc.CLASS_USD, fc.CLASS_CAD, fc.CLASS_SPLAT, fc.CLASS_IFC})
POINT_CLASSES = frozenset({fc.CLASS_POINTCLOUD, fc.CLASS_SPLAT})
AV_CLASSES = frozenset({fc.CLASS_VIDEO, fc.CLASS_AUDIO})


@dataclass(frozen=True)
class PromotedField:
    """One catalogue row. ``source`` lists dotted attribute paths tried in order (the first present,
    non-null value wins); ``transform`` maps ``(value, attributes)`` to the value written, or ``None``
    to omit the field; ``classes`` gates the row by file class."""
    key: str
    value_type: str
    source: Tuple[str, ...]
    classes: FrozenSet[str]
    transform: Optional[Callable[[Any, dict], Any]] = None


def lookup(attributes: dict, path: str):
    """The value at a dotted path such as ``sys_geometry.dimensions``; ``None`` when a step is missing."""
    node = attributes
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def first_present(attributes: dict, paths: Iterable[str]):
    for path in paths:
        value = lookup(attributes, path)
        if value is not None:
            return value
    return None


def _as_number(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _number_text(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else repr(round(number, 6))


def normalise_units(value, _attributes=None) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in UNIT_TO_METRES else None


def units_of(attributes: dict) -> Optional[str]:
    """The first recognised units code among ``UNITS_SOURCES``; a present but unrecognised value
    (``"unknown"``, a spelled-out name) does not stop the search at that source."""
    for path in UNITS_SOURCES:
        units = normalise_units(lookup(attributes, path))
        if units:
            return units
    return None


def units_row(_value, attributes: dict) -> Optional[str]:
    return units_of(attributes)


def size_category(extent_max, attributes: dict) -> Optional[str]:
    """The size class of ``extentMax`` converted to metres; ``None`` when the units are unknown."""
    units = units_of(attributes)
    extent = _as_number(extent_max)
    if units is None or extent is None or extent < 0:
        return None
    metres = extent * UNIT_TO_METRES[units]
    for threshold, label in SIZE_CATEGORY_THRESHOLDS_M:
        if metres < threshold:
            return label
    return SIZE_CATEGORY_LARGEST


def iso_date(value, _attributes=None) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def exif_datetime_to_iso(value, _attributes=None) -> Optional[str]:
    """EXIF ``YYYY:MM:DD HH:MM:SS`` to ISO-8601; an ISO-8601 string passes through."""
    text = str(value or "").strip()
    try:
        return datetime.datetime.strptime(text, "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return iso_date(text)


def camera_name(exif, _attributes=None) -> Optional[str]:
    if not isinstance(exif, dict):
        return None
    parts = [str(exif.get(key) or "").strip() for key in ("make", "model")]
    return " ".join(part for part in parts if part) or None


def resolution(media, _attributes=None) -> Optional[str]:
    if not isinstance(media, dict):
        return None
    width, height = _as_number(media.get("width")), _as_number(media.get("height"))
    if not width or not height:
        return None
    return f"{int(width)}x{int(height)}"


def crs_label(crs, _attributes=None) -> Optional[str]:
    if not isinstance(crs, dict):
        return None
    epsg = _as_number(crs.get("epsg"))
    if epsg and epsg > 0:
        return f"EPSG:{int(epsg)}"
    return str(crs.get("name") or "").strip() or None


def positive(value, _attributes=None) -> Optional[bool]:
    number = _as_number(value)
    return None if number is None else number > 0


def _field(key, value_type, source, classes, transform=None) -> PromotedField:
    paths = (source,) if isinstance(source, str) else tuple(source)
    return PromotedField(key, value_type, paths, frozenset(classes), transform)


_GEO_AND_POINTS = GEOMETRY_CLASSES | POINT_CLASSES

# One row per ext_* key, in the order the rows are written; the class set gates each row.
PROMOTED_FIELDS: List[PromotedField] = [
    _field("ext_dimensions", TYPE_XYZ, "sys_geometry.dimensions", GEOMETRY_CLASSES),
    _field("ext_bounds_min", TYPE_XYZ, ("sys_geometry.boundsMin", "sys_pointcloud.boundsMin"), _GEO_AND_POINTS),
    _field("ext_bounds_max", TYPE_XYZ, ("sys_geometry.boundsMax", "sys_pointcloud.boundsMax"), _GEO_AND_POINTS),
    _field("ext_units", TYPE_STRING, UNITS_SOURCES, GEOMETRY_CLASSES, units_row),
    _field("ext_extent_max", TYPE_NUMBER, "sys_geometry.extentMax", GEOMETRY_CLASSES),
    _field("ext_volume", TYPE_NUMBER, "sys_geometry.volume", GEOMETRY_CLASSES),
    _field("ext_surface_area", TYPE_NUMBER, "sys_geometry.surfaceArea", GEOMETRY_CLASSES),
    _field("ext_size_category", TYPE_STRING, "sys_geometry.extentMax", GEOMETRY_CLASSES, size_category),
    _field("ext_vertex_count", TYPE_NUMBER, "sys_statistics.vertices", GEOMETRY_CLASSES),
    _field("ext_face_count", TYPE_NUMBER, ("sys_statistics.faces", "sys_cad.faceCount"), GEOMETRY_CLASSES),
    _field("ext_triangle_count", TYPE_NUMBER, "sys_statistics.triangles", GEOMETRY_CLASSES),
    _field("ext_mesh_count", TYPE_NUMBER, "sys_statistics.meshCount", GEOMETRY_CLASSES),
    _field("ext_watertight", TYPE_BOOLEAN, "sys_statistics.watertight", GEOMETRY_CLASSES),
    _field("ext_material_count", TYPE_NUMBER, "sys_visual.materialCount", GEOMETRY_CLASSES),
    _field("ext_texture_count", TYPE_NUMBER, "sys_visual.textureCount", GEOMETRY_CLASSES),
    _field("ext_has_textures", TYPE_BOOLEAN, "sys_visual.textureCount", GEOMETRY_CLASSES, positive),
    _field("ext_has_uv", TYPE_BOOLEAN, "sys_visual.hasUv", GEOMETRY_CLASSES),
    _field("ext_has_vertex_colors", TYPE_BOOLEAN, "sys_visual.hasVertexColors", GEOMETRY_CLASSES),
    _field("ext_object_count", TYPE_NUMBER, "sys_scene.nodeCount", GEOMETRY_CLASSES),
    _field("ext_has_animation", TYPE_BOOLEAN, "sys_scene.hasAnimation", GEOMETRY_CLASSES),
    _field("ext_has_armature", TYPE_BOOLEAN, "sys_scene.hasArmature", GEOMETRY_CLASSES),
    _field("ext_solid_count", TYPE_NUMBER, "sys_cad.solidCount", {fc.CLASS_CAD}),
    _field("ext_edge_count", TYPE_NUMBER, "sys_cad.edgeCount", {fc.CLASS_CAD}),
    _field("ext_assembly_count", TYPE_NUMBER, "sys_cad.assemblyCount", {fc.CLASS_CAD}),
    _field("ext_point_count", TYPE_NUMBER, "sys_pointcloud.pointCount", POINT_CLASSES),
    _field("ext_has_color", TYPE_BOOLEAN, "sys_pointcloud.hasColor", POINT_CLASSES),
    _field("ext_crs", TYPE_STRING, "sys_pointcloud.crs", POINT_CLASSES, crs_label),
    _field("ext_ifc_schema", TYPE_STRING, "sys_ifc.schema", {fc.CLASS_IFC}),
    _field("ext_project_name", TYPE_STRING, "sys_ifc.projectName", {fc.CLASS_IFC}),
    _field("ext_storey_count", TYPE_NUMBER, "sys_ifc.storeyCount", {fc.CLASS_IFC}),
    _field("ext_element_count", TYPE_NUMBER, "sys_ifc.elementCount", {fc.CLASS_IFC}),
    _field("ext_geometric_error", TYPE_NUMBER, "sys_tiles3d.geometricError", {fc.CLASS_TILES3D}),
    _field("ext_tile_count", TYPE_NUMBER, "sys_tiles3d.tileCount", {fc.CLASS_TILES3D}),
    _field("ext_width", TYPE_NUMBER, ("sys_image.width", "sys_media.width"), {fc.CLASS_IMAGE, fc.CLASS_VIDEO}),
    _field("ext_height", TYPE_NUMBER, ("sys_image.height", "sys_media.height"), {fc.CLASS_IMAGE, fc.CLASS_VIDEO}),
    _field("ext_color_mode", TYPE_STRING, "sys_image.mode", {fc.CLASS_IMAGE}),
    _field("ext_camera", TYPE_STRING, "sys_image.exif", {fc.CLASS_IMAGE}, camera_name),
    _field("ext_captured_at", TYPE_DATE, "sys_image.exif.dateTimeOriginal", {fc.CLASS_IMAGE}, exif_datetime_to_iso),
    _field("ext_duration_seconds", TYPE_NUMBER, "sys_media.durationSeconds", AV_CLASSES),
    _field("ext_frame_rate", TYPE_NUMBER, "sys_media.frameRate", AV_CLASSES),
    _field("ext_bitrate_kbps", TYPE_NUMBER, "sys_media.bitrateKbps", AV_CLASSES),
    _field("ext_channels", TYPE_NUMBER, "sys_media.channels", AV_CLASSES),
    _field("ext_sample_rate", TYPE_NUMBER, "sys_media.sampleRate", AV_CLASSES),
    _field("ext_resolution", TYPE_STRING, "sys_media", {fc.CLASS_VIDEO}, resolution),
    _field("ext_video_codec", TYPE_STRING, "sys_media.videoCodec", AV_CLASSES),
    _field("ext_audio_codec", TYPE_STRING, "sys_media.audioCodec", AV_CLASSES),
    _field("ext_title", TYPE_STRING, ("sys_media.tags.title", "sys_document.title"), AV_CLASSES | {fc.CLASS_DOCUMENT}),
    _field("ext_artist", TYPE_STRING, "sys_media.tags.artist", AV_CLASSES),
    _field("ext_album", TYPE_STRING, "sys_media.tags.album", AV_CLASSES),
    _field("ext_year", TYPE_NUMBER, "sys_media.tags.year", AV_CLASSES),
    _field("ext_page_count", TYPE_NUMBER, "sys_document.pageCount", {fc.CLASS_DOCUMENT}),
    _field("ext_author", TYPE_STRING, "sys_document.author", {fc.CLASS_DOCUMENT}),
    _field("ext_created_at", TYPE_DATE, "sys_document.createdAt", {fc.CLASS_DOCUMENT}, iso_date),
    _field("ext_has_text", TYPE_BOOLEAN, "sys_document.hasText", {fc.CLASS_DOCUMENT}),
    _field("ext_language", TYPE_STRING, "sys_text.language", {fc.CLASS_TEXT}),
    _field("ext_encoding", TYPE_STRING, "sys_text.encoding", {fc.CLASS_TEXT}),
    _field("ext_line_count", TYPE_NUMBER, "sys_text.lineCount", {fc.CLASS_TEXT}),
    _field("ext_word_count", TYPE_NUMBER, "sys_text.wordCount", {fc.CLASS_TEXT}),
    _field("ext_row_count", TYPE_NUMBER, "sys_data.rowCount", {fc.CLASS_DATA}),
    _field("ext_column_count", TYPE_NUMBER, "sys_data.columnCount", {fc.CLASS_DATA}),
    _field("ext_columns", TYPE_STRING, "sys_data.columns", {fc.CLASS_DATA}),
    _field("ext_feature_count", TYPE_NUMBER, "sys_geo.featureCount", {fc.CLASS_DATA}),
    _field("ext_geometry_types", TYPE_STRING, "sys_geo.geometryTypes", {fc.CLASS_DATA}),
]


def xyz_text(value) -> Optional[str]:
    """``{"x":…,"y":…,"z":…}`` JSON for a ``{width,height,depth}``/``{x,y,z}`` object or a 3-list."""
    if isinstance(value, dict):
        keys = ("x", "y", "z") if "x" in value else ("width", "height", "depth")
        triple = [value.get(key) for key in keys]
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        triple = list(value[:3])
    else:
        return None
    numbers = [_as_number(item) for item in triple]
    if any(number is None for number in numbers):
        return None
    return json.dumps({"x": numbers[0], "y": numbers[1], "z": numbers[2]}, separators=(",", ":"))


def render_value(value, value_type: str) -> Optional[str]:
    """The ``metadataValue`` string for ``value`` under ``value_type``; ``None`` when it does not fit
    the type, so the field is omitted rather than rejected by the metadata service."""
    if value is None:
        return None
    if value_type == TYPE_STRING:
        if isinstance(value, dict):
            return None
        if isinstance(value, (list, tuple)):
            parts = [" ".join(str(item).split()) for item in value if item is not None and str(item).strip()]
            return LIST_SEPARATOR.join(parts) or None
        return " ".join(str(value).split()) or None
    if value_type == TYPE_NUMBER:
        number = _as_number(value)
        return None if number is None else _number_text(number)
    if value_type == TYPE_BOOLEAN:
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip().lower()
        return text if text in ("true", "false") else None
    if value_type == TYPE_DATE:
        return iso_date(value)
    if value_type == TYPE_XYZ:
        return xyz_text(value)
    return None


def promote(attributes: dict, file_class: str) -> List[dict]:
    """Typed ``{metadataKey, metadataValue, metadataValueType}`` items for one file's attributes, in
    catalogue order, each key at most once."""
    attributes = attributes if isinstance(attributes, dict) else {}
    items: List[dict] = []
    seen = set()
    for field in PROMOTED_FIELDS:
        if file_class not in field.classes or field.key in seen:
            continue
        raw = first_present(attributes, field.source)
        if raw is None:
            continue
        value = field.transform(raw, attributes) if field.transform else raw
        text = render_value(value, field.value_type)
        if text is None:
            continue
        seen.add(field.key)
        items.append({"metadataKey": field.key, "metadataValue": text, "metadataValueType": field.value_type})
    return items


# --- GeoJSON rules, restated from backend/backend/models/metadata.py (pinned equal by the tests) ---

MAX_GEOJSON_NESTING_DEPTH = 32

GEOJSON_GEOMETRY_TYPES = frozenset({"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                                    "MultiPolygon", "GeometryCollection"})


def _validate_lon_lat(coord, label: str) -> None:
    if not isinstance(coord, (list, tuple)) or len(coord) < 2:
        raise ValueError(f"{label} must be a [lon, lat] coordinate pair")
    lon, lat = coord[0], coord[1]
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        raise ValueError(f"{label} must contain numeric lon/lat values")
    if lon < -180 or lon > 180:
        raise ValueError(f"{label} longitude must be between -180 and 180")
    if lat < -90 or lat > 90:
        raise ValueError(f"{label} latitude must be between -90 and 90")


def _validate_linear_ring(ring, label: str) -> None:
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError(f"{label} must contain at least 4 positions (3 unique + closing)")
    for index, position in enumerate(ring):
        _validate_lon_lat(position, f"{label}[{index}]")
    first, last = ring[0], ring[-1]
    if first[0] != last[0] or first[1] != last[1]:
        raise ValueError(f"{label} must be closed: first and last positions must be identical")
    for index in range(1, len(ring)):
        previous, current = ring[index - 1], ring[index]
        if previous[0] == current[0] and previous[1] == current[1]:
            raise ValueError(f"{label} contains consecutive duplicate vertex at index {index}")
    for index in range(1, len(ring) - 1):
        if ring[index][0] == first[0] and ring[index][1] == first[1]:
            raise ValueError(f"{label} closes prematurely at index {index}; the starting vertex "
                             f"may only appear at the beginning and end of the ring")
    if len({tuple(position[:2]) for position in ring[:-1]}) < 3:
        raise ValueError(f"{label} must have at least 3 unique vertices")


def _validate_geometry(geom, label: str = "geometry", depth: int = 1) -> None:
    if depth > MAX_GEOJSON_NESTING_DEPTH:
        raise ValueError(f"geometries are nested more than {MAX_GEOJSON_NESTING_DEPTH} levels deep")
    if not isinstance(geom, dict):
        raise ValueError(f"{label} must be a JSON object")
    geometry_type = geom.get("type")
    if geometry_type not in GEOJSON_GEOMETRY_TYPES:
        raise ValueError(f"{label} type is not a supported GeoJSON geometry type "
                         f"(expected one of: {', '.join(sorted(GEOJSON_GEOMETRY_TYPES))})")
    if geometry_type == "GeometryCollection":
        geometries = geom.get("geometries")
        if not isinstance(geometries, list) or not geometries:
            raise ValueError(f"{label} GeometryCollection must contain a non-empty geometries array")
        for index, sub in enumerate(geometries):
            _validate_geometry(sub, f"{label}.geometries[{index}]", depth + 1)
        return
    coords = geom.get("coordinates")
    if coords is None:
        raise ValueError(f"{label} must contain coordinates")
    if geometry_type == "Point":
        _validate_lon_lat(coords, f"{label}.coordinates")
    elif geometry_type in ("MultiPoint", "LineString"):
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{label}.coordinates must be a non-empty array")
        for index, coord in enumerate(coords):
            _validate_lon_lat(coord, f"{label}.coordinates[{index}]")
        if geometry_type == "LineString" and len(coords) < 2:
            raise ValueError(f"{label} LineString needs at least 2 positions")
    elif geometry_type == "MultiLineString":
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{label}.coordinates must be a non-empty array")
        for index, line in enumerate(coords):
            if not isinstance(line, list) or len(line) < 2:
                raise ValueError(f"{label}.coordinates[{index}] needs at least 2 positions")
            for inner, coord in enumerate(line):
                _validate_lon_lat(coord, f"{label}.coordinates[{index}][{inner}]")
    elif geometry_type == "Polygon":
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{label}.coordinates must be a non-empty array of rings")
        for index, ring in enumerate(coords):
            _validate_linear_ring(ring, f"{label}.coordinates[{index}]")
    elif geometry_type == "MultiPolygon":
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{label}.coordinates must be a non-empty array of polygons")
        for index, polygon in enumerate(coords):
            if not isinstance(polygon, list) or not polygon:
                raise ValueError(f"{label}.coordinates[{index}] must be a non-empty array of rings")
            for inner, ring in enumerate(polygon):
                _validate_linear_ring(ring, f"{label}.coordinates[{index}][{inner}]")


def validate_geojson_value(parsed, label: str = "GeoJSON") -> None:
    """Accepts a Geometry, Feature or FeatureCollection; raises ``ValueError`` otherwise."""
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} value must be a JSON object")
    kind = parsed.get("type")
    if kind == "Feature":
        geom = parsed.get("geometry")
        if geom is None:
            raise ValueError(f"{label} Feature must contain a geometry")
        _validate_geometry(geom, f"{label}.geometry")
        return
    if kind == "FeatureCollection":
        features = parsed.get("features")
        if not isinstance(features, list) or not features:
            raise ValueError(f"{label} FeatureCollection must contain a non-empty features array")
        for index, feature in enumerate(features):
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError(f"{label}.features[{index}] must be a GeoJSON Feature")
            geom = feature.get("geometry")
            if geom is None:
                raise ValueError(f"{label}.features[{index}] must contain a geometry")
            _validate_geometry(geom, f"{label}.features[{index}].geometry")
        return
    _validate_geometry(parsed, label)


# --- location derivation ---

def _coord(value) -> Optional[float]:
    number = _as_number(value)
    return None if number is None else round(number, COORDINATE_DECIMALS)


def _point(lon, lat, alt=None) -> Optional[dict]:
    coordinates = [_coord(lon), _coord(lat)]
    if None in coordinates:
        return None
    altitude = _coord(alt) if alt is not None else None
    if altitude is not None:
        coordinates.append(altitude)
    return {"type": "Point", "coordinates": coordinates}


def bbox_geometry(west, south, east, north) -> Optional[dict]:
    """A closed counter-clockwise Polygon ring around the box; a Point when the box has no extent."""
    corners = [_coord(west), _coord(south), _coord(east), _coord(north)]
    if None in corners:
        return None
    west, south, east, north = corners
    if west == east and south == north:
        return {"type": "Point", "coordinates": [west, south]}
    return {"type": "Polygon",
            "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]]}


def _location_from_gps(attributes: dict) -> Optional[dict]:
    gps = lookup(attributes, "sys_image.exif.gps")
    if not isinstance(gps, dict):
        return None
    return _point(gps.get("longitude"), gps.get("latitude"), gps.get("altitude"))


def _location_from_site(attributes: dict) -> Optional[dict]:
    site = lookup(attributes, "sys_ifc.site")
    if not isinstance(site, dict):
        return None
    return _point(site.get("longitude"), site.get("latitude"), site.get("elevation"))


def _location_from_pointcloud(attributes: dict) -> Optional[dict]:
    cloud = attributes.get("sys_pointcloud")
    if not isinstance(cloud, dict):
        return None
    crs = cloud.get("crs")
    if not isinstance(crs, dict) or crs.get("geographic") is not True:
        return None
    bounds_min, bounds_max = cloud.get("boundsMin"), cloud.get("boundsMax")
    if not (isinstance(bounds_min, (list, tuple)) and isinstance(bounds_max, (list, tuple))
            and len(bounds_min) >= 2 and len(bounds_max) >= 2):
        return None
    return bbox_geometry(bounds_min[0], bounds_min[1], bounds_max[0], bounds_max[1])


def _location_from_region(attributes: dict) -> Optional[dict]:
    region = lookup(attributes, "sys_tiles3d.region")
    if not (isinstance(region, (list, tuple)) and len(region) >= 4):
        return None
    numbers = [_as_number(value) for value in region[:4]]
    if any(number is None for number in numbers):
        return None
    west, south, east, north = (math.degrees(number) for number in numbers)
    return bbox_geometry(west, south, east, north)


def _positions(coordinates) -> List[list]:
    """Every position in a coordinates array of any nesting depth."""
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        return []
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in coordinates[:2]) \
            and len(coordinates) >= 2 and not isinstance(coordinates[0], (list, tuple)):
        return [list(coordinates)]
    found: List[list] = []
    for item in coordinates:
        found.extend(_positions(item))
    return found


def _geometry_positions(geometry) -> List[list]:
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "GeometryCollection":
        found: List[list] = []
        for sub in geometry.get("geometries") or []:
            found.extend(_geometry_positions(sub))
        return found
    return _positions(geometry.get("coordinates"))


def _location_from_footprint(attributes: dict) -> Optional[dict]:
    footprint = lookup(attributes, "sys_geo.footprint")
    if not isinstance(footprint, dict):
        return None
    if footprint.get("type") in ("Point", "Polygon"):
        return footprint
    positions = _geometry_positions(footprint)
    if not positions:
        return None
    longitudes = [position[0] for position in positions]
    latitudes = [position[1] for position in positions]
    return bbox_geometry(min(longitudes), min(latitudes), max(longitudes), max(latitudes))


# (classes, derivation) tried in order; a class matches at most one row.
LOCATION_SOURCES: Tuple[Tuple[FrozenSet[str], Callable[[dict], Optional[dict]]], ...] = (
    (frozenset({fc.CLASS_IMAGE}), _location_from_gps),
    (frozenset({fc.CLASS_IFC}), _location_from_site),
    (POINT_CLASSES, _location_from_pointcloud),
    (frozenset({fc.CLASS_TILES3D}), _location_from_region),
    (frozenset({fc.CLASS_DATA}), _location_from_footprint),
)


def location_geojson(attributes: dict, file_class: str) -> Optional[dict]:
    """A GeoJSON Point or Polygon for the file's position, or ``None``. The class selects the source;
    a candidate that fails the GeoJSON rules is dropped rather than written."""
    attributes = attributes if isinstance(attributes, dict) else {}
    for classes, derive in LOCATION_SOURCES:
        if file_class not in classes:
            continue
        candidate = derive(attributes)
        if candidate is None:
            return None
        try:
            validate_geojson_value(candidate)
        except ValueError:
            return None
        return candidate
    return None
