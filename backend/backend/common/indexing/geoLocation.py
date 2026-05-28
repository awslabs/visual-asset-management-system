# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Helpers for deriving the geo_MD_location field on asset and file documents.

Indexers call build_geo_location(metadata) which returns a GeoJSON dict
suitable for OpenSearch geo_shape, or None if no usable location data
is present. The 'location' metadata key (case-insensitive) takes priority
over individual latitude / longitude / altitude fields.
"""

import json
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
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_valid_lon_lat(lon: float, lat: float) -> bool:
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
    """Build a GeoJSON Point from individual lat/lon (and optional altitude) fields."""
    lat = _coerce_float(_ci_lookup(metadata, _LAT_KEYS))
    lon = _coerce_float(_ci_lookup(metadata, _LON_KEYS))
    alt = _coerce_float(_ci_lookup(metadata, _ALT_KEYS))

    if lat is None or lon is None or not _is_valid_lon_lat(lon, lat):
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
      1. 'location' metadata key (GeoJSON, JSON object, or 'lat,lon[,alt]' string)
      2. Individual latitude / longitude / altitude fields

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

    return _point_from_lla(metadata)
