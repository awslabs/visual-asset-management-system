# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Helpers for deriving the geo_MD_location field on asset and file documents.

Indexers call build_geo_location(metadata) which returns a GeoJSON dict
suitable for OpenSearch geo_shape, or None if no usable location data
is present. The 'location' metadata key (case-insensitive) takes priority
over individual latitude / longitude / altitude fields.

The secondary latitude / longitude / altitude path accepts both numeric
and STRING values (VAMS stores most metadata as strings in DynamoDB),
trims whitespace, rejects null / empty / non-numeric / boolean / NaN /
Infinity, and enforces the standard WGS84 ranges (lat in [-90, 90],
lon in [-180, 180]) before populating geo_MD_location.
"""

import json
import math
from typing import Any, Dict, Optional, Tuple

from customLogging.logger import safeLogger

logger = safeLogger(service_name="GeoLocation")

GEO_LOCATION_FIELD = "geo_MD_location"

_VALID_GEOJSON_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
}

_LAT_KEYS = ("latitude", "lat")
_LON_KEYS = ("longitude", "lon", "lng", "long")
_ALT_KEYS = ("altitude", "alt", "elevation")
_LOCATION_KEYS = ("location",)


def _ci_lookup(metadata: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[Any]:
    """Case-insensitive lookup over a flat metadata dict, returning the first match."""
    if not metadata:
        return None
    lowered = {k.lower(): k for k in metadata.keys() if isinstance(k, str)}
    for key in keys:
        original = lowered.get(key)
        if original is not None:
            value = metadata.get(original)
            if value not in (None, ""):
                return value
    return None


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce a metadata value to a finite float, or return None.

    Accepts ints, floats, and numeric strings (with surrounding whitespace).
    Rejects None, empty / whitespace-only strings, booleans (Python's bool
    is a subclass of int and would otherwise convert to 0.0/1.0 silently),
    non-numeric strings, NaN, and ±Infinity. The finiteness check matters
    for the geo_MD_location pipeline because OpenSearch will reject NaN /
    Infinity coordinates with a mapper_parsing_exception.
    """
    if value is None:
        return None
    # Booleans must be rejected explicitly because bool subclasses int and
    # would otherwise pass through `float(value)` as 0.0 or 1.0, producing
    # silent (and wrong) lat=1.0 / lon=0.0 records for misconfigured
    # metadata.
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            result = float(stripped)
        except (TypeError, ValueError):
            return None
    elif isinstance(value, (int, float)):
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
    else:
        # Reject dicts, lists, tuples, custom objects — these shouldn't
        # appear here as a single coordinate component.
        return None
    if not math.isfinite(result):
        return None
    return result


def _is_valid_lon_lat(lon: float, lat: float) -> bool:
    """Range-check WGS84 longitude/latitude. Assumes finite inputs."""
    return -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def _normalize_geojson_geometry(value: Any) -> Optional[Dict[str, Any]]:
    """
    Accept a GeoJSON Geometry, Feature, or FeatureCollection and return the
    underlying geometry. Returns None if the value is not a valid GeoJSON
    structure suitable for an OpenSearch geo_shape.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None

    if not isinstance(value, dict):
        return None

    geo_type = value.get("type")
    if not isinstance(geo_type, str):
        return None

    if geo_type == "Feature":
        return _normalize_geojson_geometry(value.get("geometry"))

    if geo_type == "FeatureCollection":
        features = value.get("features") or []
        if not features:
            return None
        return _normalize_geojson_geometry(features[0])

    if geo_type in _VALID_GEOJSON_TYPES and value.get("coordinates") is not None:
        return {"type": geo_type, "coordinates": value["coordinates"]}

    return None


def _point_from_lla(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a GeoJSON Point from individual lat/lon (and optional altitude) fields.

    Values may be numeric or string. Returns None unless BOTH lat and lon
    coerce to finite floats AND fall within the WGS84 valid range. This
    is the secondary path used when the 'location' metadata key isn't
    present, and it's important the field stays absent from the indexed
    document when the inputs are bad — populating geo_MD_location with a
    nonsense point would surface as a phantom result on the search map.
    """
    raw_lat = _ci_lookup(metadata, _LAT_KEYS)
    raw_lon = _ci_lookup(metadata, _LON_KEYS)
    raw_alt = _ci_lookup(metadata, _ALT_KEYS)

    # Nothing to do — neither key is present. Quiet path; no logging.
    if raw_lat is None and raw_lon is None:
        return None

    lat = _coerce_float(raw_lat)
    lon = _coerce_float(raw_lon)
    alt = _coerce_float(raw_alt)  # Optional; OK if it stays None.

    if lat is None or lon is None:
        # At least one coordinate field is present but unparseable. Log so
        # an operator can diagnose why the document isn't appearing on the
        # geospatial map view.
        logger.info(
            "geo_MD_location: skipping document — lat/lon present but not parseable as finite floats "
            "(lat=%r, lon=%r)",
            raw_lat, raw_lon,
        )
        return None

    if not _is_valid_lon_lat(lon, lat):
        logger.info(
            "geo_MD_location: skipping document — lat/lon out of WGS84 range "
            "(lat=%s, lon=%s)",
            lat, lon,
        )
        return None

    coordinates = [lon, lat, alt] if alt is not None else [lon, lat]
    return {"type": "Point", "coordinates": coordinates}


def _point_from_location_string(value: str) -> Optional[Dict[str, Any]]:
    """Parse 'lat,lon[,alt]' or '{"latitude":..,"longitude":..}' formats."""
    text = value.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            geom = _normalize_geojson_geometry(parsed)
            if geom is not None:
                return geom
            return _point_from_lla(parsed)
        return None

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    lat = _coerce_float(parts[0])
    lon = _coerce_float(parts[1])
    alt = _coerce_float(parts[2]) if len(parts) >= 3 else None
    if lat is None or lon is None or not _is_valid_lon_lat(lon, lat):
        return None
    coordinates = [lon, lat, alt] if alt is not None else [lon, lat]
    return {"type": "Point", "coordinates": coordinates}


def build_geo_location(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Derive a GeoJSON shape for the geo_MD_location field from a flat metadata dict.

    Priority:
      1. 'location' metadata key (GeoJSON Geometry / Feature / FeatureCollection,
         a JSON object with latitude/longitude fields, or a "lat,lon[,alt]"
         comma-delimited string).
      2. Individual latitude / longitude / altitude metadata fields, accepting
         either numeric or string values. The fallback is only applied when
         (1) yields no usable shape, so a malformed 'location' key won't
         silently fall through to potentially-stale lat/lon fields elsewhere
         on the same document.

    The lat/lon fallback validates: not None / empty, parses as a finite
    float (rejecting NaN, ±Infinity, booleans), and falls within the
    WGS84 ranges (lat in [-90, 90], lon in [-180, 180]). Anything that
    fails these checks results in geo_MD_location being absent rather
    than holding bogus coordinates that would render as phantom dots on
    the geospatial map view.

    Returns a GeoJSON Geometry dict (Point, Polygon, etc.) or None.
    """
    if not metadata or not isinstance(metadata, dict):
        return None

    location = _ci_lookup(metadata, _LOCATION_KEYS)
    if location is not None:
        if isinstance(location, dict):
            geom = _normalize_geojson_geometry(location)
            if geom is not None:
                return geom
            point = _point_from_lla(location)
            if point is not None:
                return point
        elif isinstance(location, str):
            point = _point_from_location_string(location)
            if point is not None:
                return point

    # Secondary path: individual latitude / longitude / altitude fields.
    return _point_from_lla(metadata)
